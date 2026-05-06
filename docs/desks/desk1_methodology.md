# Desk 1 Methodology — Overnight VRP Capture

**Last updated:** 2026-05-01
**Branch:** feature/multi-desk
**Status:** Phase 2 multi-bot paper trial infrastructure complete; awaiting OA-side recipe configuration.

This document is the canonical mathematical and engineering reference for Desk 1 (SPX overnight iron condor desk) and the parallel paper-trial bots (B/C/D/E). It is intentionally math- and code-heavy and assumes familiarity with options pricing and the academic literature in [docs/papers/](../papers/). Every quantitative claim here is sourced to a paper and to a code path.

---

## 1. The Thesis, Stated Mathematically

Let $\sigma_{\mathrm{IV}}^{\mathrm{ON}}$ denote the implied annualised volatility for the overnight (close-to-open) period priced at $t = $ market close, and $\sigma_{\mathrm{RV}}^{\mathrm{ON}}$ the realised annualised volatility actually observed over the same period:

$$
\sigma_{\mathrm{RV}}^{\mathrm{ON}} \;=\; \sqrt{\frac{1}{\Delta t}\,\bigl(\ln S_{\mathrm{open},\,t+1} - \ln S_{\mathrm{close},\,t}\bigr)^{2}}
$$

where $\Delta t$ is the close-to-open fraction of a trading year.

The **overnight variance risk premium** is then defined per Papagelis & Dotsis (2025) as the expectation under $\mathbb{Q}$ minus expectation under $\mathbb{P}$ of realised variance over the non-trading period:

$$
\mathrm{VRP}^{\mathrm{ON}}_{t} \;\equiv\; \mathbb{E}^{\mathbb{Q}}_{t}\!\left[\bigl(\sigma_{\mathrm{RV}}^{\mathrm{ON}}\bigr)^{2}\right] \;-\; \mathbb{E}^{\mathbb{P}}_{t}\!\left[\bigl(\sigma_{\mathrm{RV}}^{\mathrm{ON}}\bigr)^{2}\right]
$$

Empirically, $\mathrm{VRP}^{\mathrm{ON}}_{t} > 0$ on average (i.e. implied > realised), and the seller of variance earns this premium in expectation.

**Desk 1 thesis.** This wedge is large enough — across enough days, after transaction costs and tail-event drawdowns — to yield positive expectancy on a strategy that:

1. **Sells** SPX index variance (via short option structures) at $t = $ ~2:00 PM ET, capturing $\mathbb{E}^{\mathbb{Q}}$;
2. **Closes** the position at $t = $ ~10:00 AM ET next trading day, when realised variance over the close-to-open window has accrued.

The 2 PM → 10 AM hold approximates the close-to-open window in [Papagelis & Dotsis (2025)](../papers/ssrn-4954623.pdf) close enough that their empirical findings transfer (within the limitations of the late-afternoon vs. close entry timing, discussed in §6).

---

## 2. Empirical Foundation — Three Load-Bearing Findings

### 2.1 Finding #1: Overnight VRP is persistently negative; intraday is mostly positive

[Papagelis & Dotsis (2025), Table 3](../papers/ssrn-4954623.pdf) decomposes daily variance-swap P&L into close-to-open (Co) and open-to-close (oC) components for 14 underlyings (US/Europe/Asia indices + individual stocks), 2012–2022:

| Underlying | $\mathrm{P\&L}_{\mathrm{Co}}$ | $\mathrm{P\&L}_{\mathrm{oC}}$ |
|---|---|---|
| SPX  | **−6.15***  | +2.89   |
| NDX  | **−6.22***  | +3.60   |
| RUT  | **−10.64*** | +6.52*  |
| VSTOXX | **−13.97*** | +7.06*** |
| (median across 14 assets) | **persistently negative**, all sig. at 1% | mostly positive, often insignificant |

(*** = $p < 0.01$; ** = $p < 0.05$; * = $p < 0.10$.)

