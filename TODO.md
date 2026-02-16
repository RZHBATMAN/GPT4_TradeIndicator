# TODO — Future Improvements

Prioritized list of improvements, each tied to a specific issue with the current system.

---

## High Priority

### 1. Reduce GPT non-determinism

**Issue:** MiniMax has 50% weight in the composite score, but LLMs are non-deterministic. The same news fed to MiniMax twice can return different risk scores. With 50% weight, a 2-point swing in GPT score moves the composite by 1.0 — enough to shift from TRADE_NORMAL to TRADE_CONSERVATIVE, or from CONSERVATIVE to SKIP.

**What to do:**
- Lower MiniMax temperature from 0.3 to 0.1 (simple change in `signals/gpt_news.py`)
- Consider calling MiniMax twice and averaging the scores (adds cost and latency but greatly reduces variance)
- Consider adding a "confidence band" — if two calls disagree by more than 2 points, default to the higher (more cautious) score

**File:** `signals/gpt_news.py` line 245 (`"temperature": 0.3`)

---

### 2. Add VIX term structure (VIX1D vs VIX 30-day)

**Issue:** The system only uses VIX1D. But the VIX1D-to-VIX ratio is a critical signal for overnight vol selling:
- **VIX1D < VIX** (contango): normal. Market expects near-term calm. Overnight premium exists. Good to sell.
- **VIX1D > VIX** (inversion): danger. Market is pricing near-term turbulence. This is exactly when overnight iron condors get blown up.

The current system has no way to detect term structure inversion.

**What to do:**
- Fetch VIX (30-day, ticker `I:VIX`) from Polygon alongside VIX1D
- Compute `VIX1D / VIX` ratio
- Add as a modifier to Factor 1 (IV/RV ratio):
  - Ratio > 1.10 (strong inversion): +3 to IV/RV score
  - Ratio 1.00-1.10 (mild inversion): +1
  - Ratio < 1.00 (contango): no change
- Or create it as a standalone Factor 4 (would require reweighting)

**Files:** `data/market_data.py` (add VIX fetch), `signals/iv_rv_ratio.py` (add modifier)

---

### 3. Handle MiniMax API errors more conservatively

**Issue:** When MiniMax returns an error or times out, the system defaults to GPT score = 5 (MODERATE). With 50% weight, this produces a composite around 3.5-4.5 for most conditions — meaning you'd likely get TRADE_NORMAL even though you have **zero news analysis**. You're flying blind but the system tells you to trade.

**What to do:**
- Change fallback from score 5 to score 7 (ELEVATED), which would produce TRADE_CONSERVATIVE or SKIP
- Or add a dedicated "API_ERROR" signal that maps to a cautious webhook
- Log API failures prominently so you can track reliability

**File:** `signals/gpt_news.py` lines 16-25 (no-news fallback) and lines 255-265 (API error fallback)

---

### 4. Add signal consistency check across poke cycles

**Issue:** The system triggers 3 times during the trading window (1:30, 1:50, 2:10 PM). If the first poke says TRADE_AGGRESSIVE and the second says SKIP (because MiniMax returned a different score), both webhooks fire to Option Alpha. The behavior depends on how Option Alpha handles conflicting signals within the same session.

**What to do:**
- Cache the first signal of the day (in memory or a file)
- On subsequent pokes: if the new signal contradicts the first by more than 1 tier (e.g., AGGRESSIVE → SKIP), log a warning and either:
  - Stick with the first signal (momentum approach)
  - Use the more conservative of the two (safety approach)
  - Send a special "CONFLICT" webhook that Option Alpha can handle
- This prevents whiplash trades

**Files:** `app.py` (add caching logic), `signal_engine.py` (add comparison function)

---

## Medium Priority

### 5. Symmetrize market trend scoring

**Issue:** Downside moves score higher than equivalent upside moves in Factor 2. -4% scores 7, +4% scores 5. For iron condors, large moves in either direction are equally damaging — the short call and short put have similar risk profiles. The current asymmetry makes the system more likely to skip during selloffs than equivalent rallies.

**Argument for keeping it:** crashes are empirically faster and more volatile (vol-of-vol is higher in drawdowns). The asymmetry roughly accounts for this. But it's a discretionary choice, not a data-driven one.

**What to do (if desired):**
- Make upside and downside thresholds symmetric:
  - |change| > 4%: score 7
  - |change| 2-4%: score 4
  - |change| 1-2%: score 2
  - |change| < 1%: score 1
- Or keep asymmetry but reduce the gap (e.g., -4% = 6, +4% = 5)

**File:** `signals/market_trend.py` lines 12-25

---

### 6. Fix RV change modifier window overlap

**Issue:** In Factor 1, current RV uses `closes[0:11]` (days 0-10) and prior RV uses `closes[10:21]` (days 10-20). Day 10 appears in BOTH windows. If there was a large move on day 10, it inflates both the current and prior RV, diluting the signal that's supposed to detect *changes* in volatility.

