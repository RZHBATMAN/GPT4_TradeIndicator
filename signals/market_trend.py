"""Indicator 2: Market Trend Analysis (20% weight)"""


def analyze_market_trend(spx_data):
    """Analyze 5-day momentum and intraday volatility"""
    current = spx_data['current']
    closes = spx_data['history_closes']
    spx_5d_ago = closes[5] if len(closes) >= 6 else current
    
    change_5d = (current - spx_5d_ago) / spx_5d_ago
    
    if change_5d > 0.04:
        base_score = 5
    elif change_5d > 0.02:
        base_score = 3
    elif change_5d > 0.01:
        base_score = 2
    elif change_5d > -0.01:
        base_score = 1
    elif change_5d > -0.02:
        base_score = 2
    elif change_5d > -0.04:
        base_score = 4
    else:
        base_score = 7
    
    high = spx_data['high_today']
    low = spx_data['low_today']
    intraday_range = (high - low) / current
    
    if intraday_range > 0.015:
        modifier = +2
    elif intraday_range > 0.010:
        modifier = +1
    else:
        modifier = 0
    
    final_score = max(1, min(10, base_score + modifier))
    
    return {
        'score': final_score,
        'change_5d': change_5d,
        'intraday_range': intraday_range
    }
