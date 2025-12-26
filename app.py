#!/usr/bin/env python3
"""
SPX Overnight Vol Premium Bot - Railway Production
Uses Alpha Vantage FREE tier with SPY/VXX proxies
Triple-Layer Filtering: Algo Dedup → Keyword Filter → GPT Analysis

Test endpoints available for future API exploration:
- /test_alpha_spy_vxx (Alpha Vantage - CURRENT)
- /test_polygon_massive (Polygon/Massive)
- /test_fmp (Financial Modeling Prep)
- /test_yahoo (Yahoo Finance)
- /test_marketstack_all (Marketstack)
"""

from flask import Flask, jsonify
import json
import math
import requests
from datetime import datetime, timedelta, time as dt_time
import pytz
import os
import threading
import time as time_module
from dateutil import parser as date_parser
import re
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET

app = Flask(__name__)

# Configuration - RAILWAY VERSION
ET_TZ = pytz.timezone('US/Eastern')

# Trading windows - PRODUCTION: 2:30-3:30 PM ET
# Trading windows - TESTING MODE: 24 hours
# PRODUCTION: 2:30-3:30 PM ET
TRADING_WINDOW_START = dt_time(hour=0, minute=0)   # Midnight
TRADING_WINDOW_END = dt_time(hour=23, minute=59)   # 11:59 PM

def load_config():
    """Load configuration from environment variables (Railway)"""
    config = {
        'OPENAI_API_KEY': os.environ.get('OPENAI_API_KEY'),
        'ALPHA_VANTAGE_KEY': os.environ.get('ALPHA_VANTAGE_KEY'),
        'TRADE_AGGRESSIVE_URL': os.environ.get('TRADE_AGGRESSIVE_URL'),
        'TRADE_NORMAL_URL': os.environ.get('TRADE_NORMAL_URL'),
        'TRADE_CONSERVATIVE_URL': os.environ.get('TRADE_CONSERVATIVE_URL'),
        'NO_TRADE_URL': os.environ.get('NO_TRADE_URL')
    }
    
    missing = [k for k, v in config.items() if not v]
    if missing:
        raise ValueError(f"Missing environment variables: {', '.join(missing)}")
    
    return config

CONFIG = load_config()
OPENAI_API_KEY = CONFIG.get('OPENAI_API_KEY')
ALPHA_VANTAGE_KEY = CONFIG.get('ALPHA_VANTAGE_KEY')

WEBHOOK_URLS = {
    'TRADE_AGGRESSIVE': CONFIG.get('TRADE_AGGRESSIVE_URL'),
    'TRADE_NORMAL': CONFIG.get('TRADE_NORMAL_URL'),
    'TRADE_CONSERVATIVE': CONFIG.get('TRADE_CONSERVATIVE_URL'),
    'NO_TRADE': CONFIG.get('NO_TRADE_URL')
}

# ============================================================================
# LAYER 1: ALGO DEDUPLICATION - Strong fuzzy matching
# ============================================================================

def normalize_title(title):
    """Normalize title for comparison"""
    normalized = title.lower()
    normalized = re.sub(r'[^\w\s]', '', normalized)
    normalized = ' '.join(normalized.split())
    return normalized

def titles_are_similar(title1, title2, threshold=0.85):
    """Check if two titles are 85%+ similar"""
    norm1 = normalize_title(title1)
    norm2 = normalize_title(title2)
    similarity = SequenceMatcher(None, norm1, norm2).ratio()
    return similarity >= threshold

def deduplicate_articles_smart(articles):
    """
    LAYER 1: Algorithmic deduplication with fuzzy matching
    Keeps the BEST version (most recent + best source priority)
    """
    if not articles:
        return []
    
    source_priority = {
        'Reuters': 1,
        'Bloomberg': 2,
        'Google News': 3,
        'Yahoo Finance': 4,
        'CNBC': 5,
        'MarketWatch': 6,
        'Other': 99
    }
    
    def get_source_priority(article):
        source = article.get('source', 'Other')
        for key in source_priority:
            if key.lower() in source.lower():
                return source_priority[key]
        return source_priority['Other']
    
    articles_sorted = sorted(
        articles,
        key=lambda x: (
            -x['published_time'].timestamp(),
            get_source_priority(x)
        )
    )
    
    unique = []
    seen_normalized = {}
    
    for article in articles_sorted:
        title = article['title']
        norm_title = normalize_title(title)
        
        if norm_title in seen_normalized:
            continue
        
        is_duplicate = False
        for seen_norm, seen_data in seen_normalized.items():
            if titles_are_similar(title, seen_data['original']):
                is_duplicate = True
                break
        
        if not is_duplicate:
            unique.append(article)
            seen_normalized[norm_title] = {
                'original': title,
                'article': article
            }
    
    return unique

# ============================================================================
# LAYER 2: KEYWORD FILTER - Remove obvious junk
# ============================================================================

def is_obvious_junk(title, description=""):
    """LAYER 2: Lenient keyword filter - only remove OBVIOUS junk"""
    obvious_junk_patterns = [
        r'secret to', r'trick to', r'\d+ ways to', r'you won\'t believe',
        r'shocking', r'amazing', r'incredible',
        r'^why you should', r'^how to', r'^what you need to know about investing',
        r'last week.*recap', r'last month.*review', r'year in review'
    ]
    
    content = (title + " " + description).lower()
    is_junk = any(re.search(pattern, content) for pattern in obvious_junk_patterns)
    return is_junk

def classify_priority(title, description=""):
    """Mark high-priority events"""
    high_priority_patterns = [
        r'(beats|misses|reports) earnings',
        r'earnings (beat|miss)',
        r'q[1-4] (earnings|results)',
        r'(raises|cuts|lowers|increases) (guidance|forecast|outlook)',
        r'stock (sinks|soars|jumps|plunges) \d+%',
        r'shares (fall|rise|jump) \d+%',
        r'(up|down) (1[0-9]|[2-9][0-9])%',
        r'(apple|microsoft|google|alphabet|amazon|nvidia|tesla|meta).*'
        r'(upgrade|downgrade|price target)',
        r'announces (acquisition|merger|layoffs|ceo)',
        r'completes (acquisition|merger)',
        r'sec (approves|rejects|investigates)',
        r'fda (approves|rejects)',
    ]
    
    content = (title + " " + description).lower()
    is_high_priority = any(re.search(pattern, content) for pattern in high_priority_patterns)
    return 'HIGH' if is_high_priority else 'NORMAL'

def filter_news_lenient(articles, verbose=False):
    """LAYER 2: Lenient keyword filter"""
    filtered = []
    stats = {'filtered_junk': 0, 'kept': 0}
    
    for article in articles:
        title = article.get('title', '')
        description = article.get('description', '')
        
        if is_obvious_junk(title, description):
            stats['filtered_junk'] += 1
            continue
        
        article['priority'] = classify_priority(title, description)
        stats['kept'] += 1
        filtered.append(article)
    
    return filtered, stats

# ============================================================================
# NEWS FETCHING - NO FEEDPARSER (Direct HTTP + XML)
# ============================================================================

