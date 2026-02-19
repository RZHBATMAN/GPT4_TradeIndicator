"""Option Alpha event-based trade gates.

OA's decision recipe blocks trades when:
  1. FOMC meeting is scheduled today
  2. FOMC meeting is scheduled in 1 market day (tomorrow)
  3. CPI release is scheduled in 1 market day (tomorrow)
  4. Market closes early (not 4:00 PM — e.g., day before holidays)

Dates are maintained as static sets.  Update annually when the Fed and BLS
publish their next-year schedules (usually every November/December).
"""
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import pytz

ET_TZ = pytz.timezone('US/Eastern')

# ── FOMC meeting dates (announcement day = last day of meeting) ──
# Source: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
# Both days of two-day meetings are listed.
FOMC_DATES = {
    # 2025
    '2025-01-28', '2025-01-29',
    '2025-03-18', '2025-03-19',
    '2025-05-06', '2025-05-07',
    '2025-06-17', '2025-06-18',
    '2025-07-29', '2025-07-30',
    '2025-09-16', '2025-09-17',
    '2025-10-28', '2025-10-29',
    '2025-12-09', '2025-12-10',
    # 2026
    '2026-01-27', '2026-01-28',
    '2026-03-17', '2026-03-18',
    '2026-04-28', '2026-04-29',
    '2026-06-16', '2026-06-17',
    '2026-07-28', '2026-07-29',
    '2026-09-15', '2026-09-16',
    '2026-10-27', '2026-10-28',
    '2026-12-08', '2026-12-09',
}

# ── CPI release dates (8:30 AM ET) ──
# Source: https://www.bls.gov/schedule/news_release/cpi.htm
CPI_DATES = {
    # 2025
    '2025-01-15', '2025-02-12', '2025-03-12', '2025-04-10',
    '2025-05-13', '2025-06-11', '2025-07-10', '2025-08-12',
    '2025-09-10', '2025-10-14', '2025-11-12', '2025-12-10',
    # 2026
    '2026-01-13', '2026-02-11', '2026-03-11', '2026-04-14',
    '2026-05-12', '2026-06-10', '2026-07-14', '2026-08-12',
    '2026-09-16', '2026-10-13', '2026-11-10', '2026-12-09',
}

# ── NYSE early close dates (1:00 PM ET instead of 4:00 PM) ──
# Typically: day before Independence Day, Black Friday, Christmas Eve
# Source: https://www.nyse.com/markets/hours-calendars
EARLY_CLOSE_DATES = {
    # 2025
    '2025-07-03',   # Day before July 4
    '2025-11-28',   # Black Friday
    '2025-12-24',   # Christmas Eve
    # 2026
    '2026-07-02',   # Day before observed July 4 (July 3 = holiday since July 4 is Sat)
    '2026-11-27',   # Black Friday
    '2026-12-24',   # Christmas Eve
}


def _next_market_day(dt: datetime) -> str:
    """Return the next market day (skip weekends) as 'YYYY-MM-DD'."""
    nxt = dt + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += nxt.__class__(days=1) if False else timedelta(days=1)
    return nxt.strftime('%Y-%m-%d')


def check_oa_event_gates(now: Optional[datetime] = None) -> List[str]:
    """Check all Option Alpha event-based gates for the current (or given) time.

    Returns a list of gate reasons that are ACTIVE (would block a trade).
    Empty list = no gates triggered, trade allowed.

    Gate reasons:
      'FOMC_TODAY'       — FOMC meeting is scheduled today
      'FOMC_NEXT_DAY'    — FOMC meeting is scheduled tomorrow (1 market day)
      'CPI_NEXT_DAY'     — CPI release is scheduled tomorrow
      'EARLY_CLOSE'      — Market closes early today (not 4:00 PM)
    """
    if now is None:
        now = datetime.now(ET_TZ)

    today = now.strftime('%Y-%m-%d')
    next_day = _next_market_day(now)

    gates = []

    if today in FOMC_DATES:
        gates.append('FOMC_TODAY')

    if next_day in FOMC_DATES:
        gates.append('FOMC_NEXT_DAY')

    if next_day in CPI_DATES:
        gates.append('CPI_NEXT_DAY')

    if today in EARLY_CLOSE_DATES:
        gates.append('EARLY_CLOSE')

    return gates


def format_gate_reasons(gates: List[str]) -> str:
    """Format gate reasons for display / logging."""
    labels = {
        'FOMC_TODAY': 'FOMC meeting today',
        'FOMC_NEXT_DAY': 'FOMC meeting tomorrow',
        'CPI_NEXT_DAY': 'CPI release tomorrow',
        'EARLY_CLOSE': 'Early market close today',
    }
    return '; '.join(labels.get(g, g) for g in gates)
