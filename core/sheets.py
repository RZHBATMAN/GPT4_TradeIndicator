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

        # 1. Try exact match
        try:
            ws = sh.worksheet(tab_name)
            print(f"[Sheets] Connected to sheet id {sheet_id[:12]}... (tab: {tab_name})")
            return ws
        except gspread.exceptions.WorksheetNotFound:
            pass

        # 2. Fall back to case-insensitive / whitespace-tolerant match against
        #    the actual list of worksheets. This guards against the user
        #    naming the tab "Live" or " live " etc.
        all_worksheets = sh.worksheets()
        target = tab_name.strip().lower()
        for w in all_worksheets:
            if w.title.strip().lower() == target:
                if w.title != tab_name:
                    print(f"[Sheets] Tab name match (case/whitespace): "
                          f"requested={tab_name!r} actual={w.title!r}")
                return w

        # 3. Truly missing — create a new tab
        try:
            ws = sh.add_worksheet(title=tab_name, rows=1000, cols=100)
            print(f"[Sheets] Created new tab: {tab_name}")
            return ws
        except Exception as create_err:
            # Race or other "already exists" — refresh and search once more
            try:
                for w in sh.worksheets():
                    if w.title.strip().lower() == target:
                        return w
            except Exception:
                pass
            print(f"[Sheets] Tab '{tab_name}' could not be created or found: {create_err}")
            raise

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
    """Append one signal row to the specified tab. No-op if not configured; never raises.

    LEGACY positional interface. Prefer log_signal_dict() for new code — it's robust
    to column reordering and surfaces schema drift via Slack alerts.
    """
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


def log_signal_dict(
    tab_name: str,
    payload: Dict[str, Any],
    expected_headers: Optional[List[str]] = None,
    desk_id: Optional[str] = None,
) -> bool:
    """Name-keyed Sheet append. Robust to column reordering / schema drift.

    Behavior:
      - Read the live header row from the target tab.
      - If the tab is empty AND expected_headers is provided: write headers first
        (bootstrap a fresh tab).
      - For each (column_name → value) in payload: place value at the matching
        column index in the live header. Columns we have no payload for: empty.
      - Payload keys NOT in the live header: log a warning + Slack alert
        ("schema drift") and continue writing what we can.
      - On any Sheets API error: Slack critical alert + return False (never raises).

    Returns True if the row was appended successfully, False otherwise.
    """
    # Local import to avoid circular dependency (alerting can use Sheets, etc.)
    try:
        from core.alerting import _send_alert
    except Exception:
        _send_alert = None  # alerting unavailable; fail-soft

    def _alert(title: str, message: str, level: str = "warning") -> None:
        if _send_alert is not None:
            try:
                _send_alert(title, message, level=level, desk_id=desk_id)
            except Exception as exc:
                # Don't let alerting failure break the signal cycle
                print(f"[Sheets] Slack alert dispatch failed: {exc}")

    print(f"[Sheets] log_signal_dict called (tab={tab_name}, payload_keys={len(payload)})")
    ws = _get_worksheet(tab_name)
    if ws is None:
        print("[Sheets] No worksheet available; row not written")
        # No alert — most likely Sheets is not configured at all (local dev)
        return False

    # Step 1: read the live header row
    try:
        live_header = ws.row_values(1)
    except Exception as e:
        msg = f"Could not read header row of tab '{tab_name}': {e}"
        print(f"[Sheets] {msg}")
        logger.warning(msg)
        _alert("Sheets header read failed", msg, level="critical")
        return False

    # Step 2: if tab is empty and we have expected headers, bootstrap
    if not live_header:
        if expected_headers:
            try:
                ws.update('A1', [expected_headers], value_input_option='RAW')
                live_header = list(expected_headers)
                print(f"[Sheets] Bootstrapped header row on empty tab '{tab_name}' "
                      f"({len(expected_headers)} columns)")
            except Exception as e:
                msg = f"Could not bootstrap header row on tab '{tab_name}': {e}"
                print(f"[Sheets] {msg}")
                _alert("Sheets header bootstrap failed", msg, level="critical")
                return False
        else:
            msg = (f"Tab '{tab_name}' has no header row and no expected_headers "
                   f"provided; cannot place named values.")
            print(f"[Sheets] {msg}")
            _alert("Sheets empty header", msg, level="critical")
            return False

    # Step 3: build a normalized index map to tolerate minor user typos
    # (whitespace; case is left strict so true mismatches still get flagged).
    def _normalize(s: str) -> str:
        return (s or "").strip()

    header_index_by_normalized = {}
    for idx, col_name in enumerate(live_header):
        norm = _normalize(col_name)
        if norm and norm not in header_index_by_normalized:
            header_index_by_normalized[norm] = idx

    # Step 4: detect schema drift (payload keys we can't place even after normalization)
    drifted_keys = [
        k for k in payload.keys()
        if _normalize(k) not in header_index_by_normalized
    ]
    if drifted_keys:
        msg = (f"Schema drift on tab '{tab_name}': payload has key(s) not in live "
               f"header — {drifted_keys[:10]}{'...' if len(drifted_keys) > 10 else ''}. "
               f"Live tab has {len(live_header)} columns. "
               f"Add missing column header to the Sheet, or remove key from payload.")
        print(f"[Sheets] WARN: {msg}")
        logger.warning(msg)
        _alert("Sheets schema drift", msg, level="warning")

    # Step 5: build the row aligned to live_header positions
    row = [""] * len(live_header)
    for col_name, val in payload.items():
        idx = header_index_by_normalized.get(_normalize(col_name))
        if idx is not None:
            row[idx] = "" if val is None else val

    # Step 5: append
    try:
        ws.append_row(row, value_input_option="USER_ENTERED")
        placed = sum(1 for v in row if v != "")
        print(f"[Sheets] Row appended (tab={tab_name}, placed={placed}/{len(live_header)} cells)")
        logger.info("Signal logged to Google Sheet tab %s (%d cells placed)", tab_name, placed)
        return True
    except Exception as e:
        msg = f"Append to tab '{tab_name}' failed: {e}"
        print(f"[Sheets] {msg}")
        logger.warning(msg)
        _alert("Sheets append failed", msg, level="critical")
        return False
