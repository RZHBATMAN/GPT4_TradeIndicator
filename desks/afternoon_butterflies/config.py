"""Desk-specific constants for 0DTE Afternoon Iron Butterflies."""
from datetime import time as dt_time

# Config prefix — desk 2 keys are prefixed in .config / env vars
CONFIG_PREFIX = "DESK2_"

# Trading window: 1:45 - 2:15 PM ET, single poke at ~2:00 PM
WINDOW_START = dt_time(13, 45)
WINDOW_END = dt_time(14, 15)
WINDOW_DAYS = [0, 1, 2, 3, 4]  # Mon-Fri
POKE_MINUTES = [0, 10]  # ~2:00 PM and 2:10 PM

# Simple VIX-level signal thresholds
# VIX < 15 → TRADE_AGGRESSIVE, 15-20 → TRADE_NORMAL, 20-25 → TRADE_CONSERVATIVE, >25 → SKIP
VIX_THRESHOLDS = {
    'TRADE_AGGRESSIVE': (0, 15),
    'TRADE_NORMAL': (15, 20),
    'TRADE_CONSERVATIVE': (20, 25),
    'SKIP': (25, float('inf')),
}

# Score mapping
VIX_SCORES = {
    'TRADE_AGGRESSIVE': 2,
    'TRADE_NORMAL': 4,
    'TRADE_CONSERVATIVE': 6,
    'SKIP': 9,
}

# Sheet tab
SHEET_TAB = "0DTE_Butterflies"

# Simplified sheet headers (~15 columns)
SHEET_HEADERS = [
    "Timestamp_ET",
    "Signal",
    "Score",
    "VIX",
    "SPX_Current",
    "Webhook_Success",
    "Trade_Executed",
    "Reason",
    "Wing_Width",
    "Exit_Strategy",
    # Outcome tracking (filled later)
    "SPX_Expiry",
    "Move_Pct",
    "Outcome",
]
