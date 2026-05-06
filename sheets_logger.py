"""Backward-compat shim — keeps desk 1's SHEET_HEADERS and log_signal interface.

Delegates actual sheet writing to core.sheets.log_signal(tab, headers, row).
"""
import logging
from typing import Any, Dict, List, Optional

from core.sheets import log_signal as _core_log_signal

logger = logging.getLogger(__name__)

# Header row for the signal log sheet (same order as row values)
SHEET_HEADERS = [
    "Timestamp_ET",
    "Poke_Number",
    "Signal",
    "Should_Trade",
    "Reason",
    "Composite_Score",
    "Category",
    "IV_RV_Score",
    "IV_RV_Ratio",
    "VIX1D",
    "Realized_Vol_10d",
    "Trend_Score",
    "Trend_5d_Chg_Pct",
    "GPT_Score",
    "GPT_Category",
    "GPT_Key_Risk",
    "Webhook_Success",
    "SPX_Current",
    "VIX",
    "Trade_Executed",
    "Raw_Articles",
    "Sent_To_GPT",
    "GPT_Reasoning",
    # GPT cost tracking
    "GPT_Tokens",
    "GPT_Cost",
    # Contradiction detection
    "Contradiction_Flags",
    "Override_Applied",
    "Score_Adjustment",
    # Outcome tracking columns (filled later by validate_outcomes.py)
    # SPX_Next_Open = exit price: 10 AM ET minute data when available, else daily open
    "SPX_Next_Open",
    "SPX_Next_Close",
    "Overnight_Move_Pct",
    "Outcome_Correct",
    # ── Enriched logging columns (appended at END to avoid shifting existing data) ──
    "Day_Of_Week",
    "IV_RV_Base_Score",
    "RV_Modifier",
    "Term_Modifier",
    "Term_Structure_Ratio",
    "Trend_Base_Score",
    "Intraday_Modifier",
    "Intraday_Range_Pct",
    "GPT_Raw_Score",
    "GPT_Direction_Risk",
    "Earnings_Modifier",
    "Earnings_Tickers",
    "GPT_Pre_Earnings_Score",
    "Pass1_Composite",
    "Pass1_Signal",
    "Pass2_Composite",
    "Pass2_Signal",
    "Passes_Agreed",
    # ── Phase 1: Log-only indicators (appended at END) ──
    "VVIX",
    "VVIX_Elevated",
    "Overnight_RV",
    "IV_Overnight_RV_Ratio",
    "Blended_Overnight_Vol",
    "StudentT_Breach_Prob",
    "StudentT_Nu",
    "VRP_Trend",
    # ── Phase 2: Multi-bot parallel paper trial (appended at END) ──
    "Desk_ID",            # Which bot fired: overnight_condors / asymmetric_condors / overnight_putspread / overnight_condors_vvix / overnight_condors_dow
    "Structure_Label",    # Human-readable structure tag: "IC_25pt_0.16d", "asymmetric_IC", "put_spread", etc.
    "Routed_Tier",        # Final tier label sent to OA after signal transform (differs from Signal for VVIX/DOW bots)
    "VVIX_Bucket",        # LOW / NORMAL / HIGH / EXTREME (Bot D only; blank for others)
    "DOW_Multiplier",     # Sizing multiplier from day-of-week: 1.0 / 1.5 / 0.0 (Bot E only; blank for others)
]