**Translation.** A variance swap held only during overnight loses money for the buyer (i.e. earns money for the seller). A variance swap held only during the trading day mostly *makes* money for the buyer (sellers lose). The total daily VRP that the literature has been studying for 20 years is principally a *non-trading-period* phenomenon.

**Code mapping.** This justifies the entire Desk 1 hold window:
- Entry: [`desks/overnight_condors/desk.py:40`](../../desks/overnight_condors/desk.py) — `window_start = dt_time(13, 30)`
- The 2 PM → 10 AM hold concentrates exposure into the close-to-open period.
- Bots that deviate from this window do so at the cost of moving from the highest-EV component to the lowest- or even negative-EV component.

### 2.2 Finding #2: Day-of-week effect is sharp

[Papagelis & Dotsis (2025), Table 4 Panel B](../papers/ssrn-4954623.pdf) — variance-swap close-to-open P&L by weekday across 14 underlyings:

| Entry day → exit day | Hold duration | Co premium |
|---|---|---|
| Friday → Monday | ~64 hours (incl. weekend) | **most negative for 12/14 underlyings** |
| Monday → Tuesday | ~17.5 hours | significantly negative |
| Tuesday → Wednesday | ~17.5 hours | significantly negative |
| Wednesday → Thursday | ~17.5 hours | significantly negative |
| Thursday → Friday | ~17.5 hours | **mostly insignificant** (premium thin) |

**Why weekends pay more:** the implied vol embedded in Friday-close option prices anticipates 64 hours of potential price-changing news (incl. weekend geopolitical / economic releases), but realised variance over a non-trading period accrues at a much lower per-hour rate than during trading hours (Muravyev & Ni 2020 "volatility seasonality" bias). The mismatch is monetised by the seller.

**Code mapping.**
- The Friday blackout was *removed* in commit `f047849` — empirically correct decision, since Friday entry → Monday exit captures the richest Co window of the week.
- Bot E ([`desks/overnight_condors_dow/desk.py`](../../desks/overnight_condors_dow/desk.py)) operationalises this finding: `DOW_BOOST_DAYS = {0, 4}` (Mon/Fri entries), `DOW_SKIP_DAYS = {3}` (Thursday entries forced to SKIP).

### 2.3 Finding #3: VVIX is a 6× edge multiplier

[Papagelis & Dotsis (2025), Table 6](../papers/ssrn-4954623.pdf) splits trading days into VVIX quartiles and reports the next-day overnight P&L:

| VVIX quartile | $\mathrm{P\&L}_{\mathrm{Co}}$ | Significance |
|---|---|---|
| Q1 (lowest) | **−3.01**  | $p < 0.01$ |
| Q2 | **−2.82**  | $p < 0.01$ |
| Q3 | **−6.77**  | $p < 0.01$ |
| Q4 (highest) | **−17.88** | $p < 0.01$ |

**Translation.** When vol-of-vol (VVIX) is in the top quartile, the overnight short-vol seller earns ~6× as much per unit notional as in the bottom quartile. This is also where tail risk is highest — but the empirical record says the premium more than compensates.

**Code mapping.**
- We collected VVIX in [`core/data/market_data.py:get_vvix_snapshot`](../../core/data/market_data.py) but never used it for sizing — only for log-only scoring.
- Bot D ([`desks/overnight_condors_vvix/desk.py`](../../desks/overnight_condors_vvix/desk.py)) operationalises Table 6 directly. Bucket boundaries are set at the empirical 25/50/75 percentiles of VVIX over the trailing 252 trading days, so bucket assignment matches Papagelis's table by construction.

### 2.4 Finding #4: VRP is asymmetric — call wings are negative-EV

[Feunou, Jahan-Parvar, Okou (2015)](../papers/2015020pap.pdf) decomposes total VRP into upside ($V_{\mathrm{RP}}^{U}$) and downside ($V_{\mathrm{RP}}^{D}$) components by computing realised semi-variances:

