#!/usr/bin/env python3
"""
SPX Overnight Vol Premium Bot - Railway Production
Uses Polygon/Massive Indices Starter ($49/mo) - 15-min delayed data
Real SPX + VIX1D data (no more proxies!)

Triple-Layer Filtering: Algo Dedup → Keyword Filter → GPT Analysis
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

# Configuration
ET_TZ = pytz.timezone('US/Eastern')

# Trading windows - PRODUCTION: 2:30-3:30 PM ET
TRADING_WINDOW_START = dt_time(hour=14, minute=30)
TRADING_WINDOW_END = dt_time(hour=15, minute=30)

def load_config():
    """Load configuration from environment variables (Railway)"""
    config = {
        'OPENAI_API_KEY': os.environ.get('OPENAI_API_KEY'),
        'POLYGON_API_KEY': os.environ.get('POLYGON_API_KEY'),
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
POLYGON_API_KEY = CONFIG.get('POLYGON_API_KEY')

WEBHOOK_URLS = {
    'TRADE_AGGRESSIVE': CONFIG.get('TRADE_AGGRESSIVE_URL'),
    'TRADE_NORMAL': CONFIG.get('TRADE_NORMAL_URL'),
    'TRADE_CONSERVATIVE': CONFIG.get('TRADE_CONSERVATIVE_URL'),
    'NO_TRADE': CONFIG.get('NO_TRADE_URL')
}

# ============================================================================
# LAYER 1: ALGO DEDUPLICATION
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
    """LAYER 1: Algorithmic deduplication with fuzzy matching"""
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
# LAYER 2: KEYWORD FILTER
# ============================================================================

def is_obvious_junk(title, description=""):
    """LAYER 2: Lenient keyword filter"""
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
# NEWS FETCHING - NO FEEDPARSER
# ============================================================================

def parse_rss_feed(url, source_name):
    """Parse RSS feed using direct HTTP + XML parsing"""
    try:
        response = requests.get(
            url, 
            timeout=15, 
            headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
        )
        response.raise_for_status()
        
        root = ET.fromstring(response.content)
        articles = []
        now = datetime.now(ET_TZ)
        
        for item in root.findall('.//item'):
            try:
                title_elem = item.find('title')
                title = title_elem.text if title_elem is not None and title_elem.text else 'No title'
                
                link_elem = item.find('link')
                link = link_elem.text if link_elem is not None and link_elem.text else ''
                
                description_elem = item.find('description')
                description = description_elem.text if description_elem is not None and description_elem.text else ''
                
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
    """Source 1: Yahoo Finance RSS"""
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
    """Source 2: Google News RSS"""
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
    """Triple-layer news processing"""
    try:
        all_articles = []
        
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
        
        # LAYER 1: Deduplication
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
        
        filtered_articles.sort(key=lambda x: x['published_time'], reverse=True)
        
        # Format for GPT
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
# DATA FETCHING - POLYGON/MASSIVE (SEGREGATED - NO DUPLICATION)
# ============================================================================

def get_spx_snapshot():
    """
    Fetch ONLY SPX current value from Polygon snapshot
    Returns 15-min delayed data from Indices Starter plan
    """
    try:
        print("  [POLYGON] Fetching SPX snapshot...")
        
        url = f"https://api.massive.com/v3/snapshot/indices?ticker.any_of=I:SPX&apiKey={POLYGON_API_KEY}"
        response = requests.get(url, timeout=15)
        
        if response.status_code != 200:
            print(f"  ❌ SPX snapshot failed: {response.status_code}")
            return None
        
        data = response.json()
        
        if 'results' not in data or len(data['results']) == 0:
            print(f"  ❌ No SPX results in snapshot")
            return None
        
        ticker_data = data['results'][0]
        
        if 'error' in ticker_data:
            print(f"  ❌ SPX error: {ticker_data.get('error')}")
            return None
        
        if ticker_data.get('ticker') != 'I:SPX':
            print(f"  ❌ Unexpected ticker: {ticker_data.get('ticker')}")
            return None
        
        spx_snapshot = {
            'current': ticker_data.get('value'),
            'session': ticker_data.get('session', {}),
            'timeframe': ticker_data.get('timeframe'),
            'market_status': ticker_data.get('market_status')
        }
        
        print(f"  ✅ SPX: {spx_snapshot['current']:.2f} ({spx_snapshot['timeframe']})")
        
        return spx_snapshot
        
    except Exception as e:
        print(f"  ❌ SPX snapshot error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_vix1d_snapshot():
    """
    Fetch ONLY VIX1D current value from Polygon snapshot
    Returns 15-min delayed data from Indices Starter plan
    """
    try:
        print("  [POLYGON] Fetching VIX1D snapshot...")
        
        url = f"https://api.massive.com/v3/snapshot/indices?ticker.any_of=I:VIX1D&apiKey={POLYGON_API_KEY}"
        response = requests.get(url, timeout=15)
        
        if response.status_code != 200:
            print(f"  ❌ VIX1D snapshot failed: {response.status_code}")
            return None
        
        data = response.json()
        
        if 'results' not in data or len(data['results']) == 0:
            print(f"  ❌ No VIX1D results in snapshot")
            return None
        
        ticker_data = data['results'][0]
        
        if 'error' in ticker_data:
            print(f"  ❌ VIX1D error: {ticker_data.get('error')}")
            return None
        
        if ticker_data.get('ticker') != 'I:VIX1D':
            print(f"  ❌ Unexpected ticker: {ticker_data.get('ticker')}")
            return None
        
        vix1d_snapshot = {
            'current': ticker_data.get('value'),
            'session': ticker_data.get('session', {}),
            'timeframe': ticker_data.get('timeframe'),
            'market_status': ticker_data.get('market_status')
        }
        
        print(f"  ✅ VIX1D: {vix1d_snapshot['current']:.2f} ({vix1d_snapshot['timeframe']})")
        
        return vix1d_snapshot
        
    except Exception as e:
        print(f"  ❌ VIX1D snapshot error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_spx_aggregates():
    """
    Fetch ONLY SPX historical data for RV calculation
    Returns last 25 days of closes
    """
    try:
        print("  [POLYGON] Fetching SPX historical data...")
        
        end_date = datetime.now(ET_TZ)
        start_date = end_date - timedelta(days=40)
        
        url = f"https://api.massive.com/v2/aggs/ticker/I:SPX/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}?adjusted=true&sort=desc&limit=50&apiKey={POLYGON_API_KEY}"
        
        response = requests.get(url, timeout=15)
        
        if response.status_code != 200:
            print(f"  ❌ SPX aggregates failed: {response.status_code}")
            return None
        
        data = response.json()
        
        if 'results' not in data or len(data['results']) == 0:
            print(f"  ❌ No SPX historical data")
            return None
        
        closes = [bar['c'] for bar in data['results'][:25]]
        
        print(f"  ✅ Got {len(closes)} days of SPX historical data")
        
        return closes
        
    except Exception as e:
        print(f"  ❌ SPX aggregates error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_spx_data_with_retry(max_retries=3):
    """Fetch SPX snapshot + aggregates with retry"""
    for attempt in range(max_retries):
        try:
            print(f"  [Attempt {attempt + 1}/{max_retries}] Fetching SPX data...")
            
            # Get snapshot (current value)
            snapshot = get_spx_snapshot()
            if not snapshot:
                print(f"  ⚠️ SPX snapshot failed on attempt {attempt + 1}")
                time_module.sleep(5)
                continue
            
            # Get historical data
            historical_closes = get_spx_aggregates()
            if not historical_closes:
                print(f"  ⚠️ SPX historical data failed on attempt {attempt + 1}")
                time_module.sleep(5)
                continue
            
            # Combine data
            result = {
                'current': snapshot['current'],
                'high_today': snapshot['session'].get('high'),
                'low_today': snapshot['session'].get('low'),
                'open_today': snapshot['session'].get('open'),
                'previous_close': snapshot['session'].get('previous_close'),
                'history_closes': historical_closes,
                'timeframe': snapshot['timeframe'],
                'market_status': snapshot['market_status']
            }
            
            print(f"  ✅ SPX data fetch succeeded on attempt {attempt + 1}")
            return result
            
        except Exception as e:
            print(f"  ❌ Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time_module.sleep(5)
    
    return None


def get_vix1d_with_retry(max_retries=3):
    """Fetch VIX1D with retry"""
    for attempt in range(max_retries):
        try:
            print(f"  [Attempt {attempt + 1}/{max_retries}] Fetching VIX1D data...")
            
            # Get snapshot
            snapshot = get_vix1d_snapshot()
            if not snapshot:
                print(f"  ⚠️ VIX1D snapshot failed on attempt {attempt + 1}")
                time_module.sleep(5)
                continue
            
            result = {
                'current': snapshot['current'],
                'tenor': '1-day (VIX1D)',
                'source': 'Polygon_VIX1D',
                'method': 'Polygon Indices Starter ($49/mo)',
                'timeframe': snapshot['timeframe'],
                'session': snapshot['session']
            }
            
            print(f"  ✅ VIX1D data fetch succeeded on attempt {attempt + 1}")
            return result
            
        except Exception as e:
            print(f"  ❌ Attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time_module.sleep(5)
    
    return None

# ============================================================================
# INDICATOR 1: IV/RV RATIO (30%) - Using Real VIX1D!
# ============================================================================

def analyze_iv_rv_ratio(spx_data, vix1d_data):
    """
    Analyze IV/RV ratio using REAL VIX1D (1-day forward implied vol)
    VIX1D = 1-day forward implied volatility (perfect for overnight strategy!)
    RV = 10-day realized volatility
    """
    
    # Calculate 10-day Realized Volatility
    closes = spx_data['history_closes'][:11]  # Need 11 days to get 10 returns
    
    returns = []
    for i in range(1, len(closes)):
        daily_return = math.log(closes[i] / closes[i-1])
        returns.append(daily_return)
    
    mean_return = sum(returns) / len(returns)
    squared_diffs = [(r - mean_return)**2 for r in returns]
    variance = sum(squared_diffs) / (len(returns) - 1)
    daily_std = math.sqrt(variance)
    realized_vol = daily_std * math.sqrt(252) * 100
    
    # VIX1D = 1-day forward implied volatility (already in percentage terms)
    implied_vol = vix1d_data['current']
    
    # IV/RV ratio
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
        closes_earlier = spx_data['history_closes'][10:21]
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
        'vix1d_value': round(implied_vol, 2),
        'tenor': '1-day (VIX1D)',
        'source': 'Polygon VIX1D (real data)',
        'rv_change': round(rv_change, 3)
    }

# ============================================================================
# INDICATOR 2: MARKET TREND (20%)
# ============================================================================

def analyze_market_trend(spx_data):
    """Analyze 5-day momentum and intraday volatility"""
    current = spx_data['current']
    closes = spx_data['history_closes']
    spx_5d_ago = closes[5] if len(closes) >= 6 else current
    
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
# INDICATOR 3: GPT NEWS ANALYSIS (50%)
# ============================================================================

def analyze_gpt_news(news_data):
    """LAYER 3: GPT analysis"""
    
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
    """Check if within 2:30-3:30 PM ET trading window"""
    if now is None:
        now = datetime.now(ET_TZ)
    current_time = now.time()
    return TRADING_WINDOW_START <= current_time <= TRADING_WINDOW_END

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route("/", methods=["GET"])
def homepage():
    """Homepage - Concise, Professional, Holistic"""
    now = datetime.now(ET_TZ)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ren's SPX Vol Signal</title>
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
                min-width: 180px;
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
                <div class="subtitle">Automated SPX Overnight Iron Condor Decision System</div>
                <div class="status">PRODUCTION - RAILWAY</div>
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
                        <strong>1. IV/RV Ratio (30%):</strong> Real VIX1D (1-day forward IV) vs 10-day realized vol.<br>
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
                    <span class="info-value">2:30 PM - 3:30 PM ET</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Environment:</span>
                    <span class="info-value">Railway Production</span>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">📡 Data Sources</div>
                <div class="info-item">
                    <span class="info-label">Market Data Provider:</span>
                    <span class="info-value">Polygon/Massive Indices Starter ($49/mo)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">SPX Data:</span>
                    <span class="info-value">Real I:SPX snapshot + aggregates (15-min delayed)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">VIX1D Data:</span>
                    <span class="info-value">Real I:VIX1D snapshot (15-min delayed, 1-day forward IV)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">News Sources:</span>
                    <span class="info-value">Yahoo Finance RSS + Google News RSS (FREE)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">AI Analysis:</span>
                    <span class="info-value">GPT-4 Turbo (OpenAI)</span>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">🔗 API Endpoints</div>
                <div class="endpoint"><a href="/health">/health</a> - Health check</div>
                <div class="endpoint"><a href="/option_alpha_trigger">/option_alpha_trigger</a> - Generate trading signal</div>
                <div class="endpoint"><a href="/test_polygon_delayed">/test_polygon_delayed</a> - Test Polygon data</div>
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
        "market_data_source": "Polygon/Massive Indices Starter ($49/mo)",
        "news_sources": "Yahoo Finance RSS + Google News RSS (FREE)",
        "spx_data": "Real I:SPX (15-min delayed)",
        "vix_data": "Real I:VIX1D (15-min delayed, 1-day forward IV)"
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
        print(f"[{timestamp}] Fetching market data from Polygon...")
        
        # Fetch SPX data (snapshot + aggregates)
        spx_data = get_spx_data_with_retry(max_retries=3)
        if not spx_data:
            return jsonify({"status": "error", "message": "SPX data failed after 3 retries (Polygon)"}), 500
        
        # Fetch VIX1D data
        vix1d_data = get_vix1d_with_retry(max_retries=3)
        if not vix1d_data:
            return jsonify({"status": "error", "message": "VIX1D data failed after 3 retries (Polygon)"}), 500
        
        # Fetch news
        news_data = fetch_news_multi_source()
        
        print(f"[{timestamp}] Running indicators...")
        
        # Run indicators
        iv_rv = analyze_iv_rv_ratio(spx_data, vix1d_data)
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
                "vix1d_current": vix1d_data['current'],
                "data_source": "Polygon/Massive Indices Starter ($49/mo)",
                "timeframe": spx_data.get('timeframe', 'DELAYED')
            },
            
            "indicator_1_iv_rv": {
                "weight": "30%",
                "score": iv_rv['score'],
                "iv_rv_ratio": iv_rv['iv_rv_ratio'],
                "realized_vol": f"{iv_rv['realized_vol']}%",
                "implied_vol": f"{iv_rv['implied_vol']}%",
                "vix1d_value": iv_rv['vix1d_value'],
                "tenor": "1-day (VIX1D)",
                "source": "Polygon VIX1D (real data)"
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

@app.route("/test_polygon_delayed", methods=["GET"])
def test_polygon_delayed():
    """Test Polygon Indices Starter - SPX and VIX1D (15-min delayed)"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'plan': 'Indices Starter ($49/mo) - 15-min delayed',
        'note': 'Using official v3 snapshot + v2 aggregates endpoints'
    }
    
    if not POLYGON_API_KEY:
        return jsonify({'error': 'No API key'}), 500
    
    # Test SPX snapshot
    spx_snapshot = get_spx_snapshot()
    results['spx_snapshot'] = {
        'status': '✅ SUCCESS' if spx_snapshot else '❌ FAILED',
        'data': spx_snapshot
    }
    
    # Test VIX1D snapshot
    vix1d_snapshot = get_vix1d_snapshot()
    results['vix1d_snapshot'] = {
        'status': '✅ SUCCESS' if vix1d_snapshot else '❌ FAILED',
        'data': vix1d_snapshot
    }
    
    # Test SPX aggregates
    spx_agg = get_spx_aggregates()
    results['spx_aggregates'] = {
        'status': '✅ SUCCESS' if spx_agg else '❌ FAILED',
        'days_returned': len(spx_agg) if spx_agg else 0,
        'sample_closes': spx_agg[:5] if spx_agg else []
    }
    
    # Summary
    if spx_snapshot and vix1d_snapshot and spx_agg:
        results['recommendation'] = '✅ POLYGON FULLY READY!'
        results['status'] = 'READY'
    else:
        results['recommendation'] = '⚠️ Some data failed'
        results['status'] = 'PARTIAL'
    
    return jsonify(results), 200

# ============================================================================
# BACKGROUND THREAD
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
    print("Ren's SPX Vol Signal - Production (Polygon/Massive)")
    print("=" * 80)
    print(f"Port: {PORT}")
    print(f"Trading Window: 2:30-3:30 PM ET")
    print(f"Market Data: Polygon/Massive Indices Starter ($49/mo)")
    print(f"News Sources: Yahoo Finance RSS + Google News RSS (FREE)")
    print(f"SPX: Real I:SPX (15-min delayed)")
    print(f"VIX1D: Real I:VIX1D (15-min delayed, 1-day forward IV)")
    print("=" * 80)
    
    # Start background thread
    t = threading.Thread(target=poke_self, daemon=True)
    t.start()
    
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)