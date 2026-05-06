# Things I Need to Do — Phase 2 Bots (B/C/D/E/F)

**Audience:** You (Ren). Everything that has to happen on your end to get the new bots running end-to-end.
**Scope:** OA recipes, `.config` / Railway env paste-in, local smoke tests, Railway deploy, verification.
**Companion doc:** [desk1_methodology.md](desk1_methodology.md) for the why; this doc is the what-you-do.
**Status:** Python side is built and registered. Awaiting your work below.

---

## 0. The pattern you're implementing

```
   [Python app]              [Webhook URL]                [OA bot]
   emits a discrete   ────►   one URL per   ────►   listening on that URL
   tier label                 tier label              fires a pre-configured
                                                      trade scenario
```

Python emits `TRADE_VVIX_HIGH` → Python POSTs `{signal: "TRADE_VVIX_HIGH", timestamp: ...}` to `DESK_D_TRADE_VVIX_HIGH_URL` → OA's bot listening on that URL fires its IC scanner with the configured delta/contract count.

**Implication:** structural parameters live entirely on the OA side. The webhook URL choice IS the parameter passing mechanism. Want to test "EXTREME bucket with hedge"? Configure that as a separate OA bot listening on a separate URL.

---

## 1. Universal cross-bot rules (apply to ALL Phase 2 bots)

Before configuring anything bot-specific, these rules are constant across B/C/D/E/F:

| Setting | Value | Source |
|---|---|---|
| Account | Paper Trading | safer than live during the trial |
| Underlying | SPX | cash-settled European, no assignment risk |
| DTE at entry | 1 | matches Papagelis close-to-open window |
| Entry window | webhook-driven (1:30–2:30 PM ET) | Python triggers it |
| Time exit | 10:00 AM ET next day | matches close-to-open exit |
| Profit target | **varies by tier** — see per-bot tables | inherits from Bot A.2 (live exit-tuned variant). **AGGR 25%, NORMAL 20%, CONSV 15%** of credit received. Confirmed current 2026-05-05 |
| Stop loss | **varies by tier** — see per-bot tables | inherits from Bot A.2. **AGGR 120%, NORMAL 100%, CONSV 80%** of credit. Confirmed current 2026-05-05. Logic: more confident signal (AGGR) gets *wider* exits (more room to work); less confident (CONSV) gets *tighter* exits (bank quickly, cut losses fast). Bots D/F override tier mapping and use NORMAL-tier exits flat (20%/100%) — see those bots' sections for rationale |
| **Touch monitor** | **DO NOT add** | SPX is European cash-settled — no assignment risk to manage. Confirmed 2026-05-04 |
| Cleanup automation | "Clean up trading tags before trading" daily at 1:25 PM ET | same as existing Bot A |
| Allocation per bot group | $5K paper | adjustable based on your account |
| Max one position per bot per day | enforced via Python's once-per-day webhook | already in app code |

Any time you see "structure" in this doc it means *option deltas + wing widths*. Any time you see "sizing" it means *contract count*.

---

## 2. The 5 bot groups — overview

| Bot | OA bot recipes needed | What varies across recipes | Total webhook URLs |
|---|---|---|---|
| **B** Asymmetric IC | 4 | Same asymmetric structure; tier varies width/delta gradient | 4 |
| **C** Put-Spread Only | 4 | Put-spread structure; tier varies delta gradient | 4 |
| **D** VVIX-Conditional | 5 | Same Bot-A IC structure; **contracts vary by VVIX bucket** | 5 |
| **E** DOW-Conditional | 7 | Same Bot-A IC structure; **contracts vary by tier × DOW variant** | 7 |
| **F** Thesis-Maximizing | 9 | Asymmetric IC structure; **contracts = VVIX × DOW; EXTREME adds hedge** | 9 |
| **Total new OA bots** | **29** | | **29 URLs** |

That's a lot. Recommended order to set them up (least → most complexity):

1. **Bot B** (4 bots, similar to A) — warmup
2. **Bot C** (4 bots, simpler structure) — different recipe but easy
3. **Bot E** (7 bots, contract counts vary) — straightforward sizing variation
4. **Bot D** (5 bots, contract counts vary) — straightforward sizing variation
5. **Bot F** (9 bots, hedged variants are the new wrinkle) — leave for last

