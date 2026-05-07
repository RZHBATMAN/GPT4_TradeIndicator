"""Unified signal logger for the firm — every desk writes to the "live" tab.

This module is the single entry point every desk's run_signal_cycle() uses to
log a signal to the Google Sheet. It owns:

  - the canonical SHEET_HEADERS list (46 columns, in inspection order)
  - the SHEET_TAB constant ("live")
  - log_signal() which builds a name-keyed payload dict and dispatches to
    core.sheets.log_signal_dict() — robust to column reordering and surfaces
    schema drift via Slack alerts

Schema design and column-by-column rationale: see
~/.claude/projects/.../memory/feedback_sheets_columns.md and the conversation
transcripts of 2026-05-06 / 2026-05-07.
"""
import logging
import os
import subprocess
from typing import Any, Dict, Optional

from core.sheets import log_signal_dict as _core_log_signal_dict

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────────

SHEET_TAB = "live"

SHEET_HEADERS = [
    # 1. Identity (4)
    "Timestamp_ET",
    "Desk_ID",
    "Poke_Number",
    "Day_Of_Week",

    # 2. Signal output (6)
    "Signal",
    "Routed_Tier",
    "Composite_Score",
    "Category",
    "Reason",
    "Skip_Reason",

    # 3. Structure & sizing (7)
    "Structure_Label",
    "Contracts",
    "VVIX_Bucket",
    "VVIX_Percentile",
    "VVIX_Bucket_Source",
    "DOW_Multiplier",
    "Hedge_Attached",

    # 4. Factor inputs (8)
    "IV_RV_Score",
    "IV_RV_Ratio",
    "Trend_Score",
    "Trend_5d_Chg_Pct",
    "Intraday_Range_Pct",
    "GPT_Score",
    "GPT_Direction_Risk",
    "GPT_Key_Risk",

    # 5. Market context (6)
    "SPX_Current",
    "VIX1D",
    "VIX",
    "VVIX",
    "Realized_Vol_10d",
    "Term_Structure_Ratio",

    # 6. Diagnostics (7)
    "Contradiction_Flags",
    "Override_Applied",
    "Score_Adjustment",
    "Passes_Agreed",
    "Earnings_Modifier",
    "Earnings_Tickers",
    "GPT_Reasoning",

    # 7. Operational status (2)
    "Webhook_Success",
    "Trade_Executed",

    # 8. Outcome (4) — backfilled by validate_outcomes.py
    "SPX_Next_Open",
    "SPX_Next_Close",
    "Overnight_Move_Pct",
    "Outcome_Correct",

    # 9. Provenance (2)
    "Code_Version",
    "Environment",
]

# Sanity check — guard against accidental schema drift in code
assert len(SHEET_HEADERS) == 46, (
    f"SHEET_HEADERS expected 46 columns, got {len(SHEET_HEADERS)}. "
    f"If you added a column intentionally, update this assertion AND the Sheet header row."
)
assert len(set(SHEET_HEADERS)) == len(SHEET_HEADERS), (
    "SHEET_HEADERS contains duplicate column names"
)


# ─────────────────────────────────────────────────────────────────────────────
# Provenance helpers (Code_Version + Environment)
# ─────────────────────────────────────────────────────────────────────────────

_CODE_VERSION_CACHE: Optional[str] = None


def _get_code_version() -> str:
    """Return the current git short-SHA, cached for the process lifetime.

    On Railway (or any env where .git isn't present), returns 'unknown'.
    """
    global _CODE_VERSION_CACHE
    if _CODE_VERSION_CACHE is not None:
        return _CODE_VERSION_CACHE
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=os.path.dirname(os.path.abspath(__file__)),
            capture_output=True,
            text=True,
            timeout=2,
        )
        sha = (result.stdout or "").strip()
        _CODE_VERSION_CACHE = sha or "unknown"
    except Exception:
        _CODE_VERSION_CACHE = "unknown"
    return _CODE_VERSION_CACHE


def _get_environment() -> str:
    """Return 'local' or 'production' based on .config presence."""
    try:
        from core.config import get_config
        return "local" if get_config().get("_FROM_FILE") else "production"
    except Exception:
        return "production"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers for normalizing payload values
# ─────────────────────────────────────────────────────────────────────────────

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


def _format_earnings_tickers(earnings: Optional[Dict[str, Any]]) -> str:
    """Format earnings tickers for the Sheet column."""
    if not earnings:
        return ""
    tickers = earnings.get("reporting_today", []) + earnings.get("reporting_tomorrow", [])
    return ", ".join(tickers) if tickers else "None"


