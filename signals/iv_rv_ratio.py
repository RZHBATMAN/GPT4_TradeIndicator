"""Indicator 1: IV/RV Ratio Analysis (30% weight)"""
import math


def analyze_iv_rv_ratio(spx_data, vix1d_data, vix_data=None, vvix_data=None):
    """
    Analyze IV/RV ratio using REAL VIX1D (1-day forward implied vol)
    VIX1D = 1-day forward implied volatility (perfect for overnight strategy!)
    RV = 10-day realized volatility

    Optional vix_data: VIX (30-day) for term structure analysis.
    VIX1D > VIX = inverted term structure = near-term fear = danger for overnight selling.

    Optional vvix_data: VVIX for vol-of-vol monitoring (log-only, no scoring impact).
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
        closes_earlier = spx_data['history_closes'][11:22]
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

    # Term structure modifier: VIX1D vs VIX (30-day)
    # VIX1D > VIX = inverted = market expects near-term turbulence
    term_structure_ratio = None
    term_modifier = 0
    if vix_data and vix_data.get('current') and vix_data['current'] > 0:
        vix_30d = vix_data['current']
        term_structure_ratio = round(implied_vol / vix_30d, 3)
        if term_structure_ratio > 1.10:
            term_modifier = +3  # Strong inversion — very dangerous
        elif term_structure_ratio > 1.00:
            term_modifier = +1  # Mild inversion — caution
        # Contango (ratio < 1.0) is normal, no adjustment

    final_score = max(1, min(10, base_score + modifier + term_modifier))

    result = {
        'score': final_score,
        'base_score': base_score,
        'rv_modifier': modifier,
        'realized_vol': round(realized_vol, 2),
        'implied_vol': round(implied_vol, 2),
        'iv_rv_ratio': round(iv_rv_ratio, 3),
        'vix1d_value': round(implied_vol, 2),
        'tenor': '1-day (VIX1D)',
        'source': 'Polygon VIX1D (real data)',
        'rv_change': round(rv_change, 3)
    }

    if vix_data and vix_data.get('current'):
        result['vix_30d'] = round(vix_data['current'], 2)
        result['term_structure_ratio'] = term_structure_ratio
        result['term_structure'] = 'INVERTED' if term_structure_ratio > 1.0 else 'CONTANGO'
        result['term_modifier'] = term_modifier

    # ── Log-only metadata (no scoring impact) ──

    # Overnight RV: std(log(open_t / close_{t-1})) * sqrt(252) * 100
    history_opens = spx_data.get('history_opens')
    if history_opens and len(history_opens) >= 10 and len(closes) >= 10:
        overnight_returns = []
        for i in range(min(len(history_opens[:10]), len(closes[:10])) - 1):
            # opens[i] is the open of day i, closes[i+1] is the close of the previous day
            # (data is in DESC order: most recent first)
            if closes[i + 1] > 0:
                overnight_returns.append(math.log(history_opens[i] / closes[i + 1]))

        if len(overnight_returns) >= 5:
            on_mean = sum(overnight_returns) / len(overnight_returns)
            on_var = sum((r - on_mean) ** 2 for r in overnight_returns) / (len(overnight_returns) - 1)
            overnight_rv = math.sqrt(on_var) * math.sqrt(252) * 100

            result['overnight_rv'] = round(overnight_rv, 2)
            result['iv_overnight_rv_ratio'] = round(implied_vol / overnight_rv, 3) if overnight_rv > 0 else None

            # Blended overnight vol: 0.93 × overnight_rv + 0.07 × vix1d
            # From model_theory_reference.md Section 6: w = T/14 = 1/14 ≈ 0.07 for 1-day horizon
            blended_overnight_vol = 0.93 * overnight_rv + 0.07 * implied_vol
            result['blended_overnight_vol'] = round(blended_overnight_vol, 2)

            # Student-t breach probability
            # Fit ν from overnight log returns, compute P(|move| > breakeven)
            try:
                from scipy.stats import t as t_dist
                nu, _, _ = t_dist.fit(overnight_returns)
                nu = max(2.5, min(15.0, nu))  # clamp for stability
                result['student_t_nu'] = round(nu, 2)

                # Breakeven for NORMAL tier = 0.90%
                breakeven_pct = 0.90 / 100.0
                # Scale parameter: match variance
                on_std = math.sqrt(on_var)
                scale = on_std * math.sqrt((nu - 2) / nu) if nu > 2 else on_std
                breach_prob = 2 * (1 - t_dist.cdf(breakeven_pct / scale, nu)) if scale > 0 else None
                if breach_prob is not None:
                    result['student_t_breach_prob'] = round(breach_prob, 4)
            except Exception as e:
                print(f"  ⚠️ Student-t fit failed: {e}")

    # VRP trend (simple MVP: compare current IV/RV to direction)
    if rv_change != 0:
        if iv_rv_ratio > 1.0 and rv_change < 0:
            result['vrp_trend'] = 'EXPANDING'  # IV rich and RV declining = edge growing
        elif iv_rv_ratio < 1.0 and rv_change > 0:
            result['vrp_trend'] = 'COMPRESSING'  # IV cheap and RV rising = edge shrinking
        elif rv_change > 0.15:
            result['vrp_trend'] = 'COMPRESSING'  # RV spiking = premium shrinking
        elif rv_change < -0.15:
            result['vrp_trend'] = 'EXPANDING'  # RV dropping = premium growing
        else:
            result['vrp_trend'] = 'STABLE'
    else:
        result['vrp_trend'] = 'STABLE'

    # VVIX metadata
    if vvix_data and vvix_data.get('current') is not None:
        vvix_val = vvix_data['current']
        result['vvix'] = round(vvix_val, 2)
        result['vvix_elevated'] = vvix_val > 120

    return result
