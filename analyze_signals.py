#!/usr/bin/env python3
"""Backward-compat CLI shim — delegates to desks.overnight_condors.analyze_signals.

The full implementation lives at desks/overnight_condors/analyze_signals.py.
This shim exists so `python analyze_signals.py` keeps working from the project
root, mirroring the pattern used by validate_outcomes.py and signal_engine.py.

For new code, import directly from the desk module:
    from desks.overnight_condors.analyze_signals import run_analysis, load_signal_data

Usage (CLI):
  python analyze_signals.py                    # full analysis (Bot A by default)
  python analyze_signals.py --min-rows 30      # require N rows with outcomes
  python analyze_signals.py --export FILE      # save plain-text report
  python analyze_signals.py --desk <desk_id>   # analyze a specific desk
  python analyze_signals.py --desk all         # all desks blended (less useful)
"""
# Re-export the public + commonly-used private symbols so any external code
# that imports from `analyze_signals` (rather than the desk path) still works.
from desks.overnight_condors.analyze_signals import *  # noqa: F401,F403
from desks.overnight_condors.analyze_signals import (  # noqa: F401
    # Public API
    run_analysis,
    load_signal_data,
    section_multibot_breakdown,
    # Constants
    MOVE_THRESHOLDS,
    NO_TRADE_THRESHOLD,
    PNL_PER_LOT,
    CURRENT_WEIGHTS,
    TIER_BOUNDARIES,
    # Helpers some callers might use
    _connect_sheet,
    _safe_float,
    _safe_int,
    _get_col,
    _partition_signals,
    _with_outcomes,
    _sections_to_text,
    _pnl_for_trade,
    _base_tier_for_pnl,
    _bucket_stats,
)


if __name__ == '__main__':
    import sys
    args = sys.argv[1:]

    min_rows = 0
    export_file = None
    desk_filter = 'overnight_condors'  # default: Bot A

    i = 0
    while i < len(args):
        if args[i] == '--min-rows' and i + 1 < len(args):
            min_rows = int(args[i + 1])
            i += 2
        elif args[i] == '--export' and i + 1 < len(args):
            export_file = args[i + 1]
            i += 2
        elif args[i] == '--desk' and i + 1 < len(args):
            desk_filter = args[i + 1] if args[i + 1] != 'all' else ''
            i += 2
        elif args[i] == '--help':
            print(__doc__)
            sys.exit(0)
        else:
            print(f"Unknown argument: {args[i]}")
            print("Usage: python analyze_signals.py [--min-rows N] [--export FILE] "
                  "[--desk <desk_id|all>]")
            sys.exit(1)

    sections = run_analysis(min_rows=min_rows, desk_filter=desk_filter)

    if not sections:
        sys.exit(1)

    # Terminal output
    report_text = _sections_to_text(sections)
    print(report_text)

    # Plain-text export if requested
    if export_file:
        with open(export_file, 'w') as f:
            f.write(report_text)
        print(f"Report saved to {export_file}")

    # Auto-save styled HTML report
    try:
        from core.report_writer import save_html_report
        path = save_html_report(sections, prefix='analysis')
        print(f"\n  Report saved: {path}")
        print(f"  View in browser: open {path}")
    except Exception as e:
        print(f"\n  (Could not save HTML report: {e})")