You can stop after any bot if you want partial coverage; the Python side is happy with any subset of webhook URLs configured (missing URLs = no-op, no error).

---

## 3. Bot B — Asymmetric IC (4 OA recipes)

### 3.1 What's being tested (and what's not)

**Direction supported by the literature:** tilting the structure toward the put side should outperform a symmetric IC. Feunou, Jahan-Parvar & Okou (2015) decompose total VRP into a downside component V_RP_D ≈ +3.4%/yr (sellers earn) and an upside component V_RP_U ≈ −4.4%/yr (sellers PAY). Kozhan-Neuberger-Schneider (2014) confirm "over 80% of VRP is compensation for downside risk." A symmetric IC sells both legs equally; an asymmetric IC tilts capital toward the positive-EV leg.

**What the literature does NOT prove:**
- The exact magnitude of the improvement. Feunou's numbers are for monthly options at threshold κ=0; we trade 1-day options at deep OTM deltas. The asymmetry direction transfers; the size doesn't.
- The specific delta numbers below. I picked them to preserve Bot A's per-tier total short-delta envelope while tilting toward the put side. That's a heuristic, not a derivation.

**So what we ARE testing:** does an asymmetric tilt (in the direction the literature supports) improve risk-adjusted return vs the symmetric baseline, with all other variables held constant?

### 3.2 Uses Python signal?

**Yes — fully.** Same composite score, same tier mapping (TRADE_AGGRESSIVE / NORMAL / CONSERVATIVE / SKIP) as Bot A. The composite score's per-tier structure amplification is preserved (more aggressive = wider strikes). Only the delta tilt within each tier differs.

### 3.3 OA recipe table for Bot B

Parameterized as **delta + width** (same style as Bot A) so width matches Bot A within each tier — only the short-leg delta tilts toward the put side. Contract count: match Bot A's count at the same tier.

**Exits** inherit Bot A.2's current per-tier ladder (confirmed 2026-05-05):

| Webhook URL config key | OA bot name | Short put Δ | Short call Δ | Width per side | Contracts | Profit target | Stop loss | Time exit |
|---|---|---|---|---|---|---|---|---|
| `DESK_B_TRADE_AGGRESSIVE_URL` | Bot B — Asym IC AGGR | 0.22 | 0.14 | 20pt | match Bot A AGGR | **25%** credit | **120%** credit | 10:00 AM ET |
| `DESK_B_TRADE_NORMAL_URL` | Bot B — Asym IC NORMAL | 0.20 | 0.12 | 25pt | match Bot A NORMAL | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_B_TRADE_CONSERVATIVE_URL` | Bot B — Asym IC CONSV | 0.18 | 0.10 | 30pt | match Bot A CONSV | 15% credit | 80% credit | 10:00 AM ET |
| `DESK_B_NO_TRADE_URL` | Bot B — NO TRADE | (no order) | | | | | | |

**How the deltas were chosen.** Bot A's per-tier short deltas are 0.18 / 0.16 / 0.14 (symmetric, so sum = 0.36 / 0.32 / 0.28). Bot B keeps the same per-tier sum but rebalances: NORMAL `0.16 + 0.16` → `0.20 + 0.12`. Sum unchanged → roughly same total credit collected; allocation tilted toward the empirically-positive-EV put leg.

### 3.4 Bot B steps in OA

1. Create a new Bot Group: "Bot B — Asym IC AGGR", paper account, $5K allocation.
2. Add an IC Scanner automation with: Symbol SPX, DTE 1, short-put Δ 0.22, short-call Δ 0.14, **wing width 20pt per side** (long legs implied), entry window 1:30–2:30 PM.
3. Add a Time-Based Exit automation: close at 10:00 AM ET next trading day.
4. Add Profit Target + Stop Loss per the table — for AGGR: **25% TP / 120% SL**.
5. Add cleanup automation: "Clean up trading tags before trading" daily at 1:25 PM ET.
6. Add a Webhook Trigger that fires the IC Scanner. Copy the webhook URL.
7. Paste URL into `.config` (or Railway env) under key `DESK_B_TRADE_AGGRESSIVE_URL`.
8. Repeat 1–7 for NORMAL (0.20/0.12, 25pt, **20% TP / 100% SL**) and CONSERVATIVE (0.18/0.10, 30pt, **15% TP / 80% SL**) tiers.
9. Create the NO_TRADE listener bot (no scanner needed — receives the webhook for logging consistency).

---

## 4. Bot C — Put-Spread Only (4 OA recipes)

### 4.1 What's being tested (and what's not)

**Direction supported by the literature:** the most aggressive expression of the Feunou asymmetry — drop the negative-EV call leg entirely and harvest only the +3.4%/yr downside premium. Bondarenko's 32-year PUT/WPUT track record provides additional real-world support for naked-put-style downside harvesting on the index.

**What the literature does NOT prove:**
- That this works at 1DTE specifically. Bondarenko's data is monthly puts. Feunou's is monthly options.
- The specific deltas. Bot C uses Bot A's exact put-leg deltas (no separate calibration), so the only structural difference vs Bot A is "no call side."

**So what we ARE testing:** if the call wing's expected return is genuinely negative, dropping it should improve risk-adjusted return. Pure isolation of the put-side EV.

### 4.2 Uses Python signal?

**Yes — fully.** Same composite score, same tier mapping. The per-tier delta gradient mirrors Bot A's put leg exactly.

### 4.3 OA recipe table for Bot C

Same delta + width as Bot A's put leg. **2× contract count** of Bot A at each tier — because the put-spread uses about half the margin per contract, doubling normalises gross margin commitment for fair P&L-per-margin comparison.

**Exits** inherit Bot A.2's current per-tier ladder, applied to the put-spread side only.

| Webhook URL config key | OA bot name | Short put Δ | Width | Contracts | Profit target | Stop loss | Time exit |
|---|---|---|---|---|---|---|---|
| `DESK_C_TRADE_AGGRESSIVE_URL` | Bot C — Put-Spread AGGR | 0.18 | 20pt | 2× Bot A AGGR | **25%** credit | **120%** credit | 10:00 AM ET |
| `DESK_C_TRADE_NORMAL_URL` | Bot C — Put-Spread NORMAL | 0.16 | 25pt | 2× Bot A NORMAL | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_C_TRADE_CONSERVATIVE_URL` | Bot C — Put-Spread CONSV | 0.14 | 30pt | 2× Bot A CONSV | 15% credit | 80% credit | 10:00 AM ET |
| `DESK_C_NO_TRADE_URL` | Bot C — NO TRADE | (no order) | | | | | |

