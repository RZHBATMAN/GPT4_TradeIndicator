# Operations Guide

Regular operational tasks for keeping the signal system running and validated. These are "just run it" items — no thinking or decisions required.

---

## Runs Automatically (No Action Needed)

These happen on Railway without any intervention:

- **Signal generation:** Poke thread triggers every 20 min during Mon-Fri 1:30-2:30 PM ET
- **Webhook dispatch:** Fires to Option Alpha after the first signal each day
- **Sheets logging:** Appends a row to Google Sheets after every signal
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

46 tests covering all indicator scoring, composite math, contradictions, alerting, and backtest helpers. Run this after modifying any signal logic to verify nothing broke.

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
