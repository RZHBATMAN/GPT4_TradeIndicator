# Code Audit & Roadmap — SPX Overnight Vol Premium Signal Engine

**Date:** 2026-03-06
**Branch:** `feature/mac-mini`
**Auditor:** Claude (CTO friend)

---

## What Was Done (Implemented)

### FIX 1 — GPT prompt had wrong entry time and hold period
**Files:** `desks/overnight_condors/signals/gpt_news.py`
**Severity:** HIGH — directly affected GPT's time-decay reasoning

The GPT prompt said "2:30-3:30 PM entry" and "~16 hours until 9:30 AM". The actual system trades at 1:30-2:30 PM and exits at 10:00 AM (~19.5-20.5h hold). This was a leftover from an earlier version. GPT was told news had 1 hour less time to be priced in, making it slightly more cautious than warranted on Sig 4-5 events.

**Changed to:** "1:30-2:30 PM ET entry" and "~19.5-20.5 hours until 10:00 AM ET tomorrow"

---

### FIX 2 — Webhook failure = lost trading day with no recovery
**Files:** `core/webhooks.py`, `desks/overnight_condors/desk.py`
**Severity:** HIGH — one network blip could silently lose an entire trading day

**Before:** Bare `except:` swallowed all errors. After any failure, `_daily_signal_cache['webhook_sent']` was set to `True`, so no retry was ever attempted. No Slack alert was sent.

**After:**
- `webhooks.py`: 3 retry attempts with 2s/4s backoff, proper `except Exception as e:` logging
- `app.py`: Only marks `webhook_sent=True` on confirmed success
- `app.py`: On final failure, sends Slack alert via `alerting._send_alert()` and logs `NO_WEBHOOK_FAIL` to Sheets
- Next poke (20 min later) will automatically retry since `webhook_sent` stays `False`

---

### FIX 3 — Confirmation pass was nearly useless
**Files:** `desks/overnight_condors/signals/gpt_news.py`, `desks/overnight_condors/signal_engine.py`, `desks/overnight_condors/desk.py`
**Severity:** MEDIUM — paying for an extra API call with no real benefit

**Before:** Both analysis passes used identical inputs with `temperature=0.1`. GPT is nearly deterministic at that temperature — the second call returned essentially the same result. The 2-second sleep changed nothing about the inputs.

**After:** First pass uses `temperature=0.1` (deterministic baseline). Confirmation pass uses `temperature=0.4` (tests score robustness). If GPT gives 3 at temp=0.1 but 7 at temp=0.4, the score was fragile and the system correctly picks the more conservative result.

Implementation: `analyze_gpt_news()` and `run_signal_analysis()` now accept a `temperature`/`gpt_temperature` parameter.

---

### FIX 4 — GPT token costs not tracked in Sheets
**Files:** `sheets_logger.py`, `desks/overnight_condors/validate_outcomes.py`
**Severity:** LOW — nice-to-have for cost monitoring

**Before:** GPT token usage and cost were calculated and printed to Railway logs but never persisted.

**After:** Added 2 new columns to Sheets: `GPT_Tokens` (total tokens) and `GPT_Cost` (e.g. `$0.0023`). These sit between `GPT_Reasoning` and `Contradiction_Flags`. All `validate_outcomes.py` column indices shifted by +2 to account for the new columns.

**Note:** The header row in your Google Sheet will auto-update on next signal (via `_ensure_header()`). Existing rows with old schema will still work — outcome backfill uses column indices, not names.

---

### FIX 5 — NFP (Non-Farm Payrolls) not gated
**Files:** `core/data/oa_event_calendar.py`
**Severity:** MEDIUM — Thursday trades were exposed to Friday 8:30 AM NFP risk

**Before:** FOMC and CPI were gated, but NFP was not. If you sell a condor Thursday afternoon and NFP drops at 8:30 AM Friday with a 100K+ surprise, the overnight move can blow through your condor.

**After:** Added `NFP_DATES` set (36 dates, 2025-2027) and `NFP_NEXT_DAY` gate. Fires on Thursdays before NFP Friday. On NFP Friday itself, the trade fires normally (NFP has already been released at 8:30 AM before our 1:30 PM entry).