### 4.4 Bot C steps in OA

Same 9-step pattern as Bot B, except the scanner template is a put-spread (no call legs). Use OA's "Vertical Spread" or "Bull Put Spread" automation type. Set strike selection by short-put delta and width as in the table.

---

## 5. Bot D — VVIX-Conditional Sizing (5 OA recipes)

### 5.1 What's being tested (and what's not)

**Direction supported by the literature:** Papagelis & Dotsis (2025) Table 6 measures variance-swap overnight P&L by VVIX quartile and finds Q4 (highest VVIX) produces a 6× richer overnight premium than Q1. Sizing INTO high-VVIX days captures more of the empirical premium gradient. This is the most directly-from-the-paper bot in the trial — we're literally implementing their bucketing.

**What the literature does NOT prove:**
- The specific sizing multipliers (0.5× / 1× / 1.5× / 2×). I picked these to roughly mirror the Q1→Q4 P&L gradient without going full 6× on EXTREME — the tail-risk gradient is also steeper there, so some restraint preserves capital for the bad nights.
- That the empirical premium gradient persists out-of-sample. Papagelis's sample ends 2022; we're trading 2026 with a different market microstructure (0DTE volume explosion).

**So what we ARE testing:** does scaling contracts with the literature-supported VVIX premium gradient improve risk-adjusted return vs constant sizing?

### 5.2 Uses Python signal?

**Partially.** The composite score is used as a binary GO/NO-GO gate (if upstream signal is SKIP, Bot D respects it). But the tier mapping (TRADE_AGGRESSIVE/NORMAL/CONSERVATIVE) is **overridden** — Bot D routes by VVIX bucket instead. The 3-factor tier amplification is discarded in favor of the VVIX dimension.

VVIX value comes from the same signal cycle (already fetched as part of the existing pipeline — no extra API call).

### 5.3 OA recipe table for Bot D

All 4 trade variants use **Bot A's NORMAL-tier structure** (Δ0.16 short / 25pt width). Only contract count varies.

Suggest baseline NORMAL = 2 contracts so multipliers come out as integers:

