"""Backward-compat shim — delegates to desks.overnight_condors.signal_engine."""
from desks.overnight_condors.signal_engine import (
    run_signal_analysis,
    calculate_composite_score,
    generate_signal,
    detect_contradictions,
)

__all__ = ['run_signal_analysis', 'calculate_composite_score', 'generate_signal', 'detect_contradictions']
