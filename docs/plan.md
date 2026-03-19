# Plan

## Done

### Infrastructure & Signal Engine (original TODO completions)
- Reduce GPT non-determinism (temp 0.3 -> 0.1, confirmation pass)
- VIX term structure (VIX1D/VIX ratio, inversion detection)
- Conservative API error handling (fallback score 7/ELEVATED)
- Signal consistency / whiplash fix (confirmation pass, once-per-day webhook)
- Symmetrize trend scoring (abs(change))
- Fix RV window overlap (closes[11:22] for prior window)
- Better P&L proxy (delta-based breakeven thresholds)
- Earnings calendar (Mag 7 detection via Polygon)
- Backtest engine (backtest.py with GPT sweep, outcome comparison)
- System failure alerting (alerting.py with webhook notifications)
- Web UI update (all features and safety layers)
- Restructure docs (README.md, TODO.md, OPERATIONS.md)
- Slack alerting webhook configured and tested
- Validation uses 10 AM exit price (matches OA time-based exit)
- Document OA exit parameters (profit/stop/touch/time as code constants)
- Scaling path documented (4-phase roadmap in OPERATIONS.md)

### Phase 1: Log-only signal model data collection
- VVIX fetch and logging
- Overnight RV (close-to-open decomposition)
- Blended vol metric
- Student-t breach probability
- VRP trend (expanding vs compressing)

### Phase 2: Small-dataset analysis improvements
- Signal log table in Sheets
- Factor contribution breakdown per signal
- Signal trajectory tracking
- Lowered thresholds for small-sample regime
- Enhanced P&L tracking
- Brier Score calibration

### Phase 3: Edge decay monitor + new indicator analysis sections
- Implied vs realized gap tracking (rolling 30-day ratio)
- New analysis report sections for indicator performance

### Phase 4: Documentation overhaul
- plan.md + signal.md created and maintained

---

## To-Do Now

### Trend scoring asymmetry
- **Decide:** Should selloffs score higher than rallies of equal magnitude?
- **When:** After 1-2 months of outcome data. Check if WRONG_TRADE correlates more with selloffs vs rallies.
- **File:** `signals/market_trend.py`

### GPT weight reduction (50% -> 40%)
- **Decide:** Does GPT score actually correlate with large overnight moves, or is it noise?
- **When:** After 1-2 months. Run `validate_outcomes.py` and `backtest.py --sweep` to compare quant-only vs composite.
- **File:** `signal_engine.py`

### Real-time data upgrade
- **Decide:** Is 15-min delay causing wrong signals on high-vol days?
- **When:** After reviewing outcome data for fast-market days. Upgrade Polygon plan (+$50-100/mo) or add Yahoo Finance cross-check if yes.
- **File:** `data/market_data.py`

### Friday signal accuracy
- **Decide:** Do Friday signals have higher WRONG_TRADE rate due to 64-hour weekend exposure?
- **When:** After 1-2 months. Filter outcomes by day-of-week. Add `FRIDAY_SCORE_MODIFIER` if significantly worse.
- **File:** `signal_engine.py`

### Paper vs live execution quality
- **Decide:** Is OA slippage acceptable (<5% of premium)? Are specific conditions (high VIX, EOD) worse?
- **When:** After 1 month of parallel paper+live data.

### Bot experiment exit strategy tuning (IN PROGRESS)
- **What:** Running original vs test bot with different profit targets and stop losses side by side.
- **Decide:** Which exit config produces better total P&L, win rate, and max drawdown over 30-40 trades. Pick winner, kill loser, proceed to scaling.

---

## Parking Lot

### Cross-asset regime detection (TNX, DXY)
- Add TNX (10Y yield) and DXY (dollar index) to distinguish risk regimes (rate-shock vs growth-scare vs calm). Feed as modifier to trend score or GPT context. Requires Polygon `I:TNX`, `I:DXY`.

### ES futures overnight monitoring for early exit
- Lightweight monitor checking ES futures at midnight and 6 AM ET. Alert via Slack if |ES move| > 0.60% from SPX close. Future: auto-close via OA API if breakeven breached. Separate cron job, not part of signal engine.
