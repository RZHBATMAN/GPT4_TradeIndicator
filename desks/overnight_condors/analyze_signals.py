#!/usr/bin/env python3
"""Performance analysis for SPX overnight vol premium strategy.

Reads signal log + outcome data from Google Sheets and produces a clear,
decision-focused report organized around: trades we placed, trades we
didn't, what-if scenarios, and patterns.

This script is READ-ONLY: it never writes to Sheets. It's the analytics
complement to validate_outcomes.py (which handles data backfilling).

Usage:
  python analyze_signals.py                    # full analysis
  python analyze_signals.py --min-rows 10      # require N rows with outcomes
  python analyze_signals.py --export report.txt # save plain-text report
"""
import sys
import json
import math
import logging
from collections import defaultdict
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytz

from core.config import get_config
from desks.overnight_condors.config import SHEET_HEADERS

logger = logging.getLogger(__name__)
ET_TZ = pytz.timezone('US/Eastern')

# ── Column indices from SHEET_HEADERS ──
COL = {name: idx for idx, name in enumerate(SHEET_HEADERS)}

# Breakeven thresholds (must match validate_outcomes.py)
MOVE_THRESHOLDS = {
    'TRADE_AGGRESSIVE': 1.00,
    'TRADE_NORMAL': 0.90,
    'TRADE_CONSERVATIVE': 0.80,
    'SKIP': 0.80,
}
NO_TRADE_THRESHOLD = 0.80

# P&L proxy per 1-lot by tier
# Credit = approximate premium collected; max_loss = width - credit
PNL_PER_LOT = {
    'TRADE_AGGRESSIVE':   {'credit': 60, 'max_loss': 140},
    'TRADE_NORMAL':       {'credit': 45, 'max_loss': 205},
    'TRADE_CONSERVATIVE': {'credit': 30, 'max_loss': 270},
}

# Current factor weights
CURRENT_WEIGHTS = {'iv_rv': 0.30, 'trend': 0.20, 'gpt': 0.50}

# Current tier boundaries
TIER_BOUNDARIES = {
    'TRADE_AGGRESSIVE': (0, 3.5),
    'TRADE_NORMAL': (3.5, 5.0),
    'TRADE_CONSERVATIVE': (5.0, 7.5),
    'SKIP': (7.5, 10.0),
}


# ============================================================================
# DATA LOADING
# ============================================================================

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


def _safe_float(val, default=None):
    if val is None or val == '':
        return default
    try:
        cleaned = str(val).replace('%', '').replace('$', '').replace('+', '').strip()
        return float(cleaned)
    except (ValueError, TypeError):
        return default



def _safe_int(val, default=None):
    if val is None or val == '':
        return default
    try:
        return int(float(str(val).strip()))
    except (ValueError, TypeError):
        return default


def _get_col(row, col_name, default=''):
    idx = COL.get(col_name)
    if idx is None or idx >= len(row):
        return default
    return row[idx]


def load_signal_data() -> List[Dict[str, Any]]:
    """Load all signal rows from Google Sheets into structured dicts."""
    ws = _connect_sheet()
    if ws is None:
        return []

    all_rows = ws.get_all_values()
    if len(all_rows) < 2:
        print("No data rows found in sheet")
        return []

    signals = []
    for row in all_rows[1:]:
        while len(row) < len(SHEET_HEADERS):
            row.append('')

        signal_tier = _get_col(row, 'Signal')
        # Filter out invalid/incomplete rows
        if not signal_tier or signal_tier not in (
            'TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE', 'SKIP'
        ):
            continue
        if not _get_col(row, 'Composite_Score') or not _get_col(row, 'Timestamp_ET'):
            continue

        entry = {
            'timestamp': _get_col(row, 'Timestamp_ET'),
            'poke_number': _safe_int(_get_col(row, 'Poke_Number'), 1),
            'signal': signal_tier,
            'should_trade': _get_col(row, 'Should_Trade') == 'TRUE',
            'reason': _get_col(row, 'Reason'),
            'composite_score': _safe_float(_get_col(row, 'Composite_Score')),
            'category': _get_col(row, 'Category'),
            'iv_rv_score': _safe_float(_get_col(row, 'IV_RV_Score')),
            'iv_rv_ratio': _safe_float(_get_col(row, 'IV_RV_Ratio')),
            'vix1d': _safe_float(_get_col(row, 'VIX1D')),
            'realized_vol': _safe_float(_get_col(row, 'Realized_Vol_10d')),
            'trend_score': _safe_float(_get_col(row, 'Trend_Score')),
            'trend_5d_chg': _safe_float(_get_col(row, 'Trend_5d_Chg_Pct')),
            'gpt_score': _safe_float(_get_col(row, 'GPT_Score')),
            'gpt_category': _get_col(row, 'GPT_Category'),
            'gpt_key_risk': _get_col(row, 'GPT_Key_Risk'),
            'spx_current': _safe_float(_get_col(row, 'SPX_Current')),
            'vix': _safe_float(_get_col(row, 'VIX')),
            'trade_executed': _get_col(row, 'Trade_Executed'),
            'raw_articles': _safe_int(_get_col(row, 'Raw_Articles')),
            'sent_to_gpt': _safe_int(_get_col(row, 'Sent_To_GPT')),
            'contradiction_flags': _get_col(row, 'Contradiction_Flags'),
            'override_applied': _get_col(row, 'Override_Applied'),
            'score_adjustment': _safe_float(_get_col(row, 'Score_Adjustment'), 0),
            'spx_next_open': _safe_float(_get_col(row, 'SPX_Next_Open')),
            'overnight_move': _safe_float(_get_col(row, 'Overnight_Move_Pct')),
            'outcome': _get_col(row, 'Outcome_Correct'),
            'day_of_week': _get_col(row, 'Day_Of_Week'),
            'iv_rv_base_score': _safe_float(_get_col(row, 'IV_RV_Base_Score')),
            'rv_modifier': _safe_float(_get_col(row, 'RV_Modifier')),
            'term_modifier': _safe_float(_get_col(row, 'Term_Modifier')),
            'term_structure_ratio': _safe_float(_get_col(row, 'Term_Structure_Ratio')),
            'trend_base_score': _safe_float(_get_col(row, 'Trend_Base_Score')),
            'intraday_modifier': _safe_float(_get_col(row, 'Intraday_Modifier')),
            'intraday_range_pct': _safe_float(_get_col(row, 'Intraday_Range_Pct')),
            'gpt_raw_score': _safe_float(_get_col(row, 'GPT_Raw_Score')),
            'gpt_direction_risk': _get_col(row, 'GPT_Direction_Risk'),
            'earnings_modifier': _safe_float(_get_col(row, 'Earnings_Modifier')),
            'earnings_tickers': _get_col(row, 'Earnings_Tickers'),
            'gpt_pre_earnings_score': _safe_float(_get_col(row, 'GPT_Pre_Earnings_Score')),
            'pass1_composite': _safe_float(_get_col(row, 'Pass1_Composite')),
            'pass1_signal': _get_col(row, 'Pass1_Signal'),
            'pass2_composite': _safe_float(_get_col(row, 'Pass2_Composite')),
            'pass2_signal': _get_col(row, 'Pass2_Signal'),
            'passes_agreed': _get_col(row, 'Passes_Agreed'),
            'gpt_tokens': _safe_int(_get_col(row, 'GPT_Tokens')),
            'gpt_cost': _safe_float(_get_col(row, 'GPT_Cost')),
            # Phase 1: log-only indicators
            'vvix': _safe_float(_get_col(row, 'VVIX')),
            'vvix_elevated': _get_col(row, 'VVIX_Elevated'),
            'overnight_rv': _safe_float(_get_col(row, 'Overnight_RV')),
            'iv_overnight_rv_ratio': _safe_float(_get_col(row, 'IV_Overnight_RV_Ratio')),
            'blended_overnight_vol': _safe_float(_get_col(row, 'Blended_Overnight_Vol')),
            'student_t_breach_prob': _safe_float(_get_col(row, 'StudentT_Breach_Prob')),
            'student_t_nu': _safe_float(_get_col(row, 'StudentT_Nu')),
            'vrp_trend': _get_col(row, 'VRP_Trend'),
        }
        signals.append(entry)

    return signals


# ============================================================================
# UTILITY FUNCTIONS
# ============================================================================