The `Trade_Executed` column will now show `NO_OA_EVENT (NFP release tomorrow)` when active.

---

### FIX 6 — Bare `except:` in webhooks
**Files:** `core/webhooks.py`
**Severity:** LOW — bad practice, masked errors

Changed bare `except:` to `except Exception as e:` with error logging. This was fixed as part of the webhook retry rewrite (FIX 2).

---

## What Was Found But Not Fixed (Deferred)

### Earnings calendar double-counts with GPT — INTENTIONAL, KEEP
When Mag 7 reports today, the calendar adds +2 to GPT score. GPT also sees the earnings news and scores it. This is intentional extra conservatism on earnings days. Owner confirmed.

### Railway restart causes duplicate webhooks — LOW RISK, SKIP FOR NOW
`_daily_signal_cache` is in-memory. A Railway restart mid-day resets it, potentially sending a duplicate webhook. No incidents reported yet. Fix when needed: persist cache to a JSON file.

### Thread safety on `_daily_signal_cache` — LOW RISK
Flask with `threaded=True` means theoretical race conditions, but the poke thread makes sequential HTTP requests so this is unlikely in practice.

### 15-minute delayed data — KNOWN LIMITATION
Polygon Massive Indices Starter provides 15-min delayed SPX/VIX1D/VIX. During volatile markets, the VIX1D reading could be stale. Not fixable without upgrading to real-time plan (~2x cost).

---

## Future Roadmap

### Phase 1 — Data Quality
- [ ] Upgrade Polygon plan to real-time (eliminates 15-min delay)
- [ ] Add news source diversification (Benzinga, NewsAPI)
- [ ] Store raw news snapshots per signal (enables GPT replay for full backtesting)

### Phase 2 — Risk Management
- [ ] Drawdown circuit breaker: pause after N consecutive losses or X% weekly drawdown
- [ ] Dynamic sizing: adjust delta/width based on current VIX level (not just signal tier)
- [ ] Post-webhook monitoring: watch for late-breaking news, send cancel signal to OA
- [ ] Intraday VIX1D trend: track whether VIX1D is rising/falling throughout the day

### Phase 3 — Validation & Analytics
- [ ] Automated daily `validate_outcomes.py` run (Railway cron or scheduled task)
- [ ] Import actual OA fills/exits for real PnL (vs 10 AM proxy)
- [ ] Performance dashboard with historical charts
- [ ] Regime detection: classify low-vol vs high-vol environments, adjust thresholds
- [ ] Friday/weekend profitability analysis: monitor Friday win rate vs other days
- [ ] Poke timing optimization: analyze if first or later signal makes better decisions

### Phase 4 — Strategy Evolution
- [ ] Multi-model confirmation: use Claude or Gemini for second-pass analysis
- [ ] Directional bias: use GPT's `direction_risk` to skew condor wings
- [ ] Adaptive thresholds: adjust composite score breakpoints based on rolling accuracy
- [ ] Alternative structures: put credit spread (directional) when GPT indicates direction

---

## Enriched Logging (Added 2026-03-08)

### Bug Fix — Missing helper functions in validate_outcomes.py
**File:** `validate_outcomes.py`
**Severity:** HIGH — script would crash on any operation

`_parse_signal_date()` and `_next_weekday()` were called but never defined. Added both functions to parse Sheets timestamps and skip weekends.

### 18 New Logging Columns
**Files:** `desks/overnight_condors/signals/iv_rv_ratio.py`, `desks/overnight_condors/signals/market_trend.py`, `desks/overnight_condors/signal_engine.py`, `sheets_logger.py`, `desks/overnight_condors/desk.py`

Added 18 new columns **at the END** of SHEET_HEADERS (indices 32-49) to capture intermediate values that were previously computed but discarded:

- **Factor sub-scores:** IV_RV_Base_Score, RV_Modifier, Term_Modifier, Term_Structure_Ratio, Trend_Base_Score, Intraday_Modifier, Intraday_Range_Pct
- **GPT details:** GPT_Raw_Score (before calibration), GPT_Direction_Risk, GPT_Pre_Earnings_Score (before +1/+2)
- **Earnings:** Earnings_Modifier, Earnings_Tickers
- **Confirmation pass:** Pass1_Composite, Pass1_Signal, Pass2_Composite, Pass2_Signal, Passes_Agreed
- **Meta:** Day_Of_Week

