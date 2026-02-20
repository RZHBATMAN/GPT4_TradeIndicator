# Operations Guide

Regular operational tasks for keeping the signal system running and validated. These are "just run it" items — no thinking or decisions required.

---

## Runs Automatically (No Action Needed)

These happen on Railway without any intervention:

- **Signal generation:** Poke thread triggers at a random time between 1:30-1:39 PM ET, then at :50 and :10 as fallbacks
- **Webhook dispatch:** Fires to Option Alpha after the first signal each day (Mon-Thu only; Fridays are log-only)
- **Sheets logging:** Appends a row to Google Sheets after every signal (all days including Friday)
- **Trade_Executed tracking:** Logs whether a trade was actually placed (YES) or blocked (NO_SKIP, NO_FRIDAY, NO_VIX_GATE, NO_OA_EVENT)
- **Contradiction detection:** Runs automatically as part of signal pipeline
- **Earnings calendar check:** Queries Polygon for Mag 7 earnings before each signal
- **Confirmation pass:** Runs analysis twice, picks conservative result
- **Alerting:** Sends Slack alerts on API failures, missed signals, poke stalls

---

## Every 2 Weeks: Validate Outcomes

Backfill next-day SPX data into your Google Sheet to track signal accuracy.

```bash
# Preview what it would do (reads Sheet, doesn't write)
python validate_outcomes.py --dry-run

# Backfill all missing outcomes + print accuracy report
python validate_outcomes.py

# Just print accuracy report (if outcomes already filled)
python validate_outcomes.py --report
```

**What it does:**
1. Reads every row from your Google Sheet
2. For rows missing outcome data: fetches next trading day's SPX open/close from Polygon
3. Calculates overnight move: `|next_open - entry_price| / entry_price`
4. Determines if signal was "correct" based on breakeven thresholds
5. Writes the 4 outcome columns (SPX_Next_Open, SPX_Next_Close, Overnight_Move_Pct, Outcome_Correct)
6. Prints accuracy report by signal tier

**Where to run:** Locally (needs `.config` file with Polygon + Google credentials).

---

## As Needed: Run Backtest

Test how signal factors would have performed historically.

```bash
# Last 60 trading days, GPT stubbed at 4 (QUIET)
python backtest.py

# Last 120 trading days
python backtest.py --days 120

# Assume elevated news risk
python backtest.py --gpt-score 6

# Specific date range
python backtest.py --start 2025-06-01 --end 2025-12-31

# Sweep GPT scores 2-8 (most useful — shows sensitivity to GPT factor)
python backtest.py --sweep

# Verbose: print each day's detail
python backtest.py --sweep -v
```

**Limitation:** Factor 3 (GPT/news, 50% weight) is stubbed with a fixed score. This only backtests the quantitative factors (IV/RV and Trend). Use `--sweep` to see how results change across different assumed GPT levels.

**Where to run:** Locally (needs `.config` file with Polygon key).

---

## Before Deploying: Test Locally First

**`main` branch is production** — it's what Railway deploys. Never push untested changes directly to `main`.

Workflow for any code change:

1. Work on a development branch (not `main`)
2. Run the full test suite locally:
   ```bash
   python -m pytest tests/test_signal_validation.py -v
   ```
3. Run the app locally and trigger a signal to do a full system-wise test:
   ```bash
   python app.py
   # Then visit http://localhost:8080/option_alpha_trigger in your browser
   ```
   Local mode uses `.config` for credentials, opens the 24hr trading window, and disables the poke scheduler — so you can trigger manually any time.
4. Verify the signal output, Sheets logging, and factor scores look correct
5. Once confident, merge/push to `main` — Railway auto-deploys

---

## After Code Changes: Run Tests

```bash
python -m pytest tests/test_signal_validation.py -v
```

53 tests covering all indicator scoring, composite math, contradictions, alerting, backtest helpers, and outcome evaluation. Run this after modifying any signal logic to verify nothing broke.

---

## Annually (Dec/Jan): Update OA Event Calendar

The file `data/oa_event_calendar.py` contains static date sets for FOMC meetings, CPI releases, and NYSE early closes. These match the Option Alpha decision recipe gates. **Update this file once a year** when the Fed and BLS publish their next-year schedules.

**When to do it:** Late November / early December, when both agencies have published their next-year calendars.

**Sources:**
- FOMC dates: https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
- CPI dates: https://www.bls.gov/schedule/news_release/cpi.htm
- NYSE early closes: https://www.nyse.com/markets/hours-calendars

**What to update in `data/oa_event_calendar.py`:**
1. Add the new year's FOMC meeting dates to `FOMC_DATES` (both days of each 2-day meeting)
2. Add the new year's CPI release dates to `CPI_DATES`
3. Add the new year's NYSE early close dates to `EARLY_CLOSE_DATES` (typically: day before July 4, Black Friday, Christmas Eve)

**If you forget:** The calendar won't detect event gates for dates it doesn't know about. Trades will still fire on FOMC/CPI days, but `Trade_Executed` will say `YES` instead of `NO_OA_EVENT` — so the Sheet log won't accurately reflect what OA actually did. The trades themselves are still gated by OA's own recipe, so no real harm, just inaccurate logging.

---

## First Month of Live Trading: Compare Paper vs Live

Run both paper and live OA bots with the **exact same recipe** side by side. The webhook fires once — both bots receive it. After 1 month, compare the two to measure your real execution cost.

