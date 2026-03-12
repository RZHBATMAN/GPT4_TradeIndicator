"""Log signal generation to Google Sheets for history and backtesting.

Optional: only runs when GOOGLE_SHEET_ID and credentials are configured.
Failures are logged and never raise, so the main app (webhook) is unaffected.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from config.loader import get_config

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
]


def _get_credentials_dict() -> Optional[Dict[str, Any]]:
    """Resolve credentials from config: GOOGLE_CREDENTIALS_JSON only (local + Railway)."""
    config = get_config()
    sheet_id = (config.get("GOOGLE_SHEET_ID") or "").strip()
    json_cfg = (config.get("GOOGLE_CREDENTIALS_JSON") or "").strip()

    if not sheet_id:
        print("[Sheets] Skip: GOOGLE_SHEET_ID not set in config/env")
        return None
    if not json_cfg:
        print("[Sheets] Skip: GOOGLE_CREDENTIALS_JSON not set in config/env")
        return None

    try:
        return json.loads(json_cfg)
    except json.JSONDecodeError as e:
        print(f"[Sheets] Invalid GOOGLE_CREDENTIALS_JSON (parse error): {e}")
        logger.warning("Invalid GOOGLE_CREDENTIALS_JSON: %s", e)
        return None


def _client_and_sheet():
    """Return (gspread client, worksheet) or (None, None) if not configured or on error."""
    try:
        import gspread
    except ImportError:
        print("[Sheets] Skip: gspread not installed (pip install gspread google-auth)")
        logger.debug("gspread not installed; skipping Sheets logging")
        return None, None

    creds_dict = _get_credentials_dict()
    if not creds_dict:
        return None, None

    config = get_config()
    sheet_id = (config.get("GOOGLE_SHEET_ID") or "").strip()
    if not sheet_id:
        return None, None

    try:
        gc = gspread.service_account_from_dict(creds_dict)
        sh = gc.open_by_key(sheet_id)
        ws = sh.sheet1
        print(f"[Sheets] Connected to sheet id {sheet_id[:12]}...")
        return gc, ws
    except Exception as e:
        print(f"[Sheets] Google Sheets client failed: {e}")
        logger.warning("Google Sheets client failed (sheet_id=%s): %s", sheet_id[:8] + "...", e)
        return None, None


def _ensure_header(ws) -> None:
    """Ensure header row matches SHEET_HEADERS exactly; replace if any mismatch."""
    try:
        first_row = ws.row_values(1)
        if not first_row or first_row[0] != SHEET_HEADERS[0]:
            ws.insert_row(SHEET_HEADERS, 1)
            print("[Sheets] Header row inserted (first run)")
        elif first_row[:len(SHEET_HEADERS)] != SHEET_HEADERS:
            # Header exists but doesn't match (wrong order, missing/extra columns, etc.)
            # Overwrite the entire header row to match current schema
            ws.update('A1', [SHEET_HEADERS], value_input_option='RAW')
            print(f"[Sheets] Header row replaced to match current schema ({len(SHEET_HEADERS)} columns)")
        else:
            print("[Sheets] Header row OK")
    except Exception as e:
        print(f"[Sheets] Could not ensure header row: {e}")
        logger.warning("Could not ensure header row: %s", e)


def _ts_day_of_week(timestamp: str) -> str:
    """Extract day of week from timestamp string (e.g. 'Monday')."""
    try:
        from datetime import datetime as _dt
        import pytz as _pytz
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
) -> None:
    """Append one signal row to the configured Google Sheet. No-op if not configured; never raises."""
    print("[Sheets] log_signal called")
    _, ws = _client_and_sheet()
    if ws is None:
        print("[Sheets] No worksheet available; row not written")
        return

    try:
        _ensure_header(ws)

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
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"[Sheets] Row appended successfully (signal={signal.get('signal')})")
        logger.info("Signal logged to Google Sheet: %s", signal.get("signal"))
    except Exception as e:
        print(f"[Sheets] Append failed: {e}")
        logger.warning("Sheets append failed (signal still sent): %s", e)
