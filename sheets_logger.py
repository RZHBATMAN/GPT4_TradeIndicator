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
    """Insert header row if missing, or update it if columns were added since last run."""
    try:
        first_row = ws.row_values(1)
        if not first_row or first_row[0] != SHEET_HEADERS[0]:
            ws.insert_row(SHEET_HEADERS, 1)
            print("[Sheets] Header row inserted (first run)")
        elif len(first_row) < len(SHEET_HEADERS):
            # Header exists but is shorter than current SHEET_HEADERS — patch missing columns
            missing = SHEET_HEADERS[len(first_row):]
            start_col = len(first_row) + 1  # 1-indexed
            for i, hdr in enumerate(missing):
                ws.update_cell(1, start_col + i, hdr)
            print(f"[Sheets] Header row extended: added {len(missing)} new columns ({', '.join(missing)})")
    except Exception as e:
        print(f"[Sheets] Could not ensure header row: {e}")
        logger.warning("Could not ensure header row: %s", e)


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
            # Contradiction columns
            flags_str,
            override,
            adj,
            # Outcome columns — left blank, filled by validate_outcomes.py
            "",  # SPX_Next_Open
            "",  # SPX_Next_Close
            "",  # Overnight_Move_Pct
            "",  # Outcome_Correct
        ]

        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"[Sheets] Row appended successfully (signal={signal.get('signal')})")
        logger.info("Signal logged to Google Sheet: %s", signal.get("signal"))
    except Exception as e:
        print(f"[Sheets] Append failed: {e}")
        logger.warning("Sheets append failed (signal still sent): %s", e)
