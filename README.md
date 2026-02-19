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
       ├─ 3. Fetch VIX1D + VIX (30-day) from Polygon
       │     • VIX1D: 1-day forward implied volatility
       │     • VIX: 30-day IV for term structure analysis
       │
       ├─ 4. Fetch news from RSS (14 feeds)
       │     • 9 Yahoo Finance feeds (market + Mag 7 tickers)
       │     • 5 Google News search queries
       │
       ├─ 5. Process news through triple-layer pipeline
       │     • Layer 1: Fuzzy deduplication (85% similarity)
       │     • Layer 2: Keyword junk filter + priority tagging
       │     • Layer 3: OpenAI GPT analysis (see below)
       │
       ├─ 6. Run three signal factors
       │     • Factor 1 - IV/RV Ratio + Term Structure (30%)
       │     • Factor 2 - Market Trend (20%)
       │     • Factor 3 - GPT News Risk (50%)
       │
       ├─ 7. Safety layers
       │     • Mag 7 earnings calendar check
       │     • Contradiction detection between factors
       │
       ├─ 8. Confirmation pass (run analysis twice)
       │     • Uses the more conservative result
       │
       ├─ 9. Calculate composite score (1-10)
       │
       ├─ 10. Generate signal
       │     • < 3.5  → TRADE_AGGRESSIVE
       │     • 3.5-5  → TRADE_NORMAL
       │     • 5-7.5  → TRADE_CONSERVATIVE
       │     • >= 7.5 → SKIP
       │
       ├─ 11. Fire webhook to Option Alpha (once per day only)
       │
       └─ 12. Log row to Google Sheets