| VVIX bucket | Multiplier | Contracts (baseline 2) |
|---|---|---|
| LOW | 0.5× | 1 |
| NORMAL | 1.0× | 2 |
| HIGH | 1.5× | 3 |
| EXTREME | 2.0× | 4 |

If your paper account allocation is different, scale the baseline — the *relative* sizing (1 / 2 / 3 / 4) is what matters.

**Exits:** Bot D overrides the tier mapping (routes by VVIX bucket, not by composite tier), so Bot A.2's per-tier exit ladder doesn't directly apply. **Default choice: use NORMAL-tier exits flat across all 4 buckets** (20% TP / 100% SL — same as Bot A.2 NORMAL). Rationale: all 4 variants use Bot A's NORMAL structural recipe (Δ0.16 / 25pt), so the NORMAL exit calibration is the structurally-consistent choice. Alternative: VVIX-keyed exits (e.g., LOW: 15/75, EXTREME: 40/100) — see playbook §a discussion above for tradeoffs. Awaiting confirmation before locking.

| Webhook URL config key | OA bot name | Structure (same all rows) | Contracts | Profit target | Stop loss | Time exit |
|---|---|---|---|---|---|---|
| `DESK_D_TRADE_VVIX_LOW_URL` | Bot D — VVIX LOW | IC, short Δ0.16, 25pt | 1 | **20%** credit | **100%** credit | 10:00 AM ET |
| `DESK_D_TRADE_VVIX_NORMAL_URL` | Bot D — VVIX NORMAL | IC, short Δ0.16, 25pt | 2 | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_D_TRADE_VVIX_HIGH_URL` | Bot D — VVIX HIGH | IC, short Δ0.16, 25pt | 3 | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_D_TRADE_VVIX_EXTREME_URL` | Bot D — VVIX EXTREME | IC, short Δ0.16, 25pt | 4 | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_D_NO_TRADE_URL` | Bot D — NO TRADE | (no order) | | | | |

### 5.4 Bot D steps in OA

Same 9-step pattern. Five OA bots, all with identical IC scanner templates (Bot A's NORMAL config). The only thing different per bot is the **contract count**.

---

## 6. Bot E — DOW-Conditional Sizing (7 OA recipes)

### 6.1 What's being tested (and what's not)

**Direction supported by the literature:** Papagelis & Dotsis (2025) Table 4 Panel B measures variance-swap close-to-open P&L by entry weekday. Monday close-to-open (= Friday→Monday weekend hold) is most negative for 12/14 underlyings; Thursday close-to-open is statistically insignificant for 13/14. Concentrating capital on the rich days and avoiding the thin day matches the empirical gradient.

**What the literature does NOT prove:**
- The 1.5× boost multiplier. I picked it; the paper doesn't tell us "size 1.5× on Mon/Fri."
- That Thursday SKIP is the right call vs simply trading Thursday at a smaller size. I went with full SKIP because the paper finds the Thu Co premium statistically *insignificant* (i.e., not reliably different from zero), so trading it is essentially long noise.

**So what we ARE testing:** does aligning sizing with the DOW premium gradient improve risk-adjusted return?

### 6.2 Uses Python signal?

**Yes — fully.** Same composite score, same tier mapping. The per-tier structure gradient (more aggressive = wider/higher delta) is preserved. The DOW dimension adds a sizing multiplier on top, but the tier itself flows through (visible in the `_BOOST` / `_NORMAL` suffix on the routed label). Thursday entries get forced to SKIP in the Python transform regardless of upstream tier.

### 6.3 OA recipe table for Bot E

Structure matches Bot A's existing tier gradient (delta + width). Contracts vary by DOW variant.

| Tier | DOW variant | Multiplier | Contracts (assuming Bot A baseline = 2) |
|---|---|---|---|
| AGGRESSIVE | BOOST (Mon/Fri) | 1.5× | 3 |
| AGGRESSIVE | NORMAL (Tue/Wed) | 1.0× | 2 |
| NORMAL | BOOST | 1.5× | 3 |
| NORMAL | NORMAL | 1.0× | 2 |
| CONSERVATIVE | BOOST | 1.5× | 3 |
| CONSERVATIVE | NORMAL | 1.0× | 2 |
| (any) | Thursday entry | forced SKIP in Python | — |

**Exits** inherit Bot A.2's per-tier ladder. Tier flows through unchanged in Bot E (only sizing varies by DOW), so AGGR variants get 15/75, NORMAL variants get 20/100, CONSV variants get 40/150 regardless of BOOST/NORMAL DOW.

| Webhook URL config key | OA bot name | Structure (Bot A's at this tier) | Contracts | Profit target | Stop loss | Time exit |
|---|---|---|---|---|---|---|
| `DESK_E_TRADE_AGGRESSIVE_BOOST_URL` | Bot E — AGGR BOOST | IC, short Δ0.18, 20pt | 3 | **25%** credit | **120%** credit | 10:00 AM ET |
| `DESK_E_TRADE_AGGRESSIVE_NORMAL_URL` | Bot E — AGGR NORMAL | IC, short Δ0.18, 20pt | 2 | 25% credit | 120% credit | 10:00 AM ET |
| `DESK_E_TRADE_NORMAL_BOOST_URL` | Bot E — NORM BOOST | IC, short Δ0.16, 25pt | 3 | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_E_TRADE_NORMAL_NORMAL_URL` | Bot E — NORM NORMAL | IC, short Δ0.16, 25pt | 2 | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_E_TRADE_CONSERVATIVE_BOOST_URL` | Bot E — CONSV BOOST | IC, short Δ0.14, 30pt | 3 | 15% credit | 80% credit | 10:00 AM ET |
| `DESK_E_TRADE_CONSERVATIVE_NORMAL_URL` | Bot E — CONSV NORMAL | IC, short Δ0.14, 30pt | 2 | 15% credit | 80% credit | 10:00 AM ET |
| `DESK_E_NO_TRADE_URL` | Bot E — NO TRADE | (no order) | | | | |

### 6.4 Bot E steps in OA

Same 9-step pattern, repeated 7 times. The structure follows Bot A's existing tier gradient; the only knob is contract count by DOW variant.

---

## 7. Bot F — Thesis-Maximizing Combined (9 OA recipes)

### 7.1 What's being tested (and what's not)

**Direction supported by the literature:** stack all the individually-supported dimensions in one bot:
- Asymmetric IC structure (Feunou 2015 — direction supported)
- VVIX-quartile sizing (Papagelis Table 6 — direction supported)
- DOW multiplier (Papagelis Table 4 — direction supported)
- Tail hedge in EXTREME (Iyer 2024 / Yang vol-managed — direction supported)
- VVIX 99th-percentile circuit breaker (plan §3.7 risk-management heuristic)

**What the literature does NOT prove:**
- That stacking these dimensions multiplicatively works. Each finding is independent in its own paper; the combined product is my construction, not the literature's. It is plausible the dimensions interact in ways the isolated tests cannot detect.
- The specific multipliers in the combined sizing matrix. Heuristic.
- That a Δ5 long put is the right hedge size/strike. Iyer surveys multiple tail-hedge options without endorsing one specifically.

**So what we ARE testing:** does the combined expression outperform every single-dimension bot? This is the production-candidate hypothesis. If true, F replaces A as the live strategy.

### 7.2 Uses Python signal?

**Partially.** Same as Bot D — composite score is used as a binary GO/NO-GO gate (upstream SKIP is preserved); tier mapping is **overridden** by the VVIX × DOW routing. VVIX value used for bucketing.

Three SKIP precedence layers (highest first):
1. Composite signal SKIP (existing 3-factor model says don't trade)
2. Thursday entry (Co premium thin per Papagelis T4)
3. VVIX percentile ≥ 99 (extreme tail circuit breaker; only enforced when 252-day percentile path is available — not on static fallback)

### 7.3 The combined sizing matrix

| | DOW NORMAL (Tue/Wed) | DOW BOOST (Mon/Fri) |
|---|---|---|
| **VVIX LOW (Q1)**     | 1 contract | 2 contracts |
| **VVIX NORMAL (Q2)**  | 2 contracts | 3 contracts |
| **VVIX HIGH (Q3)**    | 3 contracts | 4 contracts |
| **VVIX EXTREME (Q4)** | 4 contracts + hedge | 6 contracts + hedge |

(Numbers are rounded from the raw multiplicative product. The peak cell — VVIX-EXTREME Friday entry with hedge — is the empirically richest trade in the firm's entire strategy space, conditional on the literature being correct.)

### 7.4 OA recipe table for Bot F

Structure: asymmetric IC matching **Bot B's NORMAL spec** (short put Δ0.20, short call Δ0.12, 25pt width per side) across all 8 trade variants. Contracts vary per the matrix. EXTREME variants add a long Δ0.05 put for tail hedge.

**Exits:** like Bot D, Bot F overrides the tier mapping (routes by VVIX × DOW), so Bot A.2's per-tier exit ladder doesn't directly apply. **Default choice: NORMAL-tier exits (20% TP / 100% SL)** flat across all 8 trade variants. Same rationale as Bot D: structure across variants is asymmetric IC at Bot B's NORMAL spec; the NORMAL exit calibration is the structurally-consistent choice. Awaiting confirmation before locking.

| Webhook URL config key | OA bot name | Short put Δ | Short call Δ | Width | Contracts | Hedge | Profit target | Stop loss | Time exit |
|---|---|---|---|---|---|---|---|---|---|
| `DESK_F_TRADE_LOW_NORMAL_URL` | Bot F — LOW NORM | 0.20 | 0.12 | 25pt | 1 | none | **20%** credit | **100%** credit | 10:00 AM ET |
| `DESK_F_TRADE_LOW_BOOST_URL` | Bot F — LOW BOOST | 0.20 | 0.12 | 25pt | 2 | none | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_F_TRADE_NORMAL_NORMAL_URL` | Bot F — NORM NORM | 0.20 | 0.12 | 25pt | 2 | none | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_F_TRADE_NORMAL_BOOST_URL` | Bot F — NORM BOOST | 0.20 | 0.12 | 25pt | 3 | none | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_F_TRADE_HIGH_NORMAL_URL` | Bot F — HIGH NORM | 0.20 | 0.12 | 25pt | 3 | none | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_F_TRADE_HIGH_BOOST_URL` | Bot F — HIGH BOOST | 0.20 | 0.12 | 25pt | 4 | none | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_F_TRADE_EXTREME_NORMAL_HEDGED_URL` | Bot F — EXTR NORM HEDGED | 0.20 | 0.12 | 25pt | 4 | **+1 long put Δ0.05** | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_F_TRADE_EXTREME_BOOST_HEDGED_URL` | Bot F — EXTR BOOST HEDGED | 0.20 | 0.12 | 25pt | 6 | **+1 long put Δ0.05** | 20% credit | 100% credit | 10:00 AM ET |
| `DESK_F_NO_TRADE_URL` | Bot F — NO TRADE | (no order) | | | | | | | |

### 7.5 Bot F steps in OA

Same 9-step pattern, repeated 9 times. Two new wrinkles compared to B/C/D/E:

- **The 2 EXTREME-HEDGED variants** need an additional long put leg added to the IC. In OA this is usually done by adding a separate "Add Position" order in the same bot that opens a long put at Δ0.05 alongside the IC. Verify the timing — the hedge should open at the same time as the IC, not delayed.
- **Cleanup is the same** — single "Clean up trading tags" automation per bot, same as the others.

---

## 8. Complete webhook URL → config-key reference

All 29 new keys, by bot. Copy this list into your `.config` (under the `[WEBHOOKS_DESK_*]` section headers) or set as Railway env vars:

```
# Bot B — Asymmetric IC
DESK_B_TRADE_AGGRESSIVE_URL=...
DESK_B_TRADE_NORMAL_URL=...
DESK_B_TRADE_CONSERVATIVE_URL=...
DESK_B_NO_TRADE_URL=...