**What to compare:**
- **Fill quality:** For each trade, compare the premium collected on paper vs live. Paper assumes mid-price fills; live shows what you actually got.
- **Slippage per trade:** `(paper_premium - live_premium) / paper_premium`. This is the "execution tax."
- **Win/loss agreement:** Do paper and live agree on which trades survived? If paper shows a win but live shows a loss (or vice versa), the fill difference was large enough to flip the outcome — that's a red flag.

**What the results mean:**
- Slippage consistently < 5% of premium → execution is fine, OA is working well
- Slippage consistently 10-20% → worth investigating OA's order routing or timing
- Paper and live disagree on wins/losses → the fill difference is eating into your edge, consider execution alternatives (different broker, different order type, or direct broker API in the future)

**Where to check:** OA's position history / trade log for both paper and live bots.

---

## Periodically: Check Railway Logs

Check Railway deploy logs to verify the system is healthy:

- Poke thread is firing during trading hours
- Polygon data fetches are succeeding
- OpenAI API calls are returning valid responses
- Webhooks are landing successfully
- Confirmation pass results (do the two passes agree?)

If you set up Slack alerting, you'll get automatic notifications for failures. But manual log checks are still useful for spotting patterns (e.g., OpenAI consistently timing out).

---

## Periodically: Check Slack for Warnings

Once Slack alerting is configured (see [TODO.md](TODO.md)), glance at your alert channel occasionally. The system sends alerts for:

- **"No Signal Generated Today"** — trading window ended with no signal. Something broke.
- **"Polygon_SPX API Down" / "Polygon_VIX1D API Down"** — market data failed 2+ times in a row.
- **"Poke Thread Stale"** — scheduler hasn't fired in 30+ min during trading hours. Railway may have restarted.

If you see these frequently, dig into Railway logs for the root cause. If you never see them, the system is healthy.

---

## Periodically: Review Google Sheets

Open your signal log Sheet and scan for:

- **Contradiction_Flags column:** How often are contradictions firing? If it's every day, thresholds may be too aggressive.
- **Override_Applied column:** How often is SKIP being forced by the GPT >= 8 rule?
- **Outcome_Correct column:** After running validate_outcomes.py, check win/loss distribution by tier.
- **GPT_Score column:** Is GPT consistently scoring high or low? If it's always 3-4, it may not be adding value.

---

## Scaling Path: From Test to Income

A disciplined, data-driven path from initial testing to income generation. **Do not skip phases.** Each phase has a gate — only advance when the data supports it.

### Phase 1: Test (Weeks 1–8)

**Setup:** 1 contract, $5,000 capital, paper + live parallel bots

**What you're doing:** Gathering data on two sets of OA exit settings running side by side. Both bots receive the same signal webhooks — the only difference is exit behavior.

| Bot | Profit Targets | Stop Losses | Touch Monitor |
|---|---|---|---|
| Original | Aggressive 15% / Normal 20% / Conservative 40% | 75% / 100% / 150% | $40 ITM, 80% max loss |
| Test | Aggressive 30% / Normal 30% / Conservative 40% | 75% / 100% / 125% | $15 ITM, 65% max loss |

**Gate to Phase 2:** 30–40+ trades completed. Compare:
- Total P&L per bot
- Win rate per tier
- Average win $ vs average loss $
- Max drawdown (worst consecutive loss streak)

Pick the winner. Kill the loser.

### Phase 2: Validate (Weeks 8–12)

**Setup:** 2–3 contracts, $10,000–15,000 capital

**What you're doing:** Confirming the winning exit settings hold at slightly larger size. Slippage and fill quality may change with more contracts — this phase catches that.

**Gate to Phase 3:**
- Win rate per tier holds within 3% of Phase 1 numbers
- Slippage per trade stays < 5% of premium (compare paper vs live)
- No drawdown > 30% of allocated capital
- `validate_outcomes.py --report` shows consistent accuracy

### Phase 3: Scale (Months 4–6)

**Setup:** 5–10 contracts, $25,000–50,000 capital

**What you're doing:** Generating meaningful supplemental income ($1,500–3,500/month at these sizes). SPX liquidity is not a concern — even 50 contracts is invisible in the SPX options pool.

**Gate to Phase 4:**
- 3+ months of profitable operation at this size
- Survive at least one high-VIX event (VIX spike > 20) without catastrophic drawdown
- Signal accuracy (from validation reports) remains stable
- Emotional discipline: no manual overrides or panic exits outside the system

### Phase 4: Full (Month 6+)

**Setup:** 10–20 contracts, $50,000–100,000 capital

**What you're doing:** Income replacement candidate ($3,000–7,000/month target). At this level, risk management is everything.

**Rules at full scale:**
- Never risk more than 5% of total capital on a single night
- Maintain 6-month expense reserve OUTSIDE the trading account (untouchable)
- If drawdown exceeds 20% of peak account value → drop back to Phase 3 sizing until recovery
- Review `validate_outcomes.py --report` monthly — if win rate drops below break-even WR for 2 consecutive months, stop trading and reassess

### Key Principles

1. **The system earns the right to more capital.** You don't decide to scale — the data decides.
2. **Scale up slowly, scale down fast.** It takes months to earn a size increase, but a single bad week should trigger an immediate size reduction.
3. **Never add capital to recover losses.** If a phase is unprofitable, the answer is better signals/exits, not more contracts.
4. **The overnight edge is real but finite.** Variance risk premium exists, but it's not infinite free money. Respect the tail.
