"""Webhook sending to Option Alpha with retry logic"""
import time as time_module
import requests
from datetime import datetime
import pytz
from config.loader import get_config

ET_TZ = pytz.timezone('US/Eastern')

MAX_RETRIES = 3
RETRY_DELAYS = [2, 4]  # seconds between retries (2s, then 4s)


def _post_with_retry(url, payload):
    """POST to a webhook URL with retry on failure.

    Returns dict with 'success' bool, 'attempts' count, and optional 'error'.
    """
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.post(url, json=payload, timeout=10)
            success = response.status_code in [200, 201, 202]

            if success:
                if attempt > 1:
                    print(f"  [WEBHOOK] Succeeded on attempt {attempt}")
                return {'success': True, 'attempts': attempt}

            # Non-success HTTP status — retry
            last_error = f"HTTP {response.status_code}"
            print(f"  [WEBHOOK] Attempt {attempt}/{MAX_RETRIES} failed: {last_error}")

        except Exception as e:
            last_error = str(e)
            print(f"  [WEBHOOK] Attempt {attempt}/{MAX_RETRIES} error: {last_error}")

        # Wait before retrying (no sleep after last attempt)
        if attempt < MAX_RETRIES:
            delay = RETRY_DELAYS[attempt - 1] if attempt - 1 < len(RETRY_DELAYS) else RETRY_DELAYS[-1]
            time_module.sleep(delay)

    return {'success': False, 'attempts': MAX_RETRIES, 'error': last_error}


def send_webhook(signal_data):
    """Send webhook to Option Alpha with retry on failure.

    Returns dict with 'success' bool and 'attempts' count.
    """
    config = get_config()

    webhook_urls = {
        'TRADE_AGGRESSIVE': config.get('TRADE_AGGRESSIVE_URL'),
        'TRADE_NORMAL': config.get('TRADE_NORMAL_URL'),
        'TRADE_CONSERVATIVE': config.get('TRADE_CONSERVATIVE_URL'),
        'NO_TRADE': config.get('NO_TRADE_URL')
    }

    signal = signal_data['signal']
    timestamp = datetime.now(ET_TZ).isoformat()
    payload = {'signal': signal, 'timestamp': timestamp}

    if signal == "SKIP":
        url = webhook_urls.get('NO_TRADE')
        if url:
            return _post_with_retry(url, payload)
        return {'success': True, 'attempts': 0}

    url = webhook_urls.get(signal)
    if not url:
        print(f"  [WEBHOOK] No URL configured for signal: {signal}")
        return {'success': False, 'attempts': 0, 'error': f'No URL for {signal}'}

    return _post_with_retry(url, payload)
