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
- MiniMax API calls are returning valid responses
- Webhooks are landing successfully
- Confirmation pass results (do the two passes agree?)

If you set up Slack alerting, you'll get automatic notifications for failures. But manual log checks are still useful for spotting patterns (e.g., MiniMax consistently timing out).

---

## Periodically: Review Google Sheets

Open your signal log Sheet and scan for:

- **Contradiction_Flags column:** How often are contradictions firing? If it's every day, thresholds may be too aggressive.
- **Override_Applied column:** How often is SKIP being forced by the GPT >= 8 rule?
- **Outcome_Correct column:** After running validate_outcomes.py, check win/loss distribution by tier.
- **GPT_Score column:** Is MiniMax consistently scoring high or low? If it's always 3-4, it may not be adding value.

---

## One-Time Setup Tasks

These only need to be done once:

### Set up Slack alerting
1. Go to Slack > Apps > Incoming Webhooks
2. Create a new webhook for your desired channel
3. Add to Railway env var: `ALERT_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL`
4. Or add to `.config` under `[WEBHOOKS]`: `ALERT_WEBHOOK_URL = https://hooks.slack.com/services/...`

### Google Sheets setup
See `docs/GOOGLE_SHEETS_SETUP.md` for full instructions.
