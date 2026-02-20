"""Earnings calendar: check if Mag 7 stocks report today or tomorrow.

Uses Polygon's ticker events API to detect upcoming Mag 7 earnings.
Returns a risk modifier to add to the GPT news score.
"""
import requests
from datetime import datetime, timedelta
import pytz
from config.loader import get_config

ET_TZ = pytz.timezone('US/Eastern')

# Mag 7 tickers — these represent ~30% of SPX weight
MAG7_TICKERS = ['AAPL', 'MSFT', 'GOOGL', 'AMZN', 'NVDA', 'TSLA', 'META']


def check_mag7_earnings():
    """Check if any Mag 7 stocks report earnings today or tomorrow.

    Returns:
        dict with:
          - reporting_today: list of tickers reporting today
          - reporting_tomorrow: list of tickers reporting tomorrow
          - risk_modifier: int (0, +1, or +2) to add to composite
          - message: human-readable summary
    """
    config = get_config()
    api_key = config.get('POLYGON_API_KEY')
    if not api_key:
        return _empty_result("No API key")

    now = datetime.now(ET_TZ)
    today = now.strftime('%Y-%m-%d')
    tomorrow = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    # Skip to Monday if tomorrow is Saturday
    next_day = now + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    tomorrow = next_day.strftime('%Y-%m-%d')

    reporting_today = []
    reporting_tomorrow = []

    for ticker in MAG7_TICKERS:
        earnings_date = _get_next_earnings_date(ticker, api_key)
        if earnings_date == today:
            reporting_today.append(ticker)
        elif earnings_date == tomorrow:
            reporting_tomorrow.append(ticker)

    # Risk modifier
    if reporting_today:
        risk_modifier = 2  # Earnings TODAY = high overnight risk
        msg = f"Mag 7 reporting TODAY: {', '.join(reporting_today)}"
    elif reporting_tomorrow:
        risk_modifier = 1  # Earnings tomorrow = moderate pre-positioning risk
        msg = f"Mag 7 reporting TOMORROW: {', '.join(reporting_tomorrow)}"
    else:
        risk_modifier = 0
        msg = "No Mag 7 earnings today or tomorrow"

    print(f"  [EARNINGS] {msg}")

    return {
        'reporting_today': reporting_today,
        'reporting_tomorrow': reporting_tomorrow,
        'risk_modifier': risk_modifier,
        'message': msg,
    }


def _get_next_earnings_date(ticker, api_key):
    """Fetch the next earnings date for a ticker from Polygon.
    Returns date string 'YYYY-MM-DD' or None."""
    try:
        # Use Polygon's ticker details/events endpoint
        url = (
            f"https://api.massive.com/vX/reference/tickers/{ticker}/events"
            f"?types=earnings&limit=1&sort=date&order=asc"
            f"&date.gte={datetime.now(ET_TZ).strftime('%Y-%m-%d')}"
            f"&apiKey={api_key}"
        )
        resp = requests.get(url, timeout=10)
        if resp.status_code != 200:
            return None

        data = resp.json()
        events = data.get('results', {}).get('events', [])
        if not events:
            return None

        # The event date
        return events[0].get('date')
    except Exception:
        return None


def _empty_result(reason):
    return {
        'reporting_today': [],
        'reporting_tomorrow': [],
        'risk_modifier': 0,
        'message': reason,
    }
