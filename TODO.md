# TODO — Decisions & Tasks

Things to think about, decide on, or act on. These are not operational "run this script" items (see [OPERATIONS.md](OPERATIONS.md) for those).

---

## Decisions To Make (After Collecting Data)

### Think about: Trend scoring asymmetry

Currently trend scoring is symmetric: +4% and -4% both score 7. This treats rallies and selloffs equally.

**The argument for asymmetry:** Crashes are empirically faster and more volatile than rallies. A -4% 5-day move is more likely to continue overnight than a +4% rally. Vol-of-vol is higher in drawdowns. This suggests -4% should score higher than +4%.

**The argument for symmetry (current):** Iron condor P&L is symmetric — you lose on big moves in either direction. The short call and short put have similar risk profiles.

**What to decide:** After running for a month, look at the outcome data. Do more WRONG_TRADE outcomes correlate with selloffs vs rallies of equal magnitude? If yes, reintroduce mild asymmetry (e.g., -4% = 8, +4% = 6).

**File:** `signals/market_trend.py`

---

### Think about: Reducing GPT weight from 50% to 40%

GPT is 50% of the composite. If OpenAI has a bad day, half your signal is compromised. The quantitative factors (IV/RV, Trend) are deterministic and reproducible.

**What to do:**
- Run `validate_outcomes.py` for 1-2 months
- Run `python backtest.py --sweep` to see how quant factors alone perform
- Analyze: does GPT score actually correlate with large overnight moves?
- If GPT adds marginal value → change to `{'iv_rv': 0.35, 'trend': 0.25, 'gpt': 0.40}`
- If GPT is clearly predictive → keep at 50%

**File:** `signal_engine.py` line 63

---

### Think about: Upgrading to real-time data

The trading window is only 1 hour. With 15-min delayed data, at 2:00 PM you're seeing 1:45 PM prices. In fast markets, IV/RV ratio and intraday range are stale.

**What to do:**
- Check if 15-min delay is actually causing wrong signals (look at outcome data for high-vol days)
- If yes, upgrade Polygon plan to real-time indices (+$50-100/mo)
- Or add Yahoo Finance real-time as a secondary cross-check

**File:** `data/market_data.py`

---

### Think about: Friday signal accuracy vs other days

Weekend exposure is ~64 hours vs the usual ~16 hours overnight. If Friday signals have a significantly higher WRONG_TRADE rate, consider adding a configurable Friday modifier (e.g., +1.5 to composite score on Fridays, biasing toward SKIP or CONSERVATIVE).

**What to do:**
- After 1-2 months of data, run `validate_outcomes.py --report`
- Filter outcome data for Friday signals vs Mon-Thu signals
- Compare WRONG_TRADE rates and average overnight move magnitudes
- If Friday is meaningfully worse → add a `FRIDAY_SCORE_MODIFIER` to `signal_engine.py`
- If Friday is comparable → leave as-is

**File:** `signal_engine.py`

---

### Think about: Paper vs live execution quality

Running paper and live OA bots in parallel with the same recipe. After 1 month, compare fill quality and slippage (see [OPERATIONS.md](OPERATIONS.md) "Compare Paper vs Live" section).

**What to decide:**
- Is OA's execution quality acceptable? (slippage < 5% of premium = good)
- Are there specific market conditions where live fills are significantly worse? (e.g., high VIX days, end of day)
- If execution is consistently poor → investigate alternative brokers or direct broker API

---

### IN PROGRESS: Exit strategy tuning — parallel bot experiment

Running two OA bots (paper + live each) side by side to test different exit settings. Both receive the same signal webhooks. See [OPERATIONS.md](OPERATIONS.md) "Scaling Path" section for the full experimental design.

| Bot | Profit Targets | Stop Losses | Touch Monitor |
|---|---|---|---|
| Original | Aggressive 15% / Normal 20% / Conservative 40% | 75% / 100% / 150% | $40 ITM, 80% max loss |
| Test | Aggressive 30% / Normal 30% / Conservative 40% | 75% / 100% / 130% | $15 ITM, 65% max loss |

**Hypothesis:** The original bot is too conservative on wins (small profit targets) and too loose on Conservative losses (wide stop). The test bot captures more on winning nights and cuts losers faster.

**What to compare after 30–40 trades:**
- Total P&L per bot per tier
- Win rate per tier (did wider profit targets reduce win rate?)
- Average win $ vs average loss $ per tier
- Max drawdown
- Break-even win rate: Original Aggressive/Normal need 83.3%, Test needs 71.4%/76.9%/76.5%

**Decision:** Pick the winner, kill the loser, then proceed with the scaling path.

---

## Future Edge Exploration (After Core Is Validated)

These are potential signal enhancements to explore once the current system has 2-3 months of data and the core signal is proven stable. Ordered by estimated impact and implementation difficulty.

### Explore: VVIX as a second-order risk filter

VIX tells you how much SPX is expected to move. VVIX tells you how much *VIX itself* is expected to move. High VVIX (>120) means vol-of-vol is elevated — even if VIX looks calm at 16, it could spike to 25 overnight. Your iron condor's short strikes get destroyed by vol expansion, not just by SPX moving.

**Why this matters for overnight condors:** You enter at 2 PM and hold through the night. VVIX captures the risk of a VIX gap-up on the open (e.g., geopolitical news overnight), which IV/RV ratio alone doesn't see.

**What to do:**
- Add VVIX fetch from Polygon (`I:VVIX`) alongside existing VIX data
- Track VVIX in Sheets for 1 month alongside outcomes
- If WRONG_TRADE correlates with VVIX > 120 → add as a modifier to IV/RV score (e.g., +2 when VVIX > 120)
- Could also serve as a hard gate (like VIX >= 25 gate) at extreme levels (VVIX > 140)