$$
\mathrm{RV}^{D}_t(\kappa) \;=\; \sum_{j=1}^{n_t} r_{j,t}^{2}\,\mathbf{1}_{\{r_{j,t} \le \kappa\}}, \qquad
\mathrm{RV}^{U}_t(\kappa) \;=\; \sum_{j=1}^{n_t} r_{j,t}^{2}\,\mathbf{1}_{\{r_{j,t} > \kappa\}}
$$

with threshold $\kappa = 0$ in their main analysis. The risk-neutral counterparts $\mathrm{IV}^{D}, \mathrm{IV}^{U}$ are constructed from OTM put and call prices respectively (Andersen & Bondarenko 2007 corridor implied variance). Then:

$$
V_{\mathrm{RP}}^{D} \;=\; \mathbb{E}^{\mathbb{Q}}[\mathrm{RV}^{D}] - \mathbb{E}^{\mathbb{P}}[\mathrm{RV}^{D}], \qquad
V_{\mathrm{RP}}^{U} \;=\; \mathbb{E}^{\mathbb{Q}}[\mathrm{RV}^{U}] - \mathbb{E}^{\mathbb{P}}[\mathrm{RV}^{U}]
$$

S&P 500 sample 1996–2010, annualised:

| Component | Mean | Sign | Interpretation |
|---|---|---|---|
| Total VRP        | +0.08% | mildly positive | average IC seller barely breaks even |
| $V_{\mathrm{RP}}^{D}$ (downside) | **+3.39%** | **positive** | put seller earns +3.4%/yr on premium collected |
| $V_{\mathrm{RP}}^{U}$ (upside)   | **−4.42%** | **negative** | call seller *pays* 4.4%/yr |
| SRP = $V_{\mathrm{RP}}^{U} - V_{\mathrm{RP}}^{D}$ | −7.81% | negative | skew premium |

**Translation for an iron condor.** An IC sells *both* legs. Schematically:

$$
\mathrm{EV}_{\mathrm{IC}} \;\approx\; \underbrace{\mathrm{EV}_{\mathrm{put\,leg}}}_{>\,0} \;+\; \underbrace{\mathrm{EV}_{\mathrm{call\,leg}}}_{<\,0}
$$

The call leg drags the EV down. A **put-spread-only** structure captures the entire positive-EV leg without giving any back. An **asymmetric IC** (wider put, narrower call) keeps the defined-risk margin profile of an IC while reducing the call-leg drag.

**Code mapping.**
- Bot B ([`desks/asymmetric_condors/desk.py`](../../desks/asymmetric_condors/desk.py)) — short put Δ20 (wider, more credit), short call Δ10 (narrower, less giveback).
- Bot C ([`desks/overnight_putspread/desk.py`](../../desks/overnight_putspread/desk.py)) — pure put-spread, no call leg. OA-side sizes 2× contracts to match Bot A's gross margin for clean comparison.

---

## 3. Per-Bot Mathematical Specification

### 3.1 Bot A — Symmetric Iron Condor (Control)

The baseline. The signal output tier label maps directly to the OA recipe via webhook URL:

$$
\mathrm{Tier}_A(s) \;=\; \begin{cases}
\text{TRADE\_AGGRESSIVE}     & s < 3.5 \\
\text{TRADE\_NORMAL}         & 3.5 \le s < 5.0 \\
\text{TRADE\_CONSERVATIVE}   & 5.0 \le s < 7.5 \\
\text{SKIP}                  & s \ge 7.5
\end{cases}
$$

where $s$ is the composite score from [`desks/overnight_condors/signal_engine.py:calculate_composite_score`](../../desks/overnight_condors/signal_engine.py):

$$
s \;=\; 0.30 \cdot \mathrm{score}_{\mathrm{IV/RV}} \;+\; 0.20 \cdot \mathrm{score}_{\mathrm{trend}} \;+\; 0.50 \cdot \mathrm{score}_{\mathrm{GPT}} \;+\; \mathrm{adj}_{\mathrm{contradiction}}
$$

