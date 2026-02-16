"""Signal validation test suite.

Tests all indicator scoring logic, composite calculation, contradiction
detection, and edge cases to verify signals are legitimate.

Run: python -m pytest tests/test_signal_validation.py -v
"""
import math
import pytest
from signal_engine import (
    calculate_composite_score,
    generate_signal,
    detect_contradictions,
)
from signals.iv_rv_ratio import analyze_iv_rv_ratio
from signals.market_trend import analyze_market_trend


# ── Helpers ──────────────────────────────────────────────────────────────


def _make_spx_data(current, high, low, closes=None):
    """Build a minimal spx_data dict for testing."""
    if closes is None:
        # Generate flat prices for 25 days
        closes = [current] * 25
    return {
        'current': current,
        'high_today': high,
        'low_today': low,
        'open_today': current,
        'history_closes': closes,
    }


def _make_vix1d_data(value):
    return {'current': value}


def _make_indicators(iv_rv_score, trend_score, gpt_score):
    """Build minimal indicators dict for composite/contradiction tests."""
    return {
        'iv_rv': {'score': iv_rv_score},
        'trend': {'score': trend_score},
        'gpt': {'score': gpt_score},
    }


# ── IV/RV Ratio Tests ───────────────────────────────────────────────────


class TestIVRVRatio:
    """Verify IV/RV scoring matches documented thresholds."""

    def _rv_for_flat_prices(self, price, n=11):
        """RV when prices are perfectly flat = 0, but we'd get div-by-zero.
        Use tiny variation instead."""
        return [price + 0.01 * (i % 2) for i in range(n)]

    def test_iv_very_rich(self):
        """IV/RV > 1.35 should score 1 (best for selling vol)."""
        # RV ≈ 10%, IV = 20% → ratio ≈ 2.0
        closes = self._generate_closes_for_rv(target_rv=10.0, n=25)
        spx = _make_spx_data(5800, 5810, 5790, closes)
        vix = _make_vix1d_data(20.0)
        result = analyze_iv_rv_ratio(spx, vix)
        assert result['iv_rv_ratio'] > 1.35
        assert result['score'] <= 2  # should be 1, possibly 2 with modifier

    def test_iv_cheap(self):
        """IV/RV < 0.80 should score 10 (worst — don't sell cheap vol)."""
        closes = self._generate_closes_for_rv(target_rv=25.0, n=25)
        spx = _make_spx_data(5800, 5810, 5790, closes)
        vix = _make_vix1d_data(12.0)  # Very low IV
        result = analyze_iv_rv_ratio(spx, vix)
        assert result['iv_rv_ratio'] < 0.80
        assert result['score'] >= 8

    def test_iv_rv_near_parity(self):
        """IV/RV ≈ 1.0 should score around 4 (base), but RV modifier can shift it."""
        closes = self._generate_closes_for_rv(target_rv=15.0, n=25)
        spx = _make_spx_data(5800, 5810, 5790, closes)
        vix = _make_vix1d_data(15.5)
        result = analyze_iv_rv_ratio(spx, vix)
        assert 0.90 <= result['iv_rv_ratio'] <= 1.15
        # Base score 3-4, but RV change modifier can push to 6-7
        assert 3 <= result['score'] <= 7

    def test_rv_spike_modifier(self):
        """When current RV is 30%+ higher than prior RV, modifier = +3."""
        # Build closes: first 10 days with high vol, next 11 with low vol
        high_vol_closes = self._generate_closes_for_rv(target_rv=25.0, n=11)
        low_vol_closes = self._generate_closes_for_rv(target_rv=12.0, n=11)
        # closes are DESC order: most recent first
        closes = high_vol_closes + low_vol_closes[1:]  # 21 closes
        # Pad to 25
        closes += [closes[-1]] * 4
        spx = _make_spx_data(closes[0], closes[0] + 10, closes[0] - 10, closes)
        vix = _make_vix1d_data(20.0)
        result = analyze_iv_rv_ratio(spx, vix)
        # With 21+ closes, modifier should be applied
        assert 'rv_change' in result

    def test_score_clamped(self):
        """Score must always be between 1 and 10."""
        closes = self._generate_closes_for_rv(target_rv=5.0, n=25)
        spx = _make_spx_data(5800, 5810, 5790, closes)
        vix = _make_vix1d_data(50.0)  # Absurdly high IV
        result = analyze_iv_rv_ratio(spx, vix)
        assert 1 <= result['score'] <= 10

    @staticmethod
    def _generate_closes_for_rv(target_rv, n=25, base_price=5800.0):
        """Generate n daily closes (DESC order) that produce approximately
        the target annualized realized vol."""
        daily_std = (target_rv / 100.0) / math.sqrt(252)
        closes = [base_price]
        for i in range(1, n):
            # Alternate up/down to create variance
            direction = 1 if i % 2 == 0 else -1
            move = direction * daily_std * closes[-1]
            closes.append(closes[-1] + move)
        return closes  # already in DESC-ish order for recent-first


