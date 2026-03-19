"""Backward-compat shim — delegates to desks.overnight_condors.validate_outcomes."""
from desks.overnight_condors.validate_outcomes import *
from desks.overnight_condors.validate_outcomes import (
    _evaluate_outcome,
    _parse_signal_date,
    _next_weekday,
    _get_next_trading_day,
    _fetch_spx_day,
    _fetch_spx_10am_price,
    _infer_trade_executed,
    _connect_sheet,
    backfill_outcomes,
    print_backfill_summary,
    MOVE_THRESHOLDS,
    NO_TRADE_THRESHOLD,
    OA_EXIT_PARAMS,
    OA_TIME_EXIT,
    OA_TOUCH_ITM_AMOUNT,
    OA_TOUCH_MAX_LOSS_PCT,
    TRADE_PARAMS,
    COL_TIMESTAMP,
    COL_POKE_NUMBER,
    COL_SIGNAL,
    COL_SPX_CURRENT,
    COL_VIX,
    COL_TRADE_EXECUTED,
    COL_CONTRADICTION_FLAGS,
    COL_OVERRIDE,
    COL_SCORE_ADJ,
    COL_SPX_NEXT_OPEN,
    COL_SPX_NEXT_CLOSE,
    COL_OVERNIGHT_MOVE,
    COL_OUTCOME_CORRECT,
)

if __name__ == '__main__':
    import sys
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