Structure (configured on the OA side, not in Python):
- Short put: Δ ≈ 0.16, long put: Δ ≈ 0.10
- Short call: Δ ≈ 0.16, long call: Δ ≈ 0.10
- Wing width: 25 pt (TRADE_NORMAL); other tiers 20 pt / 30 pt

### 3.2 Bot B — Asymmetric IC

Same composite score $s$, same tier mapping $\mathrm{Tier}_A(s)$. Only the OA-side recipe differs:

| Leg | Bot A (symmetric) | Bot B (asymmetric) |
|---|---|---|
| Short put  | Δ ≈ 0.16 | **Δ ≈ 0.20**  (wider — more credit per Feunou downside) |
| Long put   | Δ ≈ 0.10 | Δ ≈ 0.10           |
| Short call | Δ ≈ 0.16 | **Δ ≈ 0.10**  (narrower — less negative-EV call premium given back) |
| Long call  | Δ ≈ 0.10 | Δ ≈ 0.05           |

Expected effect (per Feunou): increase the put leg's contribution to EV (+3.4%/yr × wider put), decrease the call leg's drag (−4.4%/yr × narrower call). Net EV should improve while drawdown profile stays IC-like (defined risk both sides).

### 3.3 Bot C — Put-Spread Only

Same composite score $s$, same tier mapping. OA recipe:

| Leg | Bot C |
|---|---|
| Short put  | Δ ≈ 0.16 |
| Long put   | Δ ≈ 0.10 |
| (no call legs) |  |

OA sizes contracts at $2\times$ Bot A's count to match gross margin commitment for a fair P&L-per-margin comparison.

Expected EV (per Feunou):

$$
\mathrm{EV}_C \;\approx\; 2 \cdot \mathrm{EV}_{\mathrm{put\,leg}} \;\;>\;\; \mathrm{EV}_A \;=\; \mathrm{EV}_{\mathrm{put\,leg}} + \mathrm{EV}_{\mathrm{call\,leg}}
$$

since $\mathrm{EV}_{\mathrm{call\,leg}} < 0$ implies $2 \cdot \mathrm{EV}_{\mathrm{put\,leg}} > \mathrm{EV}_{\mathrm{put\,leg}} + \mathrm{EV}_{\mathrm{call\,leg}}$ as long as $|\mathrm{EV}_{\mathrm{put\,leg}}| > |\mathrm{EV}_{\mathrm{call\,leg}}|$, which the +3.4% / −4.4% sample stats *don't* unambiguously satisfy in absolute size — but they do for risk-adjusted return (the call leg also adds variance, not just negative mean). This is the empirical question Bot C exists to answer.

### 3.4 Bot D — VVIX-Conditional Sizing

Composite score $s$ unchanged. Standard tier mapping unchanged. Then we **transform** the tier label by VVIX bucket before routing to the OA webhook.

**Step 1.** Compute VVIX percentile rank on trailing 252 trading days. Let $V_t$ be the current VVIX value, $\{V_{t-1}, V_{t-2}, \ldots, V_{t-252}\}$ the trailing window:

$$
\mathrm{pct}(V_t) \;=\; \frac{100}{n}\Bigl[\;\sum_{i=1}^{n} \mathbf{1}_{\{V_{t-i} < V_t\}} \;+\; \tfrac{1}{2}\sum_{i=1}^{n} \mathbf{1}_{\{V_{t-i} = V_t\}}\;\Bigr]
$$

(Mid-rank for ties; pure-Python implementation in [`core/data/market_data.py:_percentile_rank`](../../core/data/market_data.py).)

**Step 2.** Bucket into Papagelis-Table-6-aligned quartiles:

$$
\mathrm{Bucket}(V_t) \;=\; \begin{cases}
\text{LOW}     & 0 \le \mathrm{pct}(V_t) < 25 \\
\text{NORMAL}  & 25 \le \mathrm{pct}(V_t) < 50 \\
\text{HIGH}    & 50 \le \mathrm{pct}(V_t) < 75 \\
\text{EXTREME} & 75 \le \mathrm{pct}(V_t) \le 100
\end{cases}
$$