def _ts_day_of_week(timestamp: str) -> str:
    """Extract day of week from timestamp string (e.g. 'Monday')."""
    try:
        from datetime import datetime as _dt
        for fmt in ["%Y-%m-%d %I:%M:%S %p %Z", "%Y-%m-%d %I:%M:%S %p EST",
                     "%Y-%m-%d %I:%M:%S %p EDT", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"]:
            try:
                dt = _dt.strptime(timestamp.strip(), fmt)
                return dt.strftime('%A')
            except ValueError:
                continue
        return ""
    except Exception:
        return ""


def _format_earnings_tickers(earnings: Dict[str, Any]) -> str:
    """Format earnings tickers for Sheets column."""
    tickers = earnings.get('reporting_today', []) + earnings.get('reporting_tomorrow', [])
    return ', '.join(tickers) if tickers else 'None'


def log_signal(
    *,
    timestamp: str,
    signal: Dict[str, Any],
    composite: Dict[str, Any],
    iv_rv: Dict[str, Any],
    trend: Dict[str, Any],
    gpt: Dict[str, Any],
    spx_current: float,
    vix1d_current: float,
    filter_stats: Dict[str, Any],
    webhook_success: bool,
    contradictions: Optional[Dict[str, Any]] = None,
    vix_current: Optional[float] = None,
    trade_executed: str = "",
    poke_number: int = 1,
    # Enriched logging (all optional with defaults for backward compatibility)
    earnings: Optional[Dict[str, Any]] = None,
    confirmation_pass: Optional[Dict[str, Any]] = None,
    # Phase 2 multi-bot fields (all optional; default to "" for back-compat with desk 1 calls)
    desk_id: str = "overnight_condors",
    structure_label: str = "IC_default",
    routed_tier: str = "",
    vvix_bucket: str = "",
    dow_multiplier: str = "",
) -> None:
    """Append one signal row to the configured Google Sheet. No-op if not configured; never raises."""
    print("[Sheets] log_signal called")

    reasoning = (gpt.get("reasoning") or "")[:500]

    # Contradiction fields
    if contradictions:
        flags_str = "; ".join(contradictions.get("contradiction_flags", [])) or "None"
        override = contradictions.get("override_signal") or "None"
        adj = contradictions.get("score_adjustment", 0)
    else:
        flags_str = "N/A"
        override = "N/A"
        adj = 0

    row: List[Any] = [
        timestamp,
        poke_number,
        signal.get("signal", ""),
        signal.get("should_trade", False),
        signal.get("reason", ""),
        composite.get("score", ""),
        composite.get("category", ""),
        iv_rv.get("score", ""),
        iv_rv.get("iv_rv_ratio", ""),
        iv_rv.get("implied_vol", ""),
        iv_rv.get("realized_vol", ""),
        trend.get("score", ""),
        f"{(trend.get('change_5d') or 0) * 100:+.2f}%" if trend.get("change_5d") is not None else "",
        gpt.get("score", ""),
        gpt.get("category", ""),
        gpt.get("key_risk", ""),
        webhook_success,
        spx_current,
        vix_current if vix_current is not None else "",
        trade_executed,
        filter_stats.get("raw_articles", ""),
        filter_stats.get("sent_to_gpt", ""),
        reasoning,
        # GPT cost columns
        gpt.get("token_usage", {}).get("total", ""),
        f"${gpt.get('token_usage', {}).get('cost', 0):.4f}" if gpt.get("token_usage", {}).get("cost") else "",
        # Contradiction columns
        flags_str,
        override,
        adj,
        # Outcome columns — left blank, filled by validate_outcomes.py
        "",  # SPX_Next_Open
        "",  # SPX_Next_Close
        "",  # Overnight_Move_Pct
        "",  # Outcome_Correct
        # ── Enriched logging columns ──
        _ts_day_of_week(timestamp),                                             # Day_Of_Week
        iv_rv.get("base_score", ""),                                            # IV_RV_Base_Score
        iv_rv.get("rv_modifier", ""),                                           # RV_Modifier
        iv_rv.get("term_modifier", ""),                                         # Term_Modifier
        iv_rv.get("term_structure_ratio", ""),                                  # Term_Structure_Ratio
        trend.get("base_score", ""),                                            # Trend_Base_Score
        trend.get("intraday_modifier", ""),                                     # Intraday_Modifier
        f"{(trend.get('intraday_range') or 0) * 100:.2f}%" if trend.get("intraday_range") is not None else "",  # Intraday_Range_Pct
        gpt.get("raw_score", ""),                                              # GPT_Raw_Score
        gpt.get("direction_risk", ""),                                          # GPT_Direction_Risk
        earnings.get("risk_modifier", "") if earnings else "",                  # Earnings_Modifier
        _format_earnings_tickers(earnings) if earnings else "",                 # Earnings_Tickers
        gpt.get("pre_earnings_score", ""),                                     # GPT_Pre_Earnings_Score
        confirmation_pass.get("pass1_composite", "") if confirmation_pass else "",  # Pass1_Composite
        confirmation_pass.get("pass1_signal", "") if confirmation_pass else "",     # Pass1_Signal
        confirmation_pass.get("pass2_composite", "") if confirmation_pass else "",  # Pass2_Composite
        confirmation_pass.get("pass2_signal", "") if confirmation_pass else "",     # Pass2_Signal
        confirmation_pass.get("passes_agreed", "") if confirmation_pass else "",    # Passes_Agreed
        # ── Phase 1: Log-only indicators ──
        iv_rv.get("vvix", ""),                                                        # VVIX
        iv_rv.get("vvix_elevated", ""),                                               # VVIX_Elevated
        iv_rv.get("overnight_rv", ""),                                                 # Overnight_RV
        iv_rv.get("iv_overnight_rv_ratio", ""),                                        # IV_Overnight_RV_Ratio
        iv_rv.get("blended_overnight_vol", ""),                                        # Blended_Overnight_Vol
        iv_rv.get("student_t_breach_prob", ""),                                        # StudentT_Breach_Prob
        iv_rv.get("student_t_nu", ""),                                                 # StudentT_Nu
        iv_rv.get("vrp_trend", ""),                                                    # VRP_Trend
        # ── Phase 2: Multi-bot parallel paper trial ──
        desk_id,                                                                       # Desk_ID
        structure_label,                                                               # Structure_Label
        routed_tier or signal.get("signal", ""),                                       # Routed_Tier (falls back to original signal if no transform)
        vvix_bucket,                                                                   # VVIX_Bucket
        dow_multiplier,                                                                # DOW_Multiplier
    ]

    _core_log_signal("Sheet1", SHEET_HEADERS, row)
