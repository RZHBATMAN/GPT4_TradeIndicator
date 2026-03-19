# Signal Logic Reference

SPX overnight iron condor decision system. Three-factor composite score with contradiction detection, confirmation pass, and multiple safety gates.

Entry: 1:30-2:30 PM ET. Exit: 10:00 AM ET next day (~19.5-20.5 hour hold).

---

## 1. Three-Factor Model

| Factor | Weight | Source |
|--------|--------|--------|
| IV/RV Ratio | 30% | `signals/iv_rv_ratio.py` |
| Market Trend | 20% | `signals/market_trend.py` |
| GPT News Analysis | 50% | `signals/gpt_news.py` |

Composite = (IV/RV score x 0.30) + (Trend score x 0.20) + (GPT score x 0.50), clamped to [1.0, 10.0].

---

## 2. IV/RV Ratio (30%)

Uses VIX1D (1-day forward implied vol) vs 10-day realized volatility.

### 2.1 Base Scoring

| IV/RV Ratio | Base Score | Interpretation |
|-------------|-----------|----------------|
| > 1.35 | 1 | IV very rich — excellent premium |
| > 1.20 | 2 | IV rich |
| > 1.10 | 3 | IV moderately rich |
| > 1.00 | 4 | IV slightly rich |
| > 0.90 | 6 | IV slightly cheap |
| > 0.80 | 8 | IV cheap |
| <= 0.80 | 10 | IV very cheap — do not sell |

Lower score = more favorable for selling vol.

### 2.2 RV Change Modifier

Compares current 10-day RV to the prior 10-day RV (days 11-21). Positive modifier = more dangerous.

| RV Change | Modifier |
|-----------|----------|
| > +30% | +3 |
| > +15% | +2 |
| < -20% | -1 |
| Otherwise | 0 |

### 2.3 Term Structure Modifier (VIX1D / VIX 30-day)

Inverted term structure (VIX1D > VIX) signals near-term fear.

| VIX1D/VIX Ratio | Modifier | Label |
|-----------------|----------|-------|
| > 1.10 | +3 | Strong inversion |
| > 1.00 | +1 | Mild inversion |
| <= 1.00 | 0 | Contango (normal) |

### 2.4 Final Score

`final_score = clamp(base_score + rv_modifier + term_modifier, 1, 10)`

---

## 3. Market Trend (20%)

Symmetric scoring: iron condors lose on big moves in either direction.

### 3.1 5-Day Momentum Base Score

| |5-Day Change| | Base Score |
|-----------------|-----------|
| > 4% | 7 |
| > 2% | 4 |
| > 1% | 2 |
| <= 1% | 1 |

### 3.2 Intraday Range Modifier

| Intraday Range (high-low)/current | Modifier |
|-----------------------------------|----------|
| > 1.5% | +2 |
| > 1.0% | +1 |
| <= 1.0% | 0 |

### 3.3 Final Score

`final_score = clamp(base_score + intraday_modifier, 1, 10)`

---

## 4. GPT News Analysis (50%)

Calls OpenAI (default gpt-4o-mini) with a structured prompt. News arrives pre-filtered through a triple-layer pipeline (algo dedup, keyword filter, then GPT).

### 4.1 Four Responsibilities

1. **Duplication safety net** — Detect duplicate articles covering the same event that the algorithmic dedup missed. Count as one event.
2. **Commentary/news filter** — Filter out opinion pieces, analysis of old events, and speculation disguised as news. Only score genuine, recent catalysts.
3. **Significance classification** — Classify each unique event on a 1-5 scale based on potential SPX impact.
4. **Time-decay assessment** — Estimate what percentage of each event is already priced in based on significance level and hours elapsed.

### 4.2 Significance Scale

| Level | Label | Potential SPX Impact | Examples |
|-------|-------|---------------------|----------|
| 5 | EXTREME | 1%+ overnight | Mag 7 earnings, Fed surprises, geopolitical shocks, CPI/NFP surprise (>0.3% / >100K) |
| 4 | HIGH | 0.5-1% | Multiple Mag 7 moves, large-cap ($500B+) earnings, sector-wide news |
| 3 | MODERATE | 0.2-0.5% | Non-Mag 7 large-cap earnings, sector regulation, commodity shocks (>5%) |
| 2 | LOW | <0.2% | Mid-cap earnings, non-Mag 7 analyst ratings, minor data |
| 1 | NEGLIGIBLE | ~0% | Small-cap news, non-SPX stocks, opinion, crypto/forex |