(Boundary convention: the lower endpoint is *included* in the higher bucket. This means a current value exactly at the historical 25th-percentile level lands in NORMAL, not LOW. Defensible: a value ≥ q25 should not be counted as bottom-quartile.)

**Step 3.** Rewrite tier label, preserving SKIP from upstream:

$$
\mathrm{Tier}_D(s, V_t) \;=\; \begin{cases}
\text{SKIP}                                  & \mathrm{Tier}_A(s) = \text{SKIP} \\
\text{TRADE\_VVIX\_}\mathrm{Bucket}(V_t)     & \text{otherwise}
\end{cases}
$$

The four `TRADE_VVIX_*` URLs route to four different OA bot recipes, each running the same IC structure but with different contract counts:

| Bucket | Contracts | Empirical premium (Papagelis Table 6) |
|---|---|---|
| LOW     | 0.5× baseline | −3.01 (Q1) |
| NORMAL  | 1.0× baseline | −2.82 (Q2) |
| HIGH    | 1.5× baseline | −6.77 (Q3) |
| EXTREME | 2.0× baseline | −17.88 (Q4) |

Sizing multipliers were chosen to roughly mirror the Q1/Q2/Q3/Q4 P&L gradient without going full 6× on EXTREME (the tail risk gradient is steeper than the mean-premium gradient; some caution preserves capital for the bad nights).

**Fallback.** If the Polygon VVIX history fetch fails or returns < 60 bars, the bucketer falls back to a static-threshold path (`vvix_static_bucket()` in `market_data.py`):

$$
\mathrm{Bucket}_{\mathrm{fallback}}(V_t) \;=\; \begin{cases}
\text{LOW}     & V_t < 90 \\
\text{NORMAL}  & 90 \le V_t < 100 \\
\text{HIGH}    & 100 \le V_t < 110 \\
\text{EXTREME} & V_t \ge 110
\end{cases}
$$

Source ('percentile_252d' vs 'static_fallback') is logged in the `VVIX_Bucket` field's source attribute and the JSON response, so we can audit how often we're on the fallback path and whether bucket assignments match between methods.

**Cache behaviour.** The 252-day window is fetched at most once per ET calendar day (module-level `_VVIX_HISTORY_CACHE`). With 5 desks × 3 pokes/day = 15 transform-hook calls/day, this means 1 Polygon fetch per day instead of 15. On a fetch-failure day every poke re-attempts (failures are not cached).

### 3.5 Bot E — Day-of-Week Conditional Sizing

Composite score $s$ unchanged. Standard tier mapping unchanged. The transform hook applies a DOW multiplier to the tier label:

$$
\mathrm{Tier}_E(s, w) \;=\; \begin{cases}
\text{SKIP}                                          & \mathrm{Tier}_A(s) = \text{SKIP} \\
\text{SKIP}                                          & w = 3 \;\;(\text{Thursday: forced skip per Papagelis Table 4}) \\
\mathrm{Tier}_A(s)\,\text{\textunderscore BOOST}     & w \in \{0, 4\} \;\;(\text{Mon/Fri: 1.5}\times) \\
\mathrm{Tier}_A(s)\,\text{\textunderscore NORMAL}    & w \in \{1, 2\} \;\;(\text{Tue/Wed: 1.0}\times)
\end{cases}
$$

where $w$ is `datetime.weekday()` of the *entry* day (0=Monday). The Mon/Fri 1.5× and Tue/Wed 1.0× multipliers are configured on the OA side per the `_BOOST` vs `_NORMAL` URL routing.

Thursday entry → forced SKIP because:
- Thursday Co premium is statistically insignificant in Papagelis Table 4 Panel B for 13/14 underlyings;
- the next-day morning exit also lands ahead of Friday open, missing the rich Friday Co window;
- the trade is essentially long noise.

