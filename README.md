# Ren's SPX Overnight Vol Signal

Automated decision system for selling SPX overnight iron condors. Runs on Railway, sends trade/skip signals to Option Alpha via webhooks, and logs every decision to Google Sheets for validation.

---

## How It Works (End to End)

```
Railway (24/7)
  │
  ├─ Flask web server on port 8080
  │
  └─ Background poke thread (production only)
       │
       │  Every 20 min during Mon-Fri 1:30-2:30 PM ET
       │  (fires at :30, :50, :10 of each hour)
       │
       ▼
  /option_alpha_trigger endpoint
       │
       ├─ 1. Check trading window (reject if outside)
       │
       ├─ 2. Fetch SPX data from Polygon
       │     • Snapshot: current price, intraday high/low
       │     • Aggregates: last 25 daily closes
       │
       ├─ 3. Fetch VIX1D from Polygon
       │     • 1-day forward implied volatility
       │
       ├─ 4. Fetch news from RSS (14 feeds)
       │     • 9 Yahoo Finance feeds (market + Mag 7 tickers)
       │     • 5 Google News search queries
       │
       ├─ 5. Process news through triple-layer pipeline
       │     • Layer 1: Fuzzy deduplication (85% similarity)
       │     • Layer 2: Keyword junk filter + priority tagging
       │     • Layer 3: MiniMax AI analysis (see below)
       │
       ├─ 6. Run three signal factors
       │     • Factor 1 - IV/RV Ratio (30% weight)
       │     • Factor 2 - Market Trend (20% weight)
       │     • Factor 3 - MiniMax News Risk (50% weight)
       │
       ├─ 7. Contradiction detection
       │     • Override to SKIP if GPT score >= 8
       │     • Adjust score if indicators conflict
       │
       ├─ 8. Calculate composite score (1-10)
       │
       ├─ 9. Generate signal
       │     • < 3.5  → TRADE_AGGRESSIVE
       │     • 3.5-5  → TRADE_NORMAL
       │     • 5-7.5  → TRADE_CONSERVATIVE
       │     • >= 7.5 → SKIP
       │
       ├─ 10. Fire webhook to Option Alpha
       │
       └─ 11. Log row to Google Sheets
```

---

## The Three Signal Factors

### Factor 1: IV/RV Ratio (30% weight)

Compares implied volatility to realized volatility. The core question: is overnight IV rich enough to be worth selling?

- **IV source:** VIX1D (1-day forward implied vol from Polygon, 15-min delayed)
- **RV calculation:** 10-day realized vol from log returns of SPX daily closes, annualized

| IV/RV Ratio | Base Score | Meaning |
|-------------|------------|---------|
| > 1.35 | 1 | IV extremely rich — best setup |
| 1.20-1.35 | 2 | IV rich |
| 1.10-1.20 | 3 | IV moderately rich |
| 1.00-1.10 | 4 | IV near fair value |
| 0.90-1.00 | 6 | IV slightly cheap |
| 0.80-0.90 | 8 | IV cheap — bad setup |
| < 0.80 | 10 | IV very cheap — don't sell |

**RV change modifier** (requires 21+ days of history): compares current 10-day RV to prior 10-day RV. Rising RV (+15-30%) adds +2, sharply rising (+30%+) adds +3, falling RV (-20%+) subtracts -1.

### Factor 2: Market Trend (20% weight)

Measures 5-day momentum and intraday range. Iron condors profit from quiet markets, so large moves in either direction score higher (worse).

| 5-Day Change | Base Score |
|-------------|------------|
| -1% to +1% | 1 (quiet — ideal) |
| +1% to +2% | 2 |
| -2% to -1% | 2 |
| +2% to +4% | 3 |
| -4% to -2% | 4 |
| > +4% | 5 |
| < -4% | 7 (sharp selloff) |

**Intraday range modifier:** >1.5% adds +2, 1.0-1.5% adds +1.

Note: downside moves score higher than equivalent upside moves. This is intentional — crashes tend to be sharper and more volatile than rallies.

### Factor 3: MiniMax News Risk (50% weight)

The dominant factor. Calls MiniMax AI (default model: MiniMax-M2.1) with a prompt that implements a 4-responsibility framework:

1. **Duplication safety net** — catches duplicates the algo layer missed
2. **Commentary filter** — removes analysis of old events, speculation, advice
3. **Significance classification** (1-5 scale) — rates each event's potential SPX impact
4. **Time-decay assessment** — estimates how much of each event is already "priced in"

Returns a risk score (1-10) and category (VERY_QUIET / QUIET / MODERATE / ELEVATED / EXTREME).

**Calibration:** raw scores >= 9 are kept as-is, >= 7 get -0.5, <= 3 get +0.5.

**Fallback:** on API error or no news, defaults to score 5 (MODERATE).

### Composite Score

```
composite = (IV/RV × 0.30) + (Trend × 0.20) + (GPT × 0.50)
```

Clamped to 1.0-10.0.

### Contradiction Detection

Before the composite score determines the signal, the system checks for contradictions between the three factors:

| Rule | Trigger | Action |
|------|---------|--------|
| GPT Extreme | GPT score >= 8 | Force SKIP (override) |
| GPT + Trend Conflict | GPT >= 6 AND Trend >= 5 | +1.5 to composite |
| High Dispersion | Spread between any two factors >= 6 | +1.0 to composite |
| IV Cheap | IV/RV score >= 8 | +1.0 to composite |

The GPT Extreme rule is a hard override — it produces SKIP regardless of the composite math. The others are adjustments that push the composite toward caution.

### Signal → Trade Sizing

| Signal | Composite | Width | Delta |
|--------|-----------|-------|-------|
| TRADE_AGGRESSIVE | < 3.5 | 20pt | 0.18 |
| TRADE_NORMAL | 3.5-5.0 | 25pt | 0.16 |
| TRADE_CONSERVATIVE | 5.0-7.5 | 30pt | 0.14 |
| SKIP | >= 7.5 | — | — |

Each signal fires a distinct Option Alpha webhook URL. Option Alpha handles the actual trade execution.

---

## News Processing Pipeline

Raw articles go through three layers before being scored:

### Layer 1: Algorithmic Deduplication (`processing/news_dedup.py`)
- Normalizes titles (lowercase, strip punctuation)
- Sorts by recency then source priority (Reuters > Bloomberg > Google > Yahoo > CNBC > MarketWatch)
- Fuzzy matches at 85% similarity threshold using `difflib.SequenceMatcher`
- Keeps one article per event

### Layer 2: Keyword Filter (`processing/news_filter.py`)
- **Removes junk:** "secret to", "trick to", "X ways to", "you won't believe", "shocking", old recaps
- **Tags HIGH priority:** earnings beats/misses, guidance changes, stock moves >10%, Mag 7 news, M&A, SEC/FDA decisions

### Layer 3: MiniMax AI Analysis (`signals/gpt_news.py`)
- Top 30 filtered articles formatted with recency labels and sent to MiniMax
- AI performs its own dedup check, filters commentary, classifies significance, assesses time decay
- Returns a structured JSON risk assessment

---

## Data Sources

| Data | Source | Cost | Delay |
|------|--------|------|-------|
| SPX price + history | Polygon/Massive Indices Starter | $49/mo | 15-min |
| VIX1D | Polygon/Massive Indices Starter | (included) | 15-min |
| News | Yahoo Finance RSS + Google News RSS | Free | Real-time |
| AI analysis | MiniMax API (MiniMax-M2.1) | Usage-based | ~5-15s |

---

## Project Structure

```
GPT4_TradeIndicator/
├── app.py                      # Flask server, routes, poke scheduler
├── signal_engine.py            # Composite score, contradiction detection, signal generation
├── webhooks.py                 # Option Alpha webhook dispatch
├── sheets_logger.py            # Google Sheets logging (20+ columns per signal)
├── validate_outcomes.py        # Backfill next-day SPX data + accuracy report
├── Procfile                    # Railway: "web: python app.py"
├── requirements.txt            # Python dependencies
├── .config.example             # Template for local secrets (INI format)
│
├── config/
│   ├── __init__.py
│   └── loader.py               # Loads .config file (local) or env vars (Railway)
│
├── data/
│   ├── __init__.py
│   ├── market_data.py          # Polygon API: SPX snapshot + aggregates, VIX1D
│   └── news_fetcher.py         # RSS: 9 Yahoo Finance + 5 Google News feeds
│
├── processing/
│   ├── __init__.py
│   ├── pipeline.py             # Orchestrates Layer 1 → Layer 2 → format for GPT
│   ├── news_dedup.py           # Layer 1: fuzzy deduplication
│   └── news_filter.py          # Layer 2: keyword junk filter + priority tagging
│
├── signals/
│   ├── __init__.py
│   ├── iv_rv_ratio.py          # Factor 1: IV/RV ratio (30%)
│   ├── market_trend.py         # Factor 2: 5-day momentum + intraday range (20%)
│   └── gpt_news.py             # Factor 3: MiniMax news risk analysis (50%)
│
├── tests/
│   ├── __init__.py
│   └── test_signal_validation.py   # 34 tests: scoring, contradictions, scenarios
│
└── docs/
    └── GOOGLE_SHEETS_SETUP.md
```

---

## Configuration

Two-tier config: local `.config` file takes precedence over environment variables.

### Required

| Key | What it is |
|-----|-----------|
| `MINIMAX_API_KEY` | MiniMax AI API key |
| `POLYGON_API_KEY` | Polygon/Massive market data key |
| `TRADE_AGGRESSIVE_URL` | Option Alpha webhook for score < 3.5 |
| `TRADE_NORMAL_URL` | Option Alpha webhook for score 3.5-5.0 |
| `TRADE_CONSERVATIVE_URL` | Option Alpha webhook for score 5.0-7.5 |
| `NO_TRADE_URL` | Option Alpha webhook for score >= 7.5 |

### Optional