# ── Market Trend Tests ───────────────────────────────────────────────────


class TestMarketTrend:
    def test_quiet_market(self):
        """< 1% 5d change, < 1% intraday → score 1 (ideal for condors)."""
        # Current = 5800, 5 days ago = 5790 → +0.17%
        closes = [5800, 5798, 5796, 5794, 5792, 5790] + [5788] * 19
        spx = _make_spx_data(5800, 5810, 5795, closes)
        result = analyze_market_trend(spx)
        assert result['score'] == 1

    def test_sharp_selloff(self):
        """< -4% 5d change → base score 7."""
        closes = [5800, 5850, 5900, 5950, 6000, 6100] + [6100] * 19
        spx = _make_spx_data(5800, 5830, 5780, closes)
        result = analyze_market_trend(spx)
        assert result['score'] >= 7

    def test_strong_rally(self):
        """> +4% 5d change → base score 5."""
        closes = [6100, 6050, 6000, 5950, 5900, 5800] + [5800] * 19
        spx = _make_spx_data(6100, 6110, 6050, closes)
        result = analyze_market_trend(spx)
        assert result['score'] >= 5

    def test_wide_intraday_range(self):
        """> 1.5% intraday range → +2 modifier."""
        closes = [5800] * 25
        # 1.6% range
        high = 5800 + 5800 * 0.016
        low = 5800
        spx = _make_spx_data(5800, high, low, closes)
        result = analyze_market_trend(spx)
        # Base score 1 (flat 5d) + 2 (wide range) = 3
        assert result['score'] >= 3

    def test_asymmetry_awareness(self):
        """Document: -4% scores higher than +4% (7 vs 5).
        This is intentional but users should know about the bias."""
        closes_down = [5800, 5850, 5900, 5950, 6000, 6100] + [6100] * 19
        closes_up = [6100, 6050, 6000, 5950, 5900, 5800] + [5800] * 19

        spx_down = _make_spx_data(5800, 5830, 5780, closes_down)
        spx_up = _make_spx_data(6100, 6110, 6050, closes_up)

        score_down = analyze_market_trend(spx_down)['score']
        score_up = analyze_market_trend(spx_up)['score']

        # Down scores higher risk than equivalent up move
        assert score_down > score_up


# ── Composite Score Tests ────────────────────────────────────────────────