---

## 4. Code Architecture

### 4.1 Inheritance pattern

All Phase 2 paper-trial bots subclass `OvernightCondorsDesk` and reuse its full signal pipeline. They override only:

```python
class XYZDesk(OvernightCondorsDesk):
    desk_id = "..."                 # routes to /<desk_id>/trigger
    display_name = "..."
    structure_label = "..."         # logged for attribution
    config_prefix = "DESK_X_"       # webhook URL namespace

    def get_webhook_urls(self, config):
        # Per-bot webhook URL set (4 for B/C, 5 for D, 7 for E)

    def transform_signal_for_routing(self, signal, ctx):
        # Optional: rewrite signal['signal'] tier label before webhook fire
        # Default in parent class is identity (no transformation)
        # ctx contains: vvix_data, vix_data, spx_data, now

    def register_routes(self, app):
        # Flask route at /<desk_id>/trigger
```

The hook is called in [`OvernightCondorsDesk.run_signal_cycle`](../../desks/overnight_condors/desk.py) at exactly one place — between `get_webhook_urls()` and `send_webhook()`:

```python
webhook_urls = self.get_webhook_urls(config)
signal = self.transform_signal_for_routing(signal, ctx={
    'vvix_data': vvix_data,
    'vix_data':  vix_data,
    'spx_data':  spx_data,
    'now':       now,
})
webhook = send_webhook(signal, webhook_urls)
```

This is a 4-line, additive change to the parent class. Default behaviour for Bot A and any future desk that doesn't override the hook is identical to pre-Phase-2.

### 4.2 Webhook URL routing

OA cannot accept structured payload parameters from us (the webhook payload is `{signal, timestamp}` — see [`core/webhooks.py:72`](../../core/webhooks.py)). The only way to route to different OA recipes is to send to different webhook URLs. We encode bot identity, structure, VVIX bucket, DOW variant, and tier all in the URL choice:

| Bot | Webhook URL count | Tier labels emitted |
|---|---|---|
| A | 4 | TRADE_AGGRESSIVE / NORMAL / CONSERVATIVE / SKIP |
| B | 4 | (same as A, different OA recipe) |
| C | 4 | (same as A, different OA recipe) |
| D | 5 | TRADE_VVIX_LOW / NORMAL / HIGH / EXTREME / SKIP |
| E | 7 | TRADE_*_BOOST × 3, TRADE_*_NORMAL × 3, SKIP |

Total new env vars / config keys: 19 (see [`.config.example`](../../.config.example) `[WEBHOOKS_DESK_*]` sections).

### 4.3 Sheet schema additions

Five columns appended at the end of `SHEET_HEADERS` in [`sheets_logger.py`](../../sheets_logger.py) (per the existing rule never to insert mid-list):

| Column | Type | Purpose |
|---|---|---|
| `Desk_ID` | string | Which bot fired (overnight_condors / asymmetric_condors / ...) |
| `Structure_Label` | string | Human-readable structure tag (e.g., `IC_25pt_0.16d_VVIXpct252d`) |
| `Routed_Tier` | string | Final tier label sent to OA after transform — differs from `Signal` for Bots D and E |
| `VVIX_Bucket` | string | LOW / NORMAL / HIGH / EXTREME (Bot D only; blank for others) |
| `DOW_Multiplier` | string | "1.0" / "1.5" / "0.0_SKIP_thursday" / "0.0_SKIP_signal" (Bot E only) |

The `Routed_Tier` column is critical for Bot D/E P&L attribution: by pivoting on (`Desk_ID`, `Routed_Tier`) we can compute per-bucket / per-DOW-variant outcome stats independently.

### 4.4 Fallback ladder for VVIX bucketing

