#!/usr/bin/env python3
"""
SPX Overnight Vol Premium Bot - Railway Production (Twelve Data API)
Triple-Layer Filtering: Algo Dedup → Keyword Filter → GPT Analysis
Uses Twelve Data API for market data (800 calls/day free)
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
TRADING_WINDOW_START = dt_time(hour=14, minute=30)
TRADING_WINDOW_END = dt_time(hour=19, minute=30)

def load_config():
    """Load configuration from environment variables (Railway)"""
    config = {
        'OPENAI_API_KEY': os.environ.get('OPENAI_API_KEY'),
        'TWELVE_DATA_KEY': os.environ.get('TWELVE_DATA_KEY'),
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
TWELVE_DATA_KEY = CONFIG.get('TWELVE_DATA_KEY')

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
# DATA FETCHING - TWELVE DATA API (800 calls/day free)
# ============================================================================

def get_spx_data_twelve():
    """Fetch SPX data from Twelve Data API"""
    try:
        print("  [Twelve Data] Fetching SPX data...")
        
        # Get current price (quote)
        quote_url = f"https://api.twelvedata.com/quote?symbol=SPX&apikey={TWELVE_DATA_KEY}"
        quote_response = requests.get(quote_url, timeout=10)
        
        if quote_response.status_code != 200:
            print(f"  ❌ Twelve Data SPX quote failed: {quote_response.status_code}")
            return None
        
        quote_data = quote_response.json()
        
        if 'code' in quote_data and quote_data['code'] != 200:
            print(f"  ❌ API error: {quote_data.get('message', 'Unknown error')}")
            return None
        
        current_price = float(quote_data['close'])
        high_today = float(quote_data['high'])
        low_today = float(quote_data['low'])
        
        print(f"  ✅ SPX: {current_price:.2f} (H: {high_today:.2f}, L: {low_today:.2f})")
        
        # Get historical data (time series)
        hist_url = f"https://api.twelvedata.com/time_series?symbol=SPX&interval=1day&outputsize=25&apikey={TWELVE_DATA_KEY}"
        hist_response = requests.get(hist_url, timeout=10)
        
        if hist_response.status_code != 200:
            print(f"  ❌ Twelve Data SPX history failed: {hist_response.status_code}")
            return None
        
        hist_data = hist_response.json()
        
        if 'values' not in hist_data or len(hist_data['values']) < 6:
            print(f"  ❌ Insufficient historical data")
            return None
        
        # Extract closes (most recent first, so reverse)
        closes = [float(bar['close']) for bar in reversed(hist_data['values'])]
        print(f"  ✅ Got {len(closes)} days of historical data")
        
        return {
            'current': current_price,
            'high_today': high_today,
            'low_today': low_today,
            'history_closes': closes
        }
        
    except Exception as e:
        print(f"  ❌ Twelve Data SPX error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_vix_data_twelve():
    """Fetch VIX data from Twelve Data API"""
    try:
        print("  [Twelve Data] Fetching VIX data...")
        
        # Get current VIX quote
        quote_url = f"https://api.twelvedata.com/quote?symbol=VIX&apikey={TWELVE_DATA_KEY}"
        response = requests.get(quote_url, timeout=10)
        
        if response.status_code != 200:
            print(f"  ❌ Twelve Data VIX failed: {response.status_code}")
            return None
        
        data = response.json()
        
        if 'code' in data and data['code'] != 200:
            print(f"  ❌ API error: {data.get('message', 'Unknown error')}")
            return None
        
        vix_value = float(data['close'])
        print(f"  ✅ VIX: {vix_value:.2f}")
        
        # Sanity check
        if vix_value < 5 or vix_value > 100:
            print(f"  ❌ VIX value {vix_value:.2f} outside normal range")
            return None
        
        return {
            'current': vix_value,
            'tenor': '30-day',
            'source': 'Twelve_Data',
            'method': 'Twelve Data API'
        }
        
    except Exception as e:
        print(f"  ❌ Twelve Data VIX error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_spx_data_with_retry(max_retries=3):
    """Fetch SPX with Twelve Data (with retry)"""
    for attempt in range(max_retries):
        try:
            print(f"  [Attempt {attempt + 1}/{max_retries}] Fetching SPX...")
            result = get_spx_data_twelve()
            
            if result is not None:
                print(f"  ✅ SPX fetch succeeded on attempt {attempt + 1}")
                return result
            
            print(f"  ⚠️ Attempt {attempt + 1} returned None, retrying...")
            time_module.sleep(1)
            
        except Exception as e:
            print(f"  ❌ Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time_module.sleep(1)
    
    return None


def get_overnight_iv_with_retry(max_retries=3):
    """Fetch VIX with Twelve Data (with retry)"""
    for attempt in range(max_retries):
        try:
            print(f"  [Attempt {attempt + 1}/{max_retries}] Fetching VIX...")
            result = get_vix_data_twelve()
            
            if result is not None:
                print(f"  ✅ VIX fetch succeeded on attempt {attempt + 1}")
                return result
            
            print(f"  ⚠️ Attempt {attempt + 1} returned None, retrying...")
            time_module.sleep(1)
            
        except Exception as e:
            print(f"  ❌ Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time_module.sleep(1)
    
    return None

# ============================================================================
# INDICATOR 1: IV/RV RATIO (30%)
# ============================================================================

def analyze_iv_rv_ratio(spx_data, iv_data):
    """
    Analyze IV/RV ratio
    Using 30-day VIX vs 10-day RV
    """
    # Use 10-day RV for 30-day VIX
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
    
    implied_vol = iv_data['current']
    iv_rv_ratio = implied_vol / realized_vol
    
    # Scoring logic
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
        'implied_vol': round(implied_vol, 2),
        'iv_rv_ratio': round(iv_rv_ratio, 3),
        'tenor': '30-day',
        'source': iv_data.get('source', 'Twelve_Data'),
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
# FLASK ROUTES
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
                <div class="status">LIVE ON RAILWAY</div>
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
                        <strong>1. IV/RV Ratio (30%):</strong> 30-day implied vol vs 10-day realized vol.<br>
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
                    <span class="info-value">Twelve Data API (800 calls/day free)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">News Parsing:</span>
                    <span class="info-value">Direct HTTP + XML (No feedparser)</span>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">🔗 API Endpoints</div>
                <div class="endpoint"><a href="/health">/health</a> - Health check</div>
                <div class="endpoint"><a href="/option_alpha_trigger">/option_alpha_trigger</a> - Generate signal</div>
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
        "data_source": "Twelve Data API",
        "news_parser": "Direct HTTP + XML (No feedparser)"
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
        
        # Use Twelve Data with retry
        spx_data = get_spx_data_with_retry(max_retries=3)
        if not spx_data:
            return jsonify({"status": "error", "message": "SPX failed after 3 retries (Twelve Data)"}), 500
        
        iv_data = get_overnight_iv_with_retry(max_retries=3)
        if not iv_data:
            return jsonify({"status": "error", "message": "VIX failed after 3 retries (Twelve Data)"}), 500
        
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
                "vix_current": iv_data['current'],
                "data_source": "Twelve Data API"
            },
            
            "indicator_1_iv_rv": {
                "weight": "30%",
                "score": iv_rv['score'],
                "iv_rv_ratio": iv_rv['iv_rv_ratio'],
                "realized_vol": f"{iv_rv['realized_vol']}%",
                "implied_vol": f"{iv_rv['implied_vol']}%",
                "tenor": "30-day",
                "source": "Twelve Data API"
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
                        # Call self via localhost
                        requests.get("http://localhost:8080/option_alpha_trigger", timeout=60)
                    except Exception as e:
                        print(f"[POKE] Error: {e}")
            
            time_module.sleep(30)
            
        except Exception as e:
            print(f"[POKE] Background error: {e}")
            time_module.sleep(60)


############ TEST APIS ENDPOINTS ############


@app.route("/test_yahoo_spy", methods=["GET"])
def test_yahoo_spy():
    """Test Yahoo Finance with SPY (S&P 500 ETF) instead of SPX index"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'environment': 'Railway Production',
        'api_provider': 'Yahoo Finance (yfinance)',
        'strategy': 'Use SPY ETF as SPX proxy (SPY × 10 ≈ SPX)'
    }
    
    # Test yfinance import
    try:
        import yfinance as yf
        results['yfinance_version'] = yf.__version__
        results['yfinance_import'] = '✅ SUCCESS'
    except Exception as e:
        results['yfinance_import'] = f'❌ FAILED: {str(e)}'
        return jsonify(results), 500
    
    # Create session
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    })
    
    # ========================================================================
    # TEST SPY (S&P 500 ETF as SPX proxy)
    # ========================================================================
    results['spy_tests'] = {}
    
    try:
        print("  [TEST] Fetching SPY data...")
        spy = yf.Ticker("SPY", session=session)
        
        # Method 1: history
        hist = spy.history(period="5d")
        results['spy_tests']['history_status'] = '✅ SUCCESS' if not hist.empty else '❌ EMPTY'
        results['spy_tests']['history_length'] = len(hist)
        
        if not hist.empty:
            spy_price = float(hist['Close'].iloc[-1])
            spx_equivalent = spy_price * 10
            results['spy_tests']['spy_price'] = spy_price
            results['spy_tests']['spx_equivalent'] = spx_equivalent
            results['spy_tests']['spy_high'] = float(hist['High'].iloc[-1])
            results['spy_tests']['spy_low'] = float(hist['Low'].iloc[-1])
            
            # Get multiple days for RV calculation
            closes = [float(c) for c in hist['Close'].tolist()]
            results['spy_tests']['closes_available'] = len(closes)
            results['spy_tests']['sample_closes'] = closes[-3:] if len(closes) >= 3 else closes
        
        # Method 2: fast_info (if available)
        try:
            fast_price = spy.fast_info.get('lastPrice')
            if fast_price:
                results['spy_tests']['fast_info_status'] = '✅ SUCCESS'
                results['spy_tests']['fast_info_price'] = float(fast_price)
                results['spy_tests']['fast_info_spx_equivalent'] = float(fast_price) * 10
            else:
                results['spy_tests']['fast_info_status'] = '❌ NO PRICE'
        except Exception as e:
            results['spy_tests']['fast_info_status'] = f'❌ FAILED: {str(e)}'
    
    except Exception as e:
        results['spy_tests']['error'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST VXX (VIX Short-Term Futures ETF as VIX proxy)
    # ========================================================================
    results['vxx_tests'] = {}
    
    try:
        print("  [TEST] Fetching VXX data (VIX proxy)...")
        vxx = yf.Ticker("VXX", session=session)
        
        hist = vxx.history(period="5d")
        results['vxx_tests']['history_status'] = '✅ SUCCESS' if not hist.empty else '❌ EMPTY'
        results['vxx_tests']['history_length'] = len(hist)
        
        if not hist.empty:
            vxx_price = float(hist['Close'].iloc[-1])
            results['vxx_tests']['vxx_price'] = vxx_price
            results['vxx_tests']['note'] = 'VXX tracks VIX futures (not exact VIX, but correlates)'
    
    except Exception as e:
        results['vxx_tests']['error'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST DIRECT VIX (might still fail, but worth trying)
    # ========================================================================
    results['vix_tests'] = {}
    
    try:
        print("  [TEST] Fetching ^VIX data...")
        vix = yf.Ticker("^VIX", session=session)
        
        hist = vix.history(period="5d")
        results['vix_tests']['history_status'] = '✅ SUCCESS' if not hist.empty else '❌ EMPTY'
        results['vix_tests']['history_length'] = len(hist)
        
        if not hist.empty:
            vix_value = float(hist['Close'].iloc[-1])
            results['vix_tests']['vix_value'] = vix_value
    
    except Exception as e:
        results['vix_tests']['error'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # CONTROL: Test regular stocks
    # ========================================================================
    results['control_tests'] = {}
    
    for symbol in ['AAPL', 'MSFT']:
        try:
            ticker = yf.Ticker(symbol, session=session)
            hist = ticker.history(period="5d")
            
            if not hist.empty:
                results['control_tests'][symbol] = {
                    'status': '✅ SUCCESS',
                    'price': float(hist['Close'].iloc[-1]),
                    'days': len(hist)
                }
            else:
                results['control_tests'][symbol] = {'status': '❌ EMPTY'}
        except Exception as e:
            results['control_tests'][symbol] = {'status': f'❌ ERROR: {str(e)}'}
    
    # ========================================================================
    # SUMMARY
    # ========================================================================
    spy_working = results['spy_tests'].get('history_length', 0) > 0
    vix_working = results['vix_tests'].get('history_length', 0) > 0
    vxx_working = results['vxx_tests'].get('history_length', 0) > 0
    stocks_working = any(v.get('status') == '✅ SUCCESS' for v in results['control_tests'].values())
    
    results['summary'] = {
        'spy_working': spy_working,
        'vix_working': vix_working,
        'vxx_working': vxx_working,
        'stocks_working': stocks_working
    }
    
    if spy_working and (vix_working or vxx_working):
        results['recommendation'] = '✅ YAHOO FINANCE WORKING with SPY proxy!'
        results['status'] = 'READY'
        results['approach'] = 'Use SPY × 10 for SPX, use VIX or VXX for volatility'
    elif spy_working and stocks_working:
        results['recommendation'] = '⚠️ SPY works but VIX/VXX failed - Can use fixed VIX estimate or skip IV/RV indicator'
        results['status'] = 'SPY_ONLY'
        results['approach'] = 'Use SPY × 10 for SPX, estimate VIX at ~15-20 or skip IV/RV check'
    elif stocks_working:
        results['recommendation'] = '⚠️ Regular stocks work but SPY failed - Unexpected!'
        results['status'] = 'STOCKS_ONLY'
    else:
        results['recommendation'] = '❌ Yahoo Finance completely blocked on Railway'
        results['status'] = 'BLOCKED'
    
    return jsonify(results), 200

@app.route("/test_twelve", methods=["GET"])
def test_twelve():
    """Test Twelve Data API - SPX, VIX, and controls"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'environment': 'Railway Production',
        'api_provider': 'Twelve Data',
        'api_website': 'https://twelvedata.com'
    }
    
    # ========================================================================
    # TEST TWELVE DATA API KEY
    # ========================================================================
    results['api_key_check'] = {}
    
    if not TWELVE_DATA_KEY or TWELVE_DATA_KEY == '':
        results['api_key_check']['status'] = '❌ MISSING'
        results['api_key_check']['message'] = 'TWELVE_DATA_KEY environment variable not set'
        return jsonify(results), 500
    else:
        results['api_key_check']['status'] = '✅ PRESENT'
        results['api_key_check']['length'] = len(TWELVE_DATA_KEY)
        results['api_key_check']['preview'] = TWELVE_DATA_KEY[:8] + '...' if len(TWELVE_DATA_KEY) > 8 else TWELVE_DATA_KEY
    
    # ========================================================================
    # TEST SPX DATA - Multiple methods
    # ========================================================================
    results['spx_tests'] = {}
    
    # Test 1: Quote endpoint
    try:
        print("  [TEST] Fetching SPX quote...")
        quote_url = f"https://api.twelvedata.com/quote?symbol=SPX&apikey={TWELVE_DATA_KEY}"
        quote_response = requests.get(quote_url, timeout=10)
        
        results['spx_tests']['quote_http_status'] = quote_response.status_code
        
        if quote_response.status_code == 200:
            quote_data = quote_response.json()
            results['spx_tests']['quote_response'] = quote_data
            
            if 'code' in quote_data:
                if quote_data['code'] == 429:
                    results['spx_tests']['quote_status'] = '❌ RATE LIMITED (429)'
                elif quote_data['code'] == 401:
                    results['spx_tests']['quote_status'] = '❌ UNAUTHORIZED (401) - Bad API key'
                elif quote_data['code'] == 400:
                    results['spx_tests']['quote_status'] = f"❌ BAD REQUEST (400): {quote_data.get('message', 'Unknown')}"
                else:
                    results['spx_tests']['quote_status'] = f"❌ ERROR {quote_data['code']}: {quote_data.get('message', 'Unknown')}"
            elif 'close' in quote_data:
                results['spx_tests']['quote_status'] = '✅ SUCCESS'
                results['spx_tests']['spx_price'] = float(quote_data['close'])
                results['spx_tests']['spx_high'] = float(quote_data['high'])
                results['spx_tests']['spx_low'] = float(quote_data['low'])
                results['spx_tests']['spx_volume'] = quote_data.get('volume', 'N/A')
            else:
                results['spx_tests']['quote_status'] = '❌ UNEXPECTED RESPONSE FORMAT'
        else:
            results['spx_tests']['quote_status'] = f'❌ HTTP ERROR {quote_response.status_code}'
            results['spx_tests']['quote_error'] = quote_response.text[:200]
    
    except Exception as e:
        results['spx_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
        import traceback
        results['spx_tests']['quote_traceback'] = traceback.format_exc()
    
    # Test 2: Time series endpoint (historical data)
    try:
        print("  [TEST] Fetching SPX time series...")
        hist_url = f"https://api.twelvedata.com/time_series?symbol=SPX&interval=1day&outputsize=5&apikey={TWELVE_DATA_KEY}"
        hist_response = requests.get(hist_url, timeout=10)
        
        results['spx_tests']['time_series_http_status'] = hist_response.status_code
        
        if hist_response.status_code == 200:
            hist_data = hist_response.json()
            results['spx_tests']['time_series_response'] = hist_data
            
            if 'code' in hist_data:
                results['spx_tests']['time_series_status'] = f"❌ ERROR {hist_data['code']}: {hist_data.get('message', 'Unknown')}"
            elif 'values' in hist_data:
                results['spx_tests']['time_series_status'] = '✅ SUCCESS'
                results['spx_tests']['days_returned'] = len(hist_data['values'])
                results['spx_tests']['sample_data'] = hist_data['values'][:2] if len(hist_data['values']) > 0 else []
            else:
                results['spx_tests']['time_series_status'] = '❌ UNEXPECTED RESPONSE FORMAT'
        else:
            results['spx_tests']['time_series_status'] = f'❌ HTTP ERROR {hist_response.status_code}'
    
    except Exception as e:
        results['spx_tests']['time_series_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST VIX DATA
    # ========================================================================
    results['vix_tests'] = {}
    
    try:
        print("  [TEST] Fetching VIX quote...")
        vix_url = f"https://api.twelvedata.com/quote?symbol=VIX&apikey={TWELVE_DATA_KEY}"
        vix_response = requests.get(vix_url, timeout=10)
        
        results['vix_tests']['quote_http_status'] = vix_response.status_code
        
        if vix_response.status_code == 200:
            vix_data = vix_response.json()
            results['vix_tests']['quote_response'] = vix_data
            
            if 'code' in vix_data:
                results['vix_tests']['quote_status'] = f"❌ ERROR {vix_data['code']}: {vix_data.get('message', 'Unknown')}"
            elif 'close' in vix_data:
                results['vix_tests']['quote_status'] = '✅ SUCCESS'
                results['vix_tests']['vix_value'] = float(vix_data['close'])
            else:
                results['vix_tests']['quote_status'] = '❌ UNEXPECTED RESPONSE FORMAT'
        else:
            results['vix_tests']['quote_status'] = f'❌ HTTP ERROR {vix_response.status_code}'
    
    except Exception as e:
        results['vix_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST STOCK DATA (Control - should definitely work)
    # ========================================================================
    results['control_tests'] = {}
    
    try:
        print("  [TEST] Fetching AAPL quote (control test)...")
        aapl_url = f"https://api.twelvedata.com/quote?symbol=AAPL&apikey={TWELVE_DATA_KEY}"
        aapl_response = requests.get(aapl_url, timeout=10)
        
        if aapl_response.status_code == 200:
            aapl_data = aapl_response.json()
            
            if 'code' in aapl_data:
                results['control_tests']['AAPL'] = {
                    'status': f"❌ ERROR {aapl_data['code']}",
                    'message': aapl_data.get('message', 'Unknown')
                }
            elif 'close' in aapl_data:
                results['control_tests']['AAPL'] = {
                    'status': '✅ SUCCESS',
                    'price': float(aapl_data['close'])
                }
            else:
                results['control_tests']['AAPL'] = {'status': '❌ UNEXPECTED FORMAT'}
        else:
            results['control_tests']['AAPL'] = {'status': f'❌ HTTP {aapl_response.status_code}'}
    
    except Exception as e:
        results['control_tests']['AAPL'] = {'status': f'❌ EXCEPTION: {str(e)}'}
    
    # ========================================================================
    # TEST API RATE LIMIT STATUS
    # ========================================================================
    results['api_limits'] = {}
    
    try:
        # Twelve Data returns rate limit info in headers
        test_response = requests.get(
            f"https://api.twelvedata.com/quote?symbol=SPY&apikey={TWELVE_DATA_KEY}",
            timeout=10
        )
        
        if 'X-RateLimit-Remaining' in test_response.headers:
            results['api_limits']['calls_remaining'] = test_response.headers.get('X-RateLimit-Remaining')
            results['api_limits']['rate_limit'] = test_response.headers.get('X-RateLimit-Limit')
        else:
            results['api_limits']['note'] = 'Rate limit headers not available'
    
    except Exception as e:
        results['api_limits']['error'] = str(e)
    
    # ========================================================================
    # SUMMARY & RECOMMENDATION
    # ========================================================================
    spx_working = any('✅' in str(v) for v in results['spx_tests'].values())
    vix_working = any('✅' in str(v) for v in results['vix_tests'].values())
    stocks_working = any('✅' in str(v) for v in results['control_tests'].values())
    
    results['summary'] = {
        'spx_working': spx_working,
        'vix_working': vix_working,
        'stocks_working': stocks_working
    }
    
    # Recommendation
    if spx_working and vix_working:
        results['recommendation'] = '✅ TWELVE DATA WORKING - Bot ready to trade!'
        results['status'] = 'READY'
    elif stocks_working and not spx_working:
        results['recommendation'] = '⚠️ Stocks work but indices (SPX/VIX) blocked - Need different plan or upgrade'
        results['status'] = 'INDICES_BLOCKED'
        results['next_steps'] = 'Either upgrade Twelve Data plan OR try different API (Alpha Vantage, FMP, etc.)'
    elif not stocks_working:
        results['recommendation'] = '❌ API not working at all - Check API key or try different provider'
        results['status'] = 'API_FAILED'
        results['next_steps'] = 'Verify API key at https://twelvedata.com/account'
    else:
        results['recommendation'] = '⚠️ Mixed results - Check individual test details above'
        results['status'] = 'MIXED'
    
    return jsonify(results), 200



@app.route("/test_alpha_vantage", methods=["GET"])
def test_alpha_vantage():
    """Test Alpha Vantage API - SPX, VIX, and controls"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'environment': 'Railway Production',
        'api_provider': 'Alpha Vantage',
        'api_website': 'https://www.alphavantage.co'
    }
    
    # Check if we have ALPHA_VANTAGE_KEY
    ALPHA_VANTAGE_KEY = os.environ.get('ALPHA_VANTAGE_KEY')
    
    # ========================================================================
    # TEST ALPHA VANTAGE API KEY
    # ========================================================================
    results['api_key_check'] = {}
    
    if not ALPHA_VANTAGE_KEY or ALPHA_VANTAGE_KEY == '':
        results['api_key_check']['status'] = '❌ MISSING'
        results['api_key_check']['message'] = 'ALPHA_VANTAGE_KEY environment variable not set'
        return jsonify(results), 500
    else:
        results['api_key_check']['status'] = '✅ PRESENT'
        results['api_key_check']['length'] = len(ALPHA_VANTAGE_KEY)
        results['api_key_check']['preview'] = ALPHA_VANTAGE_KEY[:8] + '...' if len(ALPHA_VANTAGE_KEY) > 8 else ALPHA_VANTAGE_KEY
    
    # ========================================================================
    # TEST SPX DATA (^GSPC)
    # ========================================================================
    results['spx_tests'] = {}
    
    # Method 1: GLOBAL_QUOTE (real-time price)
    try:
        print("  [TEST] Fetching SPX via GLOBAL_QUOTE...")
        quote_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=SPX&apikey={ALPHA_VANTAGE_KEY}"
        quote_response = requests.get(quote_url, timeout=10)
        
        results['spx_tests']['global_quote_http_status'] = quote_response.status_code
        
        if quote_response.status_code == 200:
            quote_data = quote_response.json()
            results['spx_tests']['global_quote_response'] = quote_data
            
            if 'Error Message' in quote_data:
                results['spx_tests']['global_quote_status'] = f"❌ ERROR: {quote_data['Error Message']}"
            elif 'Note' in quote_data:
                results['spx_tests']['global_quote_status'] = f"⚠️ RATE LIMITED: {quote_data['Note']}"
            elif 'Global Quote' in quote_data and '05. price' in quote_data['Global Quote']:
                results['spx_tests']['global_quote_status'] = '✅ SUCCESS'
                results['spx_tests']['spx_price'] = float(quote_data['Global Quote']['05. price'])
                results['spx_tests']['spx_high'] = float(quote_data['Global Quote']['03. high'])
                results['spx_tests']['spx_low'] = float(quote_data['Global Quote']['04. low'])
                results['spx_tests']['spx_change'] = quote_data['Global Quote']['09. change']
            else:
                results['spx_tests']['global_quote_status'] = '❌ UNEXPECTED RESPONSE FORMAT'
        else:
            results['spx_tests']['global_quote_status'] = f'❌ HTTP ERROR {quote_response.status_code}'
    
    except Exception as e:
        results['spx_tests']['global_quote_status'] = f'❌ EXCEPTION: {str(e)}'
        import traceback
        results['spx_tests']['global_quote_traceback'] = traceback.format_exc()
    
    # Method 2: TIME_SERIES_DAILY (historical data)
    try:
        print("  [TEST] Fetching SPX via TIME_SERIES_DAILY...")
        ts_url = f"https://www.alphavantage.co/query?function=TIME_SERIES_DAILY&symbol=SPX&outputsize=compact&apikey={ALPHA_VANTAGE_KEY}"
        ts_response = requests.get(ts_url, timeout=10)
        
        results['spx_tests']['time_series_http_status'] = ts_response.status_code
        
        if ts_response.status_code == 200:
            ts_data = ts_response.json()
            
            if 'Error Message' in ts_data:
                results['spx_tests']['time_series_status'] = f"❌ ERROR: {ts_data['Error Message']}"
            elif 'Note' in ts_data:
                results['spx_tests']['time_series_status'] = f"⚠️ RATE LIMITED: {ts_data['Note']}"
            elif 'Time Series (Daily)' in ts_data:
                results['spx_tests']['time_series_status'] = '✅ SUCCESS'
                dates = list(ts_data['Time Series (Daily)'].keys())
                results['spx_tests']['days_available'] = len(dates)
                results['spx_tests']['latest_date'] = dates[0] if dates else 'None'
                results['spx_tests']['sample_data'] = {dates[0]: ts_data['Time Series (Daily)'][dates[0]]} if dates else {}
            else:
                results['spx_tests']['time_series_status'] = '❌ UNEXPECTED RESPONSE FORMAT'
                results['spx_tests']['time_series_response'] = ts_data
        else:
            results['spx_tests']['time_series_status'] = f'❌ HTTP ERROR {ts_response.status_code}'
    
    except Exception as e:
        results['spx_tests']['time_series_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST VIX DATA
    # ========================================================================
    results['vix_tests'] = {}
    
    try:
        print("  [TEST] Fetching VIX via GLOBAL_QUOTE...")
        vix_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=VIX&apikey={ALPHA_VANTAGE_KEY}"
        vix_response = requests.get(vix_url, timeout=10)
        
        results['vix_tests']['global_quote_http_status'] = vix_response.status_code
        
        if vix_response.status_code == 200:
            vix_data = vix_response.json()
            results['vix_tests']['global_quote_response'] = vix_data
            
            if 'Error Message' in vix_data:
                results['vix_tests']['global_quote_status'] = f"❌ ERROR: {vix_data['Error Message']}"
            elif 'Note' in vix_data:
                results['vix_tests']['global_quote_status'] = f"⚠️ RATE LIMITED: {vix_data['Note']}"
            elif 'Global Quote' in vix_data and '05. price' in vix_data['Global Quote']:
                results['vix_tests']['global_quote_status'] = '✅ SUCCESS'
                results['vix_tests']['vix_value'] = float(vix_data['Global Quote']['05. price'])
            else:
                results['vix_tests']['global_quote_status'] = '❌ UNEXPECTED RESPONSE FORMAT'
        else:
            results['vix_tests']['global_quote_status'] = f'❌ HTTP ERROR {vix_response.status_code}'
    
    except Exception as e:
        results['vix_tests']['global_quote_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST STOCK DATA (Control - AAPL)
    # ========================================================================
    results['control_tests'] = {}
    
    try:
        print("  [TEST] Fetching AAPL quote (control test)...")
        aapl_url = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol=AAPL&apikey={ALPHA_VANTAGE_KEY}"
        aapl_response = requests.get(aapl_url, timeout=10)
        
        if aapl_response.status_code == 200:
            aapl_data = aapl_response.json()
            
            if 'Error Message' in aapl_data:
                results['control_tests']['AAPL'] = {'status': f"❌ ERROR: {aapl_data['Error Message']}"}
            elif 'Note' in aapl_data:
                results['control_tests']['AAPL'] = {'status': f"⚠️ RATE LIMITED: {aapl_data['Note']}"}
            elif 'Global Quote' in aapl_data and '05. price' in aapl_data['Global Quote']:
                results['control_tests']['AAPL'] = {
                    'status': '✅ SUCCESS',
                    'price': float(aapl_data['Global Quote']['05. price'])
                }
            else:
                results['control_tests']['AAPL'] = {'status': '❌ UNEXPECTED FORMAT'}
        else:
            results['control_tests']['AAPL'] = {'status': f'❌ HTTP {aapl_response.status_code}'}
    
    except Exception as e:
        results['control_tests']['AAPL'] = {'status': f'❌ EXCEPTION: {str(e)}'}
    
    # ========================================================================
    # API RATE LIMIT INFO
    # ========================================================================
    results['api_limits'] = {
        'free_tier': '25 API calls per day',
        'note': 'Alpha Vantage free tier includes indices (SPX, VIX)',
        'upgrade_info': 'Premium plans available at https://www.alphavantage.co/premium/'
    }
    
    # ========================================================================
    # SUMMARY & RECOMMENDATION
    # ========================================================================
    spx_working = any('✅' in str(v) for v in results['spx_tests'].values())
    vix_working = any('✅' in str(v) for v in results['vix_tests'].values())
    stocks_working = any('✅' in str(v) for v in results['control_tests'].values())
    
    results['summary'] = {
        'spx_working': spx_working,
        'vix_working': vix_working,
        'stocks_working': stocks_working
    }
    
    # Check for rate limiting
    rate_limited = any('RATE LIMITED' in str(v) for v in [results['spx_tests'], results['vix_tests'], results['control_tests']])
    
    if rate_limited:
        results['recommendation'] = '⚠️ RATE LIMITED - You hit the 25 calls/day limit. Wait until tomorrow or upgrade.'
        results['status'] = 'RATE_LIMITED'
        results['next_steps'] = 'Wait 24 hours for limit reset OR upgrade at https://www.alphavantage.co/premium/'
    elif spx_working and vix_working:
        results['recommendation'] = '✅ ALPHA VANTAGE WORKING - Bot ready to trade!'
        results['status'] = 'READY'
    elif stocks_working and not spx_working:
        results['recommendation'] = '⚠️ Stocks work but indices (SPX/VIX) failed - Check API limits or response format'
        results['status'] = 'INDICES_FAILED'
    elif not stocks_working:
        results['recommendation'] = '❌ API not working at all - Check API key'
        results['status'] = 'API_FAILED'
        results['next_steps'] = 'Verify API key at https://www.alphavantage.co/support/#api-key'
    else:
        results['recommendation'] = '⚠️ Mixed results - Check individual test details above'
        results['status'] = 'MIXED'
    
    return jsonify(results), 200



@app.route("/test_fmp", methods=["GET"])
def test_fmp():
    """Test Financial Modeling Prep API - SPX, VIX, and controls"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'environment': 'Railway Production',
        'api_provider': 'Financial Modeling Prep (FMP)',
        'api_website': 'https://financialmodelingprep.com'
    }
    
    # Check if we have FMP_API_KEY
    FMP_API_KEY = os.environ.get('FMP_API_KEY')
    
    # ========================================================================
    # TEST FMP API KEY
    # ========================================================================
    results['api_key_check'] = {}
    
    if not FMP_API_KEY or FMP_API_KEY == '':
        results['api_key_check']['status'] = '❌ MISSING'
        results['api_key_check']['message'] = 'FMP_API_KEY environment variable not set'
        return jsonify(results), 500
    else:
        results['api_key_check']['status'] = '✅ PRESENT'
        results['api_key_check']['length'] = len(FMP_API_KEY)
        results['api_key_check']['preview'] = FMP_API_KEY[:8] + '...' if len(FMP_API_KEY) > 8 else FMP_API_KEY
    
    # ========================================================================
    # TEST SPX DATA (^GSPC)
    # ========================================================================
    results['spx_tests'] = {}
    
    # Method 1: Quote (real-time price)
    try:
        print("  [TEST] Fetching SPX quote...")
        quote_url = f"https://financialmodelingprep.com/api/v3/quote/%5EGSPC?apikey={FMP_API_KEY}"
        quote_response = requests.get(quote_url, timeout=10)
        
        results['spx_tests']['quote_http_status'] = quote_response.status_code
        
        if quote_response.status_code == 200:
            quote_data = quote_response.json()
            results['spx_tests']['quote_response'] = quote_data
            
            if isinstance(quote_data, dict) and 'Error Message' in quote_data:
                results['spx_tests']['quote_status'] = f"❌ ERROR: {quote_data['Error Message']}"
            elif isinstance(quote_data, list) and len(quote_data) > 0:
                spx_quote = quote_data[0]
                if 'price' in spx_quote:
                    results['spx_tests']['quote_status'] = '✅ SUCCESS'
                    results['spx_tests']['spx_price'] = float(spx_quote['price'])
                    results['spx_tests']['spx_high'] = float(spx_quote.get('dayHigh', 0))
                    results['spx_tests']['spx_low'] = float(spx_quote.get('dayLow', 0))
                    results['spx_tests']['spx_change'] = spx_quote.get('change', 0)
                else:
                    results['spx_tests']['quote_status'] = '❌ UNEXPECTED FORMAT'
            else:
                results['spx_tests']['quote_status'] = '❌ EMPTY OR INVALID RESPONSE'
        else:
            results['spx_tests']['quote_status'] = f'❌ HTTP ERROR {quote_response.status_code}'
            results['spx_tests']['quote_error'] = quote_response.text[:200]
    
    except Exception as e:
        results['spx_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
        import traceback
        results['spx_tests']['quote_traceback'] = traceback.format_exc()
    
    # Method 2: Historical data
    try:
        print("  [TEST] Fetching SPX historical data...")
        hist_url = f"https://financialmodelingprep.com/api/v3/historical-price-full/%5EGSPC?apikey={FMP_API_KEY}"
        hist_response = requests.get(hist_url, timeout=10)
        
        results['spx_tests']['historical_http_status'] = hist_response.status_code
        
        if hist_response.status_code == 200:
            hist_data = hist_response.json()
            
            if 'Error Message' in hist_data:
                results['spx_tests']['historical_status'] = f"❌ ERROR: {hist_data['Error Message']}"
            elif 'historical' in hist_data:
                results['spx_tests']['historical_status'] = '✅ SUCCESS'
                results['spx_tests']['days_available'] = len(hist_data['historical'])
                results['spx_tests']['latest_date'] = hist_data['historical'][0]['date'] if hist_data['historical'] else 'None'
                results['spx_tests']['sample_data'] = hist_data['historical'][:2] if hist_data['historical'] else []
            else:
                results['spx_tests']['historical_status'] = '❌ UNEXPECTED FORMAT'
        else:
            results['spx_tests']['historical_status'] = f'❌ HTTP ERROR {hist_response.status_code}'
    
    except Exception as e:
        results['spx_tests']['historical_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST VIX DATA
    # ========================================================================
    results['vix_tests'] = {}
    
    try:
        print("  [TEST] Fetching VIX quote...")
        vix_url = f"https://financialmodelingprep.com/api/v3/quote/%5EVIX?apikey={FMP_API_KEY}"
        vix_response = requests.get(vix_url, timeout=10)
        
        results['vix_tests']['quote_http_status'] = vix_response.status_code
        
        if vix_response.status_code == 200:
            vix_data = vix_response.json()
            results['vix_tests']['quote_response'] = vix_data
            
            if isinstance(vix_data, dict) and 'Error Message' in vix_data:
                results['vix_tests']['quote_status'] = f"❌ ERROR: {vix_data['Error Message']}"
            elif isinstance(vix_data, list) and len(vix_data) > 0:
                vix_quote = vix_data[0]
                if 'price' in vix_quote:
                    results['vix_tests']['quote_status'] = '✅ SUCCESS'
                    results['vix_tests']['vix_value'] = float(vix_quote['price'])
                else:
                    results['vix_tests']['quote_status'] = '❌ UNEXPECTED FORMAT'
            else:
                results['vix_tests']['quote_status'] = '❌ EMPTY OR INVALID RESPONSE'
        else:
            results['vix_tests']['quote_status'] = f'❌ HTTP ERROR {vix_response.status_code}'
    
    except Exception as e:
        results['vix_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST STOCK DATA (Control - AAPL)
    # ========================================================================
    results['control_tests'] = {}
    
    try:
        print("  [TEST] Fetching AAPL quote (control test)...")
        aapl_url = f"https://financialmodelingprep.com/api/v3/quote/AAPL?apikey={FMP_API_KEY}"
        aapl_response = requests.get(aapl_url, timeout=10)
        
        if aapl_response.status_code == 200:
            aapl_data = aapl_response.json()
            
            if isinstance(aapl_data, dict) and 'Error Message' in aapl_data:
                results['control_tests']['AAPL'] = {'status': f"❌ ERROR: {aapl_data['Error Message']}"}
            elif isinstance(aapl_data, list) and len(aapl_data) > 0 and 'price' in aapl_data[0]:
                results['control_tests']['AAPL'] = {
                    'status': '✅ SUCCESS',
                    'price': float(aapl_data[0]['price'])
                }
            else:
                results['control_tests']['AAPL'] = {'status': '❌ UNEXPECTED FORMAT'}
        else:
            results['control_tests']['AAPL'] = {'status': f'❌ HTTP {aapl_response.status_code}'}
    
    except Exception as e:
        results['control_tests']['AAPL'] = {'status': f'❌ EXCEPTION: {str(e)}'}
    
    # ========================================================================
    # API RATE LIMIT INFO
    # ========================================================================
    results['api_limits'] = {
        'free_tier': '250 API calls per day',
        'note': 'FMP free tier includes indices (SPX, VIX)',
        'upgrade_info': 'Premium plans available at https://financialmodelingprep.com/developer/docs/pricing'
    }
    
    # ========================================================================
    # SUMMARY & RECOMMENDATION
    # ========================================================================
    spx_working = any('✅' in str(v) for v in results['spx_tests'].values())
    vix_working = any('✅' in str(v) for v in results['vix_tests'].values())
    stocks_working = any('✅' in str(v) for v in results['control_tests'].values())
    
    results['summary'] = {
        'spx_working': spx_working,
        'vix_working': vix_working,
        'stocks_working': stocks_working
    }
    
    if spx_working and vix_working:
        results['recommendation'] = '✅ FMP WORKING - Bot ready to trade!'
        results['status'] = 'READY'
    elif stocks_working and not spx_working:
        results['recommendation'] = '⚠️ Stocks work but indices (SPX/VIX) failed - Check symbol format or API limits'
        results['status'] = 'INDICES_FAILED'
        results['next_steps'] = 'Check FMP documentation for index symbols'
    elif not stocks_working:
        results['recommendation'] = '❌ API not working at all - Check API key'
        results['status'] = 'API_FAILED'
        results['next_steps'] = 'Verify API key at https://financialmodelingprep.com/developer/docs'
    else:
        results['recommendation'] = '⚠️ Mixed results - Check individual test details above'
        results['status'] = 'MIXED'
    
    return jsonify(results), 200

@app.route("/test_marketstack_all", methods=["GET"])
def test_marketstack_all():
    """Test all possible SPX and VIX symbol formats on Marketstack"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'environment': 'Railway Production',
        'api_provider': 'Marketstack',
        'api_website': 'https://marketstack.com'
    }
    
    MARKETSTACK_KEY = os.environ.get('MARKETSTACK_KEY')
    
    if not MARKETSTACK_KEY:
        return jsonify({'error': 'MARKETSTACK_KEY not set'}), 500
    
    results['api_key_check'] = {
        'status': '✅ PRESENT',
        'length': len(MARKETSTACK_KEY),
        'preview': MARKETSTACK_KEY[:8] + '...'
    }
    
    # ========================================================================
    # TEST SPX SYMBOLS - Try multiple formats
    # ========================================================================
    spx_symbols = [
        'SPX',           # Without caret
        '^GSPC',         # Yahoo format
        'GSPC',          # Without caret
        '.SPX',          # Dot notation
        'INX',           # Alternative SPX code
        '$SPX',          # Dollar notation
        'SPX.INDX',      # With exchange suffix
        'SPX.US',        # US exchange
    ]
    
    results['spx_symbol_tests'] = {}
    
    for symbol in spx_symbols:
        try:
            print(f"  [TEST SPX] Trying: {symbol}")
            url = f"http://api.marketstack.com/v1/eod?access_key={MARKETSTACK_KEY}&symbols={symbol}&limit=5"
            response = requests.get(url, timeout=10)
            
            results['spx_symbol_tests'][symbol] = {
                'http_status': response.status_code
            }
            
            if response.status_code == 200:
                data = response.json()
                
                if 'error' in data:
                    results['spx_symbol_tests'][symbol]['status'] = f"❌ ERROR: {data['error'].get('message', 'Unknown')}"
                elif 'data' in data and len(data['data']) > 0:
                    results['spx_symbol_tests'][symbol]['status'] = '✅ SUCCESS'
                    results['spx_symbol_tests'][symbol]['latest_close'] = data['data'][0]['close']
                    results['spx_symbol_tests'][symbol]['latest_date'] = data['data'][0]['date']
                    results['spx_symbol_tests'][symbol]['days_returned'] = len(data['data'])
                    
                    # Get closes for RV calculation
                    closes = [d['close'] for d in data['data']]
                    results['spx_symbol_tests'][symbol]['sample_closes'] = closes
                else:
                    results['spx_symbol_tests'][symbol]['status'] = '❌ NO DATA'
            else:
                results['spx_symbol_tests'][symbol]['status'] = f'❌ HTTP {response.status_code}'
                results['spx_symbol_tests'][symbol]['error'] = response.text[:200]
        
        except Exception as e:
            results['spx_symbol_tests'][symbol]['status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST VIX SYMBOLS - Try multiple formats
    # ========================================================================
    vix_symbols = [
        'VIX',           # Without caret
        '^VIX',          # Yahoo format
        '.VIX',          # Dot notation
        '$VIX',          # Dollar notation
        'VIX.INDX',      # With exchange suffix
        'VIX.US',        # US exchange
        'VIXCLS',        # FRED style
    ]
    
    results['vix_symbol_tests'] = {}
    
    for symbol in vix_symbols:
        try:
            print(f"  [TEST VIX] Trying: {symbol}")
            url = f"http://api.marketstack.com/v1/eod?access_key={MARKETSTACK_KEY}&symbols={symbol}&limit=5"
            response = requests.get(url, timeout=10)
            
            results['vix_symbol_tests'][symbol] = {
                'http_status': response.status_code
            }
            
            if response.status_code == 200:
                data = response.json()
                
                if 'error' in data:
                    results['vix_symbol_tests'][symbol]['status'] = f"❌ ERROR: {data['error'].get('message', 'Unknown')}"
                elif 'data' in data and len(data['data']) > 0:
                    results['vix_symbol_tests'][symbol]['status'] = '✅ SUCCESS'
                    results['vix_symbol_tests'][symbol]['vix_value'] = data['data'][0]['close']
                    results['vix_symbol_tests'][symbol]['latest_date'] = data['data'][0]['date']
                else:
                    results['vix_symbol_tests'][symbol]['status'] = '❌ NO DATA'
            else:
                results['vix_symbol_tests'][symbol]['status'] = f'❌ HTTP {response.status_code}'
                results['vix_symbol_tests'][symbol]['error'] = response.text[:200]
        
        except Exception as e:
            results['vix_symbol_tests'][symbol]['status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # CONTROL TEST - AAPL (should always work)
    # ========================================================================
    results['control_test'] = {}
    
    try:
        print(f"  [TEST CONTROL] AAPL...")
        url = f"http://api.marketstack.com/v1/eod?access_key={MARKETSTACK_KEY}&symbols=AAPL&limit=5"
        response = requests.get(url, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            if 'data' in data and len(data['data']) > 0:
                results['control_test']['AAPL'] = {
                    'status': '✅ SUCCESS',
                    'price': data['data'][0]['close'],
                    'date': data['data'][0]['date']
                }
            else:
                results['control_test']['AAPL'] = {'status': '❌ NO DATA'}
        else:
            results['control_test']['AAPL'] = {'status': f'❌ HTTP {response.status_code}'}
    except Exception as e:
        results['control_test']['AAPL'] = {'status': f'❌ EXCEPTION: {str(e)}'}
    
    # ========================================================================
    # SUMMARY & RECOMMENDATION
    # ========================================================================
    working_spx = [sym for sym, data in results['spx_symbol_tests'].items() if '✅' in data.get('status', '')]
    working_vix = [sym for sym, data in results['vix_symbol_tests'].items() if '✅' in data.get('status', '')]
    stocks_working = '✅' in results['control_test'].get('AAPL', {}).get('status', '')
    
    results['summary'] = {
        'working_spx_symbols': working_spx,
        'working_vix_symbols': working_vix,
        'stocks_working': stocks_working,
        'spx_found': len(working_spx) > 0,
        'vix_found': len(working_vix) > 0
    }
    
    # Final recommendation
    if working_spx and working_vix:
        results['recommendation'] = f"✅ MARKETSTACK READY! Use SPX={working_spx[0]}, VIX={working_vix[0]}"
        results['status'] = 'READY'
        results['best_symbols'] = {
            'spx': working_spx[0],
            'vix': working_vix[0]
        }
    elif working_spx and not working_vix:
        results['recommendation'] = f"⚠️ SPX works ({working_spx[0]}) but VIX failed - Need alternative VIX source"
        results['status'] = 'SPX_ONLY'
        results['best_symbols'] = {
            'spx': working_spx[0],
            'vix': None
        }
    elif not working_spx and working_vix:
        results['recommendation'] = f"⚠️ VIX works ({working_vix[0]}) but SPX failed - Need alternative SPX source"
        results['status'] = 'VIX_ONLY'
        results['best_symbols'] = {
            'spx': None,
            'vix': working_vix[0]
        }
    elif stocks_working:
        results['recommendation'] = '❌ Indices not available on Marketstack free tier - Only stocks work'
        results['status'] = 'INDICES_BLOCKED'
        results['note'] = 'Marketstack free tier likely does not include index data (SPX, VIX)'
        results['alternatives'] = 'Either upgrade Marketstack OR use paid API (Polygon, Alpha Vantage Premium, etc.)'
    else:
        results['recommendation'] = '❌ Marketstack API not working at all'
        results['status'] = 'FAILED'
    
    # API limits reminder
    results['api_limits'] = {
        'free_tier': '100 API requests per month',
        'data_type': 'End-of-Day (EOD) only',
        'note': 'If indices not available, may need paid plan'
    }
    
    return jsonify(results), 200

@app.route("/test_polygon_massive", methods=["GET"])
def test_polygon_massive():
    """Test Polygon/Massive API - NEW api.massive.com domain"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'environment': 'Railway Production',
        'api_provider': 'Polygon (now Massive)',
        'api_domain': 'api.massive.com (NEW)',
        'old_domain': 'api.polygon.io (deprecated)',
        'api_website': 'https://polygon.io'
    }
    
    # Check if we have key
    POLYGON_KEY = os.environ.get('POLYGON_API_KEY')
    
    if not POLYGON_KEY:
        return jsonify({'error': 'POLYGON_API_KEY not set'}), 500
    
    results['api_key_check'] = {
        'status': '✅ PRESENT',
        'length': len(POLYGON_KEY),
        'preview': POLYGON_KEY[:8] + '...'
    }
    
    # ========================================================================
    # TEST 1: SPX Index Data (requires paid plan)
    # ========================================================================
    results['spx_tests'] = {}
    
    # Try SPX quote with NEW domain
    try:
        print("  [TEST] Fetching SPX from api.massive.com...")
        quote_url = f"https://api.massive.com/v2/last/trade/I:SPX?apiKey={POLYGON_KEY}"
        quote_response = requests.get(quote_url, timeout=10)
        
        results['spx_tests']['quote_http_status'] = quote_response.status_code
        results['spx_tests']['quote_response'] = quote_response.json()
        
        if quote_response.status_code == 200:
            data = quote_response.json()
            if 'results' in data and 'p' in data['results']:
                results['spx_tests']['quote_status'] = '✅ SUCCESS - YOU HAVE INDEX ACCESS!'
                results['spx_tests']['spx_price'] = float(data['results']['p'])
            elif 'status' in data and data['status'] == 'ERROR':
                results['spx_tests']['quote_status'] = f"❌ ERROR: {data.get('error', 'Unknown')}"
            else:
                results['spx_tests']['quote_status'] = '❌ UNEXPECTED FORMAT'
        elif quote_response.status_code == 403:
            results['spx_tests']['quote_status'] = '❌ FORBIDDEN (403) - Indices require paid plan'
        else:
            results['spx_tests']['quote_status'] = f'❌ HTTP {quote_response.status_code}'
    
    except Exception as e:
        results['spx_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
        import traceback
        results['spx_tests']['traceback'] = traceback.format_exc()
    
    # Try SPX aggregates (historical)
    try:
        print("  [TEST] Fetching SPX aggregates...")
        end_date = datetime.now(ET_TZ)
        start_date = end_date - timedelta(days=10)
        
        agg_url = f"https://api.massive.com/v2/aggs/ticker/I:SPX/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}?adjusted=true&sort=desc&apiKey={POLYGON_KEY}"
        agg_response = requests.get(agg_url, timeout=10)
        
        results['spx_tests']['agg_http_status'] = agg_response.status_code
        
        if agg_response.status_code == 200:
            data = agg_response.json()
            if 'results' in data and len(data['results']) > 0:
                results['spx_tests']['agg_status'] = '✅ SUCCESS'
                results['spx_tests']['days_returned'] = len(data['results'])
                results['spx_tests']['latest_close'] = data['results'][0].get('c')
                results['spx_tests']['sample_data'] = data['results'][:2]
            else:
                results['spx_tests']['agg_status'] = '❌ NO RESULTS'
                results['spx_tests']['agg_response'] = data
        elif agg_response.status_code == 403:
            results['spx_tests']['agg_status'] = '❌ FORBIDDEN (403)'
        else:
            results['spx_tests']['agg_status'] = f'❌ HTTP {agg_response.status_code}'
    
    except Exception as e:
        results['spx_tests']['agg_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST 2: VIX Index Data
    # ========================================================================
    results['vix_tests'] = {}
    
    try:
        print("  [TEST] Fetching VIX from api.massive.com...")
        vix_url = f"https://api.massive.com/v2/last/trade/I:VIX?apiKey={POLYGON_KEY}"
        vix_response = requests.get(vix_url, timeout=10)
        
        results['vix_tests']['quote_http_status'] = vix_response.status_code
        results['vix_tests']['quote_response'] = vix_response.json()
        
        if vix_response.status_code == 200:
            data = vix_response.json()
            if 'results' in data and 'p' in data['results']:
                results['vix_tests']['quote_status'] = '✅ SUCCESS'
                results['vix_tests']['vix_value'] = float(data['results']['p'])
            else:
                results['vix_tests']['quote_status'] = '❌ NO DATA'
        elif vix_response.status_code == 403:
            results['vix_tests']['quote_status'] = '❌ FORBIDDEN (403)'
        else:
            results['vix_tests']['quote_status'] = f'❌ HTTP {vix_response.status_code}'
    
    except Exception as e:
        results['vix_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST 3: SPY (Stock/ETF - should work on free tier)
    # ========================================================================
    results['spy_tests'] = {}
    
    try:
        print("  [TEST] Fetching SPY (stock) from api.massive.com...")
        spy_url = f"https://api.massive.com/v2/last/trade/SPY?apiKey={POLYGON_KEY}"
        spy_response = requests.get(spy_url, timeout=10)
        
        results['spy_tests']['quote_http_status'] = spy_response.status_code
        
        if spy_response.status_code == 200:
            data = spy_response.json()
            if 'results' in data and 'p' in data['results']:
                results['spy_tests']['quote_status'] = '✅ SUCCESS'
                results['spy_tests']['spy_price'] = float(data['results']['p'])
                results['spy_tests']['spx_equivalent'] = float(data['results']['p']) * 10
            else:
                results['spy_tests']['quote_status'] = '❌ NO DATA'
                results['spy_tests']['response'] = data
        else:
            results['spy_tests']['quote_status'] = f'❌ HTTP {spy_response.status_code}'
    
    except Exception as e:
        results['spy_tests']['quote_status'] = f'❌ EXCEPTION: {str(e)}'
    
    # ========================================================================
    # TEST 4: Control - AAPL
    # ========================================================================
    results['control_test'] = {}
    
    try:
        print("  [TEST] Fetching AAPL (control)...")
        aapl_url = f"https://api.massive.com/v2/last/trade/AAPL?apiKey={POLYGON_KEY}"
        aapl_response = requests.get(aapl_url, timeout=10)
        
        if aapl_response.status_code == 200:
            data = aapl_response.json()
            if 'results' in data and 'p' in data['results']:
                results['control_test']['AAPL'] = {
                    'status': '✅ SUCCESS',
                    'price': float(data['results']['p'])
                }
            else:
                results['control_test']['AAPL'] = {'status': '❌ NO DATA'}
        else:
            results['control_test']['AAPL'] = {'status': f'❌ HTTP {aapl_response.status_code}'}
    
    except Exception as e:
        results['control_test']['AAPL'] = {'status': f'❌ EXCEPTION: {str(e)}'}
    
    # ========================================================================
    # SUMMARY
    # ========================================================================
    spx_working = '✅' in results['spx_tests'].get('quote_status', '')
    vix_working = '✅' in results['vix_tests'].get('quote_status', '')
    spy_working = '✅' in results['spy_tests'].get('quote_status', '')
    stocks_working = '✅' in results['control_test'].get('AAPL', {}).get('status', '')
    
    results['summary'] = {
        'spx_index_access': spx_working,
        'vix_index_access': vix_working,
        'spy_stock_access': spy_working,
        'aapl_stock_access': stocks_working
    }
    
    # Determine plan tier and recommendation
    if spx_working and vix_working:
        results['detected_plan'] = '✅ PAID PLAN (Starter or higher) - Indices included!'
        results['recommendation'] = '✅ POLYGON/MASSIVE READY - Full bot functionality available!'
        results['status'] = 'READY'
    elif (spy_working or stocks_working) and not spx_working:
        results['detected_plan'] = '⚠️ FREE PLAN - Stocks/ETFs only, no indices'
        results['recommendation'] = '⚠️ Need to upgrade to Starter plan ($99/mo) for SPX/VIX access OR use SPY proxy workaround'
        results['status'] = 'FREE_TIER'
        results['upgrade_link'] = 'https://polygon.io/pricing'
    else:
        results['detected_plan'] = '❌ UNKNOWN - API might be invalid or blocked'
        results['recommendation'] = '❌ Check API key validity at https://polygon.io/dashboard'
        results['status'] = 'INVALID_KEY'
    
    return jsonify(results), 200


if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))
    
    print("=" * 80)
    print("Ren's SPX Vol Signal - Production (Twelve Data API)")
    print("=" * 80)
    print(f"Port: {PORT}")
    print(f"Trading Window: 2:30-3:30 PM ET")
    print(f"Data Source: Twelve Data API (800 calls/day free)")
    print(f"News Parser: Direct HTTP + XML (No feedparser)")
    print("=" * 80)
    
    # Start background thread
    t = threading.Thread(target=poke_self, daemon=True)
    t.start()
    
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)