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
  - Profit target (Aggressive 25%, Normal 20%, Conservative 15% of credit)
  - Stop loss (Aggressive 120%, Normal 100%, Conservative 80% of credit)
  - Touch monitor: NOT USED on SPX (European cash-settled, no assignment risk)
  Confirmed current as of 2026-05-05. Logic: more confident signal (AGGR) gets
  wider exits (more room to work); less confident (CONSV) gets tighter exits
  (bank quickly, cut losses fast).
  This validation uses the 10 AM price as a proxy since we don't have OA's
  actual fill/exit data. Directionally accurate but not exact.

The Trade_Executed column tracks whether a trade was actually placed:
  YES           — webhook fired, OA executed the trade
  NO_SKIP       — our signal said SKIP
  NO_VIX_GATE   — VIX >= 25, OA blocked the trade
  NO_DUPLICATE  — webhook already sent earlier today
  NO_FRIDAY     — legacy: Friday was previously blocked (no longer applies)
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

import gspread
import requests
import pytz

from core.config import get_config

logger = logging.getLogger(__name__)
ET_TZ = pytz.timezone('US/Eastern')

# Iron condor parameters by signal tier (Bot A canonical structure for reference)
TRADE_PARAMS = {
    'TRADE_AGGRESSIVE': {'width': 20, 'delta': 0.18},
    'TRADE_NORMAL':     {'width': 25, 'delta': 0.16},
    'TRADE_CONSERVATIVE': {'width': 30, 'delta': 0.14},
}

# OA exit settings per tier (for reference — actual exits happen in OA)
# Profit target = % of credit received; Stop loss = % of credit as loss
# Time-based exit: 10:00 AM ET next day (hard close for all tiers)
# Touch monitor: NOT USED on SPX (European cash-settled — no assignment risk)
# Confirmed current 2026-05-05.
OA_EXIT_PARAMS = {
    'TRADE_AGGRESSIVE':   {'profit_pct': 25, 'stop_pct': 120},
    'TRADE_NORMAL':       {'profit_pct': 20, 'stop_pct': 100},
    'TRADE_CONSERVATIVE': {'profit_pct': 15, 'stop_pct': 80},
}
OA_TIME_EXIT = '10:00'       # ET — hard close for all tiers

# ─────────────────────────────────────────────────────────────────────────────
# Multi-bot breakeven lookup
# Each desk's structure_label maps to a {routed_tier → breakeven_pct} table.
# Used by _evaluate_outcome to determine whether the overnight move breached
# the SHORT strike for THIS bot's structural recipe.
#
# Breakeven formula (simplified): short_strike_distance_pct ≈ delta * 5.0
#   AGGR Δ0.18 → 0.90%, NORMAL Δ0.16 → 0.80%, CONSV Δ0.14 → 0.70%
#   + ~0.10% credit cushion → net 1.00 / 0.90 / 0.80
# For asymmetric IC: the *narrower* (call) side is the binding constraint.
# ─────────────────────────────────────────────────────────────────────────────
BREAKEVEN_BY_STRUCTURE: Dict[str, Dict[str, float]] = {
    # Bot A — symmetric IC
    'IC_25pt_0.16d_symmetric': {
        'TRADE_AGGRESSIVE':   1.00,
        'TRADE_NORMAL':       0.90,
        'TRADE_CONSERVATIVE': 0.80,
    },
    # Bot B — asymmetric IC; call-side is binding (narrower)
    'asymmetric_IC_putΔ20_callΔ10': {
        'TRADE_AGGRESSIVE':   0.85,   # short call Δ0.14 → ~0.85%
        'TRADE_NORMAL':       0.75,   # short call Δ0.12 → ~0.75%
        'TRADE_CONSERVATIVE': 0.65,   # short call Δ0.10 → ~0.65%
    },
    # Bot C — put-spread only (one-sided; see STRUCTURE_DIRECTION)
    'putspread_putΔ16_2x_size': {
        'TRADE_AGGRESSIVE':   1.00,
        'TRADE_NORMAL':       0.90,
        'TRADE_CONSERVATIVE': 0.80,
    },
    # Bot D — VVIX-conditional sizing on Bot A NORMAL structure (all tiers same)
    'IC_25pt_0.16d_VVIXpct252d': {
        'TRADE_VVIX_LOW':     0.90,
        'TRADE_VVIX_NORMAL':  0.90,
        'TRADE_VVIX_HIGH':    0.90,
        'TRADE_VVIX_EXTREME': 0.90,
    },
    # Bot E — DOW-conditional sizing on Bot A's per-tier structure
    'IC_25pt_0.16d_DOWsized': {
        'TRADE_AGGRESSIVE_BOOST':    1.00, 'TRADE_AGGRESSIVE_NORMAL':    1.00,
        'TRADE_NORMAL_BOOST':        0.90, 'TRADE_NORMAL_NORMAL':        0.90,
        'TRADE_CONSERVATIVE_BOOST':  0.80, 'TRADE_CONSERVATIVE_NORMAL':  0.80,
    },
    # Bot F — combined; asymmetric IC + VVIX × DOW (call-side breakeven binding)
    'asymIC_VVIXpct252d_DOWmult_EXTRhedge': {
        'TRADE_LOW_NORMAL':              0.75, 'TRADE_LOW_BOOST':              0.75,
        'TRADE_NORMAL_NORMAL':           0.75, 'TRADE_NORMAL_BOOST':           0.75,
        'TRADE_HIGH_NORMAL':             0.75, 'TRADE_HIGH_BOOST':             0.75,
        'TRADE_EXTREME_NORMAL_HEDGED':   0.75, 'TRADE_EXTREME_BOOST_HEDGED':   0.75,
    },
}

# Symmetric structures fail on either-side moves; one-sided fail only on adverse direction.
STRUCTURE_DIRECTION: Dict[str, str] = {
    'IC_25pt_0.16d_symmetric':              'symmetric',
    'asymmetric_IC_putΔ20_callΔ10':         'symmetric',
    'putspread_putΔ16_2x_size':             'down_only',     # ONLY down moves can fail
    'IC_25pt_0.16d_VVIXpct252d':            'symmetric',
    'IC_25pt_0.16d_DOWsized':               'symmetric',
    'asymIC_VVIXpct252d_DOWmult_EXTRhedge': 'symmetric',
    # Butterfly: 0DTE — different outcome metric (intraday); see SKIPPED_DESK_IDS
    'iron_butterfly_0DTE_VIX_sized':        'symmetric',
}

# Desks whose outcome semantics are NOT next-day overnight close-to-open.
# For now we skip these in validate_outcomes.py (TODO: dedicated intraday handler).
SKIPPED_DESK_IDS = {'afternoon_butterflies'}


def breakeven_for(structure_label: str, routed_tier: str, signal_tier: str = '') -> Optional[float]:
    """Return breakeven % for this bot's structure + routed tier.

    Lookup order:
      1. (structure_label, routed_tier) — exact multi-bot match
      2. (structure_label, signal_tier) — legacy rows where Routed_Tier is blank
      3. ('IC_25pt_0.16d_symmetric', signal_tier) — pre-multibot Bot A default
         (handles historical rows AND back-compat callers that omit structure_label)

    Returns None only when even the Bot A fallback can't classify the tier.
    """
    by_tier = BREAKEVEN_BY_STRUCTURE.get(structure_label or '')
    if by_tier is not None:
        v = by_tier.get(routed_tier) or by_tier.get(signal_tier)
        if v is not None:
            return v

    # Legacy fallback — Bot A canonical IC (historical default)
    bot_a = BREAKEVEN_BY_STRUCTURE['IC_25pt_0.16d_symmetric']
    return bot_a.get(routed_tier) or bot_a.get(signal_tier)


# Threshold for "was not trading correct?" — if move >= this, not trading was right
NO_TRADE_THRESHOLD = 0.80

# ── Legacy back-compat alias — Bot A's symmetric IC breakevens.
# New code should use BREAKEVEN_BY_STRUCTURE / breakeven_for() instead, which
# handles all desks. This alias is kept so old callers (tests, scripts that
# read from analyze_signals) continue to work without immediate refactoring.
MOVE_THRESHOLDS = dict(BREAKEVEN_BY_STRUCTURE['IC_25pt_0.16d_symmetric'])
MOVE_THRESHOLDS['SKIP'] = NO_TRADE_THRESHOLD


# ─────────────────────────────────────────────────────────────────────────────
# Header-index lookup (name-keyed reads)
# Populated by _build_header_index(ws); used by _col(row, name).
# Robust to column reordering and minor whitespace typos in the Sheet header.
# ─────────────────────────────────────────────────────────────────────────────
HEADER_INDEX: Dict[str, int] = {}


def _build_header_index(ws) -> None:
    """Read the live header row from the worksheet and populate HEADER_INDEX."""
    global HEADER_INDEX
    header = ws.row_values(1)
    HEADER_INDEX = {(h or '').strip(): i
                    for i, h in enumerate(header) if (h or '').strip()}


def _col(row: List[str], name: str, default: str = '') -> str:
    """Get value from a Sheet row by header name. Returns default if missing."""
    idx = HEADER_INDEX.get(name)
    if idx is None or idx >= len(row):
        return default
    return row[idx] or default


# ── Legacy COL_* constants kept for back-compat with tests + external callers ──
# Defaults match the original SHEET_HEADERS layout. After _connect_sheet() runs,
# _refresh_legacy_col_indices() updates these to match the LIVE header positions.
# New code should prefer _col(row, name) instead of these positional indices.
COL_TIMESTAMP = 0
COL_POKE_NUMBER = 1
COL_SIGNAL = 2
COL_SPX_CURRENT = 17
COL_VIX = 18
COL_TRADE_EXECUTED = 19
COL_CONTRADICTION_FLAGS = 25
COL_OVERRIDE = 26
COL_SCORE_ADJ = 27
COL_SPX_NEXT_OPEN = 28
COL_SPX_NEXT_CLOSE = 29
COL_OVERNIGHT_MOVE = 30
COL_OUTCOME_CORRECT = 31


def _refresh_legacy_col_indices() -> None:
    """Update the module-level COL_* constants from HEADER_INDEX after the sheet
    is loaded. Lets old code paths keep working while the migration to _col()
    is in progress."""
    global COL_TIMESTAMP, COL_POKE_NUMBER, COL_SIGNAL, COL_SPX_CURRENT, COL_VIX
    global COL_TRADE_EXECUTED, COL_CONTRADICTION_FLAGS, COL_OVERRIDE, COL_SCORE_ADJ
    global COL_SPX_NEXT_OPEN, COL_SPX_NEXT_CLOSE, COL_OVERNIGHT_MOVE, COL_OUTCOME_CORRECT
    COL_TIMESTAMP           = HEADER_INDEX.get('Timestamp_ET', 0)
    COL_POKE_NUMBER         = HEADER_INDEX.get('Poke_Number', 0)
    COL_SIGNAL              = HEADER_INDEX.get('Signal', 0)
    COL_SPX_CURRENT         = HEADER_INDEX.get('SPX_Current', 0)
    COL_VIX                 = HEADER_INDEX.get('VIX', 0)
    COL_TRADE_EXECUTED      = HEADER_INDEX.get('Trade_Executed', 0)
    COL_CONTRADICTION_FLAGS = HEADER_INDEX.get('Contradiction_Flags', 0)
    COL_OVERRIDE            = HEADER_INDEX.get('Override_Applied', 0)
    COL_SCORE_ADJ           = HEADER_INDEX.get('Score_Adjustment', 0)
    COL_SPX_NEXT_OPEN       = HEADER_INDEX.get('SPX_Next_Open', 0)
    COL_SPX_NEXT_CLOSE      = HEADER_INDEX.get('SPX_Next_Close', 0)
    COL_OVERNIGHT_MOVE      = HEADER_INDEX.get('Overnight_Move_Pct', 0)
    COL_OUTCOME_CORRECT     = HEADER_INDEX.get('Outcome_Correct', 0)


def _parse_signal_date(date_str: str) -> Optional[datetime]:
    """Parse timestamp from Google Sheets (e.g. '2025-03-06 01:45:23 PM EST').

    Tries multiple formats because the Sheets timestamp format can vary
    depending on timezone (EST vs EDT) and how the cell is formatted.
    """
    for fmt in [
        "%Y-%m-%d %I:%M:%S %p %Z",
        "%Y-%m-%d %I:%M:%S %p EST",
        "%Y-%m-%d %I:%M:%S %p EDT",
        "%m/%d/%Y %I:%M:%S %p",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
    ]:
        try:
            dt = datetime.strptime(date_str.strip(), fmt)
            if dt.tzinfo is None:
                dt = ET_TZ.localize(dt)
            return dt
        except ValueError:
            continue
    return None


def _next_weekday(dt: datetime) -> datetime:
    """Return the next weekday (skip Saturday and Sunday)."""
    nxt = dt + timedelta(days=1)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


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
    structure_label: str = '',
    routed_tier: str = '',
) -> Tuple[float, str]:
    """Calculate overnight move and determine if the outcome was correct.

    The exit price should be SPX at 10:00 AM ET (matching OA's time-based
    exit) when available, or the daily open as fallback. This captures the
    unmanaged overnight exposure window from ~2 PM entry to 10 AM exit.

    Uses Trade_Executed to decide:
      - YES → check against structure-specific breakeven threshold
      - NO_* → check against NO_TRADE_THRESHOLD (was staying out correct?)

    For multi-bot trial: looks up the breakeven by `structure_label` +
    `routed_tier`, falling back to `signal` (legacy rows). Honors one-sided
    structures (Bot C put-spread): only fails on adverse-direction moves.

    Returns (overnight_move_pct, outcome_str).
    """
    # Signed and absolute overnight moves
    signed_move_pct = (spx_exit_price - spx_entry) / spx_entry * 100
    overnight_move_pct = abs(signed_move_pct)

    actually_traded = trade_executed == 'YES'

    if actually_traded:
        # Look up structure-specific breakeven; fall back to legacy MOVE_THRESHOLDS
        # (which is now Bot A's symmetric IC by structure_label='').
        threshold = breakeven_for(structure_label, routed_tier, signal_tier=signal)
        if threshold is None:
            # Unknown structure or tier — don't classify, mark for inspection
            return round(overnight_move_pct, 4), "UNKNOWN_STRUCTURE"

        # Honor one-sided structures (Bot C put-spread fails only on down moves)
        direction = STRUCTURE_DIRECTION.get(structure_label, 'symmetric')
        if direction == 'down_only':
            breached = (signed_move_pct < 0) and (overnight_move_pct >= threshold)
        else:
            breached = overnight_move_pct >= threshold

        outcome = "CORRECT_TRADE" if not breached else "WRONG_TRADE"
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




def _connect_sheet():
    """Connect to the unified 'Live' tab of the Google Sheet via the firm's
    name-tolerant lookup helper. Returns worksheet or None.

    Once connected, populate HEADER_INDEX so name-keyed reads work for the rest
    of the script. This replaces the old `sh.sheet1` lookup which silently
    targeted whatever the first tab happened to be.
    """
    try:
        from core.sheets import _get_worksheet
    except ImportError:
        print("ERROR: core.sheets not importable")
        return None
    ws = _get_worksheet('live')
    if ws is None:
        print("ERROR: Could not connect to 'Live' tab")
        return None
    _build_header_index(ws)
    _refresh_legacy_col_indices()
    return ws


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
    batch_cells = []

    print(f"\nScanning {len(all_rows) - 1} signal rows for missing outcomes...\n")

    skipped_butterfly = 0
    skipped_unknown_structure = 0

    for row_idx in range(1, len(all_rows)):
        row = all_rows[row_idx]

        # Pad row if needed (sheet may have fewer columns than expected)
        while len(row) < len(header):
            row.append("")

        # Read by header NAME — robust to column reorders / typos.
        timestamp           = _col(row, 'Timestamp_ET')
        signal              = _col(row, 'Signal')
        routed_tier         = _col(row, 'Routed_Tier') or signal  # fallback for legacy rows
        structure_label     = _col(row, 'Structure_Label')
        desk_id             = _col(row, 'Desk_ID') or 'overnight_condors'  # legacy → Bot A
        spx_current_str     = _col(row, 'SPX_Current')
        trade_executed_raw  = _col(row, 'Trade_Executed')

        # Skip if outcome already filled (use name lookup)
        if _col(row, 'SPX_Next_Open') and _col(row, 'Outcome_Correct'):
            continue

        # Multi-bot partitioning: skip desks whose outcome semantics aren't
        # next-day overnight close-to-open (e.g., 0DTE butterfly is intraday).
        if desk_id in SKIPPED_DESK_IDS:
            skipped_butterfly += 1
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
            signal, trade_executed, spx_entry, spx_exit_price, spx_next_close,
            structure_label=structure_label, routed_tier=routed_tier,
        )

        # Track unknown-structure rows so the summary can flag them
        if outcome == 'UNKNOWN_STRUCTURE':
            skipped_unknown_structure += 1

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
            # Collect batch updates (written after the loop)
            r = row_idx + 1  # 1-indexed row
            batch_cells.append(gspread.Cell(r, COL_SPX_NEXT_OPEN + 1, str(spx_exit_price)))
            batch_cells.append(gspread.Cell(r, COL_SPX_NEXT_CLOSE + 1, str(spx_next_close)))
            batch_cells.append(gspread.Cell(r, COL_OVERNIGHT_MOVE + 1, str(round(overnight_move_pct, 4))))
            batch_cells.append(gspread.Cell(r, COL_OUTCOME_CORRECT + 1, outcome))
            updates_made += 1

    # Batch write all updates at once (avoids per-cell rate limits)
    if not dry_run and batch_cells:
        try:
            ws.update_cells(batch_cells, value_input_option='RAW')
            print(f"\nBatch-wrote {len(batch_cells)} cells ({updates_made} rows)")
        except Exception as e:
            print(f"\nERROR batch-writing cells: {e}")

    print(f"\n{'DRY RUN — ' if dry_run else ''}Processed {len(results)} rows, updated {updates_made}")
    if skipped_butterfly:
        print(f"  Skipped {skipped_butterfly} butterfly row(s) "
              f"(intraday outcome semantics — TODO: dedicated handler)")
    if skipped_unknown_structure:
        print(f"  ⚠ {skipped_unknown_structure} row(s) had UNKNOWN_STRUCTURE — "
              f"check Structure_Label / Routed_Tier columns")
    return results


def print_backfill_summary() -> Optional[str]:
    """Print a short backfill summary and point to analyze_signals.py.

    Returns the summary text (for HTML saving), or None if no data.
    """
    ws = _connect_sheet()
    if ws is None:
        return None

    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        print("No data rows found")
        return None

    total_rows = len(all_rows) - 1
    with_outcomes = 0
    awaiting = 0
    traded = 0
    not_traded = 0

    for row in all_rows[1:]:
        # Pad row to header length (sheet may have fewer cells than header)
        while len(row) < len(all_rows[0]):
            row.append("")

        signal = _col(row, 'Signal')
        if not signal:
            continue

        trade_executed = _col(row, 'Trade_Executed')
        outcome = _col(row, 'Outcome_Correct')

        if outcome:
            with_outcomes += 1
            if trade_executed == 'YES':
                traded += 1
            elif trade_executed and trade_executed != 'NO_DUPLICATE':
                not_traded += 1
        elif signal and _col(row, 'SPX_Current'):
            awaiting += 1

    lines = []
    lines.append("")
    lines.append("=" * 50)
    lines.append("  BACKFILL SUMMARY")
    lines.append("=" * 50)
    lines.append(f"  Total rows:           {total_rows}")
    lines.append(f"  With outcomes:        {with_outcomes}")
    lines.append(f"    Traded (YES):       {traded}")
    lines.append(f"    Not traded:         {not_traded}")
    lines.append(f"  Awaiting backfill:    {awaiting}")
    lines.append("")
    lines.append("  For detailed analysis, run:")
    lines.append("    python analyze_signals.py")
    lines.append("=" * 50)

    summary = '\n'.join(lines)
    print(summary)
    return summary


if __name__ == '__main__':
    args = sys.argv[1:]
    dry_run = '--dry-run' in args
    report_only = '--report' in args

    if report_only:
        summary = print_backfill_summary()
    else:
        results = backfill_outcomes(dry_run=dry_run)
        summary = print_backfill_summary()

    # Auto-save as HTML
    if summary:
        try:
            from core.report_writer import save_html_report
            path = save_html_report(summary, prefix='validate')
            print(f"\n  Report saved: {path}")
        except Exception as e:
            print(f"\n  (Could not save HTML report: {e})")