```
                 vvix_percentile_bucket(V)
                          │
            ┌─────────────┴─────────────┐
            │                           │
      vvix_percentile_252d(V)      (returns None on failure)
            │                           │
   ┌────────┴─────────┐                 │
   │                  │                 │
  None              float          vvix_static_bucket(V)
   │                  │                 │
   │           Quartile bucketing       │
   │                  │            Static threshold
   │                  │           (90/100/110 cuts)
   ▼                  ▼                 ▼
(LOW,None,0,         (LOW,15.5,252,    (NORMAL,None,0,
 'static_fallback')   'percentile_252d')'static_fallback')
```

Three failure modes the fallback handles:
1. **Polygon VVIX endpoint down** → `get_vvix_aggregates()` returns None → static path
2. **Insufficient history** (< 60 bars) → `vvix_percentile_252d()` returns None → static path
3. **VVIX value itself missing** (Polygon snapshot failed) → `vvix_static_bucket(None)` returns 'NORMAL' (safe default)

In all three cases, Bot D *continues* to trade — it doesn't break the daily flow. The audit trail (`vvix_bucket_source` field) records which path was taken so we can monitor fallback frequency.

---

## 5. Decision Rules — Set in Advance

Per Section 3.5 of the [implementation plan](~/.claude/plans/okay-buddy-now-slow-enchanted-dongarra.md), promotion criteria for any challenger bot to replace Bot A as the live production strategy:

**Required conditions (all must hold):**
1. ≥ 30 closed trades on the challenger
2. Mean P&L per trade ≥ 110% of Bot A's
3. Max drawdown ≤ 130% of Bot A's
4. Win rate within ±5 pp of Bot A's (sanity check against win-rate traps)

**Demotion (any single condition triggers pause-and-review):**
- Max drawdown > 200% of Bot A's

**No early stopping.** A challenger does not get promoted on a 3-trade streak. The 30-trade minimum exists to avoid the exact mistake critiqued in §1 of the plan: a 3-trade outperformance is statistical noise.

---

## 6. Known Limitations

### 6.1 Entry timing approximates close-to-open, doesn't equal it

Papagelis & Dotsis measure the variance-swap P&L over the *exact* close-to-open window. We enter at ~2 PM ET (1.5 hours before close) and exit at ~10 AM ET (30 min after open). So our exposure window is 90 min wider than theirs on each side.

The 1.5-hour late-afternoon component is *intraday* P&L (the +2.89 column in their Table 3, sellers lose), so we are mixing in some negative-EV time at the front of every trade. The 30-min morning component is *intraday* too (early oC).

**Estimated leakage:** 2 hours of intraday exposure out of ~20 total = 10%. Empirical impact: probably small relative to the dominant Co premium, but worth quantifying once we have enough P&L data.

### 6.2 VIX1D overnight bias

Per [Albers & Kestner (2024)](../papers/1-s2.0-S1544612324002162-main.pdf), the VIX1D index has a known mechanical intraday bias from its calculation methodology (business-time weighting + dynamic shift to next-term options that include overnight risk premium). At our 2 PM entry time, VIX1D is ~80% weighted on next-term options (per their Eq. 1), so the value we read is *roughly* the right horizon. But:

- Comparing VIX1D readings across different times of day is **not apples-to-apples**.
- Our 1:30 / 1:50 / 2:10 poke schedule introduces noise into the IV/RV ratio when different pokes fire on different days.
- Mitigation: standardise on a single fixed entry time. Plan §3.6 proposes 2:00 PM ± 5 min.

### 6.3 Bot D bucket boundary edge case

The `pct < 25` bucket cutoff means a current VVIX value *exactly* at the historical 25th percentile lands in NORMAL, not LOW. With continuous data this matters approximately never; with discrete daily closes and ties, it's a tie-breaking choice. Mid-rank for ties (in `_percentile_rank`) reduces the asymmetry for clusters of equal values.

### 6.4 Static fallback is calibrated to one regime

The static thresholds (90/100/110) were chosen to roughly match VVIX historical regime ranges, but are *not* a true percentile. If VVIX goes through a sustained regime shift (e.g., new crisis or new low-vol era), static thresholds will misclassify systematically. The percentile path self-adjusts; the static fallback does not.