# Bot C — Put-Spread Only
DESK_C_TRADE_AGGRESSIVE_URL=...
DESK_C_TRADE_NORMAL_URL=...
DESK_C_TRADE_CONSERVATIVE_URL=...
DESK_C_NO_TRADE_URL=...

# Bot D — VVIX-Conditional
DESK_D_TRADE_VVIX_LOW_URL=...
DESK_D_TRADE_VVIX_NORMAL_URL=...
DESK_D_TRADE_VVIX_HIGH_URL=...
DESK_D_TRADE_VVIX_EXTREME_URL=...
DESK_D_NO_TRADE_URL=...

# Bot E — DOW-Conditional
DESK_E_TRADE_AGGRESSIVE_BOOST_URL=...
DESK_E_TRADE_AGGRESSIVE_NORMAL_URL=...
DESK_E_TRADE_NORMAL_BOOST_URL=...
DESK_E_TRADE_NORMAL_NORMAL_URL=...
DESK_E_TRADE_CONSERVATIVE_BOOST_URL=...
DESK_E_TRADE_CONSERVATIVE_NORMAL_URL=...
DESK_E_NO_TRADE_URL=...

# Bot F — Thesis-Maximizing Combined
DESK_F_TRADE_LOW_NORMAL_URL=...
DESK_F_TRADE_LOW_BOOST_URL=...
DESK_F_TRADE_NORMAL_NORMAL_URL=...
DESK_F_TRADE_NORMAL_BOOST_URL=...
DESK_F_TRADE_HIGH_NORMAL_URL=...
DESK_F_TRADE_HIGH_BOOST_URL=...
DESK_F_TRADE_EXTREME_NORMAL_HEDGED_URL=...
DESK_F_TRADE_EXTREME_BOOST_HEDGED_URL=...
DESK_F_NO_TRADE_URL=...
```

All 29 keys are also documented in [.config.example](../../.config.example) under the `[WEBHOOKS_DESK_B/C/D/E/F]` sections (Bot F section will be added in the next code update — currently only B/C/D/E are there).

---

## 9. Verification — after each bot is configured

Don't deploy all 5 bots at once. After configuring each one, smoke-test it locally before moving to the next.

### 9.1 Local smoke test (Python side)

With `.config` populated for the bot you just configured, manually trigger its endpoint:

```bash
curl http://localhost:8080/asymmetric_condors/trigger     # Bot B
curl http://localhost:8080/overnight_putspread/trigger    # Bot C
curl http://localhost:8080/overnight_condors_vvix/trigger # Bot D
curl http://localhost:8080/overnight_condors_dow/trigger  # Bot E
curl http://localhost:8080/overnight_condors_max/trigger  # Bot F
```

The JSON response should include:

| Field | Bot A | Bot B | Bot C | Bot D | Bot E | Bot F |
|---|---|---|---|---|---|---|
| `decision` | TRADE_NORMAL | TRADE_NORMAL | TRADE_NORMAL | TRADE_VVIX_HIGH | TRADE_NORMAL_BOOST | TRADE_HIGH_BOOST |
| `composite_score` | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| `vvix_bucket` | — | — | — | ✓ | — | ✓ |
| `vvix_percentile` | — | — | — | ✓ | — | ✓ |
| `vvix_bucket_source` | — | — | — | ✓ | — | ✓ |
| `dow_variant` | — | — | — | — | ✓ | ✓ |
| `dow_multiplier` | — | — | — | — | ✓ | ✓ |
| `hedge_attached` | — | — | — | — | — | ✓ |
| `webhook_success` | true | true | true | true | true | true |

### 9.2 Sheet verification

After a successful trigger, one row should appear in your Google Sheet with these new columns populated:

- `Desk_ID` = the bot's identifier (e.g., `overnight_condors_vvix`)
- `Structure_Label` = the structure tag from the desk class (e.g., `IC_25pt_0.16d_VVIXpct252d`)
- `Routed_Tier` = the tier label after transform (e.g., `TRADE_VVIX_HIGH`)
- `VVIX_Bucket` = bucket name for Bots D and F (`LOW`/`NORMAL`/`HIGH`/`EXTREME`)
- `DOW_Multiplier` = sizing multiplier for Bots E and F

### 9.3 OA-side verification

After Python fires the webhook, OA should:
1. Receive the webhook (visible in OA's bot activity log)
2. Fire the IC scanner (visible as "scanner triggered")
3. Open the position with the configured deltas + contract count (visible as a new position in OA's positions tab)

If any of these steps fails, check:
- Webhook URL pasted correctly in `.config` (no trailing whitespace)
- OA bot is enabled (toggle on)
- OA bot's webhook trigger is enabled
- Paper account has sufficient buying power for the configured contract count

### 9.4 Bot D + F specific check — VVIX bucketing source

The first time Bot D or F fires after a market-open day, check the JSON response for:

```json
"vvix_bucket_source": "percentile_252d"
```

If you see `"static_fallback"` instead, the Polygon VVIX history fetch failed. Check the Python logs for the error and confirm your Polygon tier returns I:VVIX historical aggregates.

---

## 10. After all 5 bots are running

Once Bots B/C/D/E/F are configured and smoke-tested:

1. **Push to Railway.** The scheduler will start poking all 5 paper bots automatically during the 1:30–2:30 PM window.
2. **Don't intervene for ~6 weeks.** The promotion criteria (methodology doc §5) require ≥30 closed trades per bot before any conclusion.
3. **Weekly check-in:** open the Sheet, pivot by `Desk_ID`, look at per-bot P&L per trade and max drawdown. If any bot's max drawdown exceeds 200% of Bot A's, pause it for review (see methodology doc §5 demotion rule).
4. **At ~30 trades each:** apply the promotion rule. If Bot F outperforms all of A/B/C/D/E by ≥10% on mean P&L per trade with max DD ≤130% of A's, replace Bot A's webhooks with Bot F's recipe in your live bot group.

---

## 11. Time estimate

Rough time to do the OA configuration:

| Bot | Time | Why |
|---|---|---|
| B (4 recipes) | 30–45 min | New asymmetric scanner template; 4 copies with delta variants |
| C (4 recipes) | 20–30 min | Simpler structure (no call legs); 4 copies |
| D (5 recipes) | 30–45 min | All same IC scanner; just contract counts vary |
| E (7 recipes) | 45–60 min | More copies but same pattern as D |
| F (9 recipes) | 60–90 min | Plus the hedged-variant complexity for the EXTREME pair |
| **Total** | **~3–4.5 hours** | spread across multiple sessions if you want |

This is a one-time setup cost. Once configured, the bots run themselves.

---

## 12. If you get stuck

- **OA recipe questions** (how to add a hedge leg, how to set contract count by webhook, etc.) → consult OA docs or their support; I can't see your OA UI
- **Webhook firing but no trade opening** → 99% of the time it's an OA-side issue (bot disabled, scanner config wrong, insufficient buying power)
- **Webhook not firing** → Python side issue; check `/health` endpoint and `_FROM_FILE` flag, check `.config` keys are present and non-empty
- **Bot D bucket source = static_fallback** → Polygon I:VVIX endpoint issue; check Polygon logs
- **Sheet rows missing new columns** → confirm you're on the latest `feature/multi-desk` branch and `sheets_logger.py` has the appended columns

---

## Appendix: Complete cross-bot label inventory

For your reference when configuring each OA bot's webhook trigger:

```
Bot A (existing, no change):
    TRADE_AGGRESSIVE
    TRADE_NORMAL
    TRADE_CONSERVATIVE
    SKIP (→ NO_TRADE_URL)

