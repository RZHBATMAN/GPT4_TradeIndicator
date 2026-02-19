# TODO — Decisions & Tasks

Things to think about, decide on, or act on. These are not operational "run this script" items (see [OPERATIONS.md](OPERATIONS.md) for those).

---

## Setup Tasks

### Set up Slack alerting webhook
The alerting system is built and integrated but needs a webhook URL to actually send notifications.

**Steps:**
1. Go to Slack > Apps > Incoming Webhooks
2. Create a new webhook for your desired channel
3. Add to Railway env var: `ALERT_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL`
4. Or add to `.config` under `[WEBHOOKS]`: `ALERT_WEBHOOK_URL = https://hooks.slack.com/services/...`

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

### Think about: Exit strategy tuning based on OA backtests

OA backtests show the base iron condor strategy (no signal, just selling every day) performs differently depending on exit rules. Key observations:

- **Take-profit exits matter:** Capturing a fixed % of max profit early (e.g., 50-75%) rather than holding to expiration reduces tail risk. Holding to 10 AM next day is a good balance between theta capture and avoiding morning gap continuation.
- **Stop-loss impact:** Adding a stop-loss vs not having one changes the P&L distribution significantly. No stop-loss means larger max drawdowns but higher overall win rate. Stop-loss protects capital but can trigger on intraday whipsaws before the overnight move actually plays out.
- **Time-based exit at 10 AM:** Good for capturing overnight premium while avoiding intraday noise the next morning.

**What to decide:**
- Should the signal tier ALSO influence exit parameters? (e.g., AGGRESSIVE signals use tighter take-profit since conditions are ideal, CONSERVATIVE signals use wider stop-loss since more risk)
- Should different tiers use different time-based exits? (e.g., AGGRESSIVE holds longer for more premium, CONSERVATIVE exits earlier)
- This would require more granular Option Alpha automation configuration — possibly separate OA bots per tier with different exit rules

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