### 4.3 Time-Decay Model

Higher-significance events take longer to price in. Residual risk = 100% minus "% priced in".

**Significance 5 (EXTREME):**

| Hours Elapsed | % Priced In | Overnight Risk |
|---------------|-------------|----------------|
| 0-2 | <30% | EXTREME |
| 2-4 | 30-50% | EXTREME |
| 4-8 | 50-80% | HIGH |
| 8-12 | 80-95% | MODERATE |
| 12+ | >95% | LOW |

**Significance 4 (HIGH):**

| Hours Elapsed | % Priced In | Overnight Risk |
|---------------|-------------|----------------|
| 0-1 | <40% | HIGH |
| 1-3 | 40-70% | HIGH |
| 3-6 | 70-90% | MODERATE |
| 6+ | >90% | LOW |

**Significance 3 (MODERATE):**

| Hours Elapsed | % Priced In | Overnight Risk |
|---------------|-------------|----------------|
| 0-1 | <50% | MODERATE |
| 1-3 | 50-85% | MODERATE |
| 3+ | >85% | LOW |

**Significance 1-2:** No meaningful overnight risk regardless of timing.

### 4.4 GPT Output Scoring

| Score | Category | Condition |
|-------|----------|-----------|
| 1-2 | VERY_QUIET | No real catalysts, only Sig 1-2 events |
| 3-4 | QUIET | Minor Sig 3 events mostly priced, or old Sig 4-5 fully priced |
| 5-6 | MODERATE | Sig 3-4 partially priced, or old Sig 5 events |
| 7-8 | ELEVATED | Sig 4-5 not fully priced (<70% priced in) |
| 9-10 | EXTREME | Multiple major catalysts or one massive Sig 5 <50% priced |

### 4.5 Calibration

Applied to the raw GPT score before use in composite:

| Raw Score | Adjustment |
|-----------|------------|
| >= 9 | No change |
| 7-8 | -0.5 |
| 4-6 | No change |
| <= 3 | +0.5 |

Result is rounded and clamped to [1, 10]. This compresses extremes slightly.

### 4.6 Fallback

When no news is available or the OpenAI API fails, the GPT score defaults to 7 (ELEVATED). No data = caution.

---

## 5. Composite Score and Signal Tiers

### 5.1 Composite Categories

| Composite Score | Category |
|----------------|----------|
| < 2.5 | EXCELLENT |
| < 3.5 | VERY_GOOD |
| < 5.0 | GOOD |
| < 6.5 | FAIR |
| < 7.5 | ELEVATED |
| >= 7.5 | HIGH |

### 5.2 Signal Tiers and Trade Parameters

| Signal | Composite Range | Should Trade | Width | Delta |
|--------|----------------|-------------|-------|-------|
| TRADE_AGGRESSIVE | < 3.5 | Yes | 20 pt | 0.18 |
| TRADE_NORMAL | 3.5 - 4.99 | Yes | 25 pt | 0.16 |
| TRADE_CONSERVATIVE | 5.0 - 7.49 | Yes | 30 pt | 0.14 |
| SKIP | >= 7.5 | No | — | — |

---

## 6. Contradiction Detection

Four rules evaluated after individual factor scores are computed. Rules can force a signal override or add points to the composite.

| Rule | Condition | Effect |
|------|-----------|--------|
| 1. GPT Extreme | GPT score >= 8 | Force SKIP (hard override) |
| 2. GPT + Trend Conflict | GPT >= 6 AND Trend >= 5 | +1.5 to composite |
| 3. High Dispersion | max(scores) - min(scores) >= 6 | +1.0 to composite |
| 4. IV Cheap | IV/RV score >= 8 | +1.0 to composite |