```

---

## The Three Signal Factors

### Factor 1: IV/RV Ratio + Term Structure (30% weight)

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

**VIX term structure modifier:** Computes VIX1D / VIX (30-day) ratio.
- Ratio > 1.10 (strong inversion): +3 — market pricing near-term danger
- Ratio 1.00-1.10 (mild inversion): +1
- Ratio < 1.00 (contango): no change — normal conditions

### Factor 2: Market Trend (20% weight)

Measures 5-day momentum and intraday range. Iron condors profit from quiet markets, so large moves in either direction score higher (worse).

| 5-Day |change| | Base Score |
|-------------|------------|
| < 1% | 1 (quiet — ideal) |
| 1-2% | 2 |
| 2-4% | 4 |
| > 4% | 7 (big move) |

Scoring is symmetric: +4% and -4% both score 7. Iron condors lose on large moves in either direction.

**Intraday range modifier:** >1.5% adds +2, 1.0-1.5% adds +1.

### Factor 3: GPT News Risk (50% weight)

The dominant factor. Calls OpenAI (default model: gpt-4o-mini, temperature=0.1) with a prompt that implements a 4-responsibility framework:

1. **Duplication safety net** — catches duplicates the algo layer missed
2. **Commentary filter** — removes analysis of old events, speculation, advice
3. **Significance classification** (1-5 scale) — rates each event's potential SPX impact
4. **Time-decay assessment** — estimates how much of each event is already "priced in"

Returns a risk score (1-10) and category (VERY_QUIET / QUIET / MODERATE / ELEVATED / EXTREME).

**Calibration:** raw scores >= 9 are kept as-is, >= 7 get -0.5, <= 3 get +0.5.

**Fallback:** on API error or no news, defaults to score 7 (ELEVATED) — no analysis = caution.

### Composite Score

```
composite = (IV/RV × 0.30) + (Trend × 0.20) + (GPT × 0.50) + contradiction_adjustment
```

Clamped to 1.0-10.0.

---

## Safety Layers

### Confirmation Pass

The signal pipeline runs twice per trigger. GPT is non-deterministic — the same news can produce different scores. Rather than trusting a single call, the system runs both, then uses the **more conservative** (higher-tier) result. This costs one extra OpenAI call but prevents a lucky low score from triggering an aggressive trade.

### Once-Per-Day Webhook

Once Option Alpha receives a webhook, it creates a label and places a trade. The system tracks whether a webhook has been sent today. Subsequent poke cycles (2nd and 3rd triggers during the window) still run the full analysis and log to Google Sheets, but do **not** send another webhook. This prevents duplicate or conflicting trades.

### Contradiction Detection

Before the composite score determines the signal, the system checks for contradictions between the three factors:

| Rule | Trigger | Action |
|------|---------|--------|
| GPT Extreme | GPT score >= 8 | Force SKIP (hard override) |
| GPT + Trend Conflict | GPT >= 6 AND Trend >= 5 | +1.5 to composite |
| High Dispersion | Spread between any two factors >= 6 | +1.0 to composite |
| IV Cheap | IV/RV score >= 8 | +1.0 to composite |

### Mag 7 Earnings Calendar

Checks Polygon's earnings events API for AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA, META. If any report today (+2 to GPT score) or tomorrow (+1). Acts as a safety net when RSS feeds don't have pre-earnings articles.

### System Failure Alerting

Sends Slack-compatible webhook alerts when:
- No signal generated during a trading day
- Polygon or OpenAI API fails 2+ times consecutively
- Poke thread hasn't fired in 30+ minutes during trading hours

Configure via `ALERT_WEBHOOK_URL` (env var or .config).

---

## Signal → Trade Sizing

| Signal | Composite | Width | Delta |
|--------|-----------|-------|-------|
| TRADE_AGGRESSIVE | < 3.5 | 20pt | 0.18 |
| TRADE_NORMAL | 3.5-5.0 | 25pt | 0.16 |
| TRADE_CONSERVATIVE | 5.0-7.5 | 30pt | 0.14 |
| SKIP | >= 7.5 | — | — |

Each signal fires a distinct Option Alpha webhook URL. Option Alpha handles the actual trade execution. The trade is entered at 1:30-2:30 PM ET with a time-based exit at 10:00 AM next day (capturing overnight vol premium only).

---

## News Processing Pipeline

### Layer 1: Algorithmic Deduplication (`processing/news_dedup.py`)
- Normalizes titles (lowercase, strip punctuation)
- Sorts by recency then source priority (Reuters > Bloomberg > Google > Yahoo > CNBC > MarketWatch)
- Fuzzy matches at 85% similarity threshold using `difflib.SequenceMatcher`

### Layer 2: Keyword Filter (`processing/news_filter.py`)
- **Removes junk:** "secret to", "trick to", "X ways to", "you won't believe", old recaps
- **Tags HIGH priority:** earnings beats/misses, guidance changes, stock moves >10%, Mag 7 news, M&A, SEC/FDA decisions

### Layer 3: OpenAI GPT Analysis (`signals/gpt_news.py`)
- Top 30 filtered articles formatted with recency labels and sent to OpenAI GPT
- AI performs its own dedup check, filters commentary, classifies significance, assesses time decay
- Returns a structured JSON risk assessment

---

## Data Sources

| Data | Source | Cost | Delay |
|------|--------|------|-------|
| SPX price + history | Polygon/Massive Indices Starter | $49/mo | 15-min |
| VIX1D | Polygon/Massive Indices Starter | (included) | 15-min |
| VIX (30-day) | Polygon/Massive Indices Starter | (included) | 15-min |
| Mag 7 earnings | Polygon ticker events API | (included) | N/A |
| News | Yahoo Finance RSS + Google News RSS | Free | Real-time |
| AI analysis | OpenAI API (gpt-4o-mini) | Usage-based | ~5-15s |

---

## Project Structure

```
GPT4_TradeIndicator/
├── app.py                      # Flask server, routes, poke scheduler
├── signal_engine.py            # Composite score, contradiction detection, signal generation
├── webhooks.py                 # Option Alpha webhook dispatch
├── sheets_logger.py            # Google Sheets logging (27 columns per signal)
├── alerting.py                 # System failure alerting via webhook
├── validate_outcomes.py        # Backfill next-day SPX data + accuracy report
├── backtest.py                 # Historical backtest engine with GPT sweep
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
│   ├── market_data.py          # Polygon API: SPX + VIX1D + VIX snapshots + aggregates
│   ├── earnings_calendar.py    # Mag 7 earnings date checker via Polygon
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
│   ├── iv_rv_ratio.py          # Factor 1: IV/RV ratio + term structure (30%)
│   ├── market_trend.py         # Factor 2: 5-day momentum + intraday range (20%)
│   └── gpt_news.py             # Factor 3: GPT news risk analysis (50%)
│
├── tests/
│   ├── __init__.py
│   └── test_signal_validation.py   # 46 tests: scoring, contradictions, alerting, backtest
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
| `OPENAI_API_KEY` | OpenAI API key |
| `POLYGON_API_KEY` | Polygon/Massive market data key |
| `TRADE_AGGRESSIVE_URL` | Option Alpha webhook for score < 3.5 |
| `TRADE_NORMAL_URL` | Option Alpha webhook for score 3.5-5.0 |
| `TRADE_CONSERVATIVE_URL` | Option Alpha webhook for score 5.0-7.5 |
| `NO_TRADE_URL` | Option Alpha webhook for score >= 7.5 |

### Optional

| Key | What it is |
|-----|-----------|
| `OPENAI_MODEL` | Override AI model (default: gpt-4o-mini) |
| `GOOGLE_SHEET_ID` | Google Sheet ID for signal logging |
| `GOOGLE_CREDENTIALS_JSON` | Service account JSON (single line) |
| `ALERT_WEBHOOK_URL` | Slack incoming webhook for system failure alerts |

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
| `/health` | GET | JSON health check + alerting status |
| `/option_alpha_trigger` | GET, POST | Run full signal pipeline, fire webhook, log to Sheets |
| `/test_polygon_delayed` | GET | Test Polygon connectivity (SPX + VIX1D + VIX) |
| `/test_slack` | GET | Send test Slack alert to verify webhook |

---

## Google Sheets Logging

Every signal run appends a row with 27 columns. First 23 columns fill at signal time. Last 4 columns are backfilled later by `validate_outcomes.py`. See full column list in the [sheets_logger.py](sheets_logger.py) SHEET_HEADERS constant.

---

## Costs

| Item | Monthly Cost |
|------|-------------|
| Railway hosting | ~$5 |
| Polygon/Massive Indices Starter | $49 |
| OpenAI API (gpt-4o-mini) | ~$1-3 (x2 calls per signal for confirmation) |
| Google Sheets | Free |
| News RSS feeds | Free |
| **Total** | **~$55-57/mo** |