class TestCompositeScore:
    def test_perfect_conditions(self):
        """All factors score 1 → composite near 1.0."""
        indicators = _make_indicators(1, 1, 1)
        result = calculate_composite_score(indicators)
        assert result['score'] == 1.0
        assert result['category'] == 'EXCELLENT'

    def test_worst_conditions(self):
        """All factors score 10 → composite 10.0."""
        indicators = _make_indicators(10, 10, 10)
        result = calculate_composite_score(indicators)
        assert result['score'] == 10.0
        assert result['category'] == 'HIGH'

    def test_gpt_dominance(self):
        """GPT at 50% weight dominates the score."""
        # IV/RV=1, Trend=1, GPT=10 → 0.3 + 0.2 + 5.0 = 5.5
        indicators = _make_indicators(1, 1, 10)
        result = calculate_composite_score(indicators)
        assert result['score'] == 5.5
        assert result['category'] == 'FAIR'

    def test_iv_rv_low_weight(self):
        """IV/RV at 30% can't override a high GPT score alone."""
        # IV/RV=1, Trend=5, GPT=8 → 0.3 + 1.0 + 4.0 = 5.3
        indicators = _make_indicators(1, 5, 8)
        result = calculate_composite_score(indicators)
        assert result['score'] >= 5.0

    def test_contradiction_adjustment(self):
        """Score adjustment from contradiction detection is applied."""
        indicators = _make_indicators(1, 6, 7)
        contradiction = {
            'override_signal': None,
            'override_reason': None,
            'contradiction_flags': ['test'],
            'score_adjustment': 1.5,
        }
        result = calculate_composite_score(indicators, contradiction)
        result_no_adj = calculate_composite_score(indicators)
        assert result['score'] == result_no_adj['score'] + 1.5


# ── Signal Generation Tests ─────────────────────────────────────────────


class TestSignalGeneration:
    def test_skip_threshold(self):
        assert generate_signal(7.5)['signal'] == 'SKIP'
        assert generate_signal(10.0)['signal'] == 'SKIP'

    def test_conservative_range(self):
        assert generate_signal(5.0)['signal'] == 'TRADE_CONSERVATIVE'
        assert generate_signal(7.4)['signal'] == 'TRADE_CONSERVATIVE'

    def test_normal_range(self):
        assert generate_signal(3.5)['signal'] == 'TRADE_NORMAL'
        assert generate_signal(4.9)['signal'] == 'TRADE_NORMAL'

    def test_aggressive_range(self):
        assert generate_signal(1.0)['signal'] == 'TRADE_AGGRESSIVE'
        assert generate_signal(3.4)['signal'] == 'TRADE_AGGRESSIVE'

    def test_override_forces_skip(self):
        """Hard override from contradiction should force SKIP."""
        contradiction = {
            'override_signal': 'SKIP',
            'override_reason': 'GPT extreme risk',
            'contradiction_flags': ['GPT_EXTREME'],
            'score_adjustment': 0,
        }
        result = generate_signal(2.0, contradiction)  # Would be AGGRESSIVE
        assert result['signal'] == 'SKIP'
        assert result['should_trade'] is False


# ── Contradiction Detection Tests ────────────────────────────────────────


class TestContradictions:
    def test_gpt_extreme_forces_skip(self):
        """GPT >= 8 forces SKIP override."""
        indicators = _make_indicators(1, 1, 8)
        result = detect_contradictions(indicators)
        assert result['override_signal'] == 'SKIP'
        assert 'GPT_EXTREME' in result['contradiction_flags'][0]

    def test_gpt_extreme_threshold(self):
        """GPT = 7 should NOT trigger the extreme override."""
        indicators = _make_indicators(1, 1, 7)
        result = detect_contradictions(indicators)
        assert result['override_signal'] is None

    def test_gpt_trend_conflict(self):
        """GPT >= 6 AND Trend >= 5 triggers adjustment."""
        indicators = _make_indicators(1, 5, 6)
        result = detect_contradictions(indicators)
        assert result['score_adjustment'] >= 1.5
        assert any('GPT_TREND_CONFLICT' in f for f in result['contradiction_flags'])

    def test_high_dispersion(self):
        """Spread >= 6 between indicators triggers adjustment."""
        indicators = _make_indicators(1, 5, 7)
        result = detect_contradictions(indicators)
        assert any('HIGH_DISPERSION' in f for f in result['contradiction_flags'])

    def test_iv_cheap(self):
        """IV/RV score >= 8 triggers IV_CHEAP flag."""
        indicators = _make_indicators(8, 3, 3)
        result = detect_contradictions(indicators)
        assert any('IV_CHEAP' in f for f in result['contradiction_flags'])

    def test_no_flags_when_aligned(self):
        """No contradictions when all indicators agree."""
        indicators = _make_indicators(3, 3, 3)
        result = detect_contradictions(indicators)
        assert result['contradiction_flags'] == []
        assert result['override_signal'] is None
        assert result['score_adjustment'] == 0.0


