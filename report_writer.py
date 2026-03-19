"""Backward-compat shim — delegates to core.report_writer."""
from core.report_writer import save_html_report, TIER_COLORS, TIER_SHORT, REPORTS_DIR

__all__ = ['save_html_report', 'TIER_COLORS', 'TIER_SHORT', 'REPORTS_DIR']
