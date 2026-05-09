"""Multi-desk poke scheduler.

Background thread that iterates all desks, checks each desk's window,
and pokes when due. Each desk's window + daily cache is instance state.
"""
import os
import threading
import time as time_module
from datetime import datetime, time as dt_time

import pytz
import requests

from core.alerting import record_poke, check_end_of_window, reset_daily

ET_TZ = pytz.timezone('US/Eastern')


def start_scheduler(desks, base_url=None, is_local=False):
    """Start background poke thread for all desks.

    Not started when is_local so one manual click = one run when testing.

    Args:
        desks: list of Desk instances
        base_url: URL to poke (defaults to POKE_BASE_URL env or localhost:8080)
        is_local: if True, don't start scheduler
    """
    if is_local:
        print("[POKE] Scheduler disabled (local); trigger manually")
        return

    if base_url is None:
        base_url = os.environ.get("POKE_BASE_URL", "http://localhost:8080")

    timeout_sec = int(os.environ.get("POKE_TIMEOUT", "300"))

    def _poke_loop():
        print("[POKE] Background thread started")

        while True:
            try:
                now = datetime.now(ET_TZ)
                current_time = now.time()

                # Reset alert dedup at midnight
                if current_time.hour == 0 and current_time.minute == 0 and current_time.second < 30:
                    reset_daily()

                for desk in desks:
                    desk_id = desk.desk_id

                    if desk.is_within_window(now):
                        record_poke()
                        # Fixed-time pokes per desk.poke_minutes (no randomization).
                        # For overnight desks: [30, 50, 10] → fires at 1:30, 1:50, 2:10
                        # exactly. The webhook-once-per-day cache in each desk's
                        # _daily_signal_cache makes pokes 2 and 3 effectively retries
                        # if poke 1's webhook failed.
                        if current_time.minute in desk.poke_minutes and current_time.second < 30:
                            # All desks register at /{desk_id}/trigger — canonical convention.
                            # See memory/feedback_url_conventions.md for the rule.
                            trigger_url = f"{base_url}/{desk_id}/trigger"

                            print(f"\n[POKE] {desk_id}: Triggering at {now.strftime('%I:%M %p ET')}")
                            try:
                                requests.get(trigger_url, timeout=timeout_sec)
                            except Exception as e:
                                print(f"[POKE] {desk_id} Error: {e}")

                # Check if any window just ended (use desk 1's window for backward compat)
                if dt_time(14, 31) <= current_time <= dt_time(14, 35) and now.weekday() < 5:
                    check_end_of_window()

                time_module.sleep(30)

            except Exception as e:
                print(f"[POKE] Background error: {e}")
                time_module.sleep(60)

    t = threading.Thread(target=_poke_loop, daemon=True)
    t.start()
    print("[POKE] Scheduler started (production)")
