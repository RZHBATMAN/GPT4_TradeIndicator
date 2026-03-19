"""Log signal generation to Google Sheets for history and backtesting.

Parameterized by tab name and headers so each desk can log to its own tab.
Optional: only runs when GOOGLE_SHEET_ID and credentials are configured.
Failures are logged and never raise, so the main app (webhook) is unaffected.
"""
import json
import logging
from typing import Any, Dict, List, Optional

from core.config import get_config

logger = logging.getLogger(__name__)


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


def _get_worksheet(tab_name: str = "Sheet1"):
    """Return worksheet for the given tab, or None if not configured.

    Creates the tab if it doesn't exist.
    """
    try:
        import gspread
    except ImportError:
        print("[Sheets] Skip: gspread not installed (pip install gspread google-auth)")
        logger.debug("gspread not installed; skipping Sheets logging")
        return None

    creds_dict = _get_credentials_dict()
    if not creds_dict:
        return None

    config = get_config()
    sheet_id = (config.get("GOOGLE_SHEET_ID") or "").strip()
    if not sheet_id:
        return None

    try:
        gc = gspread.service_account_from_dict(creds_dict)
        sh = gc.open_by_key(sheet_id)

        # Try to get existing worksheet by name
        try:
            ws = sh.worksheet(tab_name)
        except gspread.exceptions.WorksheetNotFound:
            # Create new tab
            ws = sh.add_worksheet(title=tab_name, rows=1000, cols=100)
            print(f"[Sheets] Created new tab: {tab_name}")

        print(f"[Sheets] Connected to sheet id {sheet_id[:12]}... (tab: {tab_name})")
        return ws
    except Exception as e:
        print(f"[Sheets] Google Sheets client failed: {e}")
        logger.warning("Google Sheets client failed (sheet_id=%s): %s", sheet_id[:8] + "...", e)
        return None


def _ensure_header(ws, headers: List[str]) -> None:
    """Ensure header row matches headers exactly; replace if any mismatch."""
    try:
        first_row = ws.row_values(1)
        if not first_row or first_row[0] != headers[0]:
            ws.insert_row(headers, 1)
            print("[Sheets] Header row inserted (first run)")
        elif first_row[:len(headers)] != headers:
            ws.update('A1', [headers], value_input_option='RAW')
            print(f"[Sheets] Header row replaced to match current schema ({len(headers)} columns)")
        else:
            print("[Sheets] Header row OK")
    except Exception as e:
        print(f"[Sheets] Could not ensure header row: {e}")
        logger.warning("Could not ensure header row: %s", e)


def log_signal(tab_name: str, headers: List[str], row: List[Any]) -> None:
    """Append one signal row to the specified tab. No-op if not configured; never raises."""
    print(f"[Sheets] log_signal called (tab: {tab_name})")
    ws = _get_worksheet(tab_name)
    if ws is None:
        print("[Sheets] No worksheet available; row not written")
        return

    try:
        _ensure_header(ws, headers)
        ws.append_row(row, value_input_option="USER_ENTERED")
        print(f"[Sheets] Row appended successfully (tab: {tab_name})")
        logger.info("Signal logged to Google Sheet tab %s", tab_name)
    except Exception as e:
        print(f"[Sheets] Append failed: {e}")
        logger.warning("Sheets append failed (signal still sent): %s", e)
