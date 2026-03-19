"""Data fetching module"""
from .market_data import get_spx_data_with_retry, get_vix1d_with_retry, get_vix_with_retry, get_vvix_with_retry, get_spx_snapshot, get_vix1d_snapshot, get_vix_snapshot, get_vvix_snapshot, get_spx_aggregates
from .news_fetcher import fetch_news_raw
from .earnings_calendar import check_mag7_earnings
from .oa_event_calendar import check_oa_event_gates, format_gate_reasons

__all__ = [
    'get_spx_data_with_retry',
    'get_vix1d_with_retry',
    'get_vix_with_retry',
    'get_vvix_with_retry',
    'get_spx_snapshot',
    'get_vix1d_snapshot',
    'get_vix_snapshot',
    'get_vvix_snapshot',
    'get_spx_aggregates',
    'fetch_news_raw',
    'check_mag7_earnings',
    'check_oa_event_gates',
    'format_gate_reasons',
]
