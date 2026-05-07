"""Desk base class — the protocol every desk implements."""
from datetime import time as dt_time, datetime
from typing import Dict, List, Optional
import pytz

ET_TZ = pytz.timezone('US/Eastern')


class Desk:
    """Base class for all trading desks.

    Each desk owns its full signal pipeline. No shared signal engine.
    """

    desk_id: str = ""                     # "overnight_condors"
    display_name: str = ""                # "SPX Overnight Iron Condors"
    description: str = ""                 # One-line strategy description

    # Optional: group multiple desks under one logical "Desk N" tab in the dashboard.
    # When desk_group is None, the desk renders as its own standalone tab (back-compat).
    # When set, all desks sharing the same desk_group are rendered together inside a
    # single tab whose header is desk_group_label.
    desk_group: str = None
    desk_group_label: str = None
    # status_label: short string shown next to the bot name in compact cards
    # ('live', 'paper', 'oa-native'). Defaults to 'paper' for safety.
    status_label: str = "paper"

    # Mapping of routed_tier label → contract count. Each desk subclass overrides
    # with its own tier-to-sizing schedule (see things_i_need_to_do.md per-bot
    # tables). Used by the Sheet logger to populate the `Contracts` column.
    # Default empty dict → contracts column blank.
    CONTRACTS_BY_TIER: Dict[str, int] = {}

    # Trading window
    window_start: dt_time = dt_time(13, 30)
    window_end: dt_time = dt_time(14, 30)
    window_days: List[int] = [0, 1, 2, 3, 4]  # Mon-Fri
    poke_minutes: List[int] = [30, 50, 10]

    # Config
    config_prefix: str = ""               # "" for desk 1, "DESK2_" for desk 2

    # Sheets
    sheet_tab: str = "Sheet1"
    sheet_headers: List[str] = []

    # Per-day state
    _daily_signal_cache: Dict

    def __init__(self):
        self._daily_signal_cache = {
            'date': None, 'webhook_sent': False,
            'signal': None, 'score': None, 'poke_count': 0,
        }

    def is_within_window(self, now: Optional[datetime] = None) -> bool:
        """Check if within this desk's trading window."""
        if now is None:
            now = datetime.now(ET_TZ)
        if now.weekday() not in self.window_days:
            return False
        current_time = now.time()
        return self.window_start <= current_time <= self.window_end

    def run_signal_cycle(self, config: Dict) -> Dict:
        """Full pipeline: fetch -> analyze -> result. Override in subclass."""
        raise NotImplementedError

    def build_sheet_row(self, result: Dict) -> List:
        """Convert result to sheet row. Override in subclass."""
        raise NotImplementedError

    def get_webhook_urls(self, config: Dict) -> Dict[str, str]:
        """Signal tier -> URL mapping. Override in subclass."""
        raise NotImplementedError

    def register_routes(self, app) -> None:
        """Add Flask endpoints. Override in subclass."""
        raise NotImplementedError

    def get_dashboard_html(self) -> str:
        """Card HTML for unified homepage. Override in subclass."""
        raise NotImplementedError

    def get_health(self) -> Dict:
        """Health status for this desk."""
        return {
            'desk_id': self.desk_id,
            'display_name': self.display_name,
            'last_signal': self._daily_signal_cache.get('signal'),
            'last_score': self._daily_signal_cache.get('score'),
            'poke_count': self._daily_signal_cache.get('poke_count', 0),
        }
