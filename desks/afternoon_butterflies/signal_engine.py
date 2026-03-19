"""Simple VIX-level signal engine for 0DTE afternoon iron butterflies.

No GPT, no confirmation pass, no contradiction detection.
VIX level -> score -> tier. Intentionally simple — the goal is infrastructure.
"""
from desks.afternoon_butterflies.config import VIX_THRESHOLDS, VIX_SCORES


def run_signal_analysis(vix_value):
    """Run simple VIX-based signal analysis.

    Args:
        vix_value: Current VIX level (30-day)

    Returns:
        dict with signal, score, reason, category
    """
    if vix_value is None:
        return {
            'signal': 'SKIP',
            'score': 9,
            'should_trade': False,
            'reason': 'VIX data unavailable — skipping',
            'category': 'NO_DATA',
        }

    # Determine signal from VIX level
    for signal, (low, high) in VIX_THRESHOLDS.items():
        if low <= vix_value < high:
            score = VIX_SCORES[signal]
            should_trade = signal != 'SKIP'

            # Wing width based on signal tier
            wing_widths = {
                'TRADE_AGGRESSIVE': '5pt',
                'TRADE_NORMAL': '10pt',
                'TRADE_CONSERVATIVE': '15pt',
                'SKIP': '-',
            }

            return {
                'signal': signal,
                'score': score,
                'should_trade': should_trade,
                'reason': f"VIX={vix_value:.1f} -> {signal}",
                'category': signal.replace('TRADE_', '') if should_trade else 'SKIP',
                'vix_value': vix_value,
                'wing_width': wing_widths.get(signal, '-'),
                'exit_strategy': 'Expire / 3:50 PM' if should_trade else '-',
            }

    # Fallback (shouldn't reach here)
    return {
        'signal': 'SKIP',
        'score': 9,
        'should_trade': False,
        'reason': f'VIX={vix_value:.1f} — unexpected range',
        'category': 'UNKNOWN',
    }
