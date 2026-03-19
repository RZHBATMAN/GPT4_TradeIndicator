"""Desk-specific constants for SPX Overnight Iron Condors.

These are the hardcoded parameters that define this desk's strategy.
"""
from datetime import time as dt_time

# Factor weights
WEIGHTS = {'iv_rv': 0.30, 'trend': 0.20, 'gpt': 0.50}

# Signal tier boundaries (composite score → signal)
TIER_BOUNDARIES = {
    'TRADE_AGGRESSIVE': (0.0, 3.5),     # score < 3.5
    'TRADE_NORMAL': (3.5, 5.0),         # 3.5 <= score < 5.0
    'TRADE_CONSERVATIVE': (5.0, 7.5),   # 5.0 <= score < 7.5
    'SKIP': (7.5, 10.0),                # score >= 7.5
}

# Breakeven thresholds (derived from delta + premium)
MOVE_THRESHOLDS = {
    'TRADE_AGGRESSIVE': 1.00,     # 0.18 delta → ~1.00% breakeven
    'TRADE_NORMAL': 0.90,         # 0.16 delta → ~0.90% breakeven
    'TRADE_CONSERVATIVE': 0.80,   # 0.14 delta → ~0.80% breakeven
    'SKIP': 0.80,
}
NO_TRADE_THRESHOLD = 0.80

# P&L proxy per 1-lot by tier
PNL_PER_LOT = {
    'TRADE_AGGRESSIVE':   {'credit': 60, 'max_loss': 140},
    'TRADE_NORMAL':       {'credit': 45, 'max_loss': 205},
    'TRADE_CONSERVATIVE': {'credit': 30, 'max_loss': 270},
}

# OA VIX gate threshold
OA_VIX_GATE = 25

# Trading window
WINDOW_START = dt_time(13, 30)
WINDOW_END = dt_time(14, 30)
WINDOW_DAYS = [0, 1, 2, 3, 4]  # Mon-Fri

# Config prefix (empty = desk 1 uses unprefixed keys)
CONFIG_PREFIX = ""

# Sheet tab and headers
SHEET_TAB = "Sheet1"

# Import SHEET_HEADERS from the authoritative location
from sheets_logger import SHEET_HEADERS
