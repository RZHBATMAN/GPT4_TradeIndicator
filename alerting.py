"""Alerting module: detect system failures and send notifications.

Tracks signal generation during trading windows and alerts on:
  - No signal generated during a trading day
  - Consecutive API failures (Polygon or MiniMax)
  - Poke thread not firing during trading hours

Notifications are sent via a configurable webhook URL (e.g. Slack incoming webhook).
Configure ALERT_WEBHOOK_URL in .config [WEBHOOKS] section or as an env var.
"""
import requests
import threading
from datetime import datetime, time as dt_time
import pytz

ET_TZ = pytz.timezone('US/Eastern')
TRADING_WINDOW_START = dt_time(hour=13, minute=30)
TRADING_WINDOW_END = dt_time(hour=14, minute=30)

# In-memory state (reset on deploy/restart)
_state = {
    'last_signal_date': None,       # Date string of last successful signal
    'last_signal_time': None,       # Datetime of last successful signal
    'last_poke_time': None,         # Datetime of last poke attempt
    'consecutive_api_failures': 0,  # Count of consecutive API errors
    'api_failure_source': None,     # Which API is failing
    'alerts_sent_today': set(),     # Avoid spamming same alert type
}

_lock = threading.Lock()


def _get_webhook_url():
    """Get alert webhook URL from config. Returns None if not configured."""
    try:
        from config.loader import get_config
        config = get_config()
        url = (config.get('ALERT_WEBHOOK_URL') or '').strip()
        return url if url else None
    except Exception:
        return None


def _send_alert(title, message, level='warning'):
    """Send an alert via webhook. Supports Slack incoming webhook format."""
    url = _get_webhook_url()
    if not url:
        print(f"  [ALERT] {level.upper()}: {title} — {message} (no webhook configured)")
        return False

    now = datetime.now(ET_TZ)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p ET")

    icon = {'info': 'information_source', 'warning': 'warning', 'critical': 'rotating_light'}.get(level, 'warning')

    payload = {
        'text': f":{icon}: *SPX Vol Signal — {title}*\n{message}\n_{timestamp}_"
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        if resp.status_code == 200:
            print(f"  [ALERT] Sent: {title}")
            return True
        else:
            print(f"  [ALERT] Webhook returned {resp.status_code}")
            return False
    except Exception as e:
        print(f"  [ALERT] Failed to send: {e}")
        return False


def record_signal_success():
    """Call this after a successful signal generation."""
    with _lock:
        now = datetime.now(ET_TZ)
        _state['last_signal_date'] = now.strftime('%Y-%m-%d')
        _state['last_signal_time'] = now
        _state['consecutive_api_failures'] = 0
        _state['api_failure_source'] = None


def record_api_failure(source):
    """Call this when an API call fails (e.g. 'Polygon', 'MiniMax').

    Sends an alert after 2 consecutive failures from the same source.
    """
    with _lock:
        if _state['api_failure_source'] == source:
            _state['consecutive_api_failures'] += 1
        else:
            _state['api_failure_source'] = source
            _state['consecutive_api_failures'] = 1

        count = _state['consecutive_api_failures']

    if count >= 2:
        today = datetime.now(ET_TZ).strftime('%Y-%m-%d')
        alert_key = f"api_failure_{source}_{today}"
        with _lock:
            if alert_key in _state['alerts_sent_today']:
                return
            _state['alerts_sent_today'].add(alert_key)

        _send_alert(
            f"{source} API Down",
            f"{source} has failed {count} consecutive times. Signal quality may be degraded.",
            level='critical',
        )


def record_poke():
    """Call this at the start of each poke cycle."""
    with _lock:
        _state['last_poke_time'] = datetime.now(ET_TZ)


def check_end_of_window():
    """Call this near the end of trading window to check if a signal was generated.

    Should be called around 2:30-2:35 PM ET (e.g. from poke thread).
    """
    now = datetime.now(ET_TZ)
    today = now.strftime('%Y-%m-%d')

    # Only check on weekdays
    if now.weekday() >= 5:
        return

    with _lock:
        last_date = _state['last_signal_date']
        alert_key = f"no_signal_{today}"
        already_sent = alert_key in _state['alerts_sent_today']

    if last_date != today and not already_sent:
        with _lock:
            _state['alerts_sent_today'].add(alert_key)
        _send_alert(
            "No Signal Generated Today",
            "The trading window has ended and no signal was generated. "
            "Check Railway logs for errors.",
            level='critical',
        )


def check_poke_health():
    """Check if the poke thread has fired recently during trading hours.

    Call this periodically (e.g. from a separate health monitor).
    """
    now = datetime.now(ET_TZ)

    # Only relevant during trading hours on weekdays
    if now.weekday() >= 5:
        return
    if not (TRADING_WINDOW_START <= now.time() <= TRADING_WINDOW_END):
        return

    with _lock:
        last_poke = _state['last_poke_time']
        alert_key = f"poke_stale_{now.strftime('%Y-%m-%d')}"
        already_sent = alert_key in _state['alerts_sent_today']

    if already_sent:
        return

    # If we're 30+ min into trading window and no poke has fired
    if last_poke is None or (now - last_poke).total_seconds() > 1800:
        with _lock:
            _state['alerts_sent_today'].add(alert_key)
        _send_alert(
            "Poke Thread Stale",
            "No poke has fired in the last 30 minutes during the trading window. "
            "The scheduler may have crashed.",
            level='warning',
        )


def reset_daily():
    """Reset daily alert dedup. Call this at midnight or start of day."""
    with _lock:
        _state['alerts_sent_today'] = set()


def get_alert_status():
    """Return current alerting state for health endpoint."""
    with _lock:
        return {
            'last_signal_date': _state['last_signal_date'],
            'last_signal_time': _state['last_signal_time'].isoformat() if _state['last_signal_time'] else None,
            'last_poke_time': _state['last_poke_time'].isoformat() if _state['last_poke_time'] else None,
            'consecutive_api_failures': _state['consecutive_api_failures'],
            'api_failure_source': _state['api_failure_source'],
            'alerts_sent_today': list(_state['alerts_sent_today']),
        }
