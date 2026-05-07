#!/usr/bin/env python3
"""Cross-bot comparison report for the Phase 2 paper trial.

Produces a side-by-side table of every desk in the firm + promotion-rule
eligibility for each Phase 2 bot (B/C/D/E/F) measured against Bot A as
the control. Per the methodology doc §5, a challenger promotes to live if
ALL THREE hold:

  - ≥ 30 closed trades on the challenger
  - Mean P&L per trade ≥ 110% of Bot A's
  - Max drawdown ≤ 130% of Bot A's
  - Win rate within ±5pp of Bot A's (sanity check, not strict promotion bar)

Demotion (any single condition triggers pause-and-review):
  - Max drawdown > 200% of Bot A's

Adds bootstrap confidence interval for the mean-P&L-difference vs Bot A,
which contextualises whether the observed gap is statistical noise or
a meaningful signal at the current sample size.

Usage:
  python -m desks.overnight_condors.analyze_compare
  python -m desks.overnight_condors.analyze_compare --export reports/compare.txt
"""
import random
import sys
from typing import Any, Dict, List, Optional, Tuple

from desks.overnight_condors.analyze_signals import (
    load_signal_data,
    _partition_signals,
    _with_outcomes,
    _pnl_for_trade,
    _mean,
    _stdev,
    _sections_to_text,
)


# ─────────────────────────────────────────────────────────────────────────────
# Desk catalog — what bots to include in the comparison + display order
# ─────────────────────────────────────────────────────────────────────────────
# Bot A is the control; everything else is measured against it.
# Order chosen to match the methodology doc / playbook.
DESKS_TO_COMPARE: List[Tuple[str, str]] = [
    ('overnight_condors',         'Bot A — Symmetric IC (control)'),
    ('asymmetric_condors',        'Bot B — Asymmetric IC'),
    ('overnight_putspread',       'Bot C — Put-Spread'),
    ('overnight_condors_vvix',    'Bot D — VVIX-Sized'),
    ('overnight_condors_dow',     'Bot E — DOW-Sized'),
    ('overnight_condors_max',     'Bot F — Thesis-Max'),
]

CONTROL_DESK_ID = 'overnight_condors'

# Promotion thresholds (methodology doc §5)
PROMOTION_MIN_TRADES = 30
PROMOTION_PNL_RATIO = 1.10           # mean P&L per trade ≥ 110% of control
PROMOTION_DD_RATIO  = 1.30           # max DD ≤ 130% of control
PROMOTION_WIN_RATE_TOLERANCE = 5.0   # win rate within ±5pp of control
DEMOTION_DD_RATIO   = 2.00           # max DD > 200% of control → pause


# ─────────────────────────────────────────────────────────────────────────────
# Per-desk stats computation
# ─────────────────────────────────────────────────────────────────────────────
def _max_drawdown(pnl_series: List[float]) -> float:
    """Compute peak-to-trough drawdown (always non-negative).

    Returns the absolute dollar amount of the deepest underwater stretch
    in the cumulative P&L curve. Useful for risk comparison across bots.
    """
    if not pnl_series:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for x in pnl_series:
        cumulative += x
        peak = max(peak, cumulative)
        dd = peak - cumulative
        max_dd = max(max_dd, dd)
    return max_dd