def parse_rss_feed(url, source_name):
    """
    Parse RSS feed using direct HTTP + XML parsing
    NO FEEDPARSER DEPENDENCY - Works on any Python version
    """
    try:
        response = requests.get(
            url, 
            timeout=15, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        response.raise_for_status()
        
        # Parse XML
        root = ET.fromstring(response.content)
        articles = []
        now = datetime.now(ET_TZ)
        
        # Find all <item> elements in RSS feed
        for item in root.findall('.//item'):
            try:
                # Extract title
                title_elem = item.find('title')
                title = title_elem.text if title_elem is not None and title_elem.text else 'No title'
                
                # Extract link
                link_elem = item.find('link')
                link = link_elem.text if link_elem is not None and link_elem.text else ''
                
                # Extract description
                description_elem = item.find('description')
                description = description_elem.text if description_elem is not None and description_elem.text else ''
                
                # Extract and parse publication date
                pubdate_elem = item.find('pubDate')
                if pubdate_elem is not None and pubdate_elem.text:
                    try:
                        pub_time = date_parser.parse(pubdate_elem.text)
                        if pub_time.tzinfo is None:
                            pub_time = ET_TZ.localize(pub_time)
                        else:
                            pub_time = pub_time.astimezone(ET_TZ)
                    except:
                        pub_time = now
                else:
                    pub_time = now
                
                hours_ago = (now - pub_time).total_seconds() / 3600
                
                articles.append({
                    'title': title,
                    'published_time': pub_time,
                    'hours_ago': hours_ago,
                    'source': source_name,
                    'description': description,
                    'link': link
                })
                
            except Exception as e:
                print(f"Error parsing item from {source_name}: {e}")
                continue
        
        return articles
        
    except Exception as e:
        print(f"Error fetching {source_name}: {e}")
        return []

def fetch_yahoo_finance_news():
    """Source 1: Yahoo Finance RSS (using direct HTTP)"""
    try:
        rss_feeds = [
            ('https://finance.yahoo.com/news/rssindex', 'Yahoo Finance - Market'),
            ('https://finance.yahoo.com/rss/headline?s=^GSPC', 'Yahoo Finance - S&P 500'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=AAPL&region=US&lang=en-US', 'Yahoo Finance - Apple'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=MSFT&region=US&lang=en-US', 'Yahoo Finance - Microsoft'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=GOOGL&region=US&lang=en-US', 'Yahoo Finance - Google'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=AMZN&region=US&lang=en-US', 'Yahoo Finance - Amazon'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=NVDA&region=US&lang=en-US', 'Yahoo Finance - Nvidia'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=TSLA&region=US&lang=en-US', 'Yahoo Finance - Tesla'),
            ('https://feeds.finance.yahoo.com/rss/2.0/headline?s=META&region=US&lang=en-US', 'Yahoo Finance - Meta'),
        ]
        
        all_articles = []
        for feed_url, feed_name in rss_feeds:
            articles = parse_rss_feed(feed_url, feed_name)
            all_articles.extend(articles)
        
        return all_articles
        
    except Exception as e:
        print(f"ERROR in Yahoo Finance: {e}")
        return []

def fetch_google_news_rss():
    """Source 2: Google News RSS (using direct HTTP)"""
    try:
        queries = [
            'stock+market+OR+S%26P+500',
            'earnings+OR+guidance',
            'Apple+OR+Microsoft+OR+Google+OR+Amazon',
            'Nvidia+OR+Tesla+OR+Meta',
            'Federal+Reserve+OR+inflation'
        ]
        
        all_articles = []
        for query in queries:
            url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
            articles = parse_rss_feed(url, 'Google News')
            all_articles.extend(articles)
        
        return all_articles
        
    except Exception as e:
        print(f"ERROR in Google News: {e}")
        return []

def fetch_news_multi_source():
    """
    Triple-layer news processing:
    Layer 1: Algo dedup → Layer 2: Keyword filter → Layer 3: GPT (sent below)
    """
    try:
        all_articles = []
        
        # Fetch from all sources
        yahoo_articles = fetch_yahoo_finance_news()
        google_articles = fetch_google_news_rss()
        
        all_articles.extend(yahoo_articles)
        all_articles.extend(google_articles)
        
        raw_count = len(all_articles)
        
        if not all_articles:
            return {
                'count': 0,
                'summary': 'No news available.',
                'articles': [],
                'filter_stats': {
                    'raw_articles': 0,
                    'duplicates_removed': 0,
                    'unique_articles': 0,
                    'junk_filtered': 0,
                    'sent_to_gpt': 0
                }
            }
        
        # LAYER 1: Strong deduplication
        unique_articles = deduplicate_articles_smart(all_articles)
        duplicates_removed = raw_count - len(unique_articles)
        
        # LAYER 2: Keyword filter
        filtered_articles, filter_stats = filter_news_lenient(unique_articles, verbose=False)
        
        if not filtered_articles:
            return {
                'count': 0,
                'summary': 'No actionable news after filtering.',
                'articles': [],
                'filter_stats': {
                    'raw_articles': raw_count,
                    'duplicates_removed': duplicates_removed,
                    'unique_articles': len(unique_articles),
                    'junk_filtered': filter_stats['filtered_junk'],
                    'sent_to_gpt': 0
                }
            }
        
        # Sort by time (most recent first)
        filtered_articles.sort(key=lambda x: x['published_time'], reverse=True)
        
        # Format for GPT (Layer 3)
        news_summary = ""
        for article in filtered_articles[:30]:
            time_str = article['published_time'].strftime("%I:%M %p")
            hours_ago = article['hours_ago']
            
            if hours_ago < 1:
                recency = "⚠️ VERY RECENT"
            elif hours_ago < 3:
                recency = "🔸 RECENT"
            elif hours_ago < 6:
                recency = "• Somewhat recent"
            else:
                recency = "• Earlier today"
            
            priority = article.get('priority', 'NORMAL')
            priority_marker = "🔥" if priority == 'HIGH' else ""
            
            news_summary += f"[{time_str}] {recency} {priority_marker} ({article['source']})\n"
            news_summary += f"   {article['title']}\n"
            if article['description']:
                desc = article['description'][:150]
                news_summary += f"   {desc}...\n"
            news_summary += "\n"
        
        return {
            'count': len(filtered_articles),
            'summary': news_summary.strip(),
            'articles': filtered_articles[:30],
            'filter_stats': {
                'raw_articles': raw_count,
                'duplicates_removed': duplicates_removed,
                'unique_articles': len(unique_articles),
                'junk_filtered': filter_stats['filtered_junk'],
                'sent_to_gpt': filter_stats['kept']
            }
        }
        
    except Exception as e:
        print(f"CRITICAL ERROR in fetch_news_multi_source: {e}")
        import traceback
        traceback.print_exc()
        return {
            'count': 0,
            'summary': f'News fetch failed: {str(e)}',
            'articles': [],
            'filter_stats': {
                'raw_articles': 0,
                'duplicates_removed': 0,
                'unique_articles': 0,
                'junk_filtered': 0,
                'sent_to_gpt': 0
            }
        }

# ============================================================================
# DATA FETCHING - ALPHA VANTAGE FREE TIER (SPY/VXX proxies)
# ============================================================================

def get_spy_data_alpha():
    """
    Fetch SPY data from Alpha Vantage (SPX proxy)
    SPY × 10 ≈ SPX (official recommendation from Alpha Vantage)
    """
    try:
        print("  [Alpha Vantage] Fetching SPY (SPX proxy)...")
        
        # Get SPY quote
        quote_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPY&apikey={ALPHA_VANTAGE_KEY}"
        quote_response = requests.get(quote_url, timeout=10)
        
        if quote_response.status_code != 200:
            print(f"  ❌ Alpha Vantage SPY quote failed: {quote_response.status_code}")
            return None
        
        quote_data = quote_response.json()
        
        if 'Global Quote' not in quote_data or '05. price' not in quote_data['Global Quote']:
            print(f"  ❌ No SPY quote data")
            return None
        
        spy_price = float(quote_data['Global Quote']['05. price'])
        spy_high = float(quote_data['Global Quote']['03. high'])
        spy_low = float(quote_data['Global Quote']['04. low'])
        
        # Convert to SPX equivalent
        spx_price = spy_price * 10
        spx_high = spy_high * 10
        spx_low = spy_low * 10
        
        print(f"  ✅ SPY: {spy_price:.2f} → SPX equivalent: {spx_price:.2f}")
        
        # Delay to respect rate limit (5 calls/minute = 12 seconds between calls)
        time_module.sleep(12)
        
        # Get SPY historical data
        hist_url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=SPY&outputsize=compact&apikey={ALPHA_VANTAGE_KEY}"
        hist_response = requests.get(hist_url, timeout=10)
        
        if hist_response.status_code != 200:
            print(f"  ❌ Alpha Vantage SPY history failed: {hist_response.status_code}")
            return None
        
        hist_data = hist_response.json()
        
        if 'Time Series (Daily)' not in hist_data:
            print(f"  ❌ No SPY historical data")
            return None
        
        ts = hist_data['Time Series (Daily)']
        dates = sorted(list(ts.keys()), reverse=True)
        
        # Get last 25 closes and convert to SPX equivalent
        spy_closes = [float(ts[date]['4. close']) for date in dates[:25]]
        spx_closes = [c * 10 for c in spy_closes]
        
        print(f"  ✅ Got {len(spx_closes)} days of SPX historical data (from SPY)")
        
        return {
            'current': spx_price,
            'high_today': spx_high,
            'low_today': spx_low,
            'history_closes': spx_closes
        }
        
    except Exception as e:
        print(f"  ❌ Alpha Vantage SPY error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_vxx_data_alpha():
    """
    Fetch VXX data from Alpha Vantage (VIX proxy)
    VXX = VIX futures ETF (official recommendation from Alpha Vantage)
    """
    try:
        print("  [Alpha Vantage] Fetching VXX (VIX proxy)...")
        
        # Delay before VXX call
        time_module.sleep(12)
        
        # Get VXX quote
        quote_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=VXX&apikey={ALPHA_VANTAGE_KEY}"
        response = requests.get(quote_url, timeout=10)
        
        if response.status_code != 200:
            print(f"  ❌ Alpha Vantage VXX failed: {response.status_code}")
            return None
        
        data = response.json()
        
        if 'Global Quote' not in data or '05. price' not in data['Global Quote']:
            print(f"  ❌ No VXX quote data")
            return None
        
        vxx_value = float(data['Global Quote']['05. price'])
        print(f"  ✅ VXX: {vxx_value:.2f} (VIX proxy)")
        
        # VXX is roughly 1/4 to 1/5 of VIX, but we'll use it directly
        # as a volatility indicator for IV/RV ratio
        
        return {
            'current': vxx_value,
            'tenor': 'VXX-based (VIX futures proxy)',
            'source': 'Alpha_Vantage_VXX',
            'method': 'Alpha Vantage FREE tier',
            'note': 'VXX tracks VIX futures, correlates with VIX'
        }
        
    except Exception as e:
        print(f"  ❌ Alpha Vantage VXX error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_spx_data_with_retry(max_retries=3):
    """Fetch SPY (SPX proxy) with retry"""
    for attempt in range(max_retries):
        try:
            print(f"  [Attempt {attempt + 1}/{max_retries}] Fetching SPY (SPX proxy)...")
            result = get_spy_data_alpha()
            
            if result is not None:
                print(f"  ✅ SPY fetch succeeded on attempt {attempt + 1}")
                return result
            
            print(f"  ⚠️ Attempt {attempt + 1} returned None, retrying...")
            time_module.sleep(12)
            
        except Exception as e:
            print(f"  ❌ Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time_module.sleep(12)
    
    return None


def get_overnight_iv_with_retry(max_retries=3):
    """Fetch VXX (VIX proxy) with retry"""
    for attempt in range(max_retries):
        try:
            print(f"  [Attempt {attempt + 1}/{max_retries}] Fetching VXX (VIX proxy)...")
            result = get_vxx_data_alpha()
            
            if result is not None:
                print(f"  ✅ VXX fetch succeeded on attempt {attempt + 1}")
                return result
            
            print(f"  ⚠️ Attempt {attempt + 1} returned None, retrying...")
            time_module.sleep(12)
            
        except Exception as e:
            print(f"  ❌ Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time_module.sleep(12)
    
    return None

# ============================================================================
# INDICATOR 1: IV/RV RATIO (30%)
# ============================================================================

def analyze_iv_rv_ratio(spx_data, iv_data):
    """
    Analyze IV/RV ratio
    Using VXX as volatility proxy vs 10-day RV
    Note: VXX ≠ VIX but correlates, so we use it as volatility indicator
    """
    # Use 10-day RV
    closes = spx_data['history_closes'][-11:]  # Last 11 days to get 10 returns
    
    # Calculate realized volatility
    returns = []
    for i in range(1, len(closes)):
        daily_return = math.log(closes[i] / closes[i-1])
        returns.append(daily_return)
    
    mean_return = sum(returns) / len(returns)
    squared_diffs = [(r - mean_return)**2 for r in returns]
    variance = sum(squared_diffs) / (len(returns) - 1)
    daily_std = math.sqrt(variance)
    realized_vol = daily_std * math.sqrt(252) * 100
    
    # VXX as volatility indicator (not exact VIX, but proxy)
    vxx_value = iv_data['current']
    
    # VXX typically ranges 15-50, VIX ranges 10-80
    # Rough conversion: VIX ≈ VXX * 0.6 (very approximate)
    implied_vol_estimate = vxx_value * 0.6
    
    iv_rv_ratio = implied_vol_estimate / realized_vol
    
    # Scoring logic (same as before)
    if iv_rv_ratio > 1.35:
        base_score = 1
    elif iv_rv_ratio > 1.20:
        base_score = 2
    elif iv_rv_ratio > 1.10:
        base_score = 3
    elif iv_rv_ratio > 1.00:
        base_score = 4
    elif iv_rv_ratio > 0.90:
        base_score = 6
    elif iv_rv_ratio > 0.80:
        base_score = 8
    else:
        base_score = 10
    
    # RV change modifier
    if len(spx_data['history_closes']) >= 21:
        closes_earlier = spx_data['history_closes'][-21:-10]
        returns_earlier = []
        for i in range(1, len(closes_earlier)):
            returns_earlier.append(math.log(closes_earlier[i] / closes_earlier[i-1]))
        
        mean_earlier = sum(returns_earlier) / len(returns_earlier)
        variance_earlier = sum([(r - mean_earlier)**2 for r in returns_earlier]) / (len(returns_earlier) - 1)
        rv_earlier = math.sqrt(variance_earlier) * math.sqrt(252) * 100
        
        rv_change = (realized_vol - rv_earlier) / rv_earlier if rv_earlier > 0 else 0
        
        if rv_change > 0.30:
            modifier = +3
        elif rv_change > 0.15:
            modifier = +2
        elif rv_change < -0.20:
            modifier = -1
        else:
            modifier = 0
    else:
        modifier = 0
        rv_change = 0
    
    final_score = max(1, min(10, base_score + modifier))
    
    return {
        'score': final_score,
        'realized_vol': round(realized_vol, 2),
        'implied_vol': round(implied_vol_estimate, 2),
        'iv_rv_ratio': round(iv_rv_ratio, 3),
        'vxx_value': round(vxx_value, 2),
        'tenor': iv_data.get('tenor', 'VXX-based'),
        'source': 'Alpha_Vantage_VXX_proxy',
        'rv_change': round(rv_change, 3)
    }

# ============================================================================
# INDICATOR 2: MARKET TREND (20%)
# ============================================================================

def analyze_market_trend(spx_data):
    """Analyze 5-day momentum and intraday volatility"""
    current = spx_data['current']
    closes = spx_data['history_closes']
    spx_5d_ago = closes[-6] if len(closes) >= 6 else current
    
    change_5d = (current - spx_5d_ago) / spx_5d_ago
    
    if change_5d > 0.04:
        base_score = 5
    elif change_5d > 0.02:
        base_score = 3
    elif change_5d > 0.01:
        base_score = 2
    elif change_5d > -0.01:
        base_score = 1
    elif change_5d > -0.02:
        base_score = 2
    elif change_5d > -0.04:
        base_score = 4
    else:
        base_score = 7
    
    high = spx_data['high_today']
    low = spx_data['low_today']
    intraday_range = (high - low) / current
    
    if intraday_range > 0.015:
        modifier = +2
    elif intraday_range > 0.010:
        modifier = +1
    else:
        modifier = 0
    
    final_score = max(1, min(10, base_score + modifier))
    
    return {
        'score': final_score,
        'change_5d': change_5d,
        'intraday_range': intraday_range
    }

# ============================================================================
# INDICATOR 3: GPT NEWS ANALYSIS (50%) - LAYER 3
# ============================================================================

def analyze_gpt_news(news_data):
    """
    LAYER 3: GPT Triple-Duty Analysis
    1. Duplication safety net (if algo missed any)
    2. Filter commentary/old news
    3. Analyze overnight risk
    """
    
    if news_data['count'] == 0:
        return {
            'score': 5,
            'raw_score': 5,
            'category': 'MODERATE',
            'reasoning': 'No actionable news available - assuming moderate baseline risk',
            'direction_risk': 'UNKNOWN',
            'key_risk': 'None',
            'duplicates_found': 'None'
        }
    
    now = datetime.now(ET_TZ)
    current_time_str = now.strftime("%I:%M %p ET")
    
    prompt = f"""You are an expert overnight volatility risk analyst for SPX iron condor positions.

CURRENT TIME: {current_time_str}

CONTEXT:
- Selling SPX iron condor NOW (2:30-3:30 PM entry)
- Holding OVERNIGHT (~16 hours until 9:30 AM tomorrow)
- Iron condor LOSES MONEY from BIG MOVES in EITHER DIRECTION

⚠️ TRIPLE-LAYER FILTERING SYSTEM:

LAYER 1 (COMPLETED): Algorithmic deduplication
- Removed duplicates using fuzzy matching (85% similarity threshold)
- Kept best version (most recent + best source)

LAYER 2 (COMPLETED): Keyword filter
- Removed obvious clickbait: "secret to", "trick to", "shocking"
- Removed obvious opinion: "why you should", "how to invest"
- Removed old retrospectives: "last week recap"

LAYER 3 (YOUR JOB - THREE RESPONSIBILITIES):

1️⃣ DUPLICATION SAFETY NET:
If you notice articles covering the SAME EVENT (algo may have missed some):
- Count as ONE event, not multiple
- Examples of duplicates:
  * "Apple earnings beat expectations" (Reuters)
  * "Apple beats Q4 earnings forecast" (Bloomberg)
  * "Apple Q4 results exceed expectations" (Yahoo)
  → These are ONE event (Apple earnings), not three!
  
- How to spot: Same company + same event + similar timeframe = Duplicate
- Don't let duplicates inflate your risk score
- Report in "duplicates_found" field

2️⃣ COMMENTARY/NEWS FILTER:
Filter out sophisticated commentary that keyword filter may have missed:

❌ FILTER OUT:
- Sophisticated commentary disguised as news:
  * "Warren Buffett dumps Apple - what it means" (analysis of known action)
  * "Why Nvidia's earnings matter for your portfolio" (opinion/advice)
  * "Here's how to play Tesla after earnings" (trading advice)
  
- Analysis of OLD events with fresh headlines:
  * "Markets digest yesterday's Fed decision" (old event)
  * "Investors react to last week's CPI print" (old data)
  * "Breaking down Apple's guidance from last quarter" (old news)
  
- Speculation dressed as news:
  * "Apple could announce new product if..." (speculation)
  * "What Tesla might do next quarter" (prediction)
  * "AMD may benefit from Nvidia's stumble" (hypothetical)

✅ ANALYZE:
- Earnings reports released TODAY
- Company announcements made in last 1-3 hours
- Analyst upgrades/downgrades issued TODAY
- Actual price moves happening NOW (stock sinks/soars X%)
- Breaking regulatory decisions
- Major product launches TODAY

3️⃣ OVERNIGHT RISK ANALYSIS:
For UNIQUE, ACTUAL events only:

TIMING - What's NOT PRICED IN:
🔥 Last 1 hour = NOT priced in → HIGHEST RISK
🔸 1-3 hours ago = Partially priced in → HIGH RISK
📊 3-8 hours ago but HUGE (Mag 7 earnings, major guidance) = Still digesting → MODERATE-HIGH
✅ 8+ hours ago = Mostly priced in → LOW RISK

Remember: Mag 7 (Apple, Microsoft, Google, Amazon, Nvidia, Tesla, Meta) = 30% of SPX
Their news has DIRECT SPX impact. Small-cap news = Ignore.

NEWS (may contain duplicates/commentary - YOU filter):
{news_data['summary']}

SCORING - Based on UNIQUE events only:
1-2: VERY_QUIET - No real unique catalysts, <0.3% overnight move
3-4: QUIET - 1-2 minor unique events mostly priced, 0.3-0.5% move
5-6: MODERATE - 2-3 real unique events, 0.5-0.8% move
7-8: ELEVATED - Major unique catalyst NOT fully priced, 0.8-1.2% move
9-10: EXTREME - Multiple major unique catalysts or one massive event, >1.2% move

In your reasoning, EXPLICITLY mention:
- Any duplicates you found (e.g., "Reuters + Bloomberg both covering Apple earnings = ONE event")
- What you filtered as commentary/old news
- What UNIQUE, ACTUAL events you found
- Why those events create overnight risk

Respond in JSON only (no markdown):
{{
  "overnight_magnitude_risk_score": 1-10,
  "risk_category": "VERY_QUIET/QUIET/MODERATE/ELEVATED/EXTREME",
  "reasoning": "MUST mention: (1) Any duplicates found, (2) Commentary filtered, (3) Unique events analyzed",
  "key_overnight_risk": "Single most important unique catalyst, or 'None - mostly commentary/duplicates'",
  "direction_risk": "UP/DOWN/BOTH/NONE",
  "duplicates_found": "List any duplicate articles (same event from multiple sources), or 'None'"
}}
"""
    
    try:
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": "gpt-4-turbo",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 800,
            "temperature": 0.3
        }
        
        response = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=30
        )
        
        if response.status_code != 200:
            print(f"OpenAI API error: {response.status_code}")
            return {
                'score': 5,
                'raw_score': 5,
                'category': 'MODERATE',
                'reasoning': f'API error: {response.status_code}',
                'direction_risk': 'UNKNOWN',
                'key_risk': 'API Error',
                'duplicates_found': 'Error'
            }
        
        result = response.json()
        response_text = result['choices'][0]['message']['content'].strip()
        
        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]
        
        gpt_analysis = json.loads(response_text)
        raw_score = gpt_analysis.get('overnight_magnitude_risk_score', 5)
        raw_score = max(1, min(10, raw_score))
        
        # Calibration
        if raw_score >= 9:
            calibrated = raw_score
        elif raw_score >= 7:
            calibrated = raw_score - 0.5
        elif raw_score <= 3:
            calibrated = raw_score + 0.5
        else:
            calibrated = raw_score
        
        calibrated = max(1, min(10, round(calibrated)))
        
        return {
            'score': calibrated,
            'raw_score': raw_score,
            'category': gpt_analysis.get('risk_category', 'MODERATE'),
            'reasoning': gpt_analysis.get('reasoning', ''),
            'key_risk': gpt_analysis.get('key_overnight_risk', 'None'),
            'direction_risk': gpt_analysis.get('direction_risk', 'UNKNOWN'),
            'duplicates_found': gpt_analysis.get('duplicates_found', 'None')
        }
        
    except Exception as e:
        print(f"GPT error: {e}")
        import traceback
        traceback.print_exc()
        return {
            'score': 5,
            'raw_score': 5,
            'category': 'MODERATE',
            'reasoning': f'GPT error: {str(e)}',
            'direction_risk': 'UNKNOWN',
            'key_risk': 'Error',
            'duplicates_found': 'Error'
        }

# ============================================================================
# COMPOSITE SCORE & SIGNAL
# ============================================================================

def calculate_composite_score(indicators):
    """Composite: IV/RV=30%, Trend=20%, GPT=50%"""
    weights = {'iv_rv': 0.30, 'trend': 0.20, 'gpt': 0.50}
    
    iv_rv_score = indicators['iv_rv']['score']
    trend_score = indicators['trend']['score']
    gpt_score = indicators['gpt']['score']
    
    composite = (
        iv_rv_score * weights['iv_rv'] +
        trend_score * weights['trend'] +
        gpt_score * weights['gpt']
    )
    
    composite = round(composite, 1)
    composite = max(1.0, min(10.0, composite))
    
    if composite < 2.5:
        category = "EXCELLENT"
    elif composite < 3.5:
        category = "VERY_GOOD"
    elif composite < 5.0:
        category = "GOOD"
    elif composite < 6.5:
        category = "FAIR"
    elif composite < 7.5:
        category = "ELEVATED"
    else:
        category = "HIGH"
    
    return {'score': composite, 'category': category}

def generate_signal(composite_score):
    """Generate trading signal"""
    if composite_score >= 7.5:
        return {
            'signal': 'SKIP',
            'should_trade': False,
            'reason': f"High risk ({composite_score:.1f})"
        }
    elif composite_score >= 5.0:
        return {
            'signal': 'TRADE_CONSERVATIVE',
            'should_trade': True,
            'reason': f"Elevated risk ({composite_score:.1f})"
        }
    elif composite_score >= 3.5:
        return {
            'signal': 'TRADE_NORMAL',
            'should_trade': True,
            'reason': f"Good setup ({composite_score:.1f})"
        }
    else:
        return {
            'signal': 'TRADE_AGGRESSIVE',
            'should_trade': True,
            'reason': f"Excellent ({composite_score:.1f})"
        }

def send_webhook(signal_data):
    """Send webhook to Option Alpha"""
    signal = signal_data['signal']
    timestamp = datetime.now(ET_TZ).isoformat()
    
    if signal == "SKIP":
        url = WEBHOOK_URLS.get('NO_TRADE')
        if url:
            try:
                payload = {'signal': 'SKIP', 'timestamp': timestamp}
                response = requests.post(url, json=payload, timeout=10)
                return {'success': response.status_code in [200, 201, 202]}
            except:
                return {'success': False}
        return {'success': True}
    
    url = WEBHOOK_URLS.get(signal)
    if not url:
        return {'success': False}
    
    try:
        payload = {'signal': signal, 'timestamp': timestamp}
        response = requests.post(url, json=payload, timeout=10)
        return {'success': response.status_code in [200, 201, 202]}
    except:
        return {'success': False}

def is_within_trading_window(now=None):
    """PRODUCTION: Check if within 2:30-3:30 PM ET trading window"""
    if now is None:
        now = datetime.now(ET_TZ)
    current_time = now.time()
    return TRADING_WINDOW_START <= current_time <= TRADING_WINDOW_END

# ============================================================================
# FLASK ROUTES - MAIN ENDPOINTS
# ============================================================================

@app.route("/", methods=["GET"])
def homepage():
    """Homepage"""
    now = datetime.now(ET_TZ)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ren's SPX Vol Signal (Production)</title>
        <style>
            body {{
                font-family: 'Segoe UI', sans-serif;
                max-width: 1000px;
                margin: 40px auto;
                padding: 20px;
                background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            }}
            .container {{
                background: white;
                border-radius: 12px;
                padding: 40px;
                box-shadow: 0 15px 50px rgba(0,0,0,0.3);
            }}
            .header {{
                border-bottom: 3px solid #2a5298;
                padding-bottom: 20px;
                margin-bottom: 30px;
            }}
            h1 {{ 
                color: #1e3c72; 
                margin: 0 0 10px 0;
                font-size: 32px;
            }}
            .subtitle {{
                color: #64748b;
                font-size: 16px;
                font-weight: 500;
            }}
            .status {{
                display: inline-block;
                padding: 10px 20px;
                border-radius: 25px;
                font-weight: bold;
                background: #10b981;
                color: white;
                margin-top: 15px;
            }}
            .section {{
                margin: 25px 0;
                padding: 20px;
                background: #f8fafc;
                border-radius: 8px;
                border-left: 4px solid #2a5298;
            }}
            .section-title {{
                font-size: 18px;
                font-weight: 700;
                color: #1e3c72;
                margin: 0 0 15px 0;
            }}
            .info-item {{
                margin: 8px 0;
                padding: 8px 12px;
                background: white;
                border-radius: 6px;
                font-size: 14px;
            }}
            .info-label {{
                font-weight: 600;
                color: #475569;
                display: inline-block;
                min-width: 150px;
            }}
            .info-value {{
                color: #1e293b;
            }}
            .strategy-box {{
                background: #eff6ff;
                border: 2px solid #3b82f6;
                padding: 20px;
                border-radius: 8px;
                margin: 25px 0;
            }}
            .strategy-title {{
                font-size: 20px;
                font-weight: 700;
                color: #1e40af;
                margin: 0 0 15px 0;
            }}
            .edge-item {{
                padding: 10px 0;
                border-bottom: 1px solid #cbd5e1;
            }}
            .edge-item:last-child {{
                border-bottom: none;
            }}
            .edge-label {{
                font-weight: 600;
                color: #1e40af;
            }}
            .edge-desc {{
                color: #475569;
                margin-top: 5px;
                font-size: 14px;
            }}
            .endpoint {{
                background: #f3f4f6;
                padding: 14px;
                margin: 10px 0;
                border-radius: 6px;
                font-family: monospace;
                font-size: 14px;
            }}
            .endpoint a {{ 
                color: #2a5298; 
                text-decoration: none; 
                font-weight: bold; 
            }}
            .endpoint a:hover {{
                text-decoration: underline;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📊 Ren's SPX Vol Signal</h1>
                <div class="subtitle">Automated SPX Overnight Iron Condor Decision System (Production)</div>
                <div class="status">LIVE ON RAILWAY - FREE TIER</div>
            </div>
            
            <div class="strategy-box">
                <div class="strategy-title">🎯 Trading Strategy: Overnight Vol Premium Capture</div>
                
                <div class="edge-item">
                    <div class="edge-label">📈 Core Edge:</div>
                    <div class="edge-desc">
                        Sell SPX iron condors (2:30-3:30 PM entry, 1 DTE) when implied volatility is rich relative to realized volatility 
                        and overnight news risk is manageable. Capture theta decay + vol premium during the ~16-hour overnight period.
                    </div>
                </div>
                
                <div class="edge-item">
                    <div class="edge-label">🔍 Signal Components (3 Indicators):</div>
                    <div class="edge-desc">
                        <strong>1. IV/RV Ratio (30%):</strong> VXX-based volatility vs 10-day realized vol.<br>
                        <strong>2. Market Trend (20%):</strong> Analyzes momentum and intraday volatility.<br>
                        <strong>3. GPT News Analysis (50%):</strong> Triple-layer filtering (Algo dedup → Keyword → GPT).
                    </div>
                </div>
                
                <div class="edge-item">
                    <div class="edge-label">⚡ Trade Sizing Logic:</div>
                    <div class="edge-desc">
                        <strong>AGGRESSIVE:</strong> Score &lt;3.5 → 20pt width, 0.18 delta<br>
                        <strong>NORMAL:</strong> Score 3.5-5.0 → 25pt width, 0.16 delta<br>
                        <strong>CONSERVATIVE:</strong> Score 5.0-7.5 → 30pt width, 0.14 delta<br>
                        <strong>SKIP:</strong> Score ≥7.5 → No trade
                    </div>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">⚙️ System Information</div>
                <div class="info-item">
                    <span class="info-label">Current Time:</span>
                    <span class="info-value">{timestamp}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Trading Window:</span>
                    <span class="info-value">2:30 PM - 3:30 PM ET (Production)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Environment:</span>
                    <span class="info-value">Railway Production</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Data Source:</span>
                    <span class="info-value">Alpha Vantage FREE tier (SPY/VXX proxies)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">SPX Proxy:</span>
                    <span class="info-value">SPY × 10 (official Alpha Vantage recommendation)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">VIX Proxy:</span>
                    <span class="info-value">VXX (VIX futures ETF)</span>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">🔗 API Endpoints</div>
                <div class="endpoint"><a href="/health">/health</a> - Health check</div>
                <div class="endpoint"><a href="/option_alpha_trigger">/option_alpha_trigger</a> - Generate trading signal</div>
                <div class="endpoint"><a href="/test_alpha_spy_vxx">/test_alpha_spy_vxx</a> - Test Alpha Vantage (current)</div>
                <div class="endpoint"><a href="/test_polygon_massive">/test_polygon_massive</a> - Test Polygon/Massive</div>
                <div class="endpoint"><a href="/test_fmp">/test_fmp</a> - Test Financial Modeling Prep</div>
                <div class="endpoint"><a href="/test_yahoo">/test_yahoo</a> - Test Yahoo Finance</div>
                <div class="endpoint"><a href="/test_marketstack_all">/test_marketstack_all</a> - Test Marketstack</div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.route("/health", methods=["GET"])
def health_check():
    """Health check"""
    now = datetime.now(ET_TZ)
    return jsonify({
        "status": "healthy",
        "timestamp": now.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
        "environment": "production",
        "trading_window": "2:30-3:30 PM ET",
        "filtering": "Triple-layer (Algo dedup → Keyword → GPT)",
        "data_source": "Alpha Vantage FREE tier",
        "spx_proxy": "SPY × 10",
        "vix_proxy": "VXX (VIX futures ETF)"
    }), 200

@app.route("/option_alpha_trigger", methods=["GET", "POST"])
def option_alpha_trigger():
    """Main trading decision endpoint"""
    now = datetime.now(ET_TZ)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    
    print(f"\n[{timestamp}] /option_alpha_trigger called")
    
    # Check trading window
    if not is_within_trading_window(now):
        return jsonify({
            "status": "outside_window",
            "message": "Outside trading window (2:30-3:30 PM ET)",
            "timestamp": timestamp
        }), 200
    
    try:
        print(f"[{timestamp}] Fetching market data...")
        
        # Use Alpha Vantage with retry
        spx_data = get_spx_data_with_retry(max_retries=3)
        if not spx_data:
            return jsonify({"status": "error", "message": "SPY failed after 3 retries (Alpha Vantage)"}), 500
        
        iv_data = get_overnight_iv_with_retry(max_retries=3)
        if not iv_data:
            return jsonify({"status": "error", "message": "VXX failed after 3 retries (Alpha Vantage)"}), 500
        
        # Fetch news
        news_data = fetch_news_multi_source()
        
        print(f"[{timestamp}] Running indicators...")
        
        # Run indicators
        iv_rv = analyze_iv_rv_ratio(spx_data, iv_data)
        trend = analyze_market_trend(spx_data)
        gpt = analyze_gpt_news(news_data)
        
        indicators = {'iv_rv': iv_rv, 'trend': trend, 'gpt': gpt}
        
        # Composite
        composite = calculate_composite_score(indicators)
        
        # Signal
        signal = generate_signal(composite['score'])
        
        # Webhook
        webhook = send_webhook(signal)
        
        print(f"[{timestamp}] Decision: {signal['signal']} (Score: {composite['score']:.1f})")
        
        # Format news headlines
        news_headlines = []
        if news_data.get('articles'):
            for article in news_data['articles'][:25]:
                time_str = article['published_time'].strftime("%I:%M %p")
                hours_ago = article['hours_ago']
                
                if hours_ago < 1:
                    recency = "⚠️"
                elif hours_ago < 3:
                    recency = "🔸"
                else:
                    recency = "•"
                
                priority = "🔥" if article.get('priority') == 'HIGH' else ""
                
                news_headlines.append(f"{recency} [{time_str}] {priority}{article['title']}")
        
        # Get filter stats
        filter_stats = news_data.get('filter_stats', {
            'raw_articles': 0,
            'duplicates_removed': 0,
            'unique_articles': 0,
            'junk_filtered': 0,
            'sent_to_gpt': 0
        })
        
        # Response
        return jsonify({
            "status": "success",
            "timestamp": timestamp,
            "environment": "production",
            
            "decision": signal['signal'],
            "composite_score": composite['score'],
            "category": composite['category'],
            "reason": signal['reason'],
            
            "market_data": {
                "spx_current": spx_data['current'],
                "spx_high": spx_data['high_today'],
                "spx_low": spx_data['low_today'],
                "vxx_current": iv_data['current'],
                "data_source": "Alpha Vantage FREE (SPY/VXX proxies)"
            },
            
            "indicator_1_iv_rv": {
                "weight": "30%",
                "score": iv_rv['score'],
                "iv_rv_ratio": iv_rv['iv_rv_ratio'],
                "realized_vol": f"{iv_rv['realized_vol']}%",
                "implied_vol": f"{iv_rv['implied_vol']}%",
                "vxx_value": iv_rv['vxx_value'],
                "tenor": iv_rv['tenor'],
                "source": "Alpha Vantage VXX proxy"
            },
            
            "indicator_2_trend": {
                "weight": "20%",
                "score": trend['score'],
                "trend_change_5d": f"{trend['change_5d'] * 100:+.2f}%",
                "intraday_range": f"{trend['intraday_range'] * 100:.2f}%"
            },
            
            "indicator_3_news_gpt": {
                "weight": "50%",
                
                "triple_layer_pipeline": {
                    "layer_1_algo_dedup": {
                        "raw_articles_fetched": filter_stats['raw_articles'],
                        "duplicates_removed": filter_stats['duplicates_removed'],
                        "unique_articles": filter_stats['unique_articles']
                    },
                    "layer_2_keyword_filter": {
                        "junk_filtered": filter_stats['junk_filtered'],
                        "sent_to_gpt": filter_stats['sent_to_gpt']
                    },
                    "layer_3_gpt": {
                        "duplicates_found_by_gpt": gpt.get('duplicates_found', 'None'),
                        "description": "GPT triple-duty: duplication safety + commentary filter + risk analysis"
                    }
                },
                
                "headlines_analyzed": news_headlines,
                
                "gpt_analysis": {
                    "score": gpt['score'],
                    "category": gpt['category'],
                    "key_risk": gpt.get('key_risk', 'None'),
                    "direction": gpt.get('direction_risk', 'UNKNOWN'),
                    "reasoning": gpt['reasoning']
                }
            },
            
            "webhook_success": webhook.get('success', False)
            
        }), 200
        
    except Exception as e:
        print(f"[{timestamp}] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

# ============================================================================
# FLASK ROUTES - TEST ENDPOINTS (For future API exploration)
# ============================================================================

@app.route("/test_alpha_spy_vxx", methods=["GET"])
def test_alpha_spy_vxx():
    """Test Alpha Vantage FREE tier with SPY/VXX (CURRENT PRODUCTION API)"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'environment': 'Railway Production',
        'api_provider': 'Alpha Vantage',
        'plan': 'FREE tier',
        'strategy': 'Use SPY (SPX proxy) and VXX (VIX proxy) - Official recommendation'
    }
    
    if not ALPHA_VANTAGE_KEY:
        return jsonify({'error': 'ALPHA_VANTAGE_KEY not set'}), 500
    
    results['api_key_check'] = {
        'status': '✅ PRESENT',
        'length': len(ALPHA_VANTAGE_KEY),
        'preview': ALPHA_VANTAGE_KEY[:8] + '...'
    }
    
    # Test SPY
    results['spy_tests'] = {}
    
    try:
        print("  [TEST] Fetching SPY via GLOBAL_QUOTE...")
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPY&apikey={ALPHA_VANTAGE_KEY}"
        response = requests.get(url, timeout=10)
        
        results['spy_tests']['quote_http_status'] = response.status_code
        
        if response.status_code == 200:
            data = response.json()
            results['spy_tests']['quote_response'] = data
            
            if 'Global Quote' in data and '05. price' in data['Global Quote']:
                spy_price = float(data['Global Quote']['05. price'])
                spx_equivalent = spy_price * 10
                
                results['spy_tests']['quote_status'] = '✅ SUCCESS'
                results['spy_tests']['spy_price'] = spy_price
                results['spy_tests']['spx_equivalent'] = spx_equivalent
                results['spy_tests']['spy_high'] = float(data['Global Quote']['03. high'])
                results['spy_tests']['spy_low'] = float(data['Global Quote']['04. low'])
            elif 'Note' in data:
                results['spy_tests']['quote_status'] = f"⚠️ RATE LIMITED: {data['Note']}"
            elif 'Error Message' in data:
                results['spy_tests']['quote_status'] = f"❌ ERROR: {data['Error Message']}"
            else:
                results['spy_tests']['quote_status'] = '❌ UNEXPECTED FORMAT'
        else:
            results['spy_tests']['quote_status'] = f'❌ HTTP {response.status_code}'
    
    except Exception as e:
        results['spy_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
    
    time_module.sleep(12)
    
    # Test SPY historical
    try:
        print("  [TEST] Fetching SPY historical via TIME_SERIES_DAILY...")
        url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=SPY&outputsize=compact&apikey={ALPHA_VANTAGE_KEY}"
        response = requests.get(url, timeout=10)
        
        results['spy_tests']['history_http_status'] = response.status_code
        
        if response.status_code == 200:
            data = response.json()
            
            if 'Time Series (Daily)' in data:
                ts = data['Time Series (Daily)']
                dates = sorted(list(ts.keys()), reverse=True)
                
                results['spy_tests']['history_status'] = '✅ SUCCESS'
                results['spy_tests']['days_available'] = len(dates)
                results['spy_tests']['latest_date'] = dates[0]
                
                closes = [float(ts[date]['4. close']) for date in dates[:10]]
                results['spy_tests']['sample_closes'] = closes
                results['spy_tests']['spx_equivalent_closes'] = [c * 10 for c in closes]
                
            elif 'Note' in data:
                results['spy_tests']['history_status'] = f"⚠️ RATE LIMITED: {data['Note']}"
            else:
                results['spy_tests']['history_status'] = '❌ UNEXPECTED FORMAT'
        else:
            results['spy_tests']['history_status'] = f'❌ HTTP {response.status_code}'
    
    except Exception as e:
        results['spy_tests']['history_status'] = f'❌ EXCEPTION: {str(e)}'
    
    time_module.sleep(12)
    
    # Test VXX
    results['vxx_tests'] = {}
    
    try:
        print("  [TEST] Fetching VXX via GLOBAL_QUOTE...")
        url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=VXX&apikey={ALPHA_VANTAGE_KEY}"
        response = requests.get(url, timeout=10)
        
        results['vxx_tests']['quote_http_status'] = response.status_code
        
        if response.status_code == 200:
            data = response.json()
            results['vxx_tests']['quote_response'] = data
            
            if 'Global Quote' in data and '05. price' in data['Global Quote']:
                vxx_price = float(data['Global Quote']['05. price'])
                
                results['vxx_tests']['quote_status'] = '✅ SUCCESS'
                results['vxx_tests']['vxx_price'] = vxx_price
                results['vxx_tests']['note'] = 'VXX tracks VIX futures (correlates with VIX but not exact)'
            elif 'Note' in data:
                results['vxx_tests']['quote_status'] = f"⚠️ RATE LIMITED: {data['Note']}"
            else:
                results['vxx_tests']['quote_status'] = '❌ UNEXPECTED FORMAT'
        else:
            results['vxx_tests']['quote_status'] = f'❌ HTTP {response.status_code}'
    
    except Exception as e:
        results['vxx_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # Summary
    spy_working = '✅' in results['spy_tests'].get('quote_status', '')
    vxx_working = '✅' in results['vxx_tests'].get('quote_status', '')
    
    results['summary'] = {
        'spy_working': spy_working,
        'vxx_working': vxx_working,
        'spy_history_working': '✅' in results['spy_tests'].get('history_status', '')
    }
    
    if spy_working and vxx_working:
        results['recommendation'] = '✅ ALPHA VANTAGE FREE TIER WORKING - PRODUCTION READY!'
        results['status'] = 'READY'
    elif spy_working:
        results['recommendation'] = '⚠️ SPY works but VXX failed'
        results['status'] = 'SPY_ONLY'
    else:
        results['recommendation'] = '❌ Alpha Vantage not working'
        results['status'] = 'FAILED'
    
    results['rate_limit_reminder'] = {
        'free_tier': '25 requests per day, 5 requests per minute',
        'note': 'This test uses 3 API calls with 12-second delays'
    }
    
    return jsonify(results), 200


@app.route("/test_polygon_delayed", methods=["GET"])
def test_polygon_delayed():
    """Test Polygon Indices Starter - SPX and VIX1D (official endpoints)"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'plan': 'Indices Starter ($49/mo) - 15-min delayed',
        'note': 'Using official v3 snapshot + v2 aggregates endpoints'
    }
    
    POLYGON_KEY = os.environ.get('POLYGON_API_KEY')
    
    if not POLYGON_KEY:
        return jsonify({'error': 'No API key'}), 500
    
    # ========================================================================
    # METHOD 1: v3 Snapshot (OFFICIAL - should give 15-min delayed quotes)
    # ========================================================================
    results['v3_snapshot'] = {}
    
    try:
        print("  [SNAPSHOT] Fetching SPX + VIX1D via v3/snapshot/indices...")
        
        # Query both tickers at once (comma-separated)
        url = f"https://api.massive.com/v3/snapshot/indices?ticker.any_of=I:SPX,I:VIX1D&apiKey={POLYGON_KEY}"
        response = requests.get(url, timeout=15)
        
        results['v3_snapshot']['http_status'] = response.status_code
        results['v3_snapshot']['response'] = response.json()
        
        if response.status_code == 200:
            data = response.json()
            
            if 'results' in data and len(data['results']) > 0:
                results['v3_snapshot']['status'] = '✅ SUCCESS'
                results['v3_snapshot']['count'] = len(data['results'])
                
                # Parse each ticker
                for ticker_data in data['results']:
                    ticker = ticker_data.get('ticker')
                    
                    # Check for errors
                    if 'error' in ticker_data:
                        results['v3_snapshot'][ticker] = {
                            'status': f"❌ ERROR: {ticker_data.get('error')}",
                            'message': ticker_data.get('message', '')
                        }
                        continue
                    
                    # Parse successful response
                    if ticker == 'I:SPX':
                        results['v3_snapshot']['SPX'] = {
                            'status': '✅ FOUND',
                            'value': ticker_data.get('value'),
                            'name': ticker_data.get('name'),
                            'timeframe': ticker_data.get('timeframe'),
                            'market_status': ticker_data.get('market_status'),
                            'last_updated': ticker_data.get('last_updated'),
                            'session': ticker_data.get('session', {})
                        }
                    
                    elif ticker == 'I:VIX1D':
                        results['v3_snapshot']['VIX1D'] = {
                            'status': '✅ FOUND',
                            'value': ticker_data.get('value'),
                            'name': ticker_data.get('name'),
                            'timeframe': ticker_data.get('timeframe'),
                            'market_status': ticker_data.get('market_status'),
                            'last_updated': ticker_data.get('last_updated'),
                            'session': ticker_data.get('session', {})
                        }
            else:
                results['v3_snapshot']['status'] = '❌ NO RESULTS'
        
        elif response.status_code == 403:
            results['v3_snapshot']['status'] = '❌ FORBIDDEN (403) - Not entitled to snapshot data'
        
        else:
            results['v3_snapshot']['status'] = f'❌ HTTP {response.status_code}'
    
    except Exception as e:
        results['v3_snapshot']['status'] = f'❌ EXCEPTION: {str(e)}'
        import traceback
        results['v3_snapshot']['traceback'] = traceback.format_exc()
    
    # ========================================================================
    # METHOD 2: v2 Aggregates - SPX (for historical RV calculation)
    # ========================================================================
    results['v2_aggregates_spx'] = {}
    
    try:
        print("  [AGGREGATES] Fetching SPX historical data...")
        
        end_date = datetime.now(ET_TZ)
        start_date = end_date - timedelta(days=30)
        
        # Official format: /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}
        url = f"https://api.massive.com/v2/aggs/ticker/I:SPX/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}?adjusted=true&sort=desc&limit=50&apiKey={POLYGON_KEY}"
        
        response = requests.get(url, timeout=15)
        
        results['v2_aggregates_spx']['http_status'] = response.status_code
        
        if response.status_code == 200:
            data = response.json()
            
            if 'results' in data and len(data['results']) > 0:
                results['v2_aggregates_spx']['status'] = '✅ SUCCESS'
                results['v2_aggregates_spx']['count'] = data.get('queryCount', len(data['results']))
                results['v2_aggregates_spx']['ticker'] = data.get('ticker')
                
                # Get latest bar
                latest = data['results'][0]
                results['v2_aggregates_spx']['latest'] = {
                    'close': latest.get('c'),
                    'open': latest.get('o'),
                    'high': latest.get('h'),
                    'low': latest.get('l'),
                    'timestamp': latest.get('t')
                }
                
                # Get closes for RV calculation (last 10 days)
                closes = [bar['c'] for bar in data['results'][:10]]
                results['v2_aggregates_spx']['sample_closes'] = closes
                results['v2_aggregates_spx']['days_for_rv'] = len(closes)
            
            else:
                results['v2_aggregates_spx']['status'] = '❌ NO RESULTS'
        
        elif response.status_code == 403:
            results['v2_aggregates_spx']['status'] = '❌ FORBIDDEN (403)'
        
        else:
            results['v2_aggregates_spx']['status'] = f'❌ HTTP {response.status_code}'
    
    except Exception as e:
        results['v2_aggregates_spx']['status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # METHOD 3: v2 Aggregates - VIX1D (if needed for historical)
    # ========================================================================
    results['v2_aggregates_vix1d'] = {}
    
    try:
        print("  [AGGREGATES] Fetching VIX1D historical data...")
        
        end_date = datetime.now(ET_TZ)
        start_date = end_date - timedelta(days=10)
        
        url = f"https://api.massive.com/v2/aggs/ticker/I:VIX1D/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}?adjusted=true&sort=desc&limit=10&apiKey={POLYGON_KEY}"
        
        response = requests.get(url, timeout=15)
        
        results['v2_aggregates_vix1d']['http_status'] = response.status_code
        
        if response.status_code == 200:
            data = response.json()
            
            if 'results' in data and len(data['results']) > 0:
                results['v2_aggregates_vix1d']['status'] = '✅ SUCCESS'
                results['v2_aggregates_vix1d']['latest_close'] = data['results'][0].get('c')
                results['v2_aggregates_vix1d']['days_returned'] = len(data['results'])
            else:
                results['v2_aggregates_vix1d']['status'] = '❌ NO RESULTS'
        
        else:
            results['v2_aggregates_vix1d']['status'] = f'❌ HTTP {response.status_code}'
    
    except Exception as e:
        results['v2_aggregates_vix1d']['status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # SUMMARY & RECOMMENDATION
    # ========================================================================
    
    # Check if snapshot worked
    snapshot_worked = results['v3_snapshot'].get('status') == '✅ SUCCESS'
    spx_in_snapshot = 'SPX' in results['v3_snapshot'] and results['v3_snapshot']['SPX'].get('status') == '✅ FOUND'
    vix1d_in_snapshot = 'VIX1D' in results['v3_snapshot'] and results['v3_snapshot']['VIX1D'].get('status') == '✅ FOUND'
    
    # Check if aggregates worked
    spx_agg_worked = results['v2_aggregates_spx'].get('status') == '✅ SUCCESS'
    vix1d_agg_worked = results['v2_aggregates_vix1d'].get('status') == '✅ SUCCESS'
    
    results['summary'] = {
        'snapshot_api_working': snapshot_worked,
        'spx_snapshot_available': spx_in_snapshot,
        'vix1d_snapshot_available': vix1d_in_snapshot,
        'spx_aggregates_working': spx_agg_worked,
        'vix1d_aggregates_working': vix1d_agg_worked
    }
    
    # Determine if ready for bot
    if spx_in_snapshot and vix1d_in_snapshot and spx_agg_worked:
        results['recommendation'] = '✅ POLYGON FULLY READY!'
        results['status'] = 'READY'
        results['data_available'] = {
            'spx_current': f"Via snapshot (timeframe: {results['v3_snapshot']['SPX'].get('timeframe')})",
            'vix1d_current': f"Via snapshot (timeframe: {results['v3_snapshot']['VIX1D'].get('timeframe')})",
            'spx_historical': 'Via aggregates (for RV calculation)'
        }
        results['next_step'] = 'Ready to build production script with Polygon!'
    
    elif spx_agg_worked and not spx_in_snapshot:
        results['recommendation'] = '⚠️ Historical works but snapshot failed - May need to wait for subscription to activate'
        results['status'] = 'PARTIAL'
        results['next_step'] = 'Wait 30 minutes or contact Polygon support'
    
    else:
        results['recommendation'] = '❌ Polygon not working - Check subscription or contact support'
        results['status'] = 'FAILED'
        results['next_step'] = 'Contact support@polygon.io'
    
    return jsonify(results), 200


@app.route("/test_fmp", methods=["GET"])
def test_fmp():
    """Test Financial Modeling Prep API (for future reference)"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'api_provider': 'Financial Modeling Prep',
        'note': 'Test endpoint preserved for future API exploration'
    }
    
    FMP_KEY = os.environ.get('FMP_API_KEY')
    
    if not FMP_KEY:
        results['status'] = 'NO_API_KEY'
        results['message'] = 'FMP_API_KEY not set in environment variables'
        return jsonify(results), 200
    
    results['api_key_check'] = {
        'status': '✅ PRESENT',
        'preview': FMP_KEY[:8] + '...'
    }
    
    # Quick test
    try:
        url = f"https://financialmodelingprep.com/api/v3/quote/AAPL?apikey={FMP_KEY}"
        response = requests.get(url, timeout=10)
        results['test_response'] = {
            'http_status': response.status_code,
            'data': response.json()
        }
    except Exception as e:
        results['test_response'] = {'error': str(e)}
    
    return jsonify(results), 200


@app.route("/test_yahoo", methods=["GET"])
def test_yahoo():
    """Test Yahoo Finance (for future reference)"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'api_provider': 'Yahoo Finance (yfinance)',
        'note': 'Test endpoint preserved for future - Yahoo blocked on Railway currently'
    }
    
    try:
        import yfinance as yf
        results['yfinance_version'] = yf.__version__
        
        session = requests.Session()
        session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        })
        
        spy = yf.Ticker("SPY", session=session)
        hist = spy.history(period="5d")
        
        results['spy_test'] = {
            'status': '✅ SUCCESS' if not hist.empty else '❌ EMPTY',
            'length': len(hist)
        }
        
    except Exception as e:
        results['error'] = str(e)
    
    return jsonify(results), 200


@app.route("/test_marketstack_all", methods=["GET"])
def test_marketstack_all():
    """Test Marketstack API (for future reference)"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'api_provider': 'Marketstack',
        'note': 'Test endpoint preserved for future API exploration'
    }
    
    MARKETSTACK_KEY = os.environ.get('MARKETSTACK_KEY')
    
    if not MARKETSTACK_KEY:
        results['status'] = 'NO_API_KEY'
        results['message'] = 'MARKETSTACK_KEY not set in environment variables'
        return jsonify(results), 200
    
    results['api_key_check'] = {
        'status': '✅ PRESENT',
        'preview': MARKETSTACK_KEY[:8] + '...'
    }
    
    # Quick test
    try:
        url = f"http://api.marketstack.com/v1/eod?access_key={MARKETSTACK_KEY}&symbols=AAPL&limit=5"
        response = requests.get(url, timeout=10)
        results['test_response'] = {
            'http_status': response.status_code,
            'data': response.json()
        }
    except Exception as e:
        results['test_response'] = {'error': str(e)}
    
    return jsonify(results), 200

# ============================================================================
# BACKGROUND THREAD - Auto-trigger during trading hours
# ============================================================================

def poke_self():
    """Background thread: Trigger analysis every 20 minutes during trading hours"""
    print("[POKE] Background thread started")
    
    while True:
        try:
            now = datetime.now(ET_TZ)
            current_time = now.time()
            
            if is_within_trading_window(now):
                if current_time.minute in [30, 50, 10] and current_time.second < 30:
                    print(f"\n[POKE] Triggering at {now.strftime('%I:%M %p ET')}")
                    try:
                        requests.get("http://localhost:8080/option_alpha_trigger", timeout=60)
                    except Exception as e:
                        print(f"[POKE] Error: {e}")
            
            time_module.sleep(30)
            
        except Exception as e:
            print(f"[POKE] Background error: {e}")
            time_module.sleep(60)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    
    print("=" * 80)
    print("Ren's SPX Vol Signal - Production (Alpha Vantage FREE tier)")
    print("=" * 80)
    print(f"Port: {PORT}")
    print(f"Trading Window: 2:30-3:30 PM ET")
    print(f"Data Source: Alpha Vantage FREE tier (SPY/VXX proxies)")
    print(f"SPX Proxy: SPY × 10")
    print(f"VIX Proxy: VXX (VIX futures ETF)")
    print("=" * 80)
    
    # Start background thread
    t = threading.Thread(target=poke_self, daemon=True)
    t.start()
    
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)