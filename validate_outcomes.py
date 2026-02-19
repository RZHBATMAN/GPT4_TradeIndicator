#!/usr/bin/env python3
"""Validate signal outcomes by backfilling next-day SPX data into Google Sheets.

This script reads your signal log from Google Sheets, fetches the next-day
SPX open/close from Polygon for each signal row, calculates the overnight
move, and determines whether the signal was "correct."

Correctness criteria for iron condor signals:
  - TRADE signals are "correct" if overnight move < breakeven for that tier
  - SKIP signals are "correct" if overnight move > CONSERVATIVE breakeven

Breakeven thresholds (derived from delta + premium collected):
  TRADE_AGGRESSIVE (20pt width, 0.18 delta): correct if |move| < 1.00%
  TRADE_NORMAL     (25pt width, 0.16 delta): correct if |move| < 0.90%
  TRADE_CONSERVATIVE (30pt width, 0.14 delta): correct if |move| < 0.80%
  SKIP: correct if |move| >= 0.80% (you were right to skip)

Usage:
  python validate_outcomes.py              # backfill all missing outcomes
  python validate_outcomes.py --dry-run    # preview without writing to Sheets
  python validate_outcomes.py --report     # print accuracy report only
"""
import sys
import json
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
import pytz

from config.loader import get_config

logger = logging.getLogger(__name__)
ET_TZ = pytz.timezone('US/Eastern')

# Iron condor parameters by signal tier
# Width = spread width in SPX points, delta = short strike delta
TRADE_PARAMS = {
    'TRADE_AGGRESSIVE': {'width': 20, 'delta': 0.18},
    'TRADE_NORMAL':     {'width': 25, 'delta': 0.16},
    'TRADE_CONSERVATIVE': {'width': 30, 'delta': 0.14},
}

# Breakeven thresholds derived from delta:
# Short strike distance ≈ delta * daily_vol * SPX_price (simplified)
# For a practical proxy, we use:
#   Approximate short-strike distance (%) = delta * 5.0
#   (5.0 is roughly sqrt(1/252) * VIX_avg, mapping delta to % move)
# This gives thresholds:
#   AGGRESSIVE:   0.18 * 5.0 = 0.90%
#   NORMAL:       0.16 * 5.0 = 0.80%
#   CONSERVATIVE: 0.14 * 5.0 = 0.70%
# The condor also collects premium, which extends the breakeven by ~0.10-0.15%.
# Net approximate breakevens:
MOVE_THRESHOLDS = {
    'TRADE_AGGRESSIVE': 1.00,     # 0.18 delta → ~1.00% breakeven
    'TRADE_NORMAL': 0.90,         # 0.16 delta → ~0.90% breakeven
    'TRADE_CONSERVATIVE': 0.80,   # 0.14 delta → ~0.80% breakeven
    'SKIP': 0.80,                 # SKIP is "correct" if move >= conservative breakeven
}

# Column indices (0-based) matching SHEET_HEADERS in sheets_logger.py
COL_TIMESTAMP = 0
COL_SIGNAL = 1
COL_SPX_CURRENT = 16
COL_CONTRADICTION_FLAGS = 20
COL_OVERRIDE = 21
COL_SCORE_ADJ = 22
COL_SPX_NEXT_OPEN = 23
COL_SPX_NEXT_CLOSE = 24
COL_OVERNIGHT_MOVE = 25
COL_OUTCOME_CORRECT = 26


def _parse_signal_date(date_str: str) -> Optional[datetime]:
    """Parse a timestamp string from the sheet into a datetime."""
    for fmt in [
        "%Y-%m-%d %I:%M:%S %p %Z",
        "%Y-%m-%d %I:%M:%S %p ET",
        "%Y-%m-%d %I:%M:%S %p EST",
        "%Y-%m-%d %I:%M:%S %p EDT",
        "%Y-%m-%d",
    ]:
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    # Last resort: dateutil
    try:
        from dateutil import parser as dp
        return dp.parse(date_str.strip())
    except Exception:
        return None


def _next_weekday(dt: datetime) -> datetime:
    """Advance a datetime to the next weekday (skip weekends)."""
    next_day = dt + timedelta(days=1)
    while next_day.weekday() >= 5:
        next_day += timedelta(days=1)
    return next_day


def _get_next_trading_day(date_str: str) -> str:
    """Given a date string from the sheet, return the next trading day (skip weekends)."""
    try:
        dt = _parse_signal_date(date_str)
        if dt is None:
            logger.warning("Could not parse date '%s'", date_str)
            return ""
        return _next_weekday(dt).strftime('%Y-%m-%d')
    except Exception as e:
        logger.warning("Could not parse date '%s': %s", date_str, e)
        return ""