Rules are evaluated in order. Rule 1 is a hard override (forces SKIP regardless of composite). Rules 2-4 apply additive adjustments; when multiple fire, the largest adjustment from rules 3-4 wins (they use `max`), and rule 2's +1.5 stacks independently.

---

## 7. Confirmation Pass

The analysis pipeline runs twice per trigger to guard against GPT non-determinism.

| Pass | Temperature | Purpose |
|------|------------|---------|
| Primary (Pass 1) | 0.1 | Low variance, deterministic-leaning |
| Confirmation (Pass 2) | 0.4 | Higher variance, tests robustness |

The system uses the **more conservative** result (higher tier index in the order AGGRESSIVE < NORMAL < CONSERVATIVE < SKIP). If both passes agree, the signal proceeds as-is.

---

## 8. Safety Layers

These gates block or modify trade execution independently of the composite score.

### 8.1 Mag 7 Earnings Gate

Checks Polygon API for upcoming earnings of AAPL, MSFT, GOOGL, AMZN, NVDA, TSLA, META.

| Condition | GPT Score Modifier |
|-----------|--------------------|
| Mag 7 reports TODAY | +2 |
| Mag 7 reports TOMORROW | +1 |
| No Mag 7 earnings | 0 |

Applied before contradiction detection. Acts as a safety net in case GPT fails to account for earnings.

### 8.2 FOMC / CPI / NFP Event Gates

Static date sets maintained in `data/oa_event_calendar.py`. When active, Option Alpha will not open the position even if the webhook fires.

| Gate | Trigger |
|------|---------|
| FOMC_TODAY | FOMC meeting date is today |
| FOMC_NEXT_DAY | FOMC meeting date is the next market day |
| CPI_NEXT_DAY | CPI release is the next market day |
| NFP_NEXT_DAY | NFP release is the next market day |
| EARLY_CLOSE | NYSE early close today (1 PM instead of 4 PM) |

Trade execution status is logged as `NO_OA_EVENT` with the specific gate reason.

### 8.3 Friday Blackout

No webhooks fire on Fridays. The signal is still computed and logged to Sheets for validation, but `trade_executed` is set to `NO_FRIDAY`. Rationale: weekend theta decay risk.

### 8.4 Once-Per-Day Webhook

Only the first signal of each trading day triggers a webhook to Option Alpha. Subsequent pokes within the same day are logged to Sheets but do not send duplicate webhooks. Tracked via `_daily_signal_cache`.

### 8.5 VIX >= 25 Gate

Option Alpha's own gate. When VIX (30-day) >= 25, OA blocks position entry. The webhook still fires from the bot side, but `trade_executed` is logged as `NO_VIX_GATE (VIX=XX.X)`.

---

## 9. Log-Only Indicators

Computed and logged for research purposes. No impact on scoring or signal generation.

### 9.1 VVIX (Vol-of-Vol)

Source: Polygon I:VVIX snapshot. Logged as `vvix` value and `vvix_elevated` flag (true when VVIX > 120).

### 9.2 Overnight RV

Standard deviation of overnight log returns (open_t / close_{t-1}) annualized. Uses the 10 most recent overnight gaps. Logged as `overnight_rv` and `iv_overnight_rv_ratio` (VIX1D / overnight RV).

### 9.3 Blended Overnight Vol

`blended_overnight_vol = 0.93 * overnight_rv + 0.07 * vix1d`

Weights derived from model theory: w = T/14 where T = 1 day, so implied weight = 1/14 ~ 0.07.

### 9.4 Student-t Breach Probability

Fits a Student-t distribution to overnight log returns using scipy. Degrees of freedom (nu) clamped to [2.5, 15.0] for stability. Computes the probability that the overnight move exceeds the NORMAL tier breakeven (0.90%). Logged as `student_t_nu` and `student_t_breach_prob`.

### 9.5 VRP Trend

Directional indicator of the variance risk premium based on IV/RV ratio and RV change:

| Condition | VRP Trend |
|-----------|-----------|
| IV/RV > 1.0 and RV declining | EXPANDING |
| IV/RV < 1.0 and RV rising | COMPRESSING |
| RV change > +15% | COMPRESSING |
| RV change < -15% | EXPANDING |
| Otherwise | STABLE |
