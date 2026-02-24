#!/usr/bin/env python3
"""Validate signal outcomes by backfilling next-day SPX data into Google Sheets.

This script reads your signal log from Google Sheets, fetches SPX price at
the OA time-based exit (10:00 AM ET next day) from Polygon, calculates the
overnight move, and determines whether the signal was "correct."

Exit price logic:
  1. Primary: SPX at 10:00 AM ET via Polygon minute aggregates
     (matches OA's time-based exit; captures the actual unmanaged overnight
     window from ~2 PM entry to 10 AM exit)
  2. Fallback: Daily open (9:30 AM) if minute data is unavailable

Note on OA exit stack — positions may exit BEFORE 10 AM via:
  - Profit target (Aggressive 15%, Normal 20%, Conservative 40% of credit)
  - Stop loss (Aggressive 75%, Normal 100%, Conservative 150% of credit)
  - Touch monitor (0 DTE: $40 ITM + <80% max loss)
  This validation uses the 10 AM price as a proxy since we don't have OA's
  actual fill/exit data. Directionally accurate but not exact.

The Trade_Executed column tracks whether a trade was actually placed:
  YES           — webhook fired, OA executed the trade
  NO_SKIP       — our signal said SKIP
  NO_FRIDAY     — Friday, no webhook sent
  NO_VIX_GATE   — VIX >= 25, OA blocked the trade
  NO_DUPLICATE  — webhook already sent earlier today
  (blank)       — legacy row before Trade_Executed was added

Outcome classification:
  For days we actually traded (Trade_Executed=YES):
    CORRECT_TRADE  — overnight move < breakeven for that tier
    WRONG_TRADE    — overnight move >= breakeven (condor blown)
  For days we did NOT trade (any NO_* reason):
    CORRECT_NO_TRADE — overnight move >= 0.80% (right to stay out)
    WRONG_NO_TRADE   — overnight move < 0.80% (missed opportunity)

Breakeven thresholds (derived from delta + premium collected):
  TRADE_AGGRESSIVE (20pt width, 0.18 delta): correct if |move| < 1.00%
  TRADE_NORMAL     (25pt width, 0.16 delta): correct if |move| < 0.90%
  TRADE_CONSERVATIVE (30pt width, 0.14 delta): correct if |move| < 0.80%

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

# OA exit settings per tier (for reference — actual exits happen in OA)
# Profit target = % of credit received; Stop loss = % of credit as loss
# Time-based exit: 10:00 AM ET next day (hard close for all tiers)
# Touch monitor (0 DTE only): close if $40+ ITM AND loss < 80% of max
OA_EXIT_PARAMS = {
    'TRADE_AGGRESSIVE':   {'profit_pct': 15, 'stop_pct': 75},
    'TRADE_NORMAL':       {'profit_pct': 20, 'stop_pct': 100},
    'TRADE_CONSERVATIVE': {'profit_pct': 40, 'stop_pct': 150},
}
OA_TIME_EXIT = '10:00'       # ET — hard close for all tiers
OA_TOUCH_ITM_AMOUNT = 40     # dollars ITM to trigger touch monitor
OA_TOUCH_MAX_LOSS_PCT = 80   # don't close if loss already exceeds this %

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

# Threshold for "was not trading correct?" — if move >= this, not trading was right
NO_TRADE_THRESHOLD = 0.80

# Columns used for poke stability analysis
COL_SENT_TO_GPT = 21

# Column indices (0-based) matching SHEET_HEADERS in sheets_logger.py
COL_TIMESTAMP = 0
COL_POKE_NUMBER = 1
COL_SIGNAL = 2
COL_SPX_CURRENT = 17
COL_VIX = 18
COL_TRADE_EXECUTED = 19
COL_CONTRADICTION_FLAGS = 23
COL_OVERRIDE = 24
COL_SCORE_ADJ = 25
COL_SPX_NEXT_OPEN = 26
COL_SPX_NEXT_CLOSE = 27
COL_OVERNIGHT_MOVE = 28
COL_OUTCOME_CORRECT = 29


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


def _fetch_spx_10am_price(date_str: str, api_key: str) -> Optional[float]:
    """Fetch SPX price at 10:00 AM ET using Polygon minute aggregates.

    This matches the OA time-based exit. Returns the close price of the
    minute bar closest to 10:00 AM ET, or None if unavailable.
    """
    try:
        url = (
            f"https://api.massive.com/v2/aggs/ticker/I:SPX/range/1/minute/"
            f"{date_str}/{date_str}?adjusted=true&sort=asc&limit=500&apiKey={api_key}"
        )
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            return None

        data = resp.json()
        results = data.get('results', [])
        if not results:
            return None

        # Find the bar at 10:00 AM ET
        target_dt = ET_TZ.localize(
            datetime.strptime(f"{date_str} 10:00", "%Y-%m-%d %H:%M")
        )
        target_ts = int(target_dt.timestamp() * 1000)

        closest_bar = min(results, key=lambda b: abs(b['t'] - target_ts))

        # Verify within 2 minutes of target (market must be open)
        diff_minutes = abs(closest_bar['t'] - target_ts) / 60000
        if diff_minutes > 2:
            return None

        return closest_bar['c']
    except Exception as e:
        print(f"  [Polygon] Minute data error for {date_str}: {e}")
        return None


def _infer_trade_executed(signal: str, trade_executed_raw: str) -> str:
    """Infer Trade_Executed for legacy rows that don't have the column.

    For rows logged before the Trade_Executed column was added, we infer:
      - SKIP signal → NO_SKIP
      - TRADE_* signal → YES (best guess — we don't know about OA gates)
    """
    if trade_executed_raw:
        return trade_executed_raw
    # Legacy row: infer from signal
    if signal == 'SKIP':
        return 'NO_SKIP'
    return 'YES'


def _evaluate_outcome(
    signal: str,
    trade_executed: str,
    spx_entry: float,
    spx_exit_price: float,
    spx_next_close: float,
) -> Tuple[float, str]:
    """Calculate overnight move and determine if the outcome was correct.

    The exit price should be SPX at 10:00 AM ET (matching OA's time-based
    exit) when available, or the daily open as fallback. This captures the
    unmanaged overnight exposure window from ~2 PM entry to 10 AM exit.

    Uses Trade_Executed to decide:
      - YES → check against tier-specific breakeven threshold
      - NO_* → check against NO_TRADE_THRESHOLD (was staying out correct?)

    Returns (overnight_move_pct, outcome_str).
    """
    # Overnight move = entry price to exit price (10 AM ET or daily open)
    overnight_move_pct = abs((spx_exit_price - spx_entry) / spx_entry) * 100

    actually_traded = trade_executed == 'YES'

    if actually_traded:
        # We were in the trade — was the condor safe?
        threshold = MOVE_THRESHOLDS.get(signal, 0.80)
        correct = overnight_move_pct < threshold
        outcome = "CORRECT_TRADE" if correct else "WRONG_TRADE"
    else:
        # We did NOT trade — was that the right call?
        correct = overnight_move_pct >= NO_TRADE_THRESHOLD
        # Tag the reason for not trading in the outcome
        if trade_executed == 'NO_SKIP':
            outcome = "CORRECT_SKIP" if correct else "WRONG_SKIP"
        elif trade_executed.startswith('NO_VIX_GATE'):
            outcome = "CORRECT_VIX_GATE" if correct else "WRONG_VIX_GATE"
        elif trade_executed == 'NO_FRIDAY':
            outcome = "CORRECT_FRIDAY" if correct else "WRONG_FRIDAY"
        elif trade_executed.startswith('NO_OA_EVENT'):
            outcome = "CORRECT_OA_EVENT" if correct else "WRONG_OA_EVENT"
        else:
            # NO_DUPLICATE or unknown
            outcome = "CORRECT_NO_TRADE" if correct else "WRONG_NO_TRADE"

    return round(overnight_move_pct, 4), outcome


def _hypothetical_outcome(signal: str, overnight_move_pct: float) -> str:
    """Determine what the outcome WOULD have been for a given signal.

    Unlike _evaluate_outcome which uses actual Trade_Executed status, this
    evaluates purely based on the signal: if we had followed this signal,
    would the overnight move have been within safe bounds?

    Returns 'CORRECT' or 'WRONG'.
    """
    if signal == 'SKIP':
        # SKIP is correct if the move was large enough to blow a condor
        return 'CORRECT' if overnight_move_pct >= NO_TRADE_THRESHOLD else 'WRONG'
    # TRADE_* signals: correct if the move was within the tier's breakeven
    threshold = MOVE_THRESHOLDS.get(signal, 0.80)
    return 'CORRECT' if overnight_move_pct < threshold else 'WRONG'


def _group_rows_by_date(all_rows: List[List[str]]) -> Dict[str, List[Dict]]:
    """Group sheet rows by trading date, ordered by timestamp.

    Returns {date_str: [list of entry dicts sorted by timestamp]}.
    The first entry in each list is the decision signal (earliest in the
    trading window), regardless of poke_number.  This makes the grouping
    robust to local runs where poke_number resets to 1 each time.
    """
    # Collect rows per date with their parsed datetime for sorting
    date_rows: Dict[str, List[Tuple[datetime, Dict]]] = {}

    for row in all_rows[1:]:
        while len(row) <= COL_OUTCOME_CORRECT:
            row.append("")

        timestamp = row[COL_TIMESTAMP]
        if not timestamp:
            continue

        dt = _parse_signal_date(timestamp)
        if dt is None:
            continue
        date_key = dt.strftime('%Y-%m-%d')

        signal = row[COL_SIGNAL]
        if not signal:
            continue

        overnight_str = row[COL_OVERNIGHT_MOVE]
        try:
            overnight = float(overnight_str.replace('%', '').replace('+', ''))
        except (ValueError, TypeError):
            overnight = None

        sent_to_gpt_str = row[COL_SENT_TO_GPT] if len(row) > COL_SENT_TO_GPT else ""
        try:
            sent_to_gpt = int(sent_to_gpt_str)
        except (ValueError, TypeError):
            sent_to_gpt = None

        entry = {
            'signal': signal,
            'overnight_move': overnight,
            'sent_to_gpt': sent_to_gpt,
            'outcome': row[COL_OUTCOME_CORRECT],
            'timestamp': timestamp,
        }

        if date_key not in date_rows:
            date_rows[date_key] = []
        date_rows[date_key].append((dt, entry))

    # Sort each date's entries by timestamp; first = decision signal
    date_groups: Dict[str, List[Dict]] = {}
    for date_key, entries in date_rows.items():
        entries.sort(key=lambda x: x[0])
        date_groups[date_key] = [e for _, e in entries]

    return date_groups


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
        trade_executed_raw = row[COL_TRADE_EXECUTED]

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

        trade_executed = _infer_trade_executed(signal, trade_executed_raw)
        te_tag = f" | Traded={trade_executed}" if trade_executed != "YES" else ""
        print(f"  Row {row_idx + 1}: {timestamp} | Signal={signal}{te_tag} | SPX={spx_entry:.2f} | Next day={next_day}")

        fetch_result = _fetch_spx_day(next_day, api_key)
        if fetch_result is None:
            continue

        day_data, actual_date = fetch_result
        if actual_date != next_day:
            print(f"           (holiday skip: {next_day} → {actual_date})")

        spx_next_open = day_data['open']
        spx_next_close = day_data['close']

        # Try to get 10 AM exit price (matches OA time-based exit)
        spx_10am = _fetch_spx_10am_price(actual_date, api_key)
        if spx_10am is not None:
            spx_exit_price = spx_10am
            exit_source = "10AM"
        else:
            spx_exit_price = spx_next_open
            exit_source = "open"

        overnight_move_pct, outcome = _evaluate_outcome(
            signal, trade_executed, spx_entry, spx_exit_price, spx_next_close
        )

        result = {
            'row': row_idx + 1,
            'timestamp': timestamp,
            'signal': signal,
            'trade_executed': trade_executed,
            'spx_entry': spx_entry,
            'spx_exit_price': spx_exit_price,
            'spx_next_close': spx_next_close,
            'overnight_move_pct': overnight_move_pct,
            'outcome': outcome,
            'exit_source': exit_source,
        }
        results.append(result)

        print(f"           Exit={spx_exit_price:.2f} ({exit_source}) | Move={overnight_move_pct:+.4f}% | {outcome}")

        if not dry_run:
            # Update cells: columns are 1-indexed in gspread
            # COL_SPX_NEXT_OPEN stores exit price (10 AM when available, else daily open)
            try:
                ws.update_cell(row_idx + 1, COL_SPX_NEXT_OPEN + 1, spx_exit_price)
                ws.update_cell(row_idx + 1, COL_SPX_NEXT_CLOSE + 1, spx_next_close)
                ws.update_cell(row_idx + 1, COL_OVERNIGHT_MOVE + 1, f"{overnight_move_pct:+.4f}%")
                ws.update_cell(row_idx + 1, COL_OUTCOME_CORRECT + 1, outcome)
                updates_made += 1
            except Exception as e:
                print(f"           ERROR updating row: {e}")

    print(f"\n{'DRY RUN — ' if dry_run else ''}Processed {len(results)} rows, updated {updates_made}")
    return results


def _print_poke_stability(all_rows: List[List[str]]) -> None:
    """Print poke stability analysis comparing later signals to the decision signal.

    Decision signal: the first signal logged in the trading window (by timestamp).
    Validation signals: any subsequent signals on the same date.

    Uses timestamp ordering (not poke_number) so local runs where
    poke_number resets to 1 are handled correctly.

    This section answers: "If we had waited, would we have made a better decision?"
    """
    date_groups = _group_rows_by_date(all_rows)

    # Only analyze dates with multiple signals AND outcome data
    multi_signal_dates = {}
    for date_key, signals in date_groups.items():
        if len(signals) < 2:
            continue
        # Need outcome data (overnight move) from the decision signal
        if signals[0].get('overnight_move') is None:
            continue
        multi_signal_dates[date_key] = signals

    if not multi_signal_dates:
        print(f"\n  {'─' * 50}")
        print(f"  POKE STABILITY ANALYSIS")
        print(f"  {'─' * 50}")
        print(f"    No multi-signal dates with outcome data yet.")
        print(f"    (Need decision signal + at least one later signal with backfilled outcomes)")
        return

    # Metrics
    total_dates = len(multi_signal_dates)
    all_agree = 0          # all signals produced the same tier
    first_better = 0       # first signal made the better call
    later_better = 0       # a later signal would have been better
    same_outcome = 0       # both would have been equally correct/wrong
    signal_changes = []    # track what changed and when

    for date_key in sorted(multi_signal_dates):
        signals = multi_signal_dates[date_key]
        decision = signals[0]   # first by timestamp = the actual trade
        overnight = decision['overnight_move']

        # Get all signal tiers for this date
        all_tiers = [s['signal'] for s in signals]
        unique_tiers = set(all_tiers)

        if len(unique_tiers) == 1:
            all_agree += 1
            same_outcome += 1
            continue

        # Signals disagree — evaluate which was better
        decision_result = _hypothetical_outcome(decision['signal'], overnight)

        # Compare against the latest validation signal
        latest = signals[-1]
        latest_result = _hypothetical_outcome(latest['signal'], overnight)

        if decision_result == latest_result:
            same_outcome += 1
        elif decision_result == 'CORRECT':
            first_better += 1
        else:
            later_better += 1

        # Track the change for detailed output
        article_change = ""
        if decision.get('sent_to_gpt') is not None and latest.get('sent_to_gpt') is not None:
            diff = latest['sent_to_gpt'] - decision['sent_to_gpt']
            if diff != 0:
                article_change = f" (articles: {decision['sent_to_gpt']}→{latest['sent_to_gpt']})"

        signal_changes.append({
            'date': date_key,
            'first_signal': decision['signal'],
            'later_signal': latest['signal'],
            'overnight': overnight,
            'first_result': decision_result,
            'later_result': latest_result,
            'article_change': article_change,
        })

    # Print section
    print(f"\n  {'─' * 50}")
    print(f"  POKE STABILITY ANALYSIS ({total_dates} nights with 2+ signals)")
    print(f"  {'─' * 50}")

    stability_pct = all_agree / total_dates * 100 if total_dates else 0
    print(f"  Signal stability: {all_agree}/{total_dates} nights all signals agree ({stability_pct:.0f}%)")

    disagreements = total_dates - all_agree
    if disagreements > 0:
        print(f"  Disagreements: {disagreements} nights")
        print(f"    First signal was better:  {first_better} ({first_better/disagreements*100:.0f}%)")
        print(f"    Later signal was better:  {later_better} ({later_better/disagreements*100:.0f}%)")
        print(f"    Same outcome either way: {same_outcome - all_agree} ({(same_outcome - all_agree)/disagreements*100:.0f}%)")

        if later_better > first_better:
            print(f"\n    ** Later signals are consistently better — consider delaying the decision **")
        elif first_better > later_better:
            print(f"\n    ** First signal timing is good — later news doesn't improve decisions **")

    # Show individual disagreements
    changes_only = [c for c in signal_changes if c['first_signal'] != c['later_signal']]
    if changes_only:
        print(f"\n    Signal changes:")
        for c in changes_only:
            arrow = "→"
            verdict = ""
            if c['first_result'] != c['later_result']:
                winner = "1st signal" if c['first_result'] == 'CORRECT' else "Later signal"
                verdict = f" [{winner} was right]"
            else:
                verdict = f" [same outcome]"
            move_str = f"{c['overnight']:.4f}"
            print(f"      {c['date']}: {c['first_signal']} {arrow} {c['later_signal']}"
                  f" (move={move_str}%){c['article_change']}{verdict}")


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
    all_outcomes = []

    for row in all_rows[1:]:
        while len(row) <= COL_OUTCOME_CORRECT:
            row.append("")

        signal = row[COL_SIGNAL]
        outcome = row[COL_OUTCOME_CORRECT]
        overnight_str = row[COL_OVERNIGHT_MOVE]
        trade_executed_raw = row[COL_TRADE_EXECUTED]

        if not outcome or not signal:
            continue

        try:
            overnight = float(overnight_str.replace('%', '').replace('+', ''))
        except (ValueError, TypeError):
            overnight = None

        trade_executed = _infer_trade_executed(signal, trade_executed_raw)

        entry = {
            'signal': signal,
            'outcome': outcome,
            'overnight_move': overnight,
            'trade_executed': trade_executed,
        }
        all_outcomes.append(entry)

    if not all_outcomes:
        print("No outcome data available yet. Run: python validate_outcomes.py")
        return

    # Split into actually traded vs not traded
    traded = [o for o in all_outcomes if o['trade_executed'] == 'YES']
    not_traded = [o for o in all_outcomes if o['trade_executed'] != 'YES']

    total = len(all_outcomes)
    total_correct = sum(1 for o in all_outcomes if 'CORRECT' in o['outcome'])

    print("\n" + "=" * 70)
    print("  SIGNAL ACCURACY REPORT")
    print("=" * 70)

    print(f"\n  Total signals: {total} | Traded: {len(traded)} | Not traded: {len(not_traded)}")
    print(f"  Overall Accuracy: {total_correct}/{total} ({total_correct/total*100:.1f}%)")

    # ── Section 1: Actually Traded ──
    if traded:
        traded_correct = sum(1 for o in traded if 'CORRECT' in o['outcome'])
        print(f"\n  {'─' * 50}")
        print(f"  ACTUALLY TRADED ({len(traded)} days)")
        print(f"  {'─' * 50}")
        print(f"  Trade Survival Rate: {traded_correct}/{len(traded)} ({traded_correct/len(traded)*100:.1f}%)")

        # By signal tier
        for tier in ['TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE']:
            entries = [o for o in traded if o['signal'] == tier]
            if not entries:
                continue
            correct = sum(1 for e in entries if 'CORRECT' in e['outcome'])
            n = len(entries)
            moves = [e['overnight_move'] for e in entries if e['overnight_move'] is not None]
            print(f"\n    {tier}: {correct}/{n} correct ({correct/n*100:.1f}%)")
            print(f"      Threshold: {MOVE_THRESHOLDS[tier]:.2f}%")
            if moves:
                print(f"      Avg overnight move: {sum(moves)/len(moves):.4f}%")
                print(f"      Max overnight move: {max(moves):.4f}%")

        blown = [o for o in traded if 'WRONG' in o['outcome']]
        if blown:
            print(f"\n    Blown trades: {len(blown)}")
            for b in blown:
                print(f"      {b['signal']} | move={b['overnight_move']:.4f}%")

    # ── Section 2: Not Traded ──
    if not_traded:
        nt_correct = sum(1 for o in not_traded if 'CORRECT' in o['outcome'])
        print(f"\n  {'─' * 50}")
        print(f"  NOT TRADED ({len(not_traded)} days)")
        print(f"  {'─' * 50}")
        print(f"  Correct to skip: {nt_correct}/{len(not_traded)} ({nt_correct/len(not_traded)*100:.1f}%)")

        # Group by skip reason
        skip_reasons = {}
        for o in not_traded:
            te = o['trade_executed']
            # Normalize entries with details in parentheses
            if te.startswith('NO_VIX_GATE'):
                reason = 'NO_VIX_GATE'
            elif te.startswith('NO_OA_EVENT'):
                reason = 'NO_OA_EVENT'
            else:
                reason = te
            if reason not in skip_reasons:
                skip_reasons[reason] = []
            skip_reasons[reason].append(o)

        for reason in ['NO_SKIP', 'NO_FRIDAY', 'NO_VIX_GATE', 'NO_OA_EVENT', 'NO_DUPLICATE']:
            entries = skip_reasons.get(reason, [])
            if not entries:
                continue
            correct = sum(1 for e in entries if 'CORRECT' in e['outcome'])
            n = len(entries)
            moves = [e['overnight_move'] for e in entries if e['overnight_move'] is not None]

            label = {
                'NO_SKIP': 'Signal SKIP',
                'NO_FRIDAY': 'Friday (no trade)',
                'NO_VIX_GATE': 'OA VIX gate (>=25)',
                'NO_OA_EVENT': 'OA event gate (FOMC/CPI/early close)',
                'NO_DUPLICATE': 'Duplicate webhook',
            }.get(reason, reason)

            print(f"\n    {label}: {correct}/{n} correct ({correct/n*100:.1f}%)")
            if moves:
                print(f"      Avg overnight move: {sum(moves)/len(moves):.4f}%")
            # Show missed opportunities
            missed = [e for e in entries if 'WRONG' in e['outcome']]
            if missed:
                print(f"      Missed opportunities: {len(missed)} (move was < 0.80%)")

    # ── Signal distribution ──
    print(f"\n  {'─' * 50}")
    print(f"  SIGNAL DISTRIBUTION")
    print(f"  {'─' * 50}")
    for tier in ['TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE', 'SKIP']:
        n = sum(1 for o in all_outcomes if o['signal'] == tier)
        pct = n / total * 100 if total else 0
        print(f"    {tier}: {n} ({pct:.0f}%)")

    # ── Poke stability analysis ──
    _print_poke_stability(all_rows)

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