These enable the analysis script to do factor attribution, parameter sensitivity, and confirmation pass effectiveness analysis.

### Comprehensive Analysis Script
**File:** `analyze_signals.py` (NEW)

Read-only analytics engine with 11 sections:
1. Executive Summary — win rate, P&L proxy, streaks
2. Factor Attribution — correlation, sub-score impact
3. Parameter Sensitivity — sweep tier boundaries and factor weights
4. GPT Calibration — raw vs calibrated, score distribution
5. Confirmation Pass — agreement rate, value assessment
6. Day-of-Week — per-day win rates
7. VIX Regime — low/normal/elevated/high performance
8. Earnings Modifier — accuracy on earnings vs non-earnings days
9. Contradiction Detection — override/adjustment effectiveness
10. Score Distribution — composite histogram, optimal boundaries
11. Actionable Recommendations — auto-generated tuning suggestions

Usage: `python analyze_signals.py` (full report) or `python analyze_signals.py --section factor` (single section)

---

## Files Modified in This Audit

| File (current path after multi-desk restructuring) | Changes |
|------|---------|
| `desks/overnight_condors/signals/gpt_news.py` | Fixed entry/exit times in prompt; added `temperature` parameter |
| `desks/overnight_condors/signal_engine.py` | Added `gpt_temperature` kwarg passthrough; store `pre_earnings_score` |
| `core/webhooks.py` | Full rewrite: retry with backoff, proper exception handling |
| `app.py` | Multi-desk rewrite (~200 lines), tabbed dashboard, desk route registration |
| `sheets_logger.py` | Added `GPT_Tokens` + `GPT_Cost` columns; added 18 enriched logging columns at END |
| `desks/overnight_condors/validate_outcomes.py` | Shifted column indices +2 for GPT cost columns; added missing `_parse_signal_date` + `_next_weekday` |
| `core/data/oa_event_calendar.py` | Added `NFP_DATES` + `NFP_NEXT_DAY` gate |
| `desks/overnight_condors/signals/iv_rv_ratio.py` | Added `base_score` + `rv_modifier` to return dict |
| `desks/overnight_condors/signals/market_trend.py` | Added `base_score` + `intraday_modifier` to return dict |
| `desks/overnight_condors/analyze_signals.py` | **NEW** — comprehensive 11-section analysis script |

---

## How to Verify After Deploy

1. Trigger `/overnight/trigger` (or `/option_alpha_trigger` backward compat) and check Railway logs:
   - GPT prompt should show "1:30-2:30 PM ET entry" and "10:00 AM ET tomorrow"
   - Confirmation pass should log `temp=0.4`
   - Webhook should show retry attempts if it fails

2. Check tabbed dashboard at `/`:
   - Should show "Ren's Trading Firm" with Overview, Overnight Condors, and 0DTE Butterflies tabs
   - Overview tab shows desk cards with last signal info
   - Each desk tab shows strategy details and endpoints

3. Check Google Sheets:
   - "Sheet1" tab: Header row should auto-update with all columns; desk 1 signals log here
   - "0DTE_Butterflies" tab: auto-created on first desk 2 signal; simplified ~13 columns

4. Trigger `/butterflies/trigger`:
   - Should return VIX-based signal (TRADE_AGGRESSIVE/NORMAL/CONSERVATIVE/SKIP)
   - Should log to "0DTE_Butterflies" tab
   - Gracefully skips webhook if DESK2_* URLs not configured

5. On a Thursday before NFP Friday:
   - `Trade_Executed` should show `NO_OA_EVENT (NFP release tomorrow)` (desk 1 only)

6. Test webhook failure:
   - Temporarily set a bad webhook URL
   - Trigger signal — should see 3 retry attempts in logs
   - Should receive Slack alert
   - Next poke should retry (since `webhook_sent` stays `False`)

7. Run analysis/validation scripts:
   - `python analyze_signals.py` — should show graceful "insufficient data" messages
   - `python validate_outcomes.py --report` — backfill summary

8. Run tests:
   - `python -m pytest tests/ -v` — 99 tests should pass
