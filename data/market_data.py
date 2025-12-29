"""Market data fetching from Polygon API"""
import requests
import time as time_module
from datetime import datetime, timedelta
import pytz
from config.loader import get_config

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
