"""Tests for 0DTE Afternoon Iron Butterflies signal engine.

Run: python -m pytest tests/test_afternoon_butterflies.py -v
"""
import pytest
from desks.afternoon_butterflies.signal_engine import run_signal_analysis


class TestButterflySignal:
    """Test VIX-based signal scoring."""

    def test_low_vix_aggressive(self):
        """VIX < 15 -> TRADE_AGGRESSIVE, score 2."""
        result = run_signal_analysis(12.0)
        assert result['signal'] == 'TRADE_AGGRESSIVE'
        assert result['score'] == 2
        assert result['should_trade'] is True
        assert result['wing_width'] == '5pt'

    def test_moderate_vix_normal(self):
        """VIX 15-20 -> TRADE_NORMAL, score 4."""
        result = run_signal_analysis(17.5)
        assert result['signal'] == 'TRADE_NORMAL'
        assert result['score'] == 4
        assert result['should_trade'] is True
        assert result['wing_width'] == '10pt'

    def test_elevated_vix_conservative(self):
        """VIX 20-25 -> TRADE_CONSERVATIVE, score 6."""
        result = run_signal_analysis(22.0)
        assert result['signal'] == 'TRADE_CONSERVATIVE'
        assert result['score'] == 6
        assert result['should_trade'] is True
        assert result['wing_width'] == '15pt'

    def test_high_vix_skip(self):
        """VIX > 25 -> SKIP, score 9."""
        result = run_signal_analysis(30.0)
        assert result['signal'] == 'SKIP'
        assert result['score'] == 9
        assert result['should_trade'] is False

    def test_vix_none_skip(self):
        """VIX unavailable -> SKIP."""
        result = run_signal_analysis(None)
        assert result['signal'] == 'SKIP'
        assert result['score'] == 9
        assert result['should_trade'] is False

    def test_boundary_15(self):
        """VIX exactly at 15 -> TRADE_NORMAL (not AGGRESSIVE)."""
        result = run_signal_analysis(15.0)
        assert result['signal'] == 'TRADE_NORMAL'

    def test_boundary_20(self):
        """VIX exactly at 20 -> TRADE_CONSERVATIVE."""
        result = run_signal_analysis(20.0)
        assert result['signal'] == 'TRADE_CONSERVATIVE'

    def test_boundary_25(self):
        """VIX exactly at 25 -> SKIP."""
        result = run_signal_analysis(25.0)
        assert result['signal'] == 'SKIP'

    def test_extreme_low_vix(self):
        """VIX at 8 -> TRADE_AGGRESSIVE."""
        result = run_signal_analysis(8.0)
        assert result['signal'] == 'TRADE_AGGRESSIVE'

    def test_extreme_high_vix(self):
        """VIX at 80 -> SKIP."""
        result = run_signal_analysis(80.0)
        assert result['signal'] == 'SKIP'
