"""Signal engine: Composite score calculation and signal generation"""
from signals.iv_rv_ratio import analyze_iv_rv_ratio
from signals.market_trend import analyze_market_trend
from signals.gpt_news import analyze_gpt_news


def calculate_composite_score(indicators):
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


def generate_signal(composite_score):
    """Generate trading signal"""
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
    
    # Calculate composite score
    composite = calculate_composite_score(indicators)
    
    # Generate signal
    signal = generate_signal(composite['score'])
    
    return {
        'indicators': indicators,
        'composite': composite,
        'signal': signal
    }