| Key | What it is |
|-----|-----------|
| `MINIMAX_MODEL` | Override AI model (default: MiniMax-M2.1) |
| `GOOGLE_SHEET_ID` | Google Sheet ID for signal logging |
| `GOOGLE_CREDENTIALS_JSON` | Service account JSON (single line) |

### Local vs Production behavior

When `.config` file exists (local dev):
- Trading window opens to 24 hours (bypass time check)
- Background poke scheduler is disabled (manual trigger only)
- Environment label shows "Local (Test)"

When running on Railway (no `.config` file):
- Trading window enforced: Mon-Fri 1:30-2:30 PM ET
- Poke scheduler active every 20 min
- Environment label shows "Railway Production"

---

## API Endpoints

| Route | Method | What it does |
|-------|--------|-------------|
| `/` | GET | HTML dashboard with system info |
| `/health` | GET | JSON health check (no API calls) |
| `/option_alpha_trigger` | GET, POST | Run full signal pipeline, fire webhook, log to Sheets |
| `/test_polygon_delayed` | GET | Test Polygon connectivity (SPX + VIX1D) |

---

## Google Sheets Logging

Every signal run appends a row with 27 columns:

| Column | Description | When filled |
|--------|-------------|-------------|
| Timestamp_ET | Signal generation time | At signal time |
| Signal | TRADE_AGGRESSIVE / NORMAL / CONSERVATIVE / SKIP | At signal time |
| Should_Trade | True/False | At signal time |
| Reason | Human-readable reason | At signal time |
| Composite_Score | 1.0-10.0 | At signal time |
| Category | EXCELLENT through HIGH | At signal time |
| IV_RV_Score | Factor 1 score | At signal time |
| IV_RV_Ratio | e.g. 1.234 | At signal time |
| VIX1D | Implied vol value | At signal time |
| Realized_Vol_10d | 10-day RV | At signal time |
| Trend_Score | Factor 2 score | At signal time |
| Trend_5d_Chg_Pct | e.g. +1.23% | At signal time |
| GPT_Score | Factor 3 score | At signal time |
| GPT_Category | VERY_QUIET through EXTREME | At signal time |
| GPT_Key_Risk | Top risk identified by AI | At signal time |
| Webhook_Success | True/False | At signal time |
| SPX_Current | SPX price at signal time | At signal time |
| Raw_Articles | Number of raw RSS articles | At signal time |
| Sent_To_GPT | Number after filtering | At signal time |
| GPT_Reasoning | AI reasoning text (500 char max) | At signal time |
| Contradiction_Flags | Any contradiction rules triggered | At signal time |
| Override_Applied | SKIP if override fired, else None | At signal time |
| Score_Adjustment | Points added from contradictions | At signal time |
| SPX_Next_Open | Next trading day's SPX open | By validate_outcomes.py |
| SPX_Next_Close | Next trading day's SPX close | By validate_outcomes.py |
| Overnight_Move_Pct | Absolute % move from entry to next open | By validate_outcomes.py |
| Outcome_Correct | CORRECT_TRADE / WRONG_TRADE / CORRECT_SKIP / WRONG_SKIP | By validate_outcomes.py |

Setup instructions: see `docs/GOOGLE_SHEETS_SETUP.md`.

---

## What You Need to Manually Run and Maintain

### Runs automatically (no action needed)

- Signal generation: Railway poke thread triggers every 20 min during trading window
- Webhook dispatch: fires automatically after each signal
- Sheets logging: appends a row automatically after each signal
- Contradiction detection: runs automatically as part of signal pipeline

### Run manually: Outcome Validation

The system cannot log what happened overnight at the time it generates a signal (tomorrow hasn't happened yet). You need to run `validate_outcomes.py` periodically to backfill next-day results.

**Recommended cadence:** Every 2 weeks, or whenever you want to check accuracy.

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
2. For rows missing outcome data: looks up the next trading day's SPX open/close from Polygon
3. Calculates overnight move: `|next_open - entry_price| / entry_price`
4. Determines if the signal was correct based on move thresholds
5. Writes the 4 outcome columns back into the Sheet
6. Prints an accuracy report by signal tier

**Where to run it:** Locally (needs `.config` file with Polygon + Google credentials). It's a one-shot script, not a server process.

### Run manually: Tests

```bash
python -m pytest tests/test_signal_validation.py -v
```

Run after making changes to signal logic to verify scoring still behaves correctly. 34 tests covering all indicators, composite math, contradictions, and real-world scenarios.

### Monitor: Railway Logs

Check Railway deploy logs periodically to verify:
- Poke thread is firing during trading hours
- Polygon data fetches are succeeding
- MiniMax API calls are returning valid responses
- Webhooks are landing successfully

---

## Costs

| Item | Monthly Cost |
|------|-------------|
| Railway hosting | ~$5 |
| Polygon/Massive Indices Starter | $49 |
| MiniMax API | ~$1-3 (usage-based) |
| Google Sheets | Free |
| News RSS feeds | Free |
| **Total** | **~$55-57/mo** |

---

## Dependencies

```
Flask==3.0.0
requests==2.31.0
pytz==2023.3
python-dateutil==2.8.2
gspread>=6.0.0
google-auth>=2.0.0
```