def _fetch_spx_day(date_str: str, api_key: str, max_holiday_retries: int = 5
                    ) -> Optional[Tuple[Dict[str, float], str]]:
    """Fetch SPX open and close for a specific date from Polygon.

    If no data is returned (market holiday), advances to the next weekday and
    retries up to ``max_holiday_retries`` times.

    Returns (bar_dict, actual_date_str) or None.
    """
    current_date = datetime.strptime(date_str, '%Y-%m-%d')

    for attempt in range(max_holiday_retries + 1):
        ds = current_date.strftime('%Y-%m-%d')
        try:
            url = (
                f"https://api.massive.com/v2/aggs/ticker/I:SPX/range/1/day/"
                f"{ds}/{ds}?adjusted=true&sort=asc&limit=1&apiKey={api_key}"
            )
            resp = requests.get(url, timeout=15)
            if resp.status_code != 200:
                print(f"  [Polygon] HTTP {resp.status_code} for {ds}")
                return None

            data = resp.json()
            results = data.get('results', [])
            if not results:
                print(f"  [Polygon] No data for {ds} (holiday?), trying next weekday...")
                current_date = _next_weekday(current_date)
                continue

            bar = results[0]
            return {
                'open': bar.get('o'),
                'close': bar.get('c'),
                'high': bar.get('h'),
                'low': bar.get('l'),
            }, ds
        except Exception as e:
            print(f"  [Polygon] Error fetching {ds}: {e}")
            return None

    print(f"  [Polygon] No data found after {max_holiday_retries} retries from {date_str}")
    return None


def _evaluate_outcome(
    signal: str,
    spx_entry: float,
    spx_next_open: float,
    spx_next_close: float,
) -> Tuple[float, str]:
    """Calculate overnight move and determine if signal was correct.

    Returns (overnight_move_pct, outcome_str).
    """
    # Overnight move = gap between entry price and next-day open
    overnight_move_pct = abs((spx_next_open - spx_entry) / spx_entry) * 100

    threshold = MOVE_THRESHOLDS.get(signal, 0.65)

    if signal == 'SKIP':
        # SKIP is "correct" if the market actually moved a lot
        correct = overnight_move_pct >= threshold
        outcome = "CORRECT_SKIP" if correct else "WRONG_SKIP"
    else:
        # Trade signals are "correct" if overnight move stayed within range
        correct = overnight_move_pct < threshold
        outcome = "CORRECT_TRADE" if correct else "WRONG_TRADE"

    return round(overnight_move_pct, 4), outcome


def _connect_sheet():
    """Connect to Google Sheet. Returns worksheet or None."""
    try:
        import gspread
    except ImportError:
        print("ERROR: gspread not installed. Run: pip install gspread google-auth")
        return None

    config = get_config()
    sheet_id = (config.get("GOOGLE_SHEET_ID") or "").strip()
    json_cfg = (config.get("GOOGLE_CREDENTIALS_JSON") or "").strip()

    if not sheet_id or not json_cfg:
        print("ERROR: GOOGLE_SHEET_ID and GOOGLE_CREDENTIALS_JSON must be configured")
        return None

    try:
        creds = json.loads(json_cfg)
        gc = gspread.service_account_from_dict(creds)
        sh = gc.open_by_key(sheet_id)
        return sh.sheet1
    except Exception as e:
        print(f"ERROR: Could not connect to Sheet: {e}")
        return None


def backfill_outcomes(dry_run: bool = False) -> List[Dict]:
    """Backfill next-day SPX data for all signal rows missing outcomes."""
    config = get_config()
    api_key = config.get('POLYGON_API_KEY')
    if not api_key:
        print("ERROR: POLYGON_API_KEY not configured")
        return []

    ws = _connect_sheet()
    if ws is None:
        return []

    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        print("No data rows found in sheet")
        return []

    header = all_rows[0]
    results = []
    updates_made = 0

    print(f"\nScanning {len(all_rows) - 1} signal rows for missing outcomes...\n")

    for row_idx in range(1, len(all_rows)):
        row = all_rows[row_idx]

        # Pad row if needed (sheet may have fewer columns than expected)
        while len(row) <= COL_OUTCOME_CORRECT:
            row.append("")

        timestamp = row[COL_TIMESTAMP]
        signal = row[COL_SIGNAL]
        spx_current_str = row[COL_SPX_CURRENT]

        # Skip if outcome already filled
        if row[COL_SPX_NEXT_OPEN] and row[COL_OUTCOME_CORRECT]:
            continue

        # Skip if missing critical data
        if not timestamp or not signal or not spx_current_str:
            missing = []
            if not timestamp: missing.append("timestamp")
            if not signal: missing.append("signal")
            if not spx_current_str: missing.append("SPX_current")
            print(f"  Row {row_idx + 1}: SKIPPED — missing {', '.join(missing)}")
            continue

        try:
            spx_entry = float(spx_current_str)
        except (ValueError, TypeError):
            continue

        next_day = _get_next_trading_day(timestamp)
        if not next_day:
            continue

        # Don't try to fetch future dates
        today = datetime.now(ET_TZ).strftime('%Y-%m-%d')
        if next_day >= today:
            print(f"  Row {row_idx + 1}: {timestamp} → next day {next_day} is today or future, skipping")
            continue

        print(f"  Row {row_idx + 1}: {timestamp} | Signal={signal} | SPX={spx_entry:.2f} | Next day={next_day}")

        fetch_result = _fetch_spx_day(next_day, api_key)
        if fetch_result is None:
            continue

        day_data, actual_date = fetch_result
        if actual_date != next_day:
            print(f"           (holiday skip: {next_day} → {actual_date})")

        spx_next_open = day_data['open']
        spx_next_close = day_data['close']
        overnight_move_pct, outcome = _evaluate_outcome(
            signal, spx_entry, spx_next_open, spx_next_close
        )

        result = {
            'row': row_idx + 1,
            'timestamp': timestamp,
            'signal': signal,
            'spx_entry': spx_entry,
            'spx_next_open': spx_next_open,
            'spx_next_close': spx_next_close,
            'overnight_move_pct': overnight_move_pct,
            'outcome': outcome,
        }
        results.append(result)

        print(f"           Next open={spx_next_open:.2f} | Move={overnight_move_pct:+.4f}% | {outcome}")

        if not dry_run:
            # Update cells: columns are 1-indexed in gspread
            try:
                ws.update_cell(row_idx + 1, COL_SPX_NEXT_OPEN + 1, spx_next_open)
                ws.update_cell(row_idx + 1, COL_SPX_NEXT_CLOSE + 1, spx_next_close)
                ws.update_cell(row_idx + 1, COL_OVERNIGHT_MOVE + 1, f"{overnight_move_pct:+.4f}%")
                ws.update_cell(row_idx + 1, COL_OUTCOME_CORRECT + 1, outcome)
                updates_made += 1
            except Exception as e:
                print(f"           ERROR updating row: {e}")

    print(f"\n{'DRY RUN — ' if dry_run else ''}Processed {len(results)} rows, updated {updates_made}")
    return results


