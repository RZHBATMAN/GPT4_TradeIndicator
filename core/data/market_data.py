"""Market data fetching from Polygon API"""
import requests
import time as time_module
from datetime import datetime, timedelta
import pytz
from core.config import get_config

ET_TZ = pytz.timezone('US/Eastern')


def get_spx_snapshot():
    """
    Fetch ONLY SPX current value from Polygon snapshot
    Returns 15-min delayed data from Indices Starter plan
    """
    config = get_config()
    polygon_api_key = config.get('POLYGON_API_KEY')
    
    try:
        print("  [POLYGON] Fetching SPX snapshot...")
        
        url = f"https://api.massive.com/v3/snapshot/indices?ticker.any_of=I:SPX&apiKey={polygon_api_key}"
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
    config = get_config()
    polygon_api_key = config.get('POLYGON_API_KEY')
    
    try:
        print("  [POLYGON] Fetching VIX1D snapshot...")
        
        url = f"https://api.massive.com/v3/snapshot/indices?ticker.any_of=I:VIX1D&apiKey={polygon_api_key}"
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
    config = get_config()
    polygon_api_key = config.get('POLYGON_API_KEY')
    
    try:
        print("  [POLYGON] Fetching SPX historical data...")
        
        end_date = datetime.now(ET_TZ)
        start_date = end_date - timedelta(days=40)
        
        url = f"https://api.massive.com/v2/aggs/ticker/I:SPX/range/1/day/{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}?adjusted=true&sort=desc&limit=50&apiKey={polygon_api_key}"
        
        response = requests.get(url, timeout=15)
        
        if response.status_code != 200:
            print(f"  ❌ SPX aggregates failed: {response.status_code}")
            return None
        
        data = response.json()
        
        if 'results' not in data or len(data['results']) == 0:
            print(f"  ❌ No SPX historical data")
            return None
        
        bars = data['results'][:25]
        closes = [bar['c'] for bar in bars]
        opens = [bar['o'] for bar in bars]

        print(f"  ✅ Got {len(closes)} days of SPX historical data")

        return {'closes': closes, 'opens': opens}
        
    except Exception as e:
        print(f"  ❌ SPX aggregates error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_vix_snapshot():
    """
    Fetch VIX (30-day) current value from Polygon snapshot.
    Used alongside VIX1D to detect term structure inversion.
    """
    config = get_config()
    polygon_api_key = config.get('POLYGON_API_KEY')

    try:
        print("  [POLYGON] Fetching VIX (30-day) snapshot...")

        url = f"https://api.massive.com/v3/snapshot/indices?ticker.any_of=I:VIX&apiKey={polygon_api_key}"
        response = requests.get(url, timeout=15)

        if response.status_code != 200:
            print(f"  ❌ VIX snapshot failed: {response.status_code}")
            return None

        data = response.json()

        if 'results' not in data or len(data['results']) == 0:
            print(f"  ❌ No VIX results in snapshot")
            return None

        ticker_data = data['results'][0]

        if 'error' in ticker_data:
            print(f"  ❌ VIX error: {ticker_data.get('error')}")
            return None

        if ticker_data.get('ticker') != 'I:VIX':
            print(f"  ❌ Unexpected ticker: {ticker_data.get('ticker')}")
            return None

        vix_snapshot = {
            'current': ticker_data.get('value'),
            'session': ticker_data.get('session', {}),
            'timeframe': ticker_data.get('timeframe'),
            'market_status': ticker_data.get('market_status')
        }

        print(f"  ✅ VIX (30-day): {vix_snapshot['current']:.2f} ({vix_snapshot['timeframe']})")

        return vix_snapshot

    except Exception as e:
        print(f"  ❌ VIX snapshot error: {e}")
        import traceback
        traceback.print_exc()
        return None


def get_vix_with_retry(max_retries=3):
    """Fetch VIX (30-day) with retry. Returns None on failure (non-critical)."""
    for attempt in range(max_retries):
        try:
            snapshot = get_vix_snapshot()
            if not snapshot:
                if attempt < max_retries - 1:
                    time_module.sleep(3)
                continue

            return {
                'current': snapshot['current'],
                'tenor': '30-day (VIX)',
                'source': 'Polygon_VIX',
                'timeframe': snapshot['timeframe'],
            }
        except Exception as e:
            print(f"  ❌ VIX attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time_module.sleep(3)

    print("  ⚠️ VIX (30-day) unavailable — term structure check skipped")
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
            historical = get_spx_aggregates()
            if not historical:
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
                'history_closes': historical['closes'],
                'history_opens': historical['opens'],
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


def get_vvix_snapshot():
    """
    Fetch VVIX (VIX-of-VIX) current value from Polygon snapshot.
    Used to detect elevated vol-of-vol — risk of overnight VIX spikes.
    """
    config = get_config()
    polygon_api_key = config.get('POLYGON_API_KEY')

    try:
        print("  [POLYGON] Fetching VVIX snapshot...")

        url = f"https://api.massive.com/v3/snapshot/indices?ticker.any_of=I:VVIX&apiKey={polygon_api_key}"
        response = requests.get(url, timeout=15)

        if response.status_code != 200:
            print(f"  ❌ VVIX snapshot failed: {response.status_code}")
            return None

        data = response.json()

        if 'results' not in data or len(data['results']) == 0:
            print(f"  ❌ No VVIX results in snapshot")
            return None

        ticker_data = data['results'][0]

        if 'error' in ticker_data:
            print(f"  ❌ VVIX error: {ticker_data.get('error')}")
            return None

        if ticker_data.get('ticker') != 'I:VVIX':
            print(f"  ❌ Unexpected ticker: {ticker_data.get('ticker')}")
            return None

        vvix_snapshot = {
            'current': ticker_data.get('value'),
            'session': ticker_data.get('session', {}),
            'timeframe': ticker_data.get('timeframe'),
            'market_status': ticker_data.get('market_status')
        }

        print(f"  ✅ VVIX: {vvix_snapshot['current']:.2f} ({vvix_snapshot['timeframe']})")

        return vvix_snapshot

    except Exception as e:
        print(f"  ❌ VVIX snapshot error: {e}")
        import traceback
        traceback.print_exc()
        return None


# VVIX static thresholds — placeholder for true 252-day rolling percentile
# Once Polygon tier supports VVIX history, replace with vvix_percentile_252d().
# Thresholds chosen from typical VVIX regime ranges (70-130 normal, 150+ crisis).
# Empirical basis: Papagelis & Dotsis 2025 Table 6 — Q4 vol-of-vol → 6× richer
# overnight premium; we approximate Q4 as VVIX ≥ 110 here.
VVIX_THRESHOLDS = {
    'LOW':     (None, 90),    # < 90  → vol-of-vol muted, premium thin
    'NORMAL':  (90, 100),     # 90-100 → typical regime
    'HIGH':    (100, 110),    # 100-110 → elevated
    'EXTREME': (110, None),   # ≥ 110 → very elevated; richest premium AND tail
}


def vvix_static_bucket(vvix_value):
    """Map a VVIX level to a static regime bucket (LOW / NORMAL / HIGH / EXTREME).

    FALLBACK PATH only — used when 252-day percentile history is unavailable.
    Returns 'NORMAL' as a safe default if vvix_value is None or non-numeric.

    Thresholds chosen from typical VVIX regime ranges (70-130 normal, 150+ crisis).
    Quartile-aligned bucketing via vvix_percentile_252d() is preferred when
    Polygon history is fetchable.
    """
    if vvix_value is None:
        return 'NORMAL'
    try:
        v = float(vvix_value)
    except (TypeError, ValueError):
        return 'NORMAL'
    if v < 90:
        return 'LOW'
    if v < 100:
        return 'NORMAL'
    if v < 110:
        return 'HIGH'
    return 'EXTREME'


# ─────────────────────────────────────────────────────────────────────────────
# VVIX historical percentile bucketing — Papagelis & Dotsis (2025) Table 6 aligned
# ─────────────────────────────────────────────────────────────────────────────
# Caches the trailing 252-trading-day VVIX close window so the 5 paper bots
# × 3 pokes/day all share one Polygon fetch (= 1 fetch per ET calendar day).
_VVIX_HISTORY_CACHE = {'date': None, 'closes': []}


def get_vvix_aggregates(lookback_calendar_days=400):
    """Fetch VVIX historical daily closes from Polygon (asc-sorted, most recent last).

    We request `lookback_calendar_days` calendar days (default 400) to reliably
    obtain >= 252 trading days even with holidays and weekends. Returns None
    on failure; callers fall back to vvix_static_bucket().
    """
    config = get_config()
    polygon_api_key = config.get('POLYGON_API_KEY')

    try:
        print("  [POLYGON] Fetching VVIX historical aggregates...")
        end_date = datetime.now(ET_TZ)
        start_date = end_date - timedelta(days=lookback_calendar_days)

        url = (
            f"https://api.massive.com/v2/aggs/ticker/I:VVIX/range/1/day/"
            f"{start_date.strftime('%Y-%m-%d')}/{end_date.strftime('%Y-%m-%d')}"
            f"?adjusted=true&sort=asc&limit=1000&apiKey={polygon_api_key}"
        )
        response = requests.get(url, timeout=15)
        if response.status_code != 200:
            print(f"  ❌ VVIX aggregates failed: HTTP {response.status_code}")
            return None

        data = response.json()
        if 'results' not in data or not data['results']:
            print(f"  ❌ No VVIX historical data in response")
            return None

        closes = [bar['c'] for bar in data['results']]
        print(f"  ✅ Got {len(closes)} days of VVIX history")
        return closes
    except Exception as e:
        print(f"  ❌ VVIX aggregates error: {e}")
        return None


def get_vvix_252d_history():
    """Return the trailing 252 trading days of VVIX closes (cached daily).

    Cache invalidates once per ET calendar day. On a successful fetch, all
    later pokes within the same day are O(1). On a failure day, every poke
    re-attempts the fetch (failures are not cached).
    """
    today_str = datetime.now(ET_TZ).strftime('%Y-%m-%d')
    if _VVIX_HISTORY_CACHE['date'] == today_str and _VVIX_HISTORY_CACHE['closes']:
        return _VVIX_HISTORY_CACHE['closes']

    all_closes = get_vvix_aggregates(lookback_calendar_days=400)
    if not all_closes:
        return None

    trailing = all_closes[-252:] if len(all_closes) >= 252 else all_closes
    _VVIX_HISTORY_CACHE['date'] = today_str
    _VVIX_HISTORY_CACHE['closes'] = trailing
    return trailing


def _percentile_rank(value, sorted_values):
    """Pure-Python percentile rank of `value` within `sorted_values` (0..100).

    Uses mid-rank for ties (count of strictly-below + 0.5 × count of equal).
    Returns None if sorted_values is empty.
    """
    n = len(sorted_values)
    if n == 0:
        return None
    below = sum(1 for v in sorted_values if v < value)
    equal = sum(1 for v in sorted_values if v == value)
    rank = below + 0.5 * equal
    return 100.0 * rank / n


def vvix_percentile_252d(current_vvix, min_sample=60):
    """Return percentile rank (0..100) of `current_vvix` in trailing 252-day window.

    Returns None if current_vvix is invalid or history is unavailable / too short
    (< `min_sample` bars). Caller should treat None as a signal to fall back to
    vvix_static_bucket().
    """
    if current_vvix is None:
        return None
    try:
        cv = float(current_vvix)
    except (TypeError, ValueError):
        return None

    history = get_vvix_252d_history()
    if not history or len(history) < min_sample:
        return None

    return _percentile_rank(cv, sorted(history))


def vvix_percentile_bucket(current_vvix):
    """Quartile-aligned bucketing matched to Papagelis & Dotsis (2025) Table 6.

    Their Table 6 quartiles of VVIX (over 2010-2022) produced overnight VRP P&L of:
        Q1 (lowest):     -3.01    (LOW    — premium thinnest)
        Q2:              -2.82    (NORMAL — baseline)
        Q3:              -6.77    (HIGH   — premium ~2× richer)
        Q4 (highest):   -17.88    (EXTREME — premium ~6× richer AND tail risk highest)

    Returns a 4-tuple: (bucket_name, percentile_or_None, sample_size, source).
    `source` is 'percentile_252d' on success or 'static_fallback' when history
    is unavailable.
    """
    pct = vvix_percentile_252d(current_vvix)
    if pct is None:
        bucket = vvix_static_bucket(current_vvix)
        return (bucket, None, 0, 'static_fallback')

    if pct < 25:
        bucket = 'LOW'
    elif pct < 50:
        bucket = 'NORMAL'
    elif pct < 75:
        bucket = 'HIGH'
    else:
        bucket = 'EXTREME'

    sample_size = len(_VVIX_HISTORY_CACHE.get('closes') or [])
    return (bucket, round(pct, 1), sample_size, 'percentile_252d')


def get_vvix_with_retry(max_retries=3):
    """Fetch VVIX with retry. Returns None on failure (non-critical)."""
    for attempt in range(max_retries):
        try:
            snapshot = get_vvix_snapshot()
            if not snapshot:
                if attempt < max_retries - 1:
                    time_module.sleep(3)
                continue

            return {
                'current': snapshot['current'],
                'tenor': 'VVIX (vol-of-vol)',
                'source': 'Polygon_VVIX',
                'timeframe': snapshot['timeframe'],
            }
        except Exception as e:
            print(f"  ❌ VVIX attempt {attempt + 1} failed: {e}")
            if attempt < max_retries - 1:
                time_module.sleep(3)

    print("  ⚠️ VVIX unavailable — vol-of-vol check skipped")
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