Bot B:
    TRADE_AGGRESSIVE
    TRADE_NORMAL
    TRADE_CONSERVATIVE
    SKIP (→ DESK_B_NO_TRADE_URL)

Bot C:
    TRADE_AGGRESSIVE
    TRADE_NORMAL
    TRADE_CONSERVATIVE
    SKIP (→ DESK_C_NO_TRADE_URL)

Bot D:
    TRADE_VVIX_LOW
    TRADE_VVIX_NORMAL
    TRADE_VVIX_HIGH
    TRADE_VVIX_EXTREME
    SKIP (→ DESK_D_NO_TRADE_URL)

Bot E:
    TRADE_AGGRESSIVE_BOOST    (Mon/Fri entry, AGGR signal)
    TRADE_AGGRESSIVE_NORMAL   (Tue/Wed entry, AGGR signal)
    TRADE_NORMAL_BOOST
    TRADE_NORMAL_NORMAL
    TRADE_CONSERVATIVE_BOOST
    TRADE_CONSERVATIVE_NORMAL
    SKIP (→ DESK_E_NO_TRADE_URL; fired on Thursday entries OR composite SKIP)

Bot F:
    TRADE_LOW_NORMAL
    TRADE_LOW_BOOST
    TRADE_NORMAL_NORMAL
    TRADE_NORMAL_BOOST
    TRADE_HIGH_NORMAL
    TRADE_HIGH_BOOST
    TRADE_EXTREME_NORMAL_HEDGED   (auto tail hedge attached)
    TRADE_EXTREME_BOOST_HEDGED    (auto tail hedge attached; peak-EV trade)
    SKIP (→ DESK_F_NO_TRADE_URL; fired on Thursday OR composite SKIP OR VVIX pct ≥ 99)
```
