"""Tests for validate_outcomes.py poke stability analysis.

Tests the helper functions that compare validation pokes (2, 3) against
the decision poke (1) to measure signal stability and identify whether
delaying the decision would produce better outcomes.

Run: python -m pytest tests/test_validate_outcomes.py -v
"""
import pytest
from validate_outcomes import (
    _hypothetical_outcome,
    _group_rows_by_date,
    MOVE_THRESHOLDS,
    NO_TRADE_THRESHOLD,
    COL_TIMESTAMP,
    COL_POKE_NUMBER,
    COL_SIGNAL,
    COL_SENT_TO_GPT,
    COL_OVERNIGHT_MOVE,
    COL_OUTCOME_CORRECT,
)


# ── _hypothetical_outcome Tests ────────────────────────────────────────


class TestHypotheticalOutcome:
    """Test hypothetical outcome evaluation for different signal/move combos."""

    def test_aggressive_correct_small_move(self):
        """TRADE_AGGRESSIVE correct when move < 1.00%."""
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', 0.50) == 'CORRECT'
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', 0.99) == 'CORRECT'

    def test_aggressive_wrong_large_move(self):
        """TRADE_AGGRESSIVE wrong when move >= 1.00%."""
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', 1.00) == 'WRONG'
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', 1.50) == 'WRONG'

    def test_normal_correct_small_move(self):
        """TRADE_NORMAL correct when move < 0.90%."""
        assert _hypothetical_outcome('TRADE_NORMAL', 0.50) == 'CORRECT'
        assert _hypothetical_outcome('TRADE_NORMAL', 0.89) == 'CORRECT'

    def test_normal_wrong_large_move(self):
        """TRADE_NORMAL wrong when move >= 0.90%."""
        assert _hypothetical_outcome('TRADE_NORMAL', 0.90) == 'WRONG'
        assert _hypothetical_outcome('TRADE_NORMAL', 1.20) == 'WRONG'

    def test_conservative_correct_small_move(self):
        """TRADE_CONSERVATIVE correct when move < 0.80%."""
        assert _hypothetical_outcome('TRADE_CONSERVATIVE', 0.50) == 'CORRECT'
        assert _hypothetical_outcome('TRADE_CONSERVATIVE', 0.79) == 'CORRECT'

    def test_conservative_wrong_large_move(self):
        """TRADE_CONSERVATIVE wrong when move >= 0.80%."""
        assert _hypothetical_outcome('TRADE_CONSERVATIVE', 0.80) == 'WRONG'
        assert _hypothetical_outcome('TRADE_CONSERVATIVE', 1.50) == 'WRONG'

    def test_skip_correct_big_move(self):
        """SKIP correct when move >= 0.80% (was right to stay out)."""
        assert _hypothetical_outcome('SKIP', 0.80) == 'CORRECT'
        assert _hypothetical_outcome('SKIP', 1.50) == 'CORRECT'

    def test_skip_wrong_small_move(self):
        """SKIP wrong when move < 0.80% (missed opportunity)."""
        assert _hypothetical_outcome('SKIP', 0.50) == 'WRONG'
        assert _hypothetical_outcome('SKIP', 0.79) == 'WRONG'

    def test_threshold_boundary_aggressive(self):
        """Exactly at threshold = WRONG for TRADE (move >= breakeven)."""
        threshold = MOVE_THRESHOLDS['TRADE_AGGRESSIVE']
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', threshold) == 'WRONG'
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', threshold - 0.001) == 'CORRECT'

    def test_threshold_boundary_skip(self):
        """Exactly at threshold = CORRECT for SKIP."""
        assert _hypothetical_outcome('SKIP', NO_TRADE_THRESHOLD) == 'CORRECT'
        assert _hypothetical_outcome('SKIP', NO_TRADE_THRESHOLD - 0.001) == 'WRONG'

    def test_poke1_trade_poke2_skip_big_move(self):
        """Scenario: Poke 1 says TRADE, Poke 2 says SKIP, big move happened.
        Poke 2 was right (saved from a blown trade)."""
        move = 1.10  # beyond all trade thresholds
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', move) == 'WRONG'
        assert _hypothetical_outcome('SKIP', move) == 'CORRECT'

    def test_poke1_skip_poke2_trade_small_move(self):
        """Scenario: Poke 1 says SKIP, Poke 2 says TRADE, small move.
        Poke 2 was right (missed opportunity caught)."""
        move = 0.30
        assert _hypothetical_outcome('SKIP', move) == 'WRONG'
        assert _hypothetical_outcome('TRADE_NORMAL', move) == 'CORRECT'

    def test_both_wrong_big_move_trade_signals(self):
        """Both pokes say TRADE at different tiers, but move blows both."""
        move = 1.50
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', move) == 'WRONG'
        assert _hypothetical_outcome('TRADE_CONSERVATIVE', move) == 'WRONG'

    def test_tier_upgrade_saves_trade(self):
        """Poke 1 CONSERVATIVE (0.80% threshold), Poke 2 AGGRESSIVE (1.00%).
        Move is 0.85% — CONSERVATIVE blown, AGGRESSIVE survives."""
        move = 0.85
        assert _hypothetical_outcome('TRADE_CONSERVATIVE', move) == 'WRONG'
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', move) == 'CORRECT'


