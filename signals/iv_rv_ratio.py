"""Indicator 1: IV/RV Ratio Analysis (30% weight)"""
import math


def analyze_iv_rv_ratio(spx_data, vix1d_data):
    """
    Analyze IV/RV ratio using REAL VIX1D (1-day forward implied vol)
    VIX1D = 1-day forward implied volatility (perfect for overnight strategy!)
    RV = 10-day realized volatility
    """
    
    # Calculate 10-day Realized Volatility
    closes = spx_data['history_closes'][:11]  # Need 11 days to get 10 returns
    
    returns = []
    for i in range(1, len(closes)):
        daily_return = math.log(closes[i] / closes[i-1])
        returns.append(daily_return)
    
    mean_return = sum(returns) / len(returns)
    squared_diffs = [(r - mean_return)**2 for r in returns]
    variance = sum(squared_diffs) / (len(returns) - 1)
    daily_std = math.sqrt(variance)
    realized_vol = daily_std * math.sqrt(252) * 100
    
    # VIX1D = 1-day forward implied volatility (already in percentage terms)
    implied_vol = vix1d_data['current']
    
    # IV/RV ratio
    iv_rv_ratio = implied_vol / realized_vol
    
    # Scoring logic
    if iv_rv_ratio > 1.35:
        base_score = 1
    elif iv_rv_ratio > 1.20:
        base_score = 2
    elif iv_rv_ratio > 1.10:
        base_score = 3
    elif iv_rv_ratio > 1.00:
        base_score = 4
    elif iv_rv_ratio > 0.90:
        base_score = 6
    elif iv_rv_ratio > 0.80:
        base_score = 8
    else:
        base_score = 10
    
    # RV change modifier
    if len(spx_data['history_closes']) >= 21:
        closes_earlier = spx_data['history_closes'][10:21]
        returns_earlier = []
        for i in range(1, len(closes_earlier)):
            returns_earlier.append(math.log(closes_earlier[i] / closes_earlier[i-1]))
        
        mean_earlier = sum(returns_earlier) / len(returns_earlier)
        variance_earlier = sum([(r - mean_earlier)**2 for r in returns_earlier]) / (len(returns_earlier) - 1)
        rv_earlier = math.sqrt(variance_earlier) * math.sqrt(252) * 100
        
        rv_change = (realized_vol - rv_earlier) / rv_earlier if rv_earlier > 0 else 0
        
        if rv_change > 0.30:
            modifier = +3
        elif rv_change > 0.15:
            modifier = +2
        elif rv_change < -0.20:
            modifier = -1
        else:
            modifier = 0
    else:
        modifier = 0
        rv_change = 0
    
    final_score = max(1, min(10, base_score + modifier))
    
    return {
        'score': final_score,
        'realized_vol': round(realized_vol, 2),
        'implied_vol': round(implied_vol, 2),
        'iv_rv_ratio': round(iv_rv_ratio, 3),
        'vix1d_value': round(implied_vol, 2),
        'tenor': '1-day (VIX1D)',
        'source': 'Polygon VIX1D (real data)',
        'rv_change': round(rv_change, 3)
    }
