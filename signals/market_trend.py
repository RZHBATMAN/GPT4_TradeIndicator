"""Indicator 2: Market Trend Analysis (20% weight)"""


def analyze_market_trend(spx_data):
    """Analyze 5-day momentum and intraday volatility.

    Scoring is symmetric: iron condors lose on big moves in EITHER direction,
    so +4% and -4% are equally dangerous.
    """
    current = spx_data['current']
    closes = spx_data['history_closes']
    spx_5d_ago = closes[5] if len(closes) >= 6 else current

    change_5d = (current - spx_5d_ago) / spx_5d_ago
    abs_change = abs(change_5d)

    if abs_change > 0.04:
        base_score = 7
    elif abs_change > 0.02:
        base_score = 4
    elif abs_change > 0.01:
        base_score = 2
    else:
        base_score = 1
    
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