def _mean(values: List[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    n = len(s)
    if n % 2 == 1:
        return s[n // 2]
    return (s[n // 2 - 1] + s[n // 2]) / 2


def _stdev(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    m = _mean(values)
    return math.sqrt(sum((x - m) ** 2 for x in values) / (len(values) - 1))


def _correlation(xs: List[float], ys: List[float]) -> Optional[float]:
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = _mean(xs), _mean(ys)
    sx, sy = _stdev(xs), _stdev(ys)
    if sx == 0 or sy == 0:
        return None
    n = len(xs)
    cov = sum((xs[i] - mx) * (ys[i] - my) for i in range(n)) / (n - 1)
    return cov / (sx * sy)


def _hypothetical_outcome(signal: str, overnight_move: float) -> str:
    """Would this signal have been correct given the overnight move?"""
    if signal == 'SKIP':
        return 'CORRECT' if overnight_move >= NO_TRADE_THRESHOLD else 'WRONG'
    threshold = MOVE_THRESHOLDS.get(signal, 0.80)
    return 'CORRECT' if overnight_move < threshold else 'WRONG'


def _hypothetical_signal(composite: float) -> str:
    """What signal tier would a given composite score produce?"""
    if composite >= 7.5:
        return 'SKIP'
    elif composite >= 5.0:
        return 'TRADE_CONSERVATIVE'
    elif composite >= 3.5:
        return 'TRADE_NORMAL'
    else:
        return 'TRADE_AGGRESSIVE'


def _pct(n: int, total: int) -> str:
    """Format percentage."""
    if total == 0:
        return "N/A"
    return f"{n / total * 100:.1f}%"


def _pnl_for_trade(signal: str, correct: bool) -> float:
    """Compute P&L proxy for a single trade."""
    params = PNL_PER_LOT.get(signal)
    if params is None:
        return 0
    return params['credit'] if correct else -params['max_loss']


def _parse_date_from_timestamp(ts: str) -> Optional[str]:
    """Extract YYYY-MM-DD from a signal timestamp string."""
    if not ts:
        return None
    for fmt in ["%Y-%m-%d %I:%M:%S %p %Z", "%Y-%m-%d %I:%M:%S %p EST",
                "%Y-%m-%d %I:%M:%S %p EDT", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(ts.strip(), fmt)
            return dt.strftime('%Y-%m-%d')
        except ValueError:
            continue
    # Try extracting first 10 characters
    if len(ts) >= 10:
        return ts[:10]
    return None


def _infer_day_of_week(s: Dict) -> str:
    """Get day of week from enriched data or by parsing timestamp."""
    if s.get('day_of_week'):
        return s['day_of_week']
    ts = s.get('timestamp', '')
    for fmt in ["%Y-%m-%d %I:%M:%S %p %Z", "%Y-%m-%d %I:%M:%S %p EST",
                "%Y-%m-%d %I:%M:%S %p EDT", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
        try:
            dt = datetime.strptime(ts.strip(), fmt)
            return dt.strftime('%A')
        except ValueError:
            continue
    return ''


# ============================================================================
# DATA PARTITIONING
# ============================================================================

def _normalize_trade_executed(te: str) -> str:
    """Normalize Trade_Executed value to category."""
    te = te.strip()
    if not te:
        return 'BLANK'
    if te == 'YES':
        return 'YES'
    if te == 'NO_SKIP':
        return 'NO_SKIP'
    if te.startswith('NO_VIX_GATE'):
        return 'NO_VIX_GATE'
    if te == 'NO_FRIDAY':
        return 'NO_FRIDAY'
    if te == 'NO_DUPLICATE':
        return 'NO_DUPLICATE'
    if te.startswith('NO_OA_EVENT'):
        return 'NO_OA_EVENT'
    if te.startswith('NO_WEBHOOK_FAIL'):
        return 'NO_WEBHOOK_FAIL'
    return te


def _partition_signals(signals: List[Dict]) -> Dict[str, Any]:
    """Partition signals by Trade_Executed into decision groups.

    Returns dict with:
        traded, no_skip, no_vix_gate, no_friday, no_oa_event,
        excluded, all_actionable, all_not_traded
    """
    groups: Dict[str, List[Dict]] = {
        'traded': [],
        'no_skip': [],
        'no_vix_gate': [],
        'no_friday': [],
        'no_oa_event': [],
        'excluded': [],
    }

    for s in signals:
        cat = _normalize_trade_executed(s.get('trade_executed', ''))
        if cat == 'YES':
            groups['traded'].append(s)
        elif cat == 'NO_SKIP':
            groups['no_skip'].append(s)
        elif cat == 'NO_VIX_GATE':
            groups['no_vix_gate'].append(s)
        elif cat == 'NO_FRIDAY':
            groups['no_friday'].append(s)
        elif cat == 'NO_OA_EVENT':
            groups['no_oa_event'].append(s)
        elif cat in ('BLANK', 'NO_DUPLICATE'):
            groups['excluded'].append(s)
        else:
            # Unknown category — treat as excluded
            groups['excluded'].append(s)

    # Computed groups
    groups['all_not_traded'] = (
        groups['no_skip'] + groups['no_vix_gate'] +
        groups['no_friday'] + groups['no_oa_event']
    )
    groups['all_actionable'] = groups['traded'] + groups['all_not_traded']

    return groups


def _with_outcomes(rows: List[Dict]) -> List[Dict]:
    """Filter to rows that have outcome data."""
    return [s for s in rows if s.get('outcome') and s.get('overnight_move') is not None]


# ============================================================================
# SECTION BUILDERS — each returns a section dict or None
# ============================================================================

def section_data_overview(
    signals: List[Dict], parts: Dict[str, Any]
) -> Dict[str, Any]:
    """Section 0: Data Overview."""
    total = len(signals)
    excluded = len(parts['excluded'])
    actionable = len(parts['all_actionable'])
    traded = len(parts['traded'])
    not_traded = len(parts['all_not_traded'])

    # Date range
    dates = []
    for s in parts['all_actionable']:
        d = _parse_date_from_timestamp(s.get('timestamp', ''))
        if d:
            dates.append(d)
    dates.sort()
    date_range = f"{dates[0]} to {dates[-1]}" if dates else "N/A"

    # Outcome coverage
    with_outcomes = _with_outcomes(parts['all_actionable'])
    awaiting = actionable - len(with_outcomes)

    kpis = [
        {'label': 'Analyzed Rows', 'value': str(actionable), 'sentiment': 'neutral'},
        {'label': 'Trade Placed (YES)', 'value': str(traded), 'sentiment': 'neutral'},
        {'label': 'Trade Not Placed', 'value': str(not_traded), 'sentiment': 'neutral'},
        {'label': 'With Outcomes', 'value': str(len(with_outcomes)), 'sentiment': 'neutral'},
    ]

    # Breakdown table
    breakdown_rows = []
    for label, key in [
        ('Trade placed (YES)', 'traded'),
        ('Signal said SKIP', 'no_skip'),
        ('VIX Gate (VIX >= 25)', 'no_vix_gate'),
        ('Friday Gate', 'no_friday'),
        ('OA Event Gate', 'no_oa_event'),
    ]:
        count = len(parts[key])
        if count > 0:
            w_out = len(_with_outcomes(parts[key]))
            breakdown_rows.append([label, str(count), str(w_out)])

    tables = [{
        'caption': 'Row Breakdown by Trade Decision',
        'headers': ['Category', 'Rows', 'With Outcomes'],
        'rows': breakdown_rows,
        'col_classes': ['', 'num', 'num'],
    }]

    text_blocks = [
        f"Date range: {date_range}",
        f"Excluded: {excluded} rows (blank Trade_Executed or NO_DUPLICATE)",
    ]
    if awaiting > 0:
        text_blocks.append(
            f"{awaiting} rows awaiting outcome backfill (recent signals whose next trading day hasn't passed yet)."
        )

    return {
        'id': 'data-overview',
        'title': '0. Data Overview',
        'kpis': kpis,
        'text_blocks': text_blocks,
        'tables': tables,
    }


def section_trades_placed(parts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Section 1: Trades We Placed (Trade_Executed = YES)."""
    traded = _with_outcomes(parts['traded'])
    if not traded:
        return None

    wins = [s for s in traded if 'CORRECT' in s['outcome']]
    losses = [s for s in traded if 'WRONG' in s['outcome']]
    win_rate = len(wins) / len(traded) * 100

    # P&L
    total_pnl = sum(_pnl_for_trade(s['signal'], 'CORRECT' in s['outcome']) for s in traded)

    # Streak
    streak = 0
    streak_type = None
    for s in reversed(traded):
        is_win = 'CORRECT' in s['outcome']
        if streak_type is None:
            streak_type = is_win
            streak = 1
        elif is_win == streak_type:
            streak += 1
        else:
            break
    streak_label = f"{streak} {'winning' if streak_type else 'losing'}"

    kpis = [
        {'label': 'Total Trades', 'value': str(len(traded)), 'sentiment': 'neutral'},
        {'label': 'Win Rate', 'value': f"{win_rate:.1f}%",
         'sentiment': 'positive' if win_rate >= 70 else 'warning' if win_rate >= 50 else 'negative'},
        {'label': 'Est. P&L (1-lot)', 'value': f"${total_pnl:+,}",
         'sentiment': 'positive' if total_pnl > 0 else 'negative'},
        {'label': 'Current Streak', 'value': streak_label,
         'sentiment': 'positive' if streak_type else 'negative'},
    ]

    # ── Performance by Tier table ──
    tier_rows = []
    for tier in ['TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE']:
        tier_trades = [s for s in traded if s['signal'] == tier]
        if not tier_trades:
            continue
        t_wins = [s for s in tier_trades if 'CORRECT' in s['outcome']]
        t_losses = [s for s in tier_trades if 'WRONG' in s['outcome']]
        t_moves = [s['overnight_move'] for s in tier_trades if s['overnight_move'] is not None]
        t_pnl = sum(_pnl_for_trade(tier, 'CORRECT' in s['outcome']) for s in tier_trades)
        threshold = MOVE_THRESHOLDS.get(tier, 0.80)

        short_name = tier.replace('TRADE_', '')
        tier_rows.append([
            short_name,
            str(len(tier_trades)),
            str(len(t_wins)),
            str(len(t_losses)),
            _pct(len(t_wins), len(tier_trades)),
            f"{_mean(t_moves):.4f}%" if t_moves else "N/A",
            f"{max(t_moves):.4f}%" if t_moves else "N/A",
            f"{threshold:.2f}%",
            f"${t_pnl:+,}",
        ])

    tables = [{
        'caption': 'Performance by Signal Tier',
        'headers': ['Tier', 'Trades', 'Wins', 'Losses', 'Win%', 'Avg Move', 'Max Move', 'Breakeven', 'Est P&L'],
        'rows': tier_rows,
        'col_classes': ['', 'num', 'num', 'num', 'num', 'num', 'num', 'num', 'num'],
    }]

    # ── Subsections ──
    subsections = []

    # Win/Loss statistics
    win_moves = [s['overnight_move'] for s in wins if s['overnight_move'] is not None]
    loss_moves = [s['overnight_move'] for s in losses if s['overnight_move'] is not None]

    stats_rows = []
    if win_moves:
        stats_rows.append(['Winning Trades', f"{_mean(win_moves):.4f}%",
                          f"{_median(win_moves):.4f}%", f"{min(win_moves):.4f}%",
                          f"{max(win_moves):.4f}%"])
    if loss_moves:
        stats_rows.append(['Losing Trades', f"{_mean(loss_moves):.4f}%",
                          f"{_median(loss_moves):.4f}%", f"{min(loss_moves):.4f}%",
                          f"{max(loss_moves):.4f}%"])
    if stats_rows:
        subsections.append({
            'title': 'Overnight Move Statistics',
            'tables': [{
                'headers': ['', 'Avg Move', 'Median', 'Min Move', 'Max Move'],
                'rows': stats_rows,
                'col_classes': ['', 'num', 'num', 'num', 'num'],
            }],
        })

    # Blown trade details
    if losses:
        detail_items = []
        for s in losses:
            date = _parse_date_from_timestamp(s.get('timestamp', '')) or '?'
            tier = s['signal'].replace('TRADE_', '')
            move = s.get('overnight_move', 0)
            threshold = MOVE_THRESHOLDS.get(s['signal'], 0.80)
            over_by = move - threshold
            score = s.get('composite_score')
            score_str = f" | Score={score:.1f}" if score is not None else ""
            vix_val = s.get('vix')
            vix_str = f" | VIX={vix_val:.1f}" if vix_val is not None else ""
            detail_items.append({
                'text': f"{date} | {tier} | Move={move:.4f}% | Breakeven={threshold:.2f}% | Over by {over_by:+.4f}%{score_str}{vix_str}",
                'sentiment': 'negative',
            })
        subsections.append({
            'title': f'Blown Trades ({len(losses)})',
            'details': detail_items,
        })

    # Recent trend (last N vs all-time) — lowered from 20→8 trades
    recent_n = min(5, len(traded))
    if len(traded) >= 8 and recent_n >= 3:
        recent = traded[-recent_n:]
        recent_wins = sum(1 for s in recent if 'CORRECT' in s['outcome'])
        recent_rate = recent_wins / recent_n * 100
        delta = recent_rate - win_rate
        direction = "improving" if delta > 5 else "declining" if delta < -5 else "stable"
        recent_sub: Dict[str, Any] = {
            'title': f'Recent Trend (Last {recent_n} Trades)',
            'kpis': [
                {'label': f'Last {recent_n} Win Rate', 'value': f"{recent_rate:.0f}%",
                 'sentiment': 'positive' if recent_rate >= 70 else 'warning' if recent_rate >= 50 else 'negative'},
                {'label': 'All-Time Win Rate', 'value': f"{win_rate:.1f}%", 'sentiment': 'neutral'},
                {'label': 'Trend', 'value': direction.upper(),
                 'sentiment': 'positive' if direction == 'improving' else 'negative' if direction == 'declining' else 'neutral'},
            ],
        }
        if len(traded) < 20:
            recent_sub['callouts'] = [{'text': f'Low sample size ({len(traded)} trades). Treat trends with caution.', 'type': 'warning'}]
        subsections.append(recent_sub)

    # ── Enhanced P&L tracking (Phase 2, Step 2.5) ──
    if traded:
        running_pnl = []
        cumulative = 0
        best_day = float('-inf')
        worst_day = float('inf')
        for s in traded:
            day_pnl = _pnl_for_trade(s['signal'], 'CORRECT' in s['outcome'])
            cumulative += day_pnl
            running_pnl.append(cumulative)
            best_day = max(best_day, day_pnl)
            worst_day = min(worst_day, day_pnl)

        # Max drawdown
        peak = running_pnl[0]
        max_dd = 0
        for val in running_pnl:
            peak = max(peak, val)
            dd = peak - val
            max_dd = max(max_dd, dd)

        pnl_kpis = [
            {'label': 'Cumulative P&L', 'value': f"${cumulative:+,}", 'sentiment': 'positive' if cumulative > 0 else 'negative'},
            {'label': 'Best Single Day', 'value': f"${best_day:+,}", 'sentiment': 'positive'},
            {'label': 'Worst Single Day', 'value': f"${worst_day:+,}", 'sentiment': 'negative'},
            {'label': 'Max Drawdown', 'value': f"${max_dd:,}", 'sentiment': 'warning' if max_dd > 0 else 'neutral'},
        ]
        subsections.append({
            'title': 'P&L Trajectory',
            'kpis': pnl_kpis,
        })

    # P&L note
    callouts = [{
        'text': (
            f"P&L uses tier-specific estimates per 1-lot: "
            f"AGGRESSIVE +${PNL_PER_LOT['TRADE_AGGRESSIVE']['credit']}/"
            f"-${PNL_PER_LOT['TRADE_AGGRESSIVE']['max_loss']}, "
            f"NORMAL +${PNL_PER_LOT['TRADE_NORMAL']['credit']}/"
            f"-${PNL_PER_LOT['TRADE_NORMAL']['max_loss']}, "
            f"CONSERVATIVE +${PNL_PER_LOT['TRADE_CONSERVATIVE']['credit']}/"
            f"-${PNL_PER_LOT['TRADE_CONSERVATIVE']['max_loss']}. "
            f"Actual fills may differ."
        ),
        'type': 'info',
    }]

    return {
        'id': 'trades-placed',
        'title': '1. Trades We Placed',
        'kpis': kpis,
        'tables': tables,
        'callouts': callouts,
        'subsections': subsections,
    }


def section_trades_not_placed(parts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Section 2: Trades We Did Not Place."""
    all_not = _with_outcomes(parts['all_not_traded'])
    if not all_not:
        return None

    correct_skips = [s for s in all_not if 'CORRECT' in s['outcome']]
    missed = [s for s in all_not if 'WRONG' in s['outcome']]
    correct_rate = len(correct_skips) / len(all_not) * 100

    kpis = [
        {'label': 'Days Not Traded', 'value': str(len(all_not)), 'sentiment': 'neutral'},
        {'label': 'Correct to Skip', 'value': f"{correct_rate:.1f}%",
         'sentiment': 'positive' if correct_rate >= 60 else 'warning' if correct_rate >= 40 else 'negative'},
        {'label': 'Missed Opportunities', 'value': str(len(missed)),
         'sentiment': 'warning' if missed else 'positive'},
    ]

    text_blocks = [
        'A "correct skip" means the overnight move was >= 0.80% (would have been dangerous). '
        'A "missed opportunity" means the move was < 0.80% (we could have traded safely).',
    ]

    # ── By-reason subsections ──
    subsections = []
    callouts = []

    reason_groups = [
        ('NO_SKIP', 'no_skip', 'Signal Said SKIP',
         'Our composite score was >= 7.5, so the signal said SKIP.'),
        ('NO_VIX_GATE', 'no_vix_gate', 'VIX Gate (VIX >= 25)',
         'Option Alpha blocks trades when VIX >= 25.'),
        ('NO_FRIDAY', 'no_friday', 'Friday Gate',
         'Fridays are blocked to avoid weekend risk.'),
        ('NO_OA_EVENT', 'no_oa_event', 'OA Event Gate (FOMC/CPI/Early Close)',
         'Option Alpha blocks trades on FOMC, CPI, or early close days.'),
    ]

    for reason_key, parts_key, label, description in reason_groups:
        group = _with_outcomes(parts[parts_key])
        if not group:
            continue

        g_correct = [s for s in group if 'CORRECT' in s['outcome']]
        g_missed = [s for s in group if 'WRONG' in s['outcome']]
        g_moves = [s['overnight_move'] for s in group if s['overnight_move'] is not None]
        correct_pct = len(g_correct) / len(group) * 100
        missed_pct = 100 - correct_pct

        sub_kpis = [
            {'label': 'Count', 'value': str(len(group)), 'sentiment': 'neutral'},
            {'label': 'Correct to Skip', 'value': f"{correct_pct:.0f}%",
             'sentiment': 'positive' if correct_pct >= 60 else 'negative'},
            {'label': 'Missed Opps', 'value': str(len(g_missed)),
             'sentiment': 'warning' if g_missed else 'positive'},
        ]

        sub_table_rows = []
        if g_moves:
            sub_table_rows.append(['Avg Overnight Move', f"{_mean(g_moves):.4f}%"])
            sub_table_rows.append(['Max Overnight Move', f"{max(g_moves):.4f}%"])
            sub_table_rows.append(['Min Overnight Move', f"{min(g_moves):.4f}%"])

        # Extra info per reason
        if reason_key == 'NO_VIX_GATE':
            vix_vals = [s['vix'] for s in group if s.get('vix') is not None]
            if vix_vals:
                sub_table_rows.append(['Avg VIX When Gated', f"{_mean(vix_vals):.1f}"])

        sub: Dict[str, Any] = {
            'title': label,
            'kpis': sub_kpis,
            'text_blocks': [description],
        }
        if sub_table_rows:
            sub['tables'] = [{
                'headers': ['Metric', 'Value'],
                'rows': sub_table_rows,
                'col_classes': ['', 'num'],
            }]

        # Missed opportunity details
        if g_missed:
            missed_details = []
            for s in g_missed:
                date = _parse_date_from_timestamp(s.get('timestamp', '')) or '?'
                move = s.get('overnight_move', 0)
                sig = s.get('signal', '?')
                missed_details.append({
                    'text': f"{date} | Signal={sig} | Move={move:.4f}% (< 0.80% - safe to trade)",
                    'sentiment': 'warning',
                })
            sub['details'] = missed_details

        subsections.append(sub)

        # Generate callout if missed opp rate is high
        if missed_pct > 50 and len(group) >= 3:
            callouts.append({
                'text': f"{label}: {missed_pct:.0f}% were missed opportunities ({len(g_missed)}/{len(group)}). "
                        f"Consider relaxing this gate.",
                'type': 'warning',
            })

    section: Dict[str, Any] = {
        'id': 'trades-not-placed',
        'title': '2. Trades We Did Not Place',
        'kpis': kpis,
        'text_blocks': text_blocks,
        'subsections': subsections,
    }
    if callouts:
        section['callouts'] = callouts
    return section


def section_what_if(parts: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Section 3: What-If Analysis."""
    all_not = _with_outcomes(parts['all_not_traded'])
    traded = _with_outcomes(parts['traded'])
    if not all_not:
        return None

    subsections = []

    # ── What if we traded on SKIP days? ──
    skip_rows = _with_outcomes(parts['no_skip'])
    if skip_rows:
        # Evaluate using CONSERVATIVE tier (most cautious trade we could have placed)
        would_survive = sum(
            1 for s in skip_rows
            if s['overnight_move'] < MOVE_THRESHOLDS['TRADE_CONSERVATIVE']
        )
        would_blow = len(skip_rows) - would_survive
        surv_rate = would_survive / len(skip_rows) * 100
        hyp_pnl = (would_survive * PNL_PER_LOT['TRADE_CONSERVATIVE']['credit'] -
                    would_blow * PNL_PER_LOT['TRADE_CONSERVATIVE']['max_loss'])

        sub_callouts = []
        if surv_rate >= 70:
            sub_callouts.append({
                'text': f"High survival rate ({surv_rate:.0f}%) suggests SKIP threshold may be too conservative. "
                        f"You could capture ~${hyp_pnl:+,} extra per lot.",
                'type': 'positive' if hyp_pnl > 0 else 'warning',
            })

        subsections.append({
            'title': 'What if we traded on SKIP days?',
            'text_blocks': [
                f"Testing {len(skip_rows)} SKIP days as if we traded CONSERVATIVE (threshold={MOVE_THRESHOLDS['TRADE_CONSERVATIVE']:.2f}%):",
            ],
            'kpis': [
                {'label': 'Would Survive', 'value': str(would_survive), 'sentiment': 'positive'},
                {'label': 'Would Blow', 'value': str(would_blow),
                 'sentiment': 'negative' if would_blow > 0 else 'positive'},
                {'label': 'Survival Rate', 'value': f"{surv_rate:.0f}%",
                 'sentiment': 'positive' if surv_rate >= 70 else 'warning'},
                {'label': 'Hypothetical P&L', 'value': f"${hyp_pnl:+,}",
                 'sentiment': 'positive' if hyp_pnl > 0 else 'negative'},
            ],
            'callouts': sub_callouts if sub_callouts else None,
        })

    # ── What if we removed the Friday gate? ──
    friday_rows = _with_outcomes(parts['no_friday'])
    if friday_rows:
        # Use NORMAL tier as default
        would_survive = sum(
            1 for s in friday_rows
            if s['overnight_move'] < MOVE_THRESHOLDS['TRADE_NORMAL']
        )
        would_blow = len(friday_rows) - would_survive
        surv_rate = would_survive / len(friday_rows) * 100
        hyp_pnl = (would_survive * PNL_PER_LOT['TRADE_NORMAL']['credit'] -
                    would_blow * PNL_PER_LOT['TRADE_NORMAL']['max_loss'])

        subsections.append({
            'title': 'What if we removed the Friday gate?',
            'text_blocks': [
                f"Testing {len(friday_rows)} Friday nights as if we traded NORMAL (threshold={MOVE_THRESHOLDS['TRADE_NORMAL']:.2f}%):",
            ],
            'kpis': [
                {'label': 'Would Survive', 'value': str(would_survive), 'sentiment': 'positive'},
                {'label': 'Would Blow', 'value': str(would_blow),
                 'sentiment': 'negative' if would_blow > 0 else 'positive'},
                {'label': 'Survival Rate', 'value': f"{surv_rate:.0f}%",
                 'sentiment': 'positive' if surv_rate >= 70 else 'warning'},
                {'label': 'Hypothetical P&L', 'value': f"${hyp_pnl:+,}",
                 'sentiment': 'positive' if hyp_pnl > 0 else 'negative'},
            ],
        })

    # ── What if we removed the VIX gate? ──
    vix_rows = _with_outcomes(parts['no_vix_gate'])
    if vix_rows:
        # Use each row's actual signal tier
        would_survive = 0
        would_blow = 0
        for s in vix_rows:
            sig = s['signal']
            threshold = MOVE_THRESHOLDS.get(sig, 0.80)
            if sig == 'SKIP':
                threshold = MOVE_THRESHOLDS['TRADE_CONSERVATIVE']
            if s['overnight_move'] < threshold:
                would_survive += 1
            else:
                would_blow += 1

        surv_rate = would_survive / len(vix_rows) * 100 if vix_rows else 0

        sub_callouts = []
        if surv_rate < 60:
            saved = would_blow * 200  # rough estimate
            sub_callouts.append({
                'text': f"VIX gate is saving you from ~{would_blow} blown trades. Keep it.",
                'type': 'positive',
            })
        elif surv_rate >= 80:
            sub_callouts.append({
                'text': f"VIX gate may be too aggressive — {surv_rate:.0f}% would have survived. "
                        f"Consider raising from VIX >= 25 to >= 30.",
                'type': 'warning',
            })

        subsections.append({
            'title': 'What if we removed the VIX gate?',
            'text_blocks': [
                f"Testing {len(vix_rows)} VIX-gated days using each day's original signal tier:",
            ],
            'kpis': [
                {'label': 'Would Survive', 'value': str(would_survive), 'sentiment': 'positive'},
                {'label': 'Would Blow', 'value': str(would_blow),
                 'sentiment': 'negative' if would_blow > 0 else 'positive'},
                {'label': 'Survival Rate', 'value': f"{surv_rate:.0f}%",
                 'sentiment': 'positive' if surv_rate >= 70 else 'warning' if surv_rate >= 50 else 'negative'},
            ],
            'callouts': sub_callouts if sub_callouts else None,
        })

    # ── Compact tier boundary comparison ──
    all_with_outcomes = _with_outcomes(parts['all_actionable'])
    valid_for_sweep = [
        s for s in all_with_outcomes
        if s.get('composite_score') is not None
        and s.get('iv_rv_score') is not None
        and s.get('trend_score') is not None
        and s.get('gpt_score') is not None
    ]
    if len(valid_for_sweep) >= 5:
        current_correct = 0
        best_correct = 0
        best_config = (3.5, 5.0, 7.5)

        configs = []
        for agg in [3.0, 3.5, 4.0]:
            for norm in [4.5, 5.0, 5.5]:
                for cons in [7.0, 7.5, 8.0]:
                    if agg >= norm or norm >= cons:
                        continue
                    correct = 0
                    for s in valid_for_sweep:
                        score = s['composite_score']
                        if score >= cons:
                            hyp = 'SKIP'
                        elif score >= norm:
                            hyp = 'TRADE_CONSERVATIVE'
                        elif score >= agg:
                            hyp = 'TRADE_NORMAL'
                        else:
                            hyp = 'TRADE_AGGRESSIVE'
                        if _hypothetical_outcome(hyp, s['overnight_move']) == 'CORRECT':
                            correct += 1
                    rate = correct / len(valid_for_sweep) * 100
                    is_current = (agg == 3.5 and norm == 5.0 and cons == 7.5)
                    configs.append((rate, agg, norm, cons, is_current))
                    if is_current:
                        current_correct = rate

        configs.sort(key=lambda x: -x[0])
        top_3 = [c for c in configs[:3] if not c[4]][:3]  # Top 3 non-current

        boundary_rows = [
            ['CURRENT', '3.5', '5.0', '7.5', f"{current_correct:.1f}%"]
        ]
        for rate, agg, norm, cons, _ in top_3:
            delta = rate - current_correct
            boundary_rows.append([
                f"{'Better' if delta > 0 else 'Same'}",
                str(agg), str(norm), str(cons),
                f"{rate:.1f}% ({delta:+.1f}%)"
            ])

        subsections.append({
            'title': 'What if we changed tier boundaries?',
            'text_blocks': [
                f"Tested {len(configs)} boundary combinations on {len(valid_for_sweep)} signals.",
            ],
            'tables': [{
                'caption': 'Current vs Top Alternatives',
                'headers': ['Config', 'AGG <', 'NORM <', 'CONS <', 'Accuracy'],
                'rows': boundary_rows,
                'col_classes': ['', 'num', 'num', 'num', 'num'],
            }],
        })

    if not subsections:
        return None

    return {
        'id': 'what-if',
        'title': '3. What-If Analysis',
        'subsections': subsections,
    }


def section_patterns(
    signals: List[Dict], parts: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Section 4: Patterns & Insights."""
    traded_with_outcomes = _with_outcomes(parts['traded'])
    all_with_outcomes = _with_outcomes(parts['all_actionable'])

    if len(traded_with_outcomes) < 3:
        return None

    subsections = []

    # ── Day of Week ──
    for s in traded_with_outcomes:
        if not s.get('day_of_week'):
            s['day_of_week'] = _infer_day_of_week(s)

    day_data = [s for s in traded_with_outcomes if s.get('day_of_week')]
    if len(day_data) >= 5:
        day_rows = []
        day_callouts = []
        for day in ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']:
            day_trades = [s for s in day_data if s['day_of_week'] == day]
            if not day_trades:
                continue
            d_wins = sum(1 for s in day_trades if 'CORRECT' in s['outcome'])
            d_moves = [s['overnight_move'] for s in day_trades if s['overnight_move'] is not None]
            win_pct = d_wins / len(day_trades) * 100
            note = ""
            if day == 'Friday':
                note = " (blocked)"
            elif len(day_trades) >= 5 and win_pct < 50:
                note = " LOW"
                day_callouts.append({
                    'text': f"{day} has only {win_pct:.0f}% win rate with {len(day_trades)} trades. Consider adding a {day} gate.",
                    'type': 'warning',
                })
            day_rows.append([
                day + note,
                str(len(day_trades)),
                str(d_wins),
                f"{win_pct:.0f}%",
                f"{_mean(d_moves):.4f}%" if d_moves else "N/A",
            ])

        sub: Dict[str, Any] = {
            'title': 'Day of Week (Traded Days Only)',
            'tables': [{
                'headers': ['Day', 'Trades', 'Wins', 'Win%', 'Avg Move'],
                'rows': day_rows,
                'col_classes': ['', 'num', 'num', 'num', 'num'],
            }],
        }
        if day_callouts:
            sub['callouts'] = day_callouts
        subsections.append(sub)

    # ── VIX Regime ──
    vix_data = [s for s in traded_with_outcomes if s.get('vix') is not None]
    if len(vix_data) >= 5:
        vix_rows = []
        regimes = [('Low (< 15)', 0, 15), ('Normal (15-25)', 15, 25), ('High (25+)', 25, 100)]
        for label, low, high in regimes:
            regime = [s for s in vix_data if low <= s['vix'] < high]
            if not regime:
                continue
            r_wins = sum(1 for s in regime if 'CORRECT' in s['outcome'])
            r_moves = [s['overnight_move'] for s in regime if s['overnight_move'] is not None]
            vix_rows.append([
                label,
                str(len(regime)),
                str(r_wins),
                _pct(r_wins, len(regime)),
                f"{_mean(r_moves):.4f}%" if r_moves else "N/A",
            ])

        if vix_rows:
            subsections.append({
                'title': 'VIX Regime (Traded Days Only)',
                'tables': [{
                    'headers': ['VIX Regime', 'Trades', 'Wins', 'Win%', 'Avg Move'],
                    'rows': vix_rows,
                    'col_classes': ['', 'num', 'num', 'num', 'num'],
                }],
            })

    # ── Factor Effectiveness ──
    factor_data = [
        s for s in traded_with_outcomes
        if s.get('iv_rv_score') is not None
        and s.get('trend_score') is not None
        and s.get('gpt_score') is not None
        and s.get('overnight_move') is not None
    ]
    if len(factor_data) >= 5:
        wins = [s for s in factor_data if 'CORRECT' in s['outcome']]
        losses = [s for s in factor_data if 'WRONG' in s['outcome']]

        # Correlation table
        moves = [s['overnight_move'] for s in factor_data]
        corr_rows = []
        for name, key, weight in [('IV/RV', 'iv_rv_score', '30%'),
                                   ('Trend', 'trend_score', '20%'),
                                   ('GPT', 'gpt_score', '50%')]:
            scores = [s[key] for s in factor_data]
            corr = _correlation(scores, moves)
            corr_str = f"{corr:+.3f}" if corr is not None else "N/A"
            strength = ""
            if corr is not None:
                strength = ("strong" if abs(corr) > 0.5 else
                           "moderate" if abs(corr) > 0.3 else "weak")
            corr_rows.append([f"{name} ({weight})", corr_str, strength])

        sub_tables = [{
            'caption': 'Factor Correlation with Overnight Move',
            'headers': ['Factor', 'Correlation', 'Strength'],
            'rows': corr_rows,
            'col_classes': ['', 'num', ''],
        }]

        # Avg scores: wins vs losses
        if wins and losses:
            score_rows = []
            for name, key in [('IV/RV', 'iv_rv_score'), ('Trend', 'trend_score'),
                              ('GPT', 'gpt_score'), ('Composite', 'composite_score')]:
                w_avg = _mean([s[key] for s in wins if s.get(key) is not None])
                l_avg = _mean([s[key] for s in losses if s.get(key) is not None])
                delta = l_avg - w_avg
                score_rows.append([name, f"{w_avg:.2f}", f"{l_avg:.2f}", f"{delta:+.2f}"])

            sub_tables.append({
                'caption': 'Avg Factor Scores: Wins vs Losses',
                'headers': ['Factor', 'Wins Avg', 'Losses Avg', 'Delta'],
                'rows': score_rows,
                'col_classes': ['', 'num', 'num', 'num'],
            })

        subsections.append({
            'title': 'Factor Effectiveness',
            'text_blocks': [f"Analyzing {len(factor_data)} trades with complete factor data."],
            'tables': sub_tables,
        })

    # ── Factor Contribution Breakdown (Phase 2, Step 2.2) ──
    factor_contrib_data = [
        s for s in traded_with_outcomes
        if s.get('iv_rv_score') is not None
        and s.get('trend_score') is not None
        and s.get('gpt_score') is not None
    ]
    if len(factor_contrib_data) >= 3:
        contrib_rows = []
        dominant_counts = defaultdict(int)
        for s in factor_contrib_data:
            iv_c = s['iv_rv_score'] * CURRENT_WEIGHTS['iv_rv']
            tr_c = s['trend_score'] * CURRENT_WEIGHTS['trend']
            gp_c = s['gpt_score'] * CURRENT_WEIGHTS['gpt']
            total = iv_c + tr_c + gp_c
            dominant = max([('IV/RV', iv_c), ('Trend', tr_c), ('GPT', gp_c)], key=lambda x: x[1])
            dominant_counts[dominant[0]] += 1

        dom_rows = [[name, str(count), _pct(count, len(factor_contrib_data))]
                     for name, count in sorted(dominant_counts.items(), key=lambda x: -x[1])]
        subsections.append({
            'title': 'Factor Dominance',
            'text_blocks': [f"Which factor contributes the most to the composite score across {len(factor_contrib_data)} signals."],
            'tables': [{
                'headers': ['Factor', 'Dominant Count', '% of Signals'],
                'rows': dom_rows,
                'col_classes': ['', 'num', 'num'],
            }],
        })

    # ── Poke Stability ──
    poke_data = _build_poke_stability(all_with_outcomes)
    if poke_data:
        subsections.append(poke_data)

    # ── Contradiction Detection ──
    contradiction_sub = _build_contradiction_analysis(all_with_outcomes)
    if contradiction_sub:
        subsections.append(contradiction_sub)

    if not subsections:
        return None

    return {
        'id': 'patterns',
        'title': '4. Patterns & Insights',
        'subsections': subsections,
    }


def _build_poke_stability(signals_with_outcomes: List[Dict]) -> Optional[Dict[str, Any]]:
    """Build poke stability sub-section (moved from validate_outcomes.py)."""
    # Group by date
    date_groups: Dict[str, List[Dict]] = {}
    for s in signals_with_outcomes:
        date = _parse_date_from_timestamp(s.get('timestamp', ''))
        if not date:
            continue
        if date not in date_groups:
            date_groups[date] = []
        date_groups[date].append(s)

    # Only dates with 2+ signals and outcome data
    multi = {d: sigs for d, sigs in date_groups.items()
             if len(sigs) >= 2 and sigs[0].get('overnight_move') is not None}

    if not multi:
        return None

    total = len(multi)
    all_agree = 0
    first_better = 0
    later_better = 0
    same_outcome = 0

    for date_key in sorted(multi):
        sigs = multi[date_key]
        decision = sigs[0]
        latest = sigs[-1]
        overnight = decision['overnight_move']

        if decision['signal'] == latest['signal']:
            all_agree += 1
            same_outcome += 1
            continue

        r1 = _hypothetical_outcome(decision['signal'], overnight)
        r2 = _hypothetical_outcome(latest['signal'], overnight)

        if r1 == r2:
            same_outcome += 1
        elif r1 == 'CORRECT':
            first_better += 1
        else:
            later_better += 1

    disagreements = total - all_agree
    stability_pct = all_agree / total * 100 if total else 0

    poke_rows = [
        ['Total Multi-Signal Dates', str(total)],
        ['All Signals Agree', f"{all_agree} ({stability_pct:.0f}%)"],
    ]
    if disagreements > 0:
        poke_rows.append(['Disagreements', str(disagreements)])
        poke_rows.append(['First Signal Better',
                         f"{first_better} ({first_better / disagreements * 100:.0f}%)" if disagreements else "0"])
        poke_rows.append(['Later Signal Better',
                         f"{later_better} ({later_better / disagreements * 100:.0f}%)" if disagreements else "0"])
        poke_rows.append(['Same Outcome Either Way',
                         f"{same_outcome - all_agree}"])

    poke_callouts = []
    if disagreements > 0:
        if later_better > first_better:
            poke_callouts.append({
                'text': 'Later signals are consistently better. Consider delaying the trading decision.',
                'type': 'warning',
            })
        elif first_better > later_better:
            poke_callouts.append({
                'text': 'First signal timing is good. Later news does not improve decisions.',
                'type': 'positive',
            })

    result: Dict[str, Any] = {
        'title': 'Poke Stability (Multi-Signal Days)',
        'text_blocks': [
            'When multiple signals fire in the same trading window, does the first signal '
            'match the later ones? If they disagree, which was right?',
        ],
        'tables': [{
            'headers': ['Metric', 'Value'],
            'rows': poke_rows,
            'col_classes': ['', 'num'],
        }],
    }
    if poke_callouts:
        result['callouts'] = poke_callouts
    return result


def _build_contradiction_analysis(
    signals_with_outcomes: List[Dict],
) -> Optional[Dict[str, Any]]:
    """Build contradiction detection sub-section."""
    with_flags = [
        s for s in signals_with_outcomes
        if s.get('contradiction_flags')
        and s['contradiction_flags'] not in ('None', 'N/A', '')
    ]
    if not with_flags:
        return None

    no_flags = [
        s for s in signals_with_outcomes
        if not s.get('contradiction_flags')
        or s['contradiction_flags'] in ('None', 'N/A', '')
    ]

    flag_pct = len(with_flags) / len(signals_with_outcomes) * 100 if signals_with_outcomes else 0

    # Count flag types
    flag_counts = defaultdict(int)
    for s in with_flags:
        for flag in s['contradiction_flags'].split('; '):
            flag_type = flag.split(':')[0].strip()
            if flag_type:
                flag_counts[flag_type] += 1

    flag_rows = []
    for flag, count in sorted(flag_counts.items(), key=lambda x: -x[1]):
        flag_rows.append([flag, str(count)])

    tables = []
    if flag_rows:
        tables.append({
            'caption': 'Contradiction Flag Frequency',
            'headers': ['Flag Type', 'Count'],
            'rows': flag_rows,
            'col_classes': ['', 'num'],
        })

    # Compare accuracy with/without
    comp_rows = []
    if with_flags and no_flags:
        f_correct = sum(1 for s in with_flags if 'CORRECT' in s['outcome'])
        nf_correct = sum(1 for s in no_flags if 'CORRECT' in s['outcome'])
        comp_rows.append(['With Contradictions', str(len(with_flags)),
                         _pct(f_correct, len(with_flags))])
        comp_rows.append(['Without Contradictions', str(len(no_flags)),
                         _pct(nf_correct, len(no_flags))])
        tables.append({
            'caption': 'Accuracy Comparison',
            'headers': ['', 'Count', 'Accuracy'],
            'rows': comp_rows,
            'col_classes': ['', 'num', 'num'],
        })

    return {
        'title': 'Contradiction Detection',
        'text_blocks': [
            f"Contradictions fired on {len(with_flags)}/{len(signals_with_outcomes)} signals ({flag_pct:.0f}%).",
        ],
        'tables': tables,
    }


# ============================================================================
# NEW SECTIONS (Phase 2 & 3)
# ============================================================================


def section_signal_log(
    signals: List[Dict], parts: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Section 5: Per-Signal Breakdown Table (Phase 2, Step 2.1).

    Works with 1+ rows. No statistics — just show the data.
    """
    actionable = parts['all_actionable']
    if not actionable:
        return None

    table_rows = []
    for s in actionable:
        date = _parse_date_from_timestamp(s.get('timestamp', '')) or '?'
        signal = s.get('signal', '?').replace('TRADE_', '')
        composite = f"{s['composite_score']:.1f}" if s.get('composite_score') is not None else '?'
        iv_rv = f"{s['iv_rv_score']:.0f}" if s.get('iv_rv_score') is not None else '?'
        trend = f"{s['trend_score']:.0f}" if s.get('trend_score') is not None else '?'
        gpt = f"{s['gpt_score']:.0f}" if s.get('gpt_score') is not None else '?'
        vix_str = f"{s['vix']:.1f}" if s.get('vix') is not None else '?'
        vvix_str = f"{s['vvix']:.0f}" if s.get('vvix') is not None else '-'
        move = f"{s['overnight_move']:.4f}%" if s.get('overnight_move') is not None else 'pending'
        outcome = s.get('outcome', 'pending') or 'pending'
        if outcome and 'CORRECT' in outcome:
            outcome = 'OK'
        elif outcome and 'WRONG' in outcome:
            outcome = 'WRONG'

        table_rows.append([date, signal, composite, iv_rv, trend, gpt, vix_str, vvix_str, move, outcome])

    return {
        'id': 'signal-log',
        'title': '5. Signal Log (All Signals)',
        'text_blocks': [f"Complete log of {len(actionable)} actionable signals."],
        'tables': [{
            'headers': ['Date', 'Signal', 'Score', 'IV/RV', 'Trend', 'GPT', 'VIX', 'VVIX', 'Move', 'Result'],
            'rows': table_rows,
            'col_classes': ['', '', 'num', 'num', 'num', 'num', 'num', 'num', 'num', ''],
        }],
    }


def section_signal_trajectory(
    signals: List[Dict], parts: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Section 6: Signal Trajectory Over Time (Phase 2, Step 2.3).

    Shows composite score trend. Works with 2+ signals.
    """
    actionable = [s for s in parts['all_actionable'] if s.get('composite_score') is not None]
    if len(actionable) < 2:
        return None

    table_rows = []
    prev_score = None
    consecutive_dir = 0
    last_dir = None
    regime_shifts = []

    for s in actionable:
        date = _parse_date_from_timestamp(s.get('timestamp', '')) or '?'
        score = s['composite_score']
        signal = s.get('signal', '?').replace('TRADE_', '')
        delta = ''
        if prev_score is not None:
            d = score - prev_score
            delta = f"{d:+.1f}"
            # Track regime shifts
            direction = 'up' if d > 0 else 'down' if d < 0 else 'flat'
            if direction == last_dir and direction != 'flat':
                consecutive_dir += 1
            else:
                consecutive_dir = 1
                last_dir = direction
            if consecutive_dir >= 3:
                regime_shifts.append((date, direction))
        prev_score = score
        table_rows.append([date, f"{score:.1f}", delta, signal])

    callouts = []
    if regime_shifts:
        for date, direction in regime_shifts[-3:]:  # show last 3
            callouts.append({
                'text': f"Regime shift detected near {date}: 3+ consecutive {direction} moves in composite score.",
                'type': 'warning',
            })

    section: Dict[str, Any] = {
        'id': 'trajectory',
        'title': '6. Signal Trajectory',
        'text_blocks': [f"Composite score evolution across {len(actionable)} signals."],
        'tables': [{
            'headers': ['Date', 'Score', 'Delta', 'Tier'],
            'rows': table_rows,
            'col_classes': ['', 'num', 'num', ''],
        }],
    }
    if callouts:
        section['callouts'] = callouts
    return section


def section_calibration(
    parts: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Section 7: Brier Score & Signal Calibration (Phase 2, Step 2.6).

    Treats composite score as implied risk probability.
    Needs 10+ signals with outcomes.
    """
    all_with_out = _with_outcomes(parts['all_actionable'])
    valid = [s for s in all_with_out if s.get('composite_score') is not None]
    if len(valid) < 10:
        return None

    # Convert composite score to implied breach probability
    # Score 1 → ~5%, Score 5 → ~35%, Score 7.5 → ~60%, Score 10 → ~85%
    def _score_to_prob(score):
        return min(0.95, max(0.05, (score - 1) * 0.089 + 0.05))

    brier_sum = 0
    cal_bins = defaultdict(lambda: {'predicted': [], 'actual': []})
    student_t_brier_sum = 0
    student_t_count = 0

    for s in valid:
        score = s['composite_score']
        actual = 1 if 'WRONG' in s.get('outcome', '') else 0
        predicted = _score_to_prob(score)
        brier_sum += (predicted - actual) ** 2

        # Bin by score range for ECE
        bucket = min(9, int(score))  # 1-10 → bins 1-9
        cal_bins[bucket]['predicted'].append(predicted)
        cal_bins[bucket]['actual'].append(actual)

        # Student-t comparison
        if s.get('student_t_breach_prob') is not None:
            st_pred = s['student_t_breach_prob']
            student_t_brier_sum += (st_pred - actual) ** 2
            student_t_count += 1

    brier_score = brier_sum / len(valid)

    # ECE
    ece = 0
    cal_rows = []
    for bucket in sorted(cal_bins.keys()):
        data = cal_bins[bucket]
        pred_avg = _mean(data['predicted'])
        actual_avg = _mean(data['actual'])
        gap = abs(pred_avg - actual_avg)
        ece += len(data['predicted']) / len(valid) * gap
        cal_rows.append([
            f"{bucket}-{bucket + 1}",
            str(len(data['predicted'])),
            f"{pred_avg * 100:.1f}%",
            f"{actual_avg * 100:.1f}%",
            f"{gap * 100:+.1f}%",
        ])

    kpis = [
        {'label': 'Brier Score', 'value': f"{brier_score:.4f}",
         'sentiment': 'positive' if brier_score < 0.20 else 'warning' if brier_score < 0.30 else 'negative'},
        {'label': 'ECE', 'value': f"{ece:.4f}",
         'sentiment': 'positive' if ece < 0.10 else 'warning' if ece < 0.20 else 'negative'},
        {'label': 'Signals Used', 'value': str(len(valid)), 'sentiment': 'neutral'},
    ]

    subsections = []
    if student_t_count >= 5:
        st_brier = student_t_brier_sum / student_t_count
        subsections.append({
            'title': 'Heuristic vs Student-t Calibration',
            'kpis': [
                {'label': 'Heuristic Brier', 'value': f"{brier_score:.4f}", 'sentiment': 'neutral'},
                {'label': 'Student-t Brier', 'value': f"{st_brier:.4f}",
                 'sentiment': 'positive' if st_brier < brier_score else 'negative'},
                {'label': 'Student-t Signals', 'value': str(student_t_count), 'sentiment': 'neutral'},
            ],
        })

    callouts = []
    if len(valid) < 20:
        callouts.append({'text': f'Low sample size ({len(valid)} signals). Calibration metrics are preliminary.', 'type': 'warning'})

    section: Dict[str, Any] = {
        'id': 'calibration',
        'title': '7. Signal Calibration',
        'text_blocks': [
            'How well-calibrated is the composite score as a risk probability? '
            'Brier Score < 0.25 is good; ECE < 0.10 means score buckets match actual breach rates.'
        ],
        'kpis': kpis,
        'tables': [{
            'caption': 'Calibration by Score Bucket',
            'headers': ['Score Range', 'Count', 'Predicted Breach%', 'Actual Breach%', 'Gap'],
            'rows': cal_rows,
            'col_classes': ['', 'num', 'num', 'num', 'num'],
        }],
    }
    if subsections:
        section['subsections'] = subsections
    if callouts:
        section['callouts'] = callouts
    return section


def section_edge_decay(
    parts: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Section 8: Edge Decay Monitor (Phase 3, Step 3.1).

    For each signal, compare implied overnight move (VIX1D/sqrt(252)) vs realized.
    Needs 5+ signals with outcomes and VIX1D.
    """
    all_with_out = _with_outcomes(parts['all_actionable'])
    valid = [s for s in all_with_out
             if s.get('vix1d') is not None
             and s.get('overnight_move') is not None]
    if len(valid) < 5:
        return None

    ratios = []
    table_rows = []
    for s in valid:
        implied_daily = s['vix1d'] / math.sqrt(252)  # VIX1D → daily % move
        realized = s['overnight_move']
        ratio = implied_daily / realized if realized > 0.001 else None
        if ratio is not None:
            ratios.append(ratio)
        date = _parse_date_from_timestamp(s.get('timestamp', '')) or '?'
        table_rows.append([
            date,
            f"{implied_daily:.4f}%",
            f"{realized:.4f}%",
            f"{ratio:.2f}" if ratio is not None else "N/A",
        ])

    avg_ratio = _mean(ratios) if ratios else 0

    # Strategy health KPI
    if avg_ratio > 1.3:
        health = 'GREEN'
        health_sentiment = 'positive'
    elif avg_ratio > 1.0:
        health = 'YELLOW'
        health_sentiment = 'warning'
    else:
        health = 'RED'
        health_sentiment = 'negative'

    kpis = [
        {'label': 'Strategy Health', 'value': health, 'sentiment': health_sentiment},
        {'label': 'Avg Implied/Realized', 'value': f"{avg_ratio:.2f}",
         'sentiment': health_sentiment},
        {'label': 'Data Points', 'value': str(len(ratios)), 'sentiment': 'neutral'},
    ]

    callouts = []
    if health == 'RED':
        callouts.append({
            'text': 'Implied vol is NOT overestimating realized moves. Edge may be gone. Consider pausing.',
            'type': 'negative',
        })
    elif health == 'YELLOW':
        callouts.append({
            'text': 'Edge is narrowing. Monitor closely — consider tightening tiers.',
            'type': 'warning',
        })
    if len(valid) < 20:
        callouts.append({'text': f'Low sample size ({len(valid)} signals). Monitor as data grows.', 'type': 'warning'})

    section: Dict[str, Any] = {
        'id': 'edge-decay',
        'title': '8. Edge Decay Monitor',
        'text_blocks': [
            'Is implied overnight vol still overestimating realized moves? '
            'Ratio > 1.3 = healthy edge. Ratio < 1.0 = edge eroded.'
        ],
        'kpis': kpis,
        'tables': [{
            'caption': 'Implied vs Realized Overnight Move',
            'headers': ['Date', 'Implied Move', 'Realized Move', 'Ratio'],
            'rows': table_rows[-10:],  # last 10 for readability
            'col_classes': ['', 'num', 'num', 'num'],
        }],
    }
    if callouts:
        section['callouts'] = callouts
    return section


def section_new_indicators(
    parts: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Section 9: New Indicators Analysis (Phase 3, Step 3.2).

    Correlate VVIX, overnight RV, VRP trend with outcomes.
    Needs 10+ data points per indicator.
    """
    all_with_out = _with_outcomes(parts['all_actionable'])
    if len(all_with_out) < 5:
        return None

    subsections = []

    # VVIX correlation
    vvix_data = [s for s in all_with_out if s.get('vvix') is not None and s.get('overnight_move') is not None]
    if len(vvix_data) >= 10:
        vvix_vals = [s['vvix'] for s in vvix_data]
        moves = [s['overnight_move'] for s in vvix_data]
        corr = _correlation(vvix_vals, moves)

        elevated = [s for s in vvix_data if s['vvix'] > 120]
        normal = [s for s in vvix_data if s['vvix'] <= 120]

        rows = []
        if normal:
            n_wrong = sum(1 for s in normal if 'WRONG' in s['outcome'])
            rows.append(['VVIX <= 120', str(len(normal)), _pct(n_wrong, len(normal)),
                         f"{_mean([s['overnight_move'] for s in normal]):.4f}%"])
        if elevated:
            e_wrong = sum(1 for s in elevated if 'WRONG' in s['outcome'])
            rows.append(['VVIX > 120', str(len(elevated)), _pct(e_wrong, len(elevated)),
                         f"{_mean([s['overnight_move'] for s in elevated]):.4f}%"])

        sub: Dict[str, Any] = {
            'title': 'VVIX (Vol-of-Vol)',
            'text_blocks': [f"Correlation with overnight move: {corr:+.3f}" if corr is not None else "Correlation: N/A"],
            'tables': [{
                'headers': ['VVIX Regime', 'Count', 'Breach Rate', 'Avg Move'],
                'rows': rows,
                'col_classes': ['', 'num', 'num', 'num'],
            }] if rows else [],
        }
        subsections.append(sub)

    # Overnight RV correlation
    orv_data = [s for s in all_with_out if s.get('overnight_rv') is not None and s.get('overnight_move') is not None]
    if len(orv_data) >= 10:
        orv_vals = [s['overnight_rv'] for s in orv_data]
        moves = [s['overnight_move'] for s in orv_data]
        corr = _correlation(orv_vals, moves)
        subsections.append({
            'title': 'Overnight RV',
            'text_blocks': [
                f"Correlation with overnight move: {corr:+.3f}" if corr is not None else "Correlation: N/A",
                f"Mean overnight RV: {_mean(orv_vals):.2f}%",
            ],
        })

    # VRP Trend
    vrp_data = [s for s in all_with_out if s.get('vrp_trend') and s['vrp_trend'] in ('EXPANDING', 'COMPRESSING', 'STABLE')]
    if len(vrp_data) >= 10:
        vrp_rows = []
        for trend_val in ['EXPANDING', 'STABLE', 'COMPRESSING']:
            group = [s for s in vrp_data if s['vrp_trend'] == trend_val]
            if not group:
                continue
            g_wrong = sum(1 for s in group if 'WRONG' in s['outcome'])
            g_moves = [s['overnight_move'] for s in group if s['overnight_move'] is not None]
            vrp_rows.append([trend_val, str(len(group)), _pct(g_wrong, len(group)),
                            f"{_mean(g_moves):.4f}%" if g_moves else "N/A"])

        subsections.append({
            'title': 'VRP Trend',
            'tables': [{
                'headers': ['VRP Trend', 'Count', 'Breach Rate', 'Avg Move'],
                'rows': vrp_rows,
                'col_classes': ['', 'num', 'num', 'num'],
            }],
        })

    if not subsections:
        return None

    callouts = []
    if len(all_with_out) < 20:
        callouts.append({'text': 'Not enough data to activate indicators for scoring yet. Continue collecting.', 'type': 'warning'})

    section: Dict[str, Any] = {
        'id': 'new-indicators',
        'title': '9. New Indicators (Log-Only)',
        'text_blocks': ['Correlating log-only indicators with outcomes to determine which to activate for scoring.'],
        'subsections': subsections,
    }
    if callouts:
        section['callouts'] = callouts
    return section


# ============================================================================
# TEXT RENDERING (for terminal output)
# ============================================================================

def _sections_to_text(sections: List[Dict[str, Any]]) -> str:
    """Convert structured section dicts to plain text for terminal output."""
    lines = []

    lines.append("")
    lines.append("=" * 70)
    lines.append("  SPX OVERNIGHT VOL PREMIUM — PERFORMANCE REPORT")
    lines.append(f"  {datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M %p ET')}")
    lines.append("=" * 70)

    for section in sections:
        title = section.get('title', '')
        lines.append("")
        lines.append("=" * 70)
        lines.append(f"  {title}")
        lines.append("=" * 70)

        # KPIs
        kpis = section.get('kpis', [])
        if kpis:
            kpi_parts = []
            for k in kpis:
                kpi_parts.append(f"{k['label']}: {k['value']}")
            lines.append("  " + "  |  ".join(kpi_parts))

        # Text blocks
        for text in section.get('text_blocks', []):
            lines.append(f"  {text}")

        # Tables
        for table in section.get('tables', []):
            lines.append("")
            caption = table.get('caption', '')
            if caption:
                lines.append(f"  {caption}:")
            headers = table.get('headers', [])
            rows = table.get('rows', [])
            if headers and rows:
                # Calculate column widths
                all_rows = [headers] + rows
                widths = [max(len(str(r[i])) if i < len(r) else 0
                             for r in all_rows)
                          for i in range(len(headers))]
                widths = [max(w, 6) for w in widths]

                # Header
                header_line = "  " + "  ".join(
                    str(h).ljust(widths[i]) for i, h in enumerate(headers)
                )
                lines.append(header_line)
                lines.append("  " + "  ".join("-" * w for w in widths))

                # Rows
                for row in rows:
                    row_line = "  " + "  ".join(
                        str(row[i] if i < len(row) else '').ljust(widths[i])
                        for i in range(len(headers))
                    )
                    lines.append(row_line)

        # Details
        for detail in section.get('details', []):
            lines.append(f"    {detail['text']}")

        # Callouts
        for callout in section.get('callouts', []):
            ctype = callout.get('type', 'info').upper()
            lines.append(f"  [{ctype}] {callout['text']}")

        # Subsections
        for sub in section.get('subsections', []):
            sub_title = sub.get('title', '')
            lines.append("")
            lines.append(f"  --- {sub_title} ---")

            for k in sub.get('kpis', []):
                lines.append(f"    {k['label']}: {k['value']}")

            for text in sub.get('text_blocks', []):
                lines.append(f"    {text}")

            for table in sub.get('tables', []):
                caption = table.get('caption', '')
                if caption:
                    lines.append(f"    {caption}:")
                headers = table.get('headers', [])
                rows = table.get('rows', [])
                if headers and rows:
                    all_rows_t = [headers] + rows
                    widths = [max(len(str(r[i])) if i < len(r) else 0
                                 for r in all_rows_t)
                              for i in range(len(headers))]
                    widths = [max(w, 6) for w in widths]

                    lines.append("    " + "  ".join(
                        str(h).ljust(widths[i]) for i, h in enumerate(headers)
                    ))
                    lines.append("    " + "  ".join("-" * w for w in widths))
                    for row in rows:
                        lines.append("    " + "  ".join(
                            str(row[i] if i < len(row) else '').ljust(widths[i])
                            for i in range(len(headers))
                        ))

            for detail in sub.get('details', []):
                lines.append(f"      {detail['text']}")

            for callout in (sub.get('callouts') or []):
                ctype = callout.get('type', 'info').upper()
                lines.append(f"    [{ctype}] {callout['text']}")

    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  Report complete.")
    lines.append("=" * 70)
    lines.append("")

    return '\n'.join(lines)


# ============================================================================
# MAIN
# ============================================================================

def run_analysis(min_rows: int = 0) -> List[Dict[str, Any]]:
    """Run the full analysis and return list of section dicts."""
    print("Loading signal data from Google Sheets...")
    signals = load_signal_data()

    if not signals:
        print("No signal data found. Check your Google Sheets configuration.")
        return []

    parts = _partition_signals(signals)
    with_outcomes = _with_outcomes(parts['all_actionable'])
    print(f"Loaded {len(signals)} signals ({len(with_outcomes)} with outcomes)")
    print(f"  Traded: {len(parts['traded'])} | Not traded: {len(parts['all_not_traded'])} | Excluded: {len(parts['excluded'])}")

    if len(with_outcomes) < min_rows:
        print(f"Only {len(with_outcomes)} signals have outcomes (minimum {min_rows} required).")
        print("Run validate_outcomes.py first.")
        return []

    result_sections = []

    # Section 0: Data Overview (always shown)
    result_sections.append(section_data_overview(signals, parts))

    # Section 1: Trades We Placed
    s1 = section_trades_placed(parts)
    if s1:
        result_sections.append(s1)

    # Section 2: Trades We Did Not Place
    s2 = section_trades_not_placed(parts)
    if s2:
        result_sections.append(s2)

    # Section 3: What-If Analysis
    s3 = section_what_if(parts)
    if s3:
        result_sections.append(s3)

    # Section 4: Patterns & Insights
    s4 = section_patterns(signals, parts)
    if s4:
        result_sections.append(s4)

    # Section 5: Signal Log (all signals, works with 1+ rows)
    s5 = section_signal_log(signals, parts)
    if s5:
        result_sections.append(s5)

    # Section 6: Signal Trajectory (works with 2+ signals)
    s6 = section_signal_trajectory(signals, parts)
    if s6:
        result_sections.append(s6)

    # Section 7: Calibration (needs 10+ signals with outcomes)
    s7 = section_calibration(parts)
    if s7:
        result_sections.append(s7)

    # Section 8: Edge Decay Monitor (needs 5+ signals with outcomes)
    s8 = section_edge_decay(parts)
    if s8:
        result_sections.append(s8)

    # Section 9: New Indicators Analysis (needs 10+ data points)
    s9 = section_new_indicators(parts)
    if s9:
        result_sections.append(s9)

    return result_sections


if __name__ == '__main__':
    args = sys.argv[1:]

    min_rows = 0
    export_file = None

    i = 0
    while i < len(args):
        if args[i] == '--min-rows' and i + 1 < len(args):
            min_rows = int(args[i + 1])
            i += 2
        elif args[i] == '--export' and i + 1 < len(args):
            export_file = args[i + 1]
            i += 2
        elif args[i] == '--help':
            print(__doc__)
            sys.exit(0)
        else:
            print(f"Unknown argument: {args[i]}")
            print("Usage: python analyze_signals.py [--min-rows N] [--export FILE]")
            sys.exit(1)

    sections = run_analysis(min_rows=min_rows)

    if not sections:
        sys.exit(1)

    # Terminal output
    report_text = _sections_to_text(sections)
    print(report_text)

    # Export plain text if requested
    if export_file:
        with open(export_file, 'w') as f:
            f.write(report_text)
        print(f"Report saved to {export_file}")

    # Auto-save as styled HTML report
    try:
        from report_writer import save_html_report
        path = save_html_report(sections, prefix='analysis')
        print(f"\n  Report saved: {path}")
        print(f"  View in browser: open {path}")
    except Exception as e:
        print(f"\n  (Could not save HTML report: {e})")
