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
