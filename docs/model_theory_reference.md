# Model Theory: Statistical Foundations

A rigorous walkthrough of every probability model in the system. Written for someone with an applied math / statistics background who wants to understand exactly what's happening under the hood.

---

## Table of Contents

1. [Problem Setup](#1-problem-setup)
2. [Log-Normal Model](#2-log-normal-model)
3. [Student-t Model](#3-student-t-model)
4. [Options-Implied Model (Breeden-Litzenberger)](#4-options-implied-model-breeden-litzenberger)
5. [Composite Model & Fallback Chain](#5-composite-model--fallback-chain)
6. [Volatility Estimation & Blending](#6-volatility-estimation--blending)
7. [Intraday Vol Adjustment](#7-intraday-vol-adjustment)
8. [Kelly Criterion & Position Sizing](#8-kelly-criterion--position-sizing)
9. [Calibration Metrics](#9-calibration-metrics)
10. [Bias Exploitation Model](#10-bias-exploitation-model)
11. [Directional Exposure Risk](#11-directional-exposure-risk)
12. [Optimal Exit Theory](#12-optimal-exit-theory)
13. [Dynamic Calibration](#13-dynamic-calibration)

---

## 1. Problem Setup

We trade binary contracts on Kalshi. Each contract pays $1 if an event occurs, $0 otherwise. The market price is the implied probability. Our job: estimate the true probability and trade when our estimate diverges from the market's.

**Formally:** Given a contract with payoff

$$
X = \begin{cases} 1 & \text{if event } E \text{ occurs} \\ 0 & \text{otherwise} \end{cases}
$$

The market prices this at $p_{\text{mkt}}$, implying $\hat{P}_{\text{mkt}}(E) = p_{\text{mkt}}$.

We estimate $\hat{P}_{\text{model}}(E)$ using statistical models. The **edge** is:

$$
\text{edge} = \hat{P}_{\text{model}}(E) - p_{\text{mkt}} - \text{fees}
$$

We trade when $\text{edge} > 0.03$ (3% minimum) and $\text{edge} < 0.20$ (20% maximum — larger is likely a model error).

**The key question for index markets:** What is $P(S_T > K)$ or $P(A < S_T < B)$, where $S_T$ is the index level at expiry time $T$ and $K$ (or $[A, B]$) is the contract's threshold?

---

## 2. Log-Normal Model

**File:** `src/models/index_range.py` → `LogNormalIndexModel`

### The Assumption

Under geometric Brownian motion (GBM), the log return of the index is normally distributed:

$$
\ln\!\left(\frac{S_T}{S_0}\right) \sim \mathcal{N}\!\left(-\tfrac{1}{2}\sigma^2 T,\; \sigma\sqrt{T}\right)
$$

where:
- $S_0$ = current index level
- $S_T$ = index level at time $T$
- $\sigma$ = annualized volatility
- $T$ = time to expiry in years (= `days_to_expiry / 252`)

The drift term is set to $\mu = -\frac{1}{2}\sigma^2$ (risk-neutral measure), not the physical drift. For short horizons (1–5 days), the expected return is negligible relative to volatility, so the risk-neutral and physical measures give nearly identical probabilities.

### Computing Probabilities

**Above threshold:** $P(S_T > K)$

$$
P(S_T > K) = 1 - \Phi\!\left(\frac{\ln(K/S_0) + \frac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}\right)
$$

where $\Phi$ is the standard normal CDF.

**Below threshold:** $P(S_T < K) = 1 - P(S_T > K)$

**Range:** $P(A < S_T < B)$

$$
P(A < S_T < B) = \Phi\!\left(\frac{\ln(B/S_0) + \frac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}\right) - \Phi\!\left(\frac{\ln(A/S_0) + \frac{1}{2}\sigma^2 T}{\sigma\sqrt{T}}\right)
$$

### Why This Is the Simplest Model

The log-normal assumption is equivalent to Black-Scholes. It's the "default" model of asset returns. It captures the log-scale nature of returns (prices can't go negative) and the time-scaling of volatility ($\sigma_T = \sigma_{\text{annual}} \cdot \sqrt{T}$).

### Where It Fails

The normal distribution on log returns has thin tails. It assigns negligibly small probability to moves beyond ~3 standard deviations:

| Move | Normal P(exceed) | Historical S&P frequency |
|---|---|---|
| 2σ daily | 2.28% | ~3-4% |
| 3σ daily | 0.13% | ~0.5-1% |
| 4σ daily | 0.003% | ~0.1% |

For far-out-of-the-money contracts (e.g., "S&P drops 5% in one day"), the log-normal model dramatically underestimates the probability. This is exactly where we need the Student-t model.

---

## 3. Student-t Model

**File:** `src/models/index_range.py` → `StudentTIndexModel`

### Motivation: Fat Tails in Equity Returns

Empirical equity return distributions are leptokurtic — they have heavier tails and a sharper peak than the normal distribution. This is well-documented:

- **Excess kurtosis** of daily S&P returns is typically 5–10 (normal = 0)
- **Mandelbrot (1963)** first noted this; Fama (1965) confirmed it
- The Student-t distribution captures this with a single parameter: degrees of freedom ($\nu$)

### The Model

Replace the normal CDF with the Student-t CDF. The standardized log return follows:

$$
\frac{\ln(S_T/S_0) - \mu}{\sigma_{\text{scale}}} \sim t_\nu
$$

where:
- $\mu = -\frac{1}{2}\sigma_{\text{period}}^2$ (same drift adjustment as log-normal)
- $\nu$ = degrees of freedom (calibrated from data)
- $\sigma_{\text{scale}}$ = adjusted scale parameter (see below)

### Variance Matching

The Student-t distribution with $\nu > 2$ has variance $\text{Var}(t_\nu) = \frac{\nu}{\nu - 2}$. If we naively used `period_vol` as the scale, the t-distribution would have higher variance than intended.

To match the variance of our volatility estimate, we set:

$$
\sigma_{\text{scale}} = \sigma_{\text{period}} \cdot \sqrt{\frac{\nu - 2}{\nu}}
$$

This ensures $\text{Var}\!\left(\sigma_{\text{scale}} \cdot t_\nu\right) = \sigma_{\text{period}}^2$, so the t-distribution and the normal distribution agree on the total variance. The difference is purely in the shape: the t-distribution allocates more probability mass to the tails and less to the center.

### Calibration: Fitting Degrees of Freedom

On startup, we fit $\nu$ to 252 days (1 year) of historical S&P 500 daily log returns using maximum likelihood estimation (`scipy.stats.t.fit()`):

$$
\hat{\nu}, \hat{\mu}, \hat{\sigma} = \arg\max_{\nu, \mu, \sigma} \prod_{i=1}^{n} f_{t_\nu}(r_i \mid \mu, \sigma)
$$

where $f_{t_\nu}$ is the Student-t PDF and $r_i = \ln(S_i / S_{i-1})$.

**Typical result:** $\hat{\nu} \approx 3\text{–}5$ for S&P 500 daily returns. We clamp to $[2.5, 15]$ for numerical stability ($\nu \leq 2$ has infinite variance; $\nu > 15$ is essentially normal).

### Impact on Tail Probabilities

For a 3-sigma event at $\nu = 4$:

$$
P(|X| > 3) = 2 \cdot (1 - F_{t_4}(3)) \approx 2 \times 0.02 = 4\%
$$

Compare to normal: $P(|X| > 3) \approx 0.27\%$. The Student-t assigns ~15x more probability to 3-sigma events. This is the entire edge for pricing far-OTM Kalshi contracts.

### Confidence Assignment

- Base confidence: 0.55 (slightly higher than log-normal due to better tail modeling)
- Penalty if not calibrated (using default $\nu = 4$): −0.05
- Penalty for long horizons ($T > 5$ days): −0.10
- Bonus for very short horizons ($T < 0.5$ days): +0.05

---

## 4. Options-Implied Model (Breeden-Litzenberger)

**File:** `src/models/options_implied.py` → `OptionsImpliedModel`

### Core Idea

SPX options prices encode the market's risk-neutral probability distribution of future S&P levels. Rather than building our own parametric distribution, we can extract the market's distribution directly.

This is superior to parametric models because it captures:
- Volatility skew (puts are more expensive than calls, reflecting crash risk)
- Term structure of volatility
- Event risk premia
- The collective information of all options market participants

### Breeden-Litzenberger Theorem (1978)

The risk-neutral probability density function $f^*(K)$ of the terminal asset price $S_T$ is related to the second derivative of European call prices with respect to strike:

$$
f^*(K) = e^{rT} \frac{\partial^2 C(K)}{\partial K^2}
$$

where:
- $C(K)$ = price of a European call with strike $K$ and time to expiry $T$
- $r$ = risk-free rate
- $f^*(K)$ = risk-neutral density at strike $K$

**Derivation sketch:**

1. A butterfly spread with strikes $K - \Delta K$, $K$, $K + \Delta K$ has payoff that approximates a Dirac delta at $K$
2. Its price is $C(K-\Delta K) - 2C(K) + C(K+\Delta K) \approx \frac{\partial^2 C}{\partial K^2} (\Delta K)^2$
3. This price equals $e^{-rT} f^*(K) (\Delta K)^2$ (discounted probability mass × payoff width)
4. Solving: $f^*(K) = e^{rT} \frac{\partial^2 C}{\partial K^2}$

### Implementation Algorithm

1. **Fetch options chain** from Polygon.io for the matching expiry date
2. **Filter for quality:** OTM only (calls for $K > S_0$, puts for $K < S_0$), open interest > 10, bid-ask spread < 50% of mid
3. **Interpolate IV:** Fit a cubic spline through (strike, implied_vol) pairs → smooth volatility smile
4. **Reconstruct call prices** on a fine $5 grid via Black-Scholes:
$$
C(K) = S_0 \Phi(d_1) - K e^{-rT} \Phi(d_2)
$$
where $d_1 = \frac{\ln(S_0/K) + (r + \frac{1}{2}\sigma(K)^2)T}{\sigma(K)\sqrt{T}}$, $d_2 = d_1 - \sigma(K)\sqrt{T}$, and $\sigma(K)$ is the interpolated IV at strike $K$
5. **Numerical second derivative** via central differences:
$$
\frac{\partial^2 C}{\partial K^2} \bigg|_{K_i} \approx \frac{C(K_{i+1}) - 2C(K_i) + C(K_{i-1})}{(\Delta K)^2}
$$
6. **Density:** $f^*(K_i) = e^{rT} \cdot \frac{\partial^2 C}{\partial K^2}\bigg|_{K_i}$, floored at 0
7. **Normalize:** Divide by $\int f^* \, dK$ (via trapezoidal rule) so the density integrates to 1

### Computing Probabilities from the Density

$$
P(S_T > K) = \int_K^\infty f^*(x) \, dx \approx \text{trapz}(f^*[x \geq K], x[x \geq K])
$$

### Limitations

- **Tail noise:** The second derivative amplifies noise, especially at extreme strikes. We limit the grid to $S_0 \pm 20\%$ and floor density at 0.
- **Discrete strikes:** Cubic spline extrapolation beyond observed strikes is unreliable. The density is only valid within the observed strike range.
- **Delayed data:** Polygon free tier provides 15-minute delayed options data. For intraday contracts, the density may be slightly stale.

---

## 5. Composite Model & Fallback Chain

**File:** `src/models/index_range.py` → `CompositeIndexModel`

The composite model tries each model in priority order:

1. **Options-implied** (confidence 0.70) — if Polygon API is configured and chain has ≥10 strikes with valid IV
2. **Student-t** (confidence 0.55) — if calibrated; 0.50 if using default $\nu$
3. **Log-normal** (confidence 0.50) — always works

The first model that returns a non-None prediction wins. This cascading design means we always get a prediction, with quality degrading gracefully.

**Why not ensemble/average them?** The options-implied model is categorically better than the parametric models when it works — it IS the market's distribution, not an approximation of it. Averaging would dilute this signal. The parametric models exist only as fallbacks.

---

## 6. Volatility Estimation & Blending

### Realized Volatility

Computed from 21 trading days of historical daily log returns:

$$
\hat{\sigma}_{\text{realized}} = \sqrt{\frac{252}{n-1} \sum_{i=1}^{n} (r_i - \bar{r})^2}
$$

where $r_i = \ln(S_i / S_{i-1})$ and the $\sqrt{252}$ annualizes from daily to annual.

This is the standard close-to-close estimator. It underestimates true volatility because it misses intraday price action (the "overnight gap" problem), but it's simple and robust.

### Implied Volatility (VIX)

The VIX index represents the market's expectation of 30-day realized volatility on the S&P 500, derived from SPX option prices. We use $\hat{\sigma}_{\text{implied}} = \text{VIX} / 100$.

**Important:** VIX is not a forecast — it includes a **volatility risk premium (VRP)**. Historically, VIX exceeds subsequent realized vol by 2–4 percentage points on average. Investors pay this premium for downside protection (puts are expensive).

### Blending Formula

$$
\hat{\sigma} = (1 - w) \cdot \hat{\sigma}_{\text{realized}} + w \cdot \hat{\sigma}_{\text{implied}}
$$

where:

$$
w = \min\!\left(0.5,\; \frac{T_{\text{days}}}{14}\right)
$$

| Horizon | $w$ | Interpretation |
|---|---|---|
| 1 day | 0.07 | Almost entirely realized vol (VRP would overestimate) |
| 3 days | 0.21 | Mostly realized, some implied |
| 7 days | 0.50 | Equal weight |
| 14+ days | 0.50 | Capped at 50% implied |

**Rationale:** For very short horizons (same-day to 1–2 days), the next price move is essentially determined by today's realized volatility regime. The VRP in VIX would overpredict actual moves. For longer horizons, the VIX risk premium is partially justified because there's more time for tail events and regime changes.

### Period Volatility

Scale annualized vol to the contract's time horizon:

$$
\sigma_{\text{period}} = \hat{\sigma} \cdot \sqrt{\frac{T_{\text{days}}}{252}}
$$

This is the square-root-of-time scaling from GBM. It assumes returns are i.i.d. (independent across days), which is approximately correct for daily returns but breaks down for intraday (see next section).

---

## 7. Intraday Vol Adjustment

### The Problem

For same-day contracts (KXINXU, KXINXD), the old model used `days_to_expiry = max(1, ...)`. A contract expiring in 3 hours got the same vol as one expiring tomorrow. Since $\sigma_{\text{period}} = \sigma \sqrt{T/252}$, using $T=1$ instead of $T=0.46$ overstates period vol by a factor of $\sqrt{1/0.46} \approx 1.47$.

### Fractional Days

We now compute:

$$
T_{\text{days}} = \frac{\text{hours remaining}}{6.5}
$$

where 6.5 is the number of US market hours per trading day (9:30 AM – 4:00 PM ET). For a contract with 3 hours remaining: $T = 3/6.5 \approx 0.46$ days.

Floor at 0.01 to avoid division by zero for about-to-expire contracts.

### Intraday Vol Multiplier

For $T < 1.0$ day, we apply:

$$
\sigma_{\text{period}}^{\text{intraday}} = 1.15 \times \sigma_{\text{period}}
$$

**Why 1.15?** Empirical studies show that intraday volatility is 10–20% higher than what sqrt-time scaling from daily vol would predict. This is because:

1. **Mean reversion at daily frequency:** Daily returns exhibit slight negative autocorrelation (today's up day is slightly more likely to be followed by a down day). This means daily vol overstates multi-day vol but understates intraday vol when sqrt-scaled.

2. **Intraday patterns:** Volatility is U-shaped within the day (high at open, low midday, high at close). The open and close are disproportionately volatile.

3. **Information arrival:** News doesn't arrive uniformly. The opening auction resolves overnight information accumulation.

The 1.15 multiplier is a conservative middle estimate. Academic literature (e.g., Andersen et al., 2001) suggests the ratio varies from 1.05 to 1.30 depending on market conditions.

---

## 8. Kelly Criterion & Position Sizing

### Full Kelly

For a binary bet at price $p$ with estimated true probability $q = p + \text{edge}$:

- Win payout per contract: $1 - p$ (you pay $p$, receive $1$)
- Loss per contract: $p$
- Win probability: $q$

The Kelly criterion maximizes expected log wealth growth. The optimal fraction of bankroll to wager is:

$$
f^* = \frac{q \cdot (1-p) - (1-q) \cdot p}{1 - p} = \frac{q(1-p) - p(1-q)}{1-p}
$$

Simplifying:

$$
f^* = \frac{q - p}{1 - p} = \frac{\text{edge}}{1 - p}
$$

This is the edge divided by the odds (since the payout per dollar risked is $(1-p)/p$, and the Kelly fraction for odds $b$ is $f = \frac{bq - (1-q)}{b}$).

### Why Quarter-Kelly

Full Kelly maximizes the **long-run growth rate** but has extreme short-run variance. The key property:

$$
\text{Var}(\text{growth rate}) \propto f^2
$$

Quarter-Kelly ($f = f^*/4$) gives:
- **Growth rate:** 75% of full Kelly's growth rate
- **Variance:** $1/16$ of full Kelly's variance
- **Probability of 50% drawdown** drops from ~25% (full Kelly) to ~1% (quarter-Kelly)

For a $5K bankroll where drawdown tolerance is low, quarter-Kelly is the standard choice. The growth rate sacrifice is small; the drawdown protection is massive.

### Contract Sizing

$$
\text{contracts} = \left\lfloor \frac{f \cdot \text{bankroll}}{p} \right\rfloor
$$

Capped at:
- `max_contracts = 100` (prevents unrealistic fills at penny prices, where Kelly dollar amounts are small but contract counts explode: e.g., $25 / $0.015 = 1,667 contracts)
- 5% of desk capital per market (position concentration limit)

### Fee-Adjusted Edge

Edge is computed **net of Kalshi fees** before any sizing decision:

$$
\text{edge}_{\text{net}} = (q - p) - \text{fee per contract}
$$

Kalshi fees are probability-weighted: ~1.75¢/contract at 50/50 prices, <1¢ at extreme prices. This means:
- A 5% gross edge on a 50¢ contract → ~3.3% net edge
- A 5% gross edge on a 5¢ contract → ~4.9% net edge

The fee structure inherently favors extreme-priced contracts, which aligns with the bias exploitation strategy.

---

## 9. Calibration Metrics

### Brier Score

For a single prediction with predicted probability $\hat{p}$ and binary outcome $y \in \{0, 1\}$:

$$
\text{BS} = (\hat{p} - y)^2
$$

Properties:
- **Range:** $[0, 1]$. Lower is better.
- **Perfect:** $\text{BS} = 0$ (predicted exactly 0 or 1 correctly)
- **Worst:** $\text{BS} = 1$ (predicted 1.0 for an event that didn't happen)
- **Climatology baseline:** For 50/50 events, always predicting 0.5 gives $\text{BS} = 0.25$

The Brier score is a **strictly proper scoring rule** — it is minimized in expectation when $\hat{p}$ equals the true probability. This means a forecaster cannot game it by systematically over- or under-predicting. This property is essential for meaningful calibration measurement.

**Decomposition** (Murphy, 1973):

$$
\text{BS} = \text{Reliability} - \text{Resolution} + \text{Uncertainty}
$$

- **Reliability** (calibration): How close are predicted probabilities to observed frequencies? Lower is better.
- **Resolution** (discrimination): How much do predicted probabilities vary? Higher is better.
- **Uncertainty** (base rate): Entropy of the outcomes. Fixed for a given dataset.

### Expected Calibration Error (ECE)

Predictions are binned by predicted probability. For each bin $B_k$:

$$
\text{ECE} = \sum_{k=1}^{K} \frac{|B_k|}{n} \left| \bar{p}_k - \bar{y}_k \right|
$$

where $\bar{p}_k$ is the average predicted probability in bin $k$ and $\bar{y}_k$ is the actual frequency of positive outcomes in bin $k$.

**Interpretation:** If we predict 70% for a batch of events, we expect ~70% of them to actually occur. ECE measures the average absolute gap between predicted and actual frequencies, weighted by bin size.

We use $K = 10$ bins (0–10%, 10–20%, ..., 90–100%).

### Edge Captured

$$
\text{Edge Captured} = \frac{\text{Avg Realized Edge}}{\text{Avg Predicted Edge}}
$$

where:
- Predicted edge = $\hat{p}_{\text{model}} - p_{\text{market}}$ for each trade
- Realized edge = $(1 - p_{\text{market}})$ if the event occurred, $(-p_{\text{market}})$ if it didn't

An edge capture rate > 0 means the model is adding value. An edge capture rate > 1 means we're capturing more than we predicted (lucky or model is conservative). A rate near 0 or negative means the model's edge estimates are not predictive.

---

## 10. Bias Exploitation Model

**File:** `src/desks/bias_exploit.py`

### The Longshot Bias

Well-documented in economics literature (Griffith 1949, Thaler & Ziemba 1988, Snowberg & Wolfers 2010): bettors systematically overpay for low-probability events and underpay for high-probability events.

**In prediction markets:** A contract trading at 5¢ (implied 5% probability) may have a true probability of only 2–3%. The 2–3% excess is the longshot bias premium.

### Model

This desk uses no statistical model — it's a rule-based system:

$$
\hat{P}_{\text{model}}(\text{YES}) = \begin{cases}
p_{\text{mkt}} - 0.03 & \text{if } p_{\text{mkt}} < 0.10 \text{ (fade longshot)} \\
p_{\text{mkt}} + 0.03 & \text{if } p_{\text{mkt}} > 0.90 \text{ (buy favorite)} \\
\text{no signal} & \text{otherwise}
\end{cases}
$$

The fixed 3% adjustment is conservative. It should eventually be calibrated from historical Kalshi settlement data: for each price bucket, compute $P(\text{YES settles} \mid p_{\text{mkt}} \in [a, b])$ and compare to the average market price in that bucket.

### Why Not a More Sophisticated Model?

The beauty of bias exploitation is that it doesn't require modeling the underlying event at all. We don't need to know whether the S&P will be above 6800 — we just need to know that the market systematically misprices extreme-probability contracts. The 3% adjustment is a bet on the existence of the bias, not on the direction of the market.

---

## 11. Directional Exposure Risk

**File:** `src/execution/exposure.py`

### The Problem

The portfolio manager deduplicates by ticker (won't buy the same contract twice) but not by direction. Consider 10 positions:
- KXINXU-T5500: YES (bullish)
- KXINXU-T5400: YES (bullish)
- KXINXU-T5300: YES (bullish)
- ... etc.

These are all directionally long the S&P. A single bad day causes correlated losses across all positions.

### Exposure Tracking

We classify each position by underlying asset and direction:

$$
\text{exposure}_{u} = \sum_{i \in \text{positions on underlying } u} \text{sign}(i) \cdot \text{cost}(i)
$$

where:
- $\text{sign}(\text{YES buy}) = +1$ (long the underlying going up)
- $\text{sign}(\text{NO buy}) = -1$ (short the underlying / long it going down)
- $\text{cost}(i) = \text{quantity}_i \times \text{price}_i$

### Limit

$$
|\text{exposure}_{u}| \leq 0.50 \times \text{bankroll}
$$

A new trade is blocked if it would push directional exposure above 50% of the bankroll for any underlying.

**Why 50%?** This is a balance between opportunity (we want to trade S&P markets, our primary venue) and concentration risk. With quarter-Kelly and 5% max position size per market, hitting 50% aggregate exposure requires ~10 correlated positions — that's when diversification benefits are exhausted.

**Key property:** Opposite sides cancel. If we're $+300 long from YES buys and add a $-200 NO buy, net exposure drops to $+100. This encourages natural hedging.

---

## Appendix: Distribution Reference

| Distribution | PDF | Tails | Use Case |
|---|---|---|---|
| Normal / Log-normal | $\frac{1}{\sigma\sqrt{2\pi}} e^{-\frac{(x-\mu)^2}{2\sigma^2}}$ | Exponential decay (thin) | Baseline model, always works |
| Student-t ($\nu$) | $\frac{\Gamma(\frac{\nu+1}{2})}{\sqrt{\nu\pi}\;\Gamma(\frac{\nu}{2})} \left(1+\frac{x^2}{\nu}\right)^{-\frac{\nu+1}{2}}$ | Power-law decay (fat) | Equity returns, tail pricing |
| Options-implied (non-parametric) | Extracted from market prices | Captures skew + kurtosis | Best available when chain is thick |

**Student-t tail behavior:** For large $|x|$:

$$
f_{t_\nu}(x) \sim |x|^{-(\nu+1)}
$$

This is polynomial decay vs. the normal's exponential decay $e^{-x^2/2}$. With $\nu = 4$:
- $P(|X| > 3\sigma)$: Student-t ≈ 4%, Normal ≈ 0.27%
- $P(|X| > 5\sigma)$: Student-t ≈ 0.5%, Normal ≈ 0.00006%

The difference grows dramatically in the tails — this is the entire rationale for using Student-t.

---

## 12. Optimal Exit Theory

**File:** `src/execution/position_manager.py`

### The Optimal Stopping Problem

At each monitoring point $t_i$, we hold a binary contract and must decide: sell now, or continue holding? This is a discrete-time optimal stopping problem, structurally identical to American option exercise or callable bond execution.

### The EV Framework

For a YES contract bought at $p_{\text{entry}}$, the decision at time $t$ is:

**Value of selling now:**

$$
V_{\text{sell}} = p_{\text{bid}} - p_{\text{entry}}
$$

This is certain — we cross the bid and realize this P&L immediately.

**Value of holding to settlement:**

$$
V_{\text{hold}} = q_{\text{model}} \cdot (1 - p_{\text{entry}}) + (1 - q_{\text{model}}) \cdot (-p_{\text{entry}}) = q_{\text{model}} - p_{\text{entry}}
$$

where $q_{\text{model}} = \hat{P}(\text{YES})$ is our model's probability estimate.

**Decision rule:** Sell when $V_{\text{sell}} > V_{\text{hold}}$:

$$
p_{\text{bid}} - p_{\text{entry}} > q_{\text{model}} - p_{\text{entry}}
$$

$$
\boxed{p_{\text{bid}} > q_{\text{model}}}
$$

The entry price cancels out. **The exit decision depends only on whether the market's current bid exceeds our model's probability estimate.** This is elegant: it handles both "take profit" (market moved our way past fair value) and "cut loss" (our model says the contract was never worth the current bid) in a single rule.

### Why This Dominates Fixed Thresholds

Fixed thresholds like "take profit at +50%" or "stop loss at -30%" have fundamental problems:

1. **They ignore the model.** A contract up 50% might still be undervalued (model says probability is even higher). Selling captures 50% when you could capture 100%.

2. **They don't scale across price levels.** A 30% loss on a 50¢ contract (losing 15¢) is very different from a 30% loss on a 5¢ contract (losing 1.5¢), even though the decision framework should be the same.

3. **They create perverse incentives.** A stop-loss at -30% means you sell at exactly the wrong time — when the market has moved against you (and the contract may be cheap relative to true value).

The EV rule avoids all of these. It asks the only question that matters: "Is the market offering me more than this contract is worth?"

### For NO Positions

For a NO contract, the bid we'd receive is $p_{\text{no\_bid}}$ (the NO bid), and the model's NO probability is $1 - q_{\text{model}}$ (since $q_{\text{model}}$ is the model's P(YES)):

$$
\text{Sell when: } p_{\text{no\_bid}} > 1 - q_{\text{model}}
$$

### The Hard Stop-Loss as Insurance

The EV rule assumes the model is correct. But models can be wrong. If the true probability is 5% but our model says 60%, the EV rule would tell us to hold while we bleed to zero.

The hard stop-loss (50% of entry price) is insurance against model error. It fires rarely — only when the model is catastrophically wrong — but it prevents any single position from destroying the bankroll.

### Near-Settlement Hold Rule

Within 40 minutes of settlement, we always hold. The argument is transaction cost:

**Cost of selling early:** Bid-ask spread, typically 2-5 cents on Kalshi. This is the certain cost of exiting.

**Cost of holding:** The difference between $V_{\text{sell}}$ and $V_{\text{hold}}$, which may be positive or negative.

Near settlement, the model's probability estimate is most accurate (less uncertainty with $T \to 0$), and the spread cost is a larger fraction of remaining value. The expected gain from selling early almost never exceeds the spread cost. Settlement is free — no spread, no fees.

Formally, sell only if:

$$
p_{\text{bid}} - q_{\text{model}} > \text{half-spread}
$$

But near settlement, $p_{\text{bid}}$ converges to the true probability (markets are efficient at the limit), so $p_{\text{bid}} \approx q_{\text{model}}$ and the inequality rarely holds.

### Limitation: Stale Model Price

Currently, $q_{\text{model}}$ is the model's estimate at **entry time**, not re-evaluated with current market data. If the S&P moved 2% since entry, the model would give a different probability today. This creates a lag in exit decisions.

The ideal solution: re-run the model each cycle with current prices and vol. This requires threading model references through the position manager — a future enhancement.

---

## 13. Dynamic Calibration

### What Recalibrates

| Parameter | Frequency | Mechanism |
|---|---|---|
| Student-t degrees of freedom ($\nu$) | Every ~288 cycles (~24h) | `calibrate_df()` refits to trailing 252 days of S&P returns |
| Current index prices | Every cycle | Price cache cleared, `_refresh_market_data()` re-fetches |
| Realized volatility (21-day) | Every cycle | Vol cache cleared, re-computed from latest returns |
| VIX (implied vol) | Every cycle | Fetched fresh via Yahoo Finance |

### What Does NOT Recalibrate (Yet)

| Parameter | Current Value | How It Could Adapt |
|---|---|---|
| Vol blending weights | `min(0.5, T/14)` | Optimize against historical forecast errors per horizon |
| Intraday vol multiplier | 1.15 | Calibrate from actual intraday vs. daily vol ratio |
| Bias desk adjustment | 3% fixed | Calibrate from Kalshi settlement data per price bucket |
| Confidence levels | 0.50/0.55/0.70 | Not used in sizing (only Kelly edge matters), low priority |

### Why Rolling Matters

The market regime changes. In January 2026, realized vol might be 12% with $\nu = 5$ (normal times). After a crash, realized vol jumps to 35% and $\nu$ drops to 3 (fat tails become fatter). If the system ran for months without recalibrating, it would be pricing February's options with January's parameters.

By clearing caches each cycle and recalibrating $\nu$ daily, the system adapts to regime changes within 24 hours. This is a compromise between responsiveness (recalibrate every cycle) and stability (don't overreact to a single noisy day of returns).

---

## References

- Breeden, D.T. & Litzenberger, R.H. (1978). "Prices of State-Contingent Claims Implicit in Option Prices." *Journal of Business*, 51(4), 621–651.
- Kelly, J.L. (1956). "A New Interpretation of Information Rate." *Bell System Technical Journal*, 35(4), 917–926.
- Mandelbrot, B. (1963). "The Variation of Certain Speculative Prices." *Journal of Business*, 36(4), 394–419.
- Murphy, A.H. (1973). "A New Vector Partition of the Probability Score." *Journal of Applied Meteorology*, 12(4), 595–600.
- Snowberg, E. & Wolfers, J. (2010). "Explaining the Favorite–Long Shot Bias: Is it Risk-Love or Misperceptions?" *Journal of Political Economy*, 118(4), 723–746.
- Andersen, T.G., Bollerslev, T., Diebold, F.X., & Ebens, H. (2001). "The Distribution of Realized Stock Return Volatility." *Journal of Financial Economics*, 61(1), 43–76.
- Thaler, R.H. & Ziemba, W.T. (1988). "Anomalies: Parimutuel Betting Markets: Racetracks and Lotteries." *Journal of Economic Perspectives*, 2(2), 161–174.