**Data:** Available from Polygon on same plan (`I:VVIX` snapshot)
**File:** `data/market_data.py`, `signals/iv_rv_ratio.py`

---

### Explore: Overnight-specific vol decomposition (close-to-open vs open-to-close)

Your IV/RV ratio uses daily close-to-close returns to calculate RV. But your actual exposure is overnight only (2 PM → 10 AM). Daily RV includes the intraday session, which is irrelevant to your P&L.

**The insight:** If you decompose returns into close-to-open (overnight) and open-to-close (intraday), you can calculate an overnight-specific RV. If IV is rich relative to *overnight* RV specifically — not daily RV — that's a stronger signal for your strategy.

**What to do:**
- Fetch daily bars from Polygon (already available) — open and close prices
- Calculate overnight RV: std(log(open_t / close_t-1)) * sqrt(252) * 100
- Compare IV/overnight_RV ratio vs IV/daily_RV ratio as predictors
- If overnight-specific ratio has better CORRECT_TRADE correlation → replace current RV calculation

**Data:** Already available (Polygon daily bars have open + close)
**File:** `signals/iv_rv_ratio.py`

---

### Explore: VRP trend (expanding vs compressing)

The current IV/RV ratio is a snapshot — it tells you if vol is rich *right now*. But the *direction* matters: is the premium expanding (getting richer = better entry) or compressing (edge shrinking = be cautious)?

**What to do:**
- Track 5-day rolling IV/RV ratio (already have the data, just need the rolling window)
- If today's ratio > 5-day average → VRP expanding → slight positive modifier
- If today's ratio < 5-day average → VRP compressing → slight negative modifier
- This is a refinement to the existing IV/RV indicator, not a new indicator

**Data:** Derived from existing IV/RV history
**File:** `signals/iv_rv_ratio.py`

---

### Explore: Cross-asset regime detection (TNX, DXY)

SPX VIX alone doesn't distinguish between risk regimes. A selloff caused by rising rates (TNX up + SPX down) behaves differently overnight than a selloff caused by growth fears (TNX down + SPX down). Rate-driven selloffs often stabilize overnight; growth scares tend to cascade.

**What to do:**
- Add TNX (10Y yield) and optionally DXY (dollar index) from Polygon
- Define simple regime flags: "risk-off" (TNX down + SPX down), "rate-shock" (TNX up + SPX down), "calm" (low moves in both)
- Feed as a modifier to trend score or GPT prompt context
- Analyze: do specific regimes correlate with higher WRONG_TRADE rates?

**Data:** Available from Polygon (`I:TNX`, `I:DXY`)
**File:** `data/market_data.py`, `signals/market_trend.py`

---

### Explore: Realized gap vs implied gap tracking (edge decay monitor)

The entire strategy's edge rests on the premise that implied overnight vol consistently overestimates realized overnight moves. If this gap narrows over time (more overnight sellers = less premium), the edge erodes.

**What to do:**
- For each trading day, compute: implied overnight move (from VIX1D) vs actual overnight move (SPX close → next open)
- Track a rolling ratio of implied/realized gap over 30 days
- If the ratio trends toward 1.0 → the edge is compressing → consider pausing or tightening tiers
- This is a meta-signal about the strategy itself, not a trade signal

**Data:** Already have both sides (VIX1D in Sheets, overnight move in validate_outcomes)
**File:** `validate_outcomes.py` (add to accuracy report)

---

### Explore: ES futures overnight monitoring for early exit

OA exits at 10 AM ET or on profit/stop triggers. But you have no visibility into the overnight session between 4 PM and 9:30 AM. ES futures (E-mini S&P 500) trade nearly 24 hours and track SPX.

**What to do:**
- Set up a lightweight monitor that checks ES futures at midnight and 6 AM ET
- If |ES move from SPX close| > 0.60% → send Slack alert (potential blown condor)
- Future: if OA supports API-triggered closes, auto-close when ES breaches breakeven threshold
- This doesn't change the signal — it's a risk management layer between entry and exit

**Data:** ES futures via Polygon or a free futures API
**Complexity:** New service (separate cron job), not part of the signal engine

---

## Completed

- ~~Reduce GPT non-determinism~~ — temperature 0.3 → 0.1, plus confirmation pass
- ~~Add VIX term structure~~ — VIX1D/VIX ratio with inversion detection
- ~~Handle API errors conservatively~~ — fallback changed to score 7/ELEVATED
- ~~Signal consistency / whiplash~~ — confirmation pass + once-per-day webhook
- ~~Symmetrize trend scoring~~ — uses abs(change) now
- ~~Fix RV window overlap~~ — closes[11:22] for prior window
- ~~Better P&L proxy~~ — delta-based breakeven thresholds
- ~~Earnings calendar~~ — Mag 7 earnings detection via Polygon
- ~~Backtest engine~~ — `backtest.py` with GPT sweep, outcome comparison
- ~~System failure alerting~~ — `alerting.py` with webhook notifications
- ~~Web UI update~~ — reflects all new features and safety layers
- ~~Fix webhook whiplash~~ — confirmation pass before webhook, once-per-day send
- ~~Restructure docs~~ — README.md (architecture), TODO.md (decisions), OPERATIONS.md (runbook)
- ~~Set up Slack alerting webhook~~ — incoming webhook configured and tested
- ~~Validation uses 10 AM exit price~~ — matches OA time-based exit instead of 9:30 AM open
- ~~Document OA exit parameters~~ — profit/stop/touch/time exit settings as code constants
- ~~Scaling path documented~~ — 4-phase roadmap in OPERATIONS.md (Test → Validate → Scale → Full)
