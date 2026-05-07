#!/usr/bin/env python3
"""CLI shim — delegates to desks.overnight_condors.analyze_compare.

Cross-bot comparison report for the Phase 2 paper trial: side-by-side
performance across all desks + per-Phase-2-bot promotion-rule eligibility
vs Bot A as the control.

Usage:
  python analyze_compare.py                      # full comparison
  python analyze_compare.py --export FILE        # save plain-text report

Reads from the unified 'Live' tab (assumes validate_outcomes.py has been
run to populate outcome columns for each desk).
"""
from desks.overnight_condors.analyze_compare import *  # noqa: F401,F403
from desks.overnight_condors.analyze_compare import (  # noqa: F401
    run_comparison,
    desk_stats,
    bootstrap_mean_diff_ci,
    evaluate_promotion,
    DESKS_TO_COMPARE,
    CONTROL_DESK_ID,
)


if __name__ == '__main__':
    import sys
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
            print("Usage: python analyze_compare.py [--export FILE]")
            sys.exit(1)

    sections = run_comparison()
    if not sections:
        print("No data to compare.")
        sys.exit(1)

    from desks.overnight_condors.analyze_signals import _sections_to_text
    text_report = _sections_to_text(sections)
    print(text_report)

    if export_file:
        with open(export_file, 'w') as f:
            f.write(text_report)
        print(f"Report saved to {export_file}")

    try:
        from core.report_writer import save_html_report
        path = save_html_report(sections, prefix='compare')
        print(f"\n  Report saved: {path}")
        print(f"  View in browser: open {path}")
    except Exception as e:
        print(f"\n  (Could not save HTML report: {e})")
