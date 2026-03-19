"""Backward-compat shim — delegates to core.alerting."""
from core.alerting import (
    _send_alert,
    _get_webhook_url,
    record_signal_success,
    record_api_failure,
    record_poke,
    check_end_of_window,
    check_poke_health,
    reset_daily,
    get_alert_status,
    _state,
    _lock,
    ET_TZ,
    TRADING_WINDOW_START,
    TRADING_WINDOW_END,
)

__all__ = [
    '_send_alert',
    '_get_webhook_url',
    'record_signal_success',
    'record_api_failure',
    'record_poke',
    'check_end_of_window',
    'check_poke_health',
    'reset_daily',
    'get_alert_status',
    '_state',
    '_lock',
]