# ─────────────────────────────────────────────────────────────────────────────
# Public API
# ─────────────────────────────────────────────────────────────────────────────

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
    earnings: Optional[Dict[str, Any]] = None,
    confirmation_pass: Optional[Dict[str, Any]] = None,
    # Multi-bot fields (every Phase 2 desk passes these from its transform hook)
    desk_id: str = "overnight_condors",
    structure_label: str = "IC_25pt_0.16d_symmetric",
    contracts: Optional[int] = None,
) -> None:
    """Append one signal row to the "live" tab. Never raises; on failure sends
    a critical Slack alert via core.alerting.

    Each desk's run_signal_cycle() calls this with whatever fields it has;
    desks that don't compute a particular field (e.g. butterfly has no IV/RV)
    pass empty dicts and the corresponding columns end up blank.
    """
    # ── Build the name-keyed payload ──
    # Pull conditional dimensions stashed on the signal dict by transform hooks.
    # (Desks that don't override transform_signal_for_routing leave these blank.)
    routed_tier = signal.get("signal", "")  # post-transform tier
    original_tier = signal.get("original_tier", routed_tier)  # pre-transform tier
    # When original_tier is unset (Bot A/B/C — no transform), the original
    # `signal['signal']` IS the original tier. Using routed_tier as fallback
    # ensures both columns get populated correctly.
    signal_tier = original_tier if original_tier else routed_tier

    contradiction_flags = ""
    override = ""
    score_adjustment = ""
    if contradictions:
        contradiction_flags = "; ".join(contradictions.get("contradiction_flags", [])) or "None"
        override = contradictions.get("override_signal") or "None"
        score_adjustment = contradictions.get("score_adjustment", 0)

    # Normalize percent fields to NUMERIC (not formatted strings)
    trend_chg_5d = trend.get("change_5d")  # already numeric (e.g. 0.0142)
    intraday_range = trend.get("intraday_range")  # already numeric

    payload: Dict[str, Any] = {
        # Identity
        "Timestamp_ET":          timestamp,
        "Desk_ID":               desk_id,
        "Poke_Number":           poke_number,
        "Day_Of_Week":           _ts_day_of_week(timestamp),

        # Signal output
        "Signal":                signal_tier,
        "Routed_Tier":           routed_tier,
        "Composite_Score":       composite.get("score", ""),
        "Category":              composite.get("category", ""),
        "Reason":                signal.get("reason", ""),
        "Skip_Reason":           signal.get("skip_reason", ""),

        # Structure & sizing
        "Structure_Label":       structure_label,
        "Contracts":             contracts if contracts is not None else "",
        "VVIX_Bucket":           signal.get("vvix_bucket", ""),
        "VVIX_Percentile":       signal.get("vvix_percentile", ""),
        "VVIX_Bucket_Source":    signal.get("vvix_bucket_source", ""),
        "DOW_Multiplier":        signal.get("dow_multiplier", ""),
        "Hedge_Attached":        bool(signal.get("hedge_attached", False))
                                 if "hedge_attached" in signal else "",

        # Factor inputs
        "IV_RV_Score":           iv_rv.get("score", ""),
        "IV_RV_Ratio":           iv_rv.get("iv_rv_ratio", ""),
        "Trend_Score":           trend.get("score", ""),
        "Trend_5d_Chg_Pct":      trend_chg_5d if trend_chg_5d is not None else "",
        "Intraday_Range_Pct":    intraday_range if intraday_range is not None else "",
        "GPT_Score":             gpt.get("score", ""),
        "GPT_Direction_Risk":    gpt.get("direction_risk", ""),
        "GPT_Key_Risk":          gpt.get("key_risk", ""),

        # Market context
        "SPX_Current":           spx_current if spx_current is not None else "",
        "VIX1D":                 iv_rv.get("implied_vol", vix1d_current) or "",
        "VIX":                   vix_current if vix_current is not None else "",
        "VVIX":                  iv_rv.get("vvix", ""),
        "Realized_Vol_10d":      iv_rv.get("realized_vol", ""),
        "Term_Structure_Ratio":  iv_rv.get("term_structure_ratio", ""),

        # Diagnostics
        "Contradiction_Flags":   contradiction_flags,
        "Override_Applied":      override,
        "Score_Adjustment":      score_adjustment,
        "Passes_Agreed":         confirmation_pass.get("passes_agreed", "")
                                 if confirmation_pass else "",
        "Earnings_Modifier":     earnings.get("risk_modifier", "") if earnings else "",
        "Earnings_Tickers":      _format_earnings_tickers(earnings),
        "GPT_Reasoning":         (gpt.get("reasoning") or "")[:500],

        # Operational status
        "Webhook_Success":       bool(webhook_success),
        "Trade_Executed":        trade_executed,

        # Outcome (backfilled later by validate_outcomes.py — leave blank now)
        "SPX_Next_Open":         "",
        "SPX_Next_Close":        "",
        "Overnight_Move_Pct":    "",
        "Outcome_Correct":       "",

        # Provenance
        "Code_Version":          _get_code_version(),
        "Environment":           _get_environment(),
    }

    print(f"[Sheets] log_signal called (desk={desk_id}, tier={routed_tier})")
    success = _core_log_signal_dict(
        tab_name=SHEET_TAB,
        payload=payload,
        expected_headers=SHEET_HEADERS,
        desk_id=desk_id,
    )
    if not success:
        # core.sheets already sent the Slack alert; we just log here for trace.
        logger.warning("Sheet append failed for desk=%s; signal still sent to OA",
                       desk_id)