def desk_stats(desk_id: str) -> Optional[Dict[str, Any]]:
    """Compute all comparison metrics for one desk. Returns None if no data."""
    signals = load_signal_data(desk_filter=desk_id)
    if not signals:
        return None

    parts = _partition_signals(signals)
    traded = parts.get('traded', [])
    closed = [t for t in traded
              if 'CORRECT' in (t.get('outcome') or '')
              or 'WRONG' in (t.get('outcome') or '')]

    if not closed:
        return {
            'desk_id': desk_id, 'rows': len(signals), 'traded': len(traded),
            'closed': 0, 'wins': 0, 'win_rate': 0.0,
            'mean_pnl': 0.0, 'total_pnl': 0.0, 'max_dd': 0.0,
            'pnls': [],
        }

    wins = sum(1 for r in closed if 'CORRECT' in (r.get('outcome') or ''))
    pnls = [_pnl_for_trade(r['signal'], 'CORRECT' in (r.get('outcome') or ''),
                           r.get('contracts') or 1)
            for r in closed]
    return {
        'desk_id': desk_id,
        'rows': len(signals),
        'traded': len(traded),
        'closed': len(closed),
        'wins': wins,
        'win_rate': 100.0 * wins / len(closed),
        'mean_pnl': _mean(pnls),
        'total_pnl': sum(pnls),
        'max_dd': _max_drawdown(pnls),
        'pnls': pnls,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Bootstrap confidence interval for mean P&L difference
# ─────────────────────────────────────────────────────────────────────────────
def bootstrap_mean_diff_ci(
    pnls_a: List[float],
    pnls_b: List[float],
    iterations: int = 10000,
    confidence: float = 0.95,
    seed: Optional[int] = 42,
) -> Optional[Tuple[float, float, float]]:
    """Bootstrap CI for (mean(B) − mean(A)).

    Resamples each P&L list with replacement `iterations` times, computes
    the difference of means each time, and returns the (low, point_est, high)
    of the difference distribution at the requested confidence level.

    If the CI excludes 0, the difference is statistically meaningful at this
    sample size. If it includes 0, more data is needed before drawing a
    promotion conclusion.

    Returns None if either input is empty.
    """
    if not pnls_a or not pnls_b:
        return None
    rng = random.Random(seed)
    n_a, n_b = len(pnls_a), len(pnls_b)
    diffs: List[float] = []
    for _ in range(iterations):
        sa = [pnls_a[rng.randrange(n_a)] for _ in range(n_a)]
        sb = [pnls_b[rng.randrange(n_b)] for _ in range(n_b)]
        diffs.append(_mean(sb) - _mean(sa))
    diffs.sort()
    alpha = (1.0 - confidence) / 2.0
    lo = diffs[int(alpha * iterations)]
    hi = diffs[int((1.0 - alpha) * iterations) - 1]
    point = _mean(diffs)
    return (lo, point, hi)


# ─────────────────────────────────────────────────────────────────────────────
# Promotion-rule evaluation
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_promotion(
    challenger: Dict[str, Any], control: Dict[str, Any]
) -> Dict[str, Any]:
    """Evaluate whether `challenger` meets each promotion criterion vs control.

    Returns a dict of {criterion → {pass: bool, detail: str}} suitable for
    rendering as a row of pass/fail badges.
    """
    result: Dict[str, Any] = {}

    # Sample size
    closed = challenger['closed']
    result['sample_size'] = {
        'pass': closed >= PROMOTION_MIN_TRADES,
        'detail': f"{closed}/{PROMOTION_MIN_TRADES}",
    }

    # Mean P&L per trade
    if control['mean_pnl'] != 0:
        ratio = challenger['mean_pnl'] / abs(control['mean_pnl'])
        # Sign matters: control negative + challenger less negative is GOOD
        # but ratio math gets confusing. Simplify: compare directly.
        better_pnl = challenger['mean_pnl'] >= PROMOTION_PNL_RATIO * control['mean_pnl']
    else:
        ratio = float('inf') if challenger['mean_pnl'] > 0 else 0.0
        better_pnl = challenger['mean_pnl'] > 0
    result['mean_pnl'] = {
        'pass': better_pnl,
        'detail': (f"${challenger['mean_pnl']:+,.0f} vs "
                   f"${control['mean_pnl']:+,.0f} (need ≥110%)"),
    }

    # Max drawdown
    if control['max_dd'] > 0:
        dd_ratio = challenger['max_dd'] / control['max_dd']
        ok_dd = dd_ratio <= PROMOTION_DD_RATIO
        bad_dd = dd_ratio > DEMOTION_DD_RATIO
    else:
        dd_ratio = float('inf') if challenger['max_dd'] > 0 else 0.0
        ok_dd = challenger['max_dd'] == 0
        bad_dd = challenger['max_dd'] > 0
    result['max_dd'] = {
        'pass': ok_dd,
        'detail': (f"${challenger['max_dd']:,.0f} vs "
                   f"${control['max_dd']:,.0f} ({dd_ratio:.0%}, need ≤130%)"),
        'demotion': bad_dd,
    }

    # Win rate within tolerance
    win_diff = abs(challenger['win_rate'] - control['win_rate'])
    result['win_rate'] = {
        'pass': win_diff <= PROMOTION_WIN_RATE_TOLERANCE,
        'detail': (f"{challenger['win_rate']:.1f}% vs {control['win_rate']:.1f}% "
                   f"(±{win_diff:.1f}pp, need ≤±5pp)"),
    }

    result['overall_promote'] = (
        result['sample_size']['pass']
        and result['mean_pnl']['pass']
        and result['max_dd']['pass']
        and result['win_rate']['pass']
    )
    result['demotion_triggered'] = result['max_dd'].get('demotion', False)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Section builders (match the section dict format used by analyze_signals)
# ─────────────────────────────────────────────────────────────────────────────
def section_overview(stats_by_desk: Dict[str, Optional[Dict[str, Any]]]) -> Dict[str, Any]:
    """Section 0: cross-bot summary table."""
    rows: List[List[str]] = []
    for desk_id, label in DESKS_TO_COMPARE:
        s = stats_by_desk.get(desk_id)
        if s is None:
            rows.append([label, '— no data —', '—', '—', '—', '—', '—'])
            continue
        rows.append([
            label,
            str(s['rows']),
            str(s['traded']),
            str(s['closed']),
            f"{s['win_rate']:.1f}%" if s['closed'] else '—',
            f"${s['mean_pnl']:+,.0f}" if s['closed'] else '—',
            f"${s['max_dd']:,.0f}" if s['closed'] else '—',
        ])

    total_rows = sum((stats_by_desk[d['desk_id']] or {}).get('rows', 0)
                     for d, _ in [(d, l) for d, l in DESKS_TO_COMPARE]
                     if stats_by_desk.get(d['desk_id'] if isinstance(d, dict) else d))
    # Simpler total
    total_rows = sum((s.get('rows', 0)) for s in stats_by_desk.values() if s)
    total_closed = sum((s.get('closed', 0)) for s in stats_by_desk.values() if s)

    kpis = [
        {'label': 'Desks compared', 'value': str(sum(1 for s in stats_by_desk.values() if s)),
         'sentiment': 'neutral'},
        {'label': 'Total rows', 'value': str(total_rows), 'sentiment': 'neutral'},
        {'label': 'Total closed trades', 'value': str(total_closed), 'sentiment': 'neutral'},
    ]

    return {
        'id': 'compare-overview',
        'title': '0. Cross-Bot Summary',
        'kpis': kpis,
        'text_blocks': [
            "All metrics derived from the unified 'Live' tab in Google Sheets, "
            "filtered by Desk_ID. P&L is structurally estimated (Bot A's tier "
            "credit/max-loss × Contracts), not actual OA fill data — useful for "
            "RELATIVE comparison; absolute dollars are estimates.",
        ],
        'tables': [{
            'caption': 'Per-desk performance summary',
            'headers': ['Desk', 'Rows', 'Traded', 'Closed', 'Win %',
                        'Mean P&L/trade', 'Max DD'],
            'rows': rows,
            'col_classes': ['', 'num', 'num', 'num', 'num', 'num', 'num'],
        }],
    }


def section_promotion(
    stats_by_desk: Dict[str, Optional[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Section 1: per-Phase-2-bot promotion-rule evaluation vs control."""
    control = stats_by_desk.get(CONTROL_DESK_ID)
    if control is None or control['closed'] == 0:
        return {
            'id': 'compare-promotion',
            'title': '1. Promotion Rules vs Bot A (Control)',
            'kpis': [],
            'text_blocks': [
                "No closed trades on Bot A (the control). Promotion-rule "
                "evaluation requires control data — run validate_outcomes.py "
                "and accumulate trades on Bot A first."
            ],
            'tables': [],
        }

    rows: List[List[str]] = []
    for desk_id, label in DESKS_TO_COMPARE:
        if desk_id == CONTROL_DESK_ID:
            continue
        challenger = stats_by_desk.get(desk_id)
        if challenger is None or challenger['closed'] == 0:
            rows.append([label, '— no data —', '—', '—', '—', '—'])
            continue
        ev = evaluate_promotion(challenger, control)

        def mark(c: Dict[str, Any]) -> str:
            return ('✅' if c['pass'] else '❌') + ' ' + c['detail']

        verdict = '🚀 PROMOTE' if ev['overall_promote'] else (
            '⚠ DEMOTE' if ev['demotion_triggered'] else '⏳ continue trial')
        rows.append([
            label,
            mark(ev['sample_size']),
            mark(ev['mean_pnl']),
            mark(ev['max_dd']),
            mark(ev['win_rate']),
            verdict,
        ])

    return {
        'id': 'compare-promotion',
        'title': '1. Promotion Rules vs Bot A (Control)',
        'kpis': [],
        'text_blocks': [
            f"Promotion gates (all must hold): {PROMOTION_MIN_TRADES}+ closed "
            f"trades, mean P&L ≥ {PROMOTION_PNL_RATIO:.0%} of control, max DD "
            f"≤ {PROMOTION_DD_RATIO:.0%} of control, win rate within "
            f"±{PROMOTION_WIN_RATE_TOLERANCE:.0f}pp of control.",
            f"Demotion (any single trigger): max DD > {DEMOTION_DD_RATIO:.0%} "
            f"of control → pause for review.",
        ],
        'tables': [{
            'caption': 'Promotion-rule evaluation per challenger',
            'headers': ['Desk', 'Sample size (≥30)', 'Mean P&L vs control',
                        'Max DD vs control', 'Win rate ±5pp', 'Verdict'],
            'rows': rows,
            'col_classes': ['', '', '', '', '', ''],
        }],
    }


def section_bootstrap_ci(
    stats_by_desk: Dict[str, Optional[Dict[str, Any]]],
) -> Optional[Dict[str, Any]]:
    """Section 2: bootstrap CI for mean P&L difference vs control."""
    control = stats_by_desk.get(CONTROL_DESK_ID)
    if control is None or len(control.get('pnls', [])) < 5:
        return None

    rows: List[List[str]] = []
    has_meaningful_diff = False
    for desk_id, label in DESKS_TO_COMPARE:
        if desk_id == CONTROL_DESK_ID:
            continue
        challenger = stats_by_desk.get(desk_id)
        if challenger is None or len(challenger.get('pnls', [])) < 5:
            rows.append([label, '— need ≥5 trades —', '', '', ''])
            continue
        ci = bootstrap_mean_diff_ci(control['pnls'], challenger['pnls'])
        if ci is None:
            rows.append([label, '—', '', '', ''])
            continue
        lo, point, hi = ci
        excludes_zero = (lo > 0) or (hi < 0)
        if excludes_zero:
            has_meaningful_diff = True
        verdict = ('signal' if excludes_zero else 'noise') + (
            ' (challenger ahead)' if point > 0 else ' (challenger behind)' if excludes_zero else ''
        )
        rows.append([
            label,
            str(challenger['closed']),
            f"${point:+,.0f}",
            f"[${lo:+,.0f}, ${hi:+,.0f}]",
            verdict,
        ])

    text_blocks = [
        "Bootstrap CI on (challenger mean − control mean) per trade. 10,000 "
        "resamples, 95% confidence. If the interval EXCLUDES $0, the gap is "
        "statistically meaningful at current sample size; if INCLUDES $0, "
        "more data needed before drawing conclusions.",
    ]
    if not has_meaningful_diff:
        text_blocks.append(
            "All CIs include $0 — no challenger has yet shown a statistically "
            "distinguishable difference vs the control. Continue paper trial."
        )

    return {
        'id': 'compare-bootstrap',
        'title': '2. Bootstrap Confidence Intervals',
        'kpis': [],
        'text_blocks': text_blocks,
        'tables': [{
            'caption': 'Mean P&L per-trade difference vs Bot A (95% bootstrap CI)',
            'headers': ['Desk', 'Trades', 'Point estimate', '95% CI', 'Verdict'],
            'rows': rows,
            'col_classes': ['', 'num', 'num', '', ''],
        }],
    }


def run_comparison() -> List[Dict[str, Any]]:
    """Build the full cross-bot comparison report.

    Loads each desk's data once, computes summary stats, then assembles
    the section list. Returns a list of section dicts compatible with
    `_sections_to_text` from analyze_signals (and HTML report writer).
    """
    print("Loading data for all desks...")
    stats_by_desk: Dict[str, Optional[Dict[str, Any]]] = {}
    for desk_id, label in DESKS_TO_COMPARE:
        print(f"  - {desk_id} ({label})")
        stats_by_desk[desk_id] = desk_stats(desk_id)

    sections: List[Dict[str, Any]] = []
    sections.append(section_overview(stats_by_desk))
    promo = section_promotion(stats_by_desk)
    if promo:
        sections.append(promo)
    boot = section_bootstrap_ci(stats_by_desk)
    if boot:
        sections.append(boot)
    return sections


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    args = sys.argv[1:]
    export_file = None
    i = 0
    while i < len(args):
        if args[i] == '--export' and i + 1 < len(args):
            export_file = args[i + 1]
            i += 2
        elif args[i] == '--help':
            print(__doc__)
            sys.exit(0)
        else:
            print(f"Unknown argument: {args[i]}")
            print("Usage: python -m desks.overnight_condors.analyze_compare [--export FILE]")
            sys.exit(1)

    sections = run_comparison()
    if not sections:
        print("No data to compare.")
        sys.exit(1)

    text_report = _sections_to_text(sections)
    print(text_report)

    if export_file:
        with open(export_file, 'w') as f:
            f.write(text_report)
        print(f"Report saved to {export_file}")

    # Auto-save styled HTML
    try:
        from core.report_writer import save_html_report
        path = save_html_report(sections, prefix='compare')
        print(f"\n  Report saved: {path}")
        print(f"  View in browser: open {path}")
    except Exception as e:
        print(f"\n  (Could not save HTML report: {e})")