# ── _group_rows_by_date Tests ──────────────────────────────────────────


def _build_row(timestamp, poke_num, signal, overnight_move="", outcome="",
               sent_to_gpt=""):
    """Build a minimal sheet row for testing _group_rows_by_date."""
    # Create a row with enough columns to cover all indices
    row = [""] * (COL_OUTCOME_CORRECT + 1)
    row[COL_TIMESTAMP] = timestamp
    row[COL_POKE_NUMBER] = str(poke_num)
    row[COL_SIGNAL] = signal
    row[COL_OVERNIGHT_MOVE] = overnight_move
    row[COL_OUTCOME_CORRECT] = outcome
    if len(row) > COL_SENT_TO_GPT:
        row[COL_SENT_TO_GPT] = str(sent_to_gpt) if sent_to_gpt != "" else ""
    return row


class TestGroupRowsByDate:
    """Test date grouping for poke stability analysis."""

    def test_single_date_multiple_pokes(self):
        """Three pokes on the same date grouped correctly."""
        header = [""] * (COL_OUTCOME_CORRECT + 1)
        rows = [
            header,
            _build_row("2025-02-10 01:32:00 PM ET", 1, "TRADE_NORMAL", "+0.45%", "CORRECT_TRADE"),
            _build_row("2025-02-10 01:40:00 PM ET", 2, "TRADE_NORMAL", "+0.45%", "CORRECT_TRADE"),
            _build_row("2025-02-10 01:50:00 PM ET", 3, "TRADE_CONSERVATIVE", "+0.45%", "CORRECT_TRADE"),
        ]
        groups = _group_rows_by_date(rows)
        assert "2025-02-10" in groups
        assert len(groups["2025-02-10"]) == 3
        assert groups["2025-02-10"][1]['signal'] == 'TRADE_NORMAL'
        assert groups["2025-02-10"][3]['signal'] == 'TRADE_CONSERVATIVE'

    def test_multiple_dates(self):
        """Different dates are grouped separately."""
        header = [""] * (COL_OUTCOME_CORRECT + 1)
        rows = [
            header,
            _build_row("2025-02-10 01:32:00 PM ET", 1, "TRADE_NORMAL", "+0.45%", "CORRECT_TRADE"),
            _build_row("2025-02-11 01:32:00 PM ET", 1, "SKIP", "+1.20%", "CORRECT_SKIP"),
        ]
        groups = _group_rows_by_date(rows)
        assert len(groups) == 2
        assert "2025-02-10" in groups
        assert "2025-02-11" in groups

    def test_overnight_move_parsed(self):
        """Overnight move percentage is parsed from string."""
        header = [""] * (COL_OUTCOME_CORRECT + 1)
        rows = [
            header,
            _build_row("2025-02-10 01:32:00 PM ET", 1, "TRADE_NORMAL", "+0.4500%", "CORRECT_TRADE"),
        ]
        groups = _group_rows_by_date(rows)
        assert groups["2025-02-10"][1]['overnight_move'] == pytest.approx(0.45)

    def test_missing_overnight_move(self):
        """Row without overnight move data has None."""
        header = [""] * (COL_OUTCOME_CORRECT + 1)
        rows = [
            header,
            _build_row("2025-02-10 01:32:00 PM ET", 1, "TRADE_NORMAL"),
        ]
        groups = _group_rows_by_date(rows)
        assert groups["2025-02-10"][1]['overnight_move'] is None

    def test_empty_rows_skipped(self):
        """Rows with no timestamp or signal are skipped."""
        header = [""] * (COL_OUTCOME_CORRECT + 1)
        rows = [
            header,
            _build_row("", 1, "TRADE_NORMAL"),
            _build_row("2025-02-10 01:32:00 PM ET", 1, ""),
        ]
        groups = _group_rows_by_date(rows)
        assert len(groups) == 0

    def test_sent_to_gpt_tracked(self):
        """Sent_To_GPT count is captured for article comparison."""
        header = [""] * (COL_OUTCOME_CORRECT + 1)
        rows = [
            header,
            _build_row("2025-02-10 01:32:00 PM ET", 1, "TRADE_NORMAL",
                        "+0.45%", "CORRECT_TRADE", sent_to_gpt="5"),
            _build_row("2025-02-10 01:50:00 PM ET", 3, "SKIP",
                        "+0.45%", "WRONG_SKIP", sent_to_gpt="8"),
        ]
        groups = _group_rows_by_date(rows)
        assert groups["2025-02-10"][1]['sent_to_gpt'] == 5
        assert groups["2025-02-10"][3]['sent_to_gpt'] == 8

    def test_poke_number_default_for_legacy(self):
        """Legacy rows without poke number default to poke 1."""
        header = [""] * (COL_OUTCOME_CORRECT + 1)
        row = _build_row("2025-02-10 01:32:00 PM ET", "", "TRADE_NORMAL", "+0.45%", "CORRECT_TRADE")
        row[COL_POKE_NUMBER] = ""  # simulate legacy
        rows = [header, row]
        groups = _group_rows_by_date(rows)
        assert 1 in groups["2025-02-10"]