**What to do:**
- Shift the prior window to `closes[11:22]` so the windows don't overlap
- Or use `closes[10:20]` for prior (non-overlapping 10-day windows)

**File:** `signals/iv_rv_ratio.py` line 50 (`closes_earlier = spx_data['history_closes'][10:21]`)

---

### 7. Track actual P&L, not just overnight move

**Issue:** `validate_outcomes.py` currently uses a simple overnight move threshold to determine if a signal was "correct." But iron condors have non-linear payoffs — a 0.5% move might be fine for a 30pt-wide condor but not for a 20pt one. The thresholds (0.80%, 0.65%, 0.50%) are rough estimates, not actual P&L.

**What to do:**
- Fetch options chain data from Polygon (if available on your plan) to calculate actual iron condor P&L
- Or improve the proxy: use actual delta and width to compute breakeven distance, then compare overnight move to that breakeven
- At minimum, log the actual iron condor strikes from Option Alpha (would need Option Alpha API integration or manual input)

**File:** `validate_outcomes.py` `_evaluate_outcome()` function

---

### 8. Upgrade from 15-min delayed to real-time data

**Issue:** The trading window is only 1 hour (1:30-2:30 PM). With 15-minute delayed data, at 2:00 PM you're seeing 1:45 PM prices. In a fast-moving market (exactly when you most need accurate data), your IV/RV ratio and intraday range are stale.

**What to do:**
- Upgrade Polygon plan to real-time indices (check current pricing)
- Or use a free real-time source for SPX (e.g., Yahoo Finance real-time quote as a cross-check)
- At minimum, log the data delay alongside each signal so you can correlate stale data with wrong signals

**Cost impact:** likely +$50-100/mo depending on Polygon plan

**File:** `data/market_data.py` (change API endpoints or add secondary source)

---

## Low Priority

### 9. Add a simple backtest engine

**Issue:** The system can only validate signals going forward. There's no way to ask "what would my signals have looked like over the past 3 months?" This matters because you might want to tune thresholds or weights based on historical performance before going live with real money.

**What to do:**
- Create `backtest.py` that:
  - Fetches historical SPX + VIX1D data from Polygon for a date range
  - Simulates Factor 1 and Factor 2 for each trading day (these are deterministic)
  - Stubs Factor 3 (GPT) with a fixed score (e.g., 4) or uses historical VIX levels as a proxy
  - Computes what signal would have been generated
  - Compares against actual next-day SPX moves
  - Reports win rate, max loss, average P&L proxy
- This won't capture GPT's contribution, but it validates the quantitative factors

**Limitation:** Factor 3 (50% of score) cannot be backtested since you can't replay historical news through MiniMax retroactively (the news is gone from RSS feeds).

---

### 10. Add earnings calendar awareness

**Issue:** The system relies entirely on MiniMax to detect earnings events from news headlines. But earnings dates are known in advance. A simple calendar check could flag "NVDA reports after close today" and automatically boost the risk score, independent of whether any news articles about it happen to appear in RSS feeds.

**What to do:**
- Integrate a free earnings calendar API (e.g., Polygon's `/v3/reference/tickers/{ticker}/events`)
- Check if any Mag 7 stocks report today or tomorrow
- Add as a modifier: Mag 7 reporting today → +2 to GPT score minimum
- This serves as a safety net for when news about upcoming earnings doesn't appear in RSS feeds (the event hasn't happened yet, so there are no headlines)

---

### 11. Add alerting for system failures

**Issue:** If Polygon goes down, MiniMax goes down, or Railway restarts during the trading window, you may miss a signal cycle entirely — and you'd only know by checking Railway logs manually.

**What to do:**
- Add a simple health-check webhook that pings you (Slack, email, or a separate monitoring URL) if:
  - No signal was generated during a trading window day
  - Polygon or MiniMax returned errors more than once in a row
  - The poke thread hasn't fired in over 30 minutes during trading hours
- Could be as simple as a webhook to a free Slack incoming webhook URL

---

### 12. Consider reducing GPT weight from 50% to 40%

**Issue:** GPT is 50% of the composite — this makes the entire system's quality dependent on MiniMax's output. If MiniMax has a bad day (hallucinating, inconsistent scores, API errors), half your signal is compromised. The quantitative factors (IV/RV ratio, market trend) are deterministic and reproducible.

**What to do (after collecting data):**
- Run `validate_outcomes.py` for 1-2 months
- Analyze: is GPT score actually predictive? Do high GPT scores correlate with large overnight moves?
- If GPT adds marginal value, reduce to 40% and increase IV/RV to 35% and Trend to 25%
- If GPT is clearly predictive, keep at 50%
- This decision should be data-driven, not made in advance

**File:** `signal_engine.py` line 62 (`weights = {'iv_rv': 0.30, 'trend': 0.20, 'gpt': 0.50}`)