def print_accuracy_report(results: Optional[List[Dict]] = None):
    """Print a signal accuracy report from Sheet data."""
    ws = _connect_sheet()
    if ws is None:
        return

    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        print("No data rows found")
        return

    # Collect outcomes
    signals = {'TRADE_AGGRESSIVE': [], 'TRADE_NORMAL': [], 'TRADE_CONSERVATIVE': [], 'SKIP': []}
    all_outcomes = []

    for row in all_rows[1:]:
        while len(row) <= COL_OUTCOME_CORRECT:
            row.append("")

        signal = row[COL_SIGNAL]
        outcome = row[COL_OUTCOME_CORRECT]
        overnight_str = row[COL_OVERNIGHT_MOVE]

        if not outcome or not signal:
            continue

        try:
            overnight = float(overnight_str.replace('%', '').replace('+', ''))
        except (ValueError, TypeError):
            overnight = None

        entry = {'signal': signal, 'outcome': outcome, 'overnight_move': overnight}
        all_outcomes.append(entry)
        if signal in signals:
            signals[signal].append(entry)

    if not all_outcomes:
        print("No outcome data available yet. Run: python validate_outcomes.py")
        return

    print("\n" + "=" * 70)
    print("  SIGNAL ACCURACY REPORT")
    print("=" * 70)

    total_correct = sum(1 for o in all_outcomes if 'CORRECT' in o['outcome'])
    total = len(all_outcomes)
    print(f"\n  Overall Accuracy: {total_correct}/{total} ({total_correct/total*100:.1f}%)")

    for tier in ['TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE', 'SKIP']:
        entries = signals[tier]
        if not entries:
            print(f"\n  {tier}: No data")
            continue

        correct = sum(1 for e in entries if 'CORRECT' in e['outcome'])
        n = len(entries)
        moves = [e['overnight_move'] for e in entries if e['overnight_move'] is not None]

        print(f"\n  {tier}: {correct}/{n} correct ({correct/n*100:.1f}%)")
        print(f"    Threshold: {MOVE_THRESHOLDS[tier]:.2f}%")
        if moves:
            avg_move = sum(moves) / len(moves)
            max_move = max(moves)
            print(f"    Avg overnight move: {avg_move:.4f}%")
            print(f"    Max overnight move: {max_move:.4f}%")

    # P&L proxy: count how many trades would have survived
    trade_entries = [o for o in all_outcomes if o['signal'] != 'SKIP']
    if trade_entries:
        survived = sum(1 for o in trade_entries if 'CORRECT' in o['outcome'])
        blown = len(trade_entries) - survived
        print(f"\n  Trade Survival Rate: {survived}/{len(trade_entries)} "
              f"({survived/len(trade_entries)*100:.1f}%)")
        print(f"  Blown Trades: {blown}")

    # Contradiction analysis
    print(f"\n  Signal Distribution:")
    for tier in ['TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE', 'SKIP']:
        n = len(signals[tier])
        pct = n / total * 100 if total else 0
        print(f"    {tier}: {n} ({pct:.0f}%)")

    print("\n" + "=" * 70)


if __name__ == '__main__':
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    report_only = '--report' in args

    if report_only:
        print_accuracy_report()
    else:
        results = backfill_outcomes(dry_run=dry_run)
        if results:
            print_accuracy_report()
        elif not dry_run:
            # Try report anyway if outcomes already exist
            print_accuracy_report()