# ── Integration: Poke Comparison Scenarios ─────────────────────────────


class TestPokeComparisonScenarios:
    """End-to-end scenarios testing what the poke comparison reveals."""

    def test_stable_signal_all_agree(self):
        """When all pokes agree, the signal is stable."""
        move = 0.40
        p1_result = _hypothetical_outcome('TRADE_NORMAL', move)
        p2_result = _hypothetical_outcome('TRADE_NORMAL', move)
        p3_result = _hypothetical_outcome('TRADE_NORMAL', move)
        assert p1_result == p2_result == p3_result == 'CORRECT'

    def test_late_news_saves_from_blown_trade(self):
        """Late-breaking news caused poke 3 to say SKIP.
        Market moved 1.1% — poke 3 was right to skip."""
        move = 1.10
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', move) == 'WRONG'
        assert _hypothetical_outcome('SKIP', move) == 'CORRECT'

    def test_early_scare_missed_opportunity(self):
        """Poke 1 scared into SKIP by early news, poke 3 calmed down to TRADE.
        Market only moved 0.30% — poke 3 was right to trade."""
        move = 0.30
        assert _hypothetical_outcome('SKIP', move) == 'WRONG'
        assert _hypothetical_outcome('TRADE_NORMAL', move) == 'CORRECT'

    def test_tier_downgrade_was_wise(self):
        """Poke 1 said AGGRESSIVE, poke 3 downgraded to CONSERVATIVE.
        Move was 0.95% — AGGRESSIVE blown, CONSERVATIVE also blown (0.80)."""
        move = 0.95
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', move) == 'CORRECT'
        assert _hypothetical_outcome('TRADE_CONSERVATIVE', move) == 'WRONG'

    def test_both_pokes_correct_different_tiers(self):
        """Both tiers survive the move, just with different margins."""
        move = 0.50
        assert _hypothetical_outcome('TRADE_AGGRESSIVE', move) == 'CORRECT'
        assert _hypothetical_outcome('TRADE_CONSERVATIVE', move) == 'CORRECT'