This is acceptable as a fallback because:
- It only fires when Polygon history fetch fails (rare);
- We log `static_fallback` so we can monitor how often it triggers;
- The bot continues to trade rather than blocking on data unavailability.

If `static_fallback` shows up in > 5% of signal cycles, that's a signal to investigate the Polygon connection or fallback to a different history source.

---

## 7. Verification Checklist

Before going live with any Phase 2 bot, confirm:

- [ ] OA paper account has 4 new bot groups created with $5K allocation each
- [ ] OA recipes match the structure specs in §3.2-§3.5
- [ ] All 19 webhook URLs from `.config.example` `[WEBHOOKS_DESK_*]` sections are filled in `.config` (or Railway env)
- [ ] Manual `curl http://localhost:8080/<desk_id>/trigger` succeeds for each new desk and the response includes the expected fields (`vvix_bucket`, `dow_multiplier`, etc.)
- [ ] First Sheet row written by each new bot has the new columns (`Desk_ID`, `Structure_Label`, `Routed_Tier`, `VVIX_Bucket`, `DOW_Multiplier`) populated
- [ ] `/health` endpoint shows all 6 desks healthy
- [ ] Bot D's first day in production logs `vvix_bucket_source = percentile_252d` (not `static_fallback`)

---

## References

In order of importance to this desk:

1. **Papagelis & Dotsis (2025)** — *The Variance Risk Premium Over Trading and Non-Trading Periods.* JFM. → [`docs/papers/ssrn-4954623.pdf`](../papers/ssrn-4954623.pdf)
2. **Feunou, Jahan-Parvar, Okou (2015)** — *Downside Variance Risk Premium.* FRB FEDS Working Paper. → [`docs/papers/2015020pap.pdf`](../papers/2015020pap.pdf)
3. **Bondarenko (2019)** — *Historical Performance of Put-Writing Strategies.* CBOE/SSRN. → [`docs/papers/ssrn-3393940.pdf`](../papers/ssrn-3393940.pdf)
4. **AQR** — *PutWrite vs BuyWrite: Yes, Put-Call Parity Holds Here Too.* → [`docs/papers/AQR PutWrite vs BuyWritevF.pdf`](../papers/AQR%20PutWrite%20vs%20BuyWritevF.pdf)
5. **Boyarchenko, Larsen, Whelan** — *The Overnight Drift.* NY Fed Staff Report 917. → [`docs/papers/sr917.pdf`](../papers/sr917.pdf)
6. **Lou, Polk, Skouras (2019)** — *A Tug of War: Overnight versus Intraday Expected Returns.* JFE. → [`docs/papers/lou_polk_skouras.pdf`](../papers/lou_polk_skouras.pdf)
7. **Albers & Kestner (2024)** — *The Daily Rise and Fall of the VIX1D.* Finance Research Letters. → [`docs/papers/1-s2.0-S1544612324002162-main.pdf`](../papers/1-s2.0-S1544612324002162-main.pdf)
8. **Bollerslev, Tauchen, Zhou (2009)** — *Expected Stock Returns and Variance Risk Premia.* RFS. → [`docs/papers/rfs_09.pdf`](../papers/rfs_09.pdf)
9. **Carr & Wu (2009)** — *Variance Risk Premia.* RFS. → [`docs/papers/CarrReviewofFinStudiesMarch2009-a.pdf`](../papers/CarrReviewofFinStudiesMarch2009-a.pdf)
10. **Gârleanu, Pedersen, Poteshman (2009)** — *Demand-Based Option Pricing.* RFS. → [`docs/papers/DBOP.pdf`](../papers/DBOP.pdf)

Full library of 15 papers in [`docs/papers/`](../papers/) with tier-ranked summary in [`~/.claude/projects/.../memory/reference_papers.md`](~/.claude/projects/-Users-zhihaoren-Desktop-GPT4-TradeIndicator/memory/reference_papers.md).
