# TODO — Future Improvements

Prioritized list of remaining improvements.

**Completed items:**
- ~~#1 Reduce GPT non-determinism~~ — temperature lowered 0.3→0.1
- ~~#2 Add VIX term structure~~ — VIX1D/VIX ratio with inversion detection
- ~~#3 Handle API errors conservatively~~ — fallback changed to score 7/ELEVATED
- ~~#4 Signal consistency check~~ — intra-day whiplash detection added
- ~~#5 Symmetrize trend scoring~~ — uses abs(change) now
- ~~#6 Fix RV window overlap~~ — closes[11:22] for prior window
- ~~#7 Better P&L proxy~~ — delta-based breakeven thresholds
- ~~#10 Earnings calendar~~ — Mag 7 earnings detection via Polygon
- ~~Backtest engine~~ — `backtest.py` with GPT sweep, outcome comparison
- ~~System failure alerting~~ — `alerting.py` with webhook notifications

---

## Remaining

### 1. Upgrade from 15-min delayed to real-time data (Medium)

**Issue:** The trading window is only 1 hour (1:30-2:30 PM). With 15-minute delayed data, at 2:00 PM you're seeing 1:45 PM prices. In a fast-moving market, your IV/RV ratio and intraday range are stale.

**What to do:**
- Upgrade Polygon plan to real-time indices (check current pricing)
- Or use a free real-time source for SPX (e.g., Yahoo Finance real-time quote as a cross-check)

**Cost impact:** likely +$50-100/mo depending on Polygon plan

**File:** `data/market_data.py`

---

### 2. Consider reducing GPT weight from 50% to 40% (Low — needs data)

**Issue:** GPT is 50% of the composite — this makes the entire system's quality dependent on MiniMax's output. The quantitative factors (IV/RV ratio, market trend) are deterministic and reproducible.

**What to do (after collecting data):**
- Run `validate_outcomes.py` for 1-2 months
- Analyze: is GPT score actually predictive? Do high GPT scores correlate with large overnight moves?
- If GPT adds marginal value, reduce to 40% and increase IV/RV to 35% and Trend to 25%
- This decision should be data-driven, not made in advance

**File:** `signal_engine.py` line 62 (`weights = {'iv_rv': 0.30, 'trend': 0.20, 'gpt': 0.50}`)