# ── Edge Case / Scenario Tests ───────────────────────────────────────────


class TestRealWorldScenarios:
    """Test composite scores for realistic trading scenarios."""

    def test_quiet_day_iv_rich(self):
        """Ideal setup: quiet market, IV rich, no news risk.
        Should produce TRADE_AGGRESSIVE."""
        indicators = _make_indicators(2, 1, 2)
        composite = calculate_composite_score(indicators)
        contradiction = detect_contradictions(indicators)
        signal = generate_signal(composite['score'], contradiction)
        assert signal['signal'] == 'TRADE_AGGRESSIVE'

    def test_mag7_earnings_override(self):
        """Mag 7 earnings after hours → GPT=9, everything else calm.
        Should force SKIP regardless of IV/RV."""
        indicators = _make_indicators(1, 1, 9)
        contradiction = detect_contradictions(indicators)
        signal = generate_signal(
            calculate_composite_score(indicators, contradiction)['score'],
            contradiction,
        )
        assert signal['signal'] == 'SKIP'

    def test_moderate_news_cheap_iv(self):
        """IV is cheap (8), moderate news (5), quiet trend (2).
        Should be cautious due to IV being cheap."""
        indicators = _make_indicators(8, 2, 5)
        contradiction = detect_contradictions(indicators)
        composite = calculate_composite_score(indicators, contradiction)
        signal = generate_signal(composite['score'], contradiction)
        # IV_CHEAP flag adds +1.0, so should be more conservative
        assert signal['signal'] in ('TRADE_CONSERVATIVE', 'SKIP')

    def test_fed_day_high_volatility(self):
        """Fed decision day: high trend (6), GPT elevated (7), IV rich (2).
        GPT+Trend conflict should add +1.5."""
        indicators = _make_indicators(2, 6, 7)
        contradiction = detect_contradictions(indicators)
        composite = calculate_composite_score(indicators, contradiction)
        # Without adjustment: 0.6 + 1.2 + 3.5 = 5.3
        # With +1.5: 6.8 → ELEVATED
        assert composite['score'] >= 6.5

    def test_api_error_defaults(self):
        """GPT error defaults to score 5. Should not produce AGGRESSIVE."""
        indicators = _make_indicators(2, 2, 5)  # GPT default = 5
        composite = calculate_composite_score(indicators)
        signal = generate_signal(composite['score'])
        # 0.6 + 0.4 + 2.5 = 3.5 → TRADE_NORMAL (not aggressive)
        assert signal['signal'] != 'TRADE_AGGRESSIVE'


# ── Weight Sensitivity Tests ─────────────────────────────────────────────


class TestWeightSensitivity:
    """Test how sensitive the final signal is to single-factor changes."""

    def test_gpt_swing_impact(self):
        """A 3-point GPT swing (from 3→6) should move composite by 1.5."""
        ind_low = _make_indicators(3, 3, 3)
        ind_high = _make_indicators(3, 3, 6)
        score_low = calculate_composite_score(ind_low)['score']
        score_high = calculate_composite_score(ind_high)['score']
        assert abs((score_high - score_low) - 1.5) < 0.2

    def test_iv_rv_swing_impact(self):
        """A 3-point IV/RV swing should move composite by 0.9."""
        ind_low = _make_indicators(3, 3, 3)
        ind_high = _make_indicators(6, 3, 3)
        score_low = calculate_composite_score(ind_low)['score']
        score_high = calculate_composite_score(ind_high)['score']
        assert abs((score_high - score_low) - 0.9) < 0.2

    def test_trend_swing_impact(self):
        """A 3-point trend swing should move composite by 0.6."""
        ind_low = _make_indicators(3, 3, 3)
        ind_high = _make_indicators(3, 6, 3)
        score_low = calculate_composite_score(ind_low)['score']
        score_high = calculate_composite_score(ind_high)['score']
        assert abs((score_high - score_low) - 0.6) < 0.2
