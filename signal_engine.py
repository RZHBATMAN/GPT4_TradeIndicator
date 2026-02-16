"""Signal engine: Composite score calculation and signal generation"""
from signals.iv_rv_ratio import analyze_iv_rv_ratio
from signals.market_trend import analyze_market_trend
from signals.gpt_news import analyze_gpt_news


def detect_contradictions(indicators):
    """Detect when indicators strongly disagree and apply safety overrides.

    Returns a dict with:
      - override_signal: None if no override, or a signal string (e.g. 'SKIP')
      - override_reason: human-readable explanation
      - contradiction_flags: list of detected contradictions
      - score_adjustment: additional points to add to composite (can be 0)
    """
    iv_rv_score = indicators['iv_rv']['score']
    trend_score = indicators['trend']['score']
    gpt_score = indicators['gpt']['score']

    flags = []
    override_signal = None
    override_reason = None
    score_adjustment = 0.0

    # Rule 1: GPT extreme risk (>=8) should force SKIP regardless of IV/RV
    # Rationale: a genuine overnight catalyst (Mag 7 earnings, Fed surprise)
    # will blow through any vol premium you're collecting.
    if gpt_score >= 8:
        flags.append(f"GPT_EXTREME: GPT score {gpt_score} indicates major overnight catalyst")
        override_signal = 'SKIP'
        override_reason = f"GPT override: extreme overnight risk (GPT={gpt_score})"

    # Rule 2: GPT elevated + trend elevated = force at least CONSERVATIVE
    # Both momentum and news are warning; don't let cheap IV lure you in.
    elif gpt_score >= 6 and trend_score >= 5:
        flags.append(f"GPT_TREND_CONFLICT: GPT={gpt_score}, Trend={trend_score} both elevated")
        score_adjustment = +1.5

    # Rule 3: All three factors disagree strongly (spread >= 6)
    # This means high uncertainty — better to be cautious.
    scores = [iv_rv_score, trend_score, gpt_score]
    spread = max(scores) - min(scores)
    if spread >= 6:
        flags.append(f"HIGH_DISPERSION: spread={spread} (IV/RV={iv_rv_score}, Trend={trend_score}, GPT={gpt_score})")
        score_adjustment = max(score_adjustment, +1.0)

    # Rule 4: IV is extremely cheap (score >= 8) — don't sell vol at a discount
    if iv_rv_score >= 8:
        flags.append(f"IV_CHEAP: IV/RV score {iv_rv_score} means IV is cheap relative to RV")
        score_adjustment = max(score_adjustment, +1.0)

    return {
        'override_signal': override_signal,
        'override_reason': override_reason,
        'contradiction_flags': flags,
        'score_adjustment': score_adjustment
    }


def calculate_composite_score(indicators, contradiction_result=None):
    """Composite: IV/RV=30%, Trend=20%, GPT=50%"""
    weights = {'iv_rv': 0.30, 'trend': 0.20, 'gpt': 0.50}

    iv_rv_score = indicators['iv_rv']['score']
    trend_score = indicators['trend']['score']
    gpt_score = indicators['gpt']['score']

    composite = (
        iv_rv_score * weights['iv_rv'] +
        trend_score * weights['trend'] +
        gpt_score * weights['gpt']
    )

    # Apply contradiction adjustment
    if contradiction_result and contradiction_result['score_adjustment']:
        composite += contradiction_result['score_adjustment']

    composite = round(composite, 1)
    composite = max(1.0, min(10.0, composite))

    if composite < 2.5:
        category = "EXCELLENT"
    elif composite < 3.5:
        category = "VERY_GOOD"
    elif composite < 5.0:
        category = "GOOD"
    elif composite < 6.5:
        category = "FAIR"
    elif composite < 7.5:
        category = "ELEVATED"
    else:
        category = "HIGH"

    return {'score': composite, 'category': category}


def generate_signal(composite_score, contradiction_result=None):
    """Generate trading signal, respecting any contradiction overrides."""
    # Check for hard override first
    if contradiction_result and contradiction_result['override_signal']:
        return {
            'signal': contradiction_result['override_signal'],
            'should_trade': False,
            'reason': contradiction_result['override_reason']
        }

    if composite_score >= 7.5:
        return {
            'signal': 'SKIP',
            'should_trade': False,
            'reason': f"High risk ({composite_score:.1f})"
        }
    elif composite_score >= 5.0:
        return {
            'signal': 'TRADE_CONSERVATIVE',
            'should_trade': True,
            'reason': f"Elevated risk ({composite_score:.1f})"
        }
    elif composite_score >= 3.5:
        return {
            'signal': 'TRADE_NORMAL',
            'should_trade': True,
            'reason': f"Good setup ({composite_score:.1f})"
        }
    else:
        return {
            'signal': 'TRADE_AGGRESSIVE',
            'should_trade': True,
            'reason': f"Excellent ({composite_score:.1f})"
        }


def run_signal_analysis(spx_data, vix1d_data, news_data):
    """Run all indicators and generate composite signal"""
    # Run all three indicators
    iv_rv = analyze_iv_rv_ratio(spx_data, vix1d_data)
    trend = analyze_market_trend(spx_data)
    gpt = analyze_gpt_news(news_data)

    indicators = {'iv_rv': iv_rv, 'trend': trend, 'gpt': gpt}

    # Detect contradictions between indicators
    contradiction = detect_contradictions(indicators)

    if contradiction['contradiction_flags']:
        print(f"\n  [CONTRADICTION DETECTION]")
        for flag in contradiction['contradiction_flags']:
            print(f"    - {flag}")
        if contradiction['override_signal']:
            print(f"    >>> OVERRIDE: Forcing {contradiction['override_signal']} — {contradiction['override_reason']}")
        elif contradiction['score_adjustment']:
            print(f"    >>> ADJUSTMENT: +{contradiction['score_adjustment']} added to composite score")

    # Calculate composite score (with contradiction adjustment)
    composite = calculate_composite_score(indicators, contradiction)

    # Generate signal (with possible override)
    signal = generate_signal(composite['score'], contradiction)

    return {
        'indicators': indicators,
        'composite': composite,
        'signal': signal,
        'contradictions': contradiction
    }
