"""OvernightCondorsMaxDesk — Bot F in the parallel paper trial.

The "thesis-maximizing" bot: encodes EVERY actionable conditional dimension
the literature supports, all in one bot, into the discrete-label space that
OA can route on. This is the production candidate the multi-bot trial is
designed to converge toward.

═══════════════════════════════════════════════════════════════════════════════
DIMENSIONS COMBINED IN BOT F'S LABEL SPACE
═══════════════════════════════════════════════════════════════════════════════

(1) STRUCTURE — fixed at asymmetric IC for all variants
    Per Feunou et al. (2015): downside VRP is +3.4%, upside VRP is -4.4%.
    Asymmetric IC (short put Δ20, short call Δ10) maximises the positive-EV
    leg while minimising negative-EV call drag. Defined risk both sides.

(2) VVIX BUCKET — 4 quartile-aligned regimes (Papagelis & Dotsis Table 6)
    Q1 (LOW)    : 0.5× contracts   — overnight P&L -3.01
    Q2 (NORMAL) : 1.0× contracts   — overnight P&L -2.82
    Q3 (HIGH)   : 1.5× contracts   — overnight P&L -6.77
    Q4 (EXTR)   : 2.0× contracts   — overnight P&L -17.88   (~6× richer)

(3) DAY-OF-WEEK MULTIPLIER (Papagelis Table 4 Panel B)
    Mon/Fri entry  → ×1.5  (BOOST: rich Co window — Mon = weekend hold,
                            Fri = next-Mo hold)
    Tue/Wed entry  → ×1.0  (NORMAL: significantly negative Co premium)
    Thu entry      → SKIP   (Co premium statistically insignificant; trade
                             is essentially long noise)

(4) TAIL HEDGE — auto-attached when VVIX bucket = EXTREME
    Per Iyer (2024) tail-risk overlay literature and the empirical fact that
    the EXTREME quartile is where short-vol can blow up: every EXTREME-bucket
    trade routes to a `_HEDGED` recipe that opens the asymmetric IC PLUS a
    long deep-OTM put (e.g., 5Δ) for tail protection. The premium gradient
    in Q4 is 6× — but so is the tail; sizing up without hedging is reckless.

(5) EXTREME TAIL CIRCUIT BREAKER — VVIX percentile ≥ 99 → SKIP
    Per the implementation plan §3.7: at the very top of the VVIX
    distribution, VRP can invert (realised > implied) and short-vol
    strategies blow up. Force SKIP regardless of upstream signal.

═══════════════════════════════════════════════════════════════════════════════
LABEL SPACE — 9 webhook URLs total
═══════════════════════════════════════════════════════════════════════════════

  TIER LABEL                          → OA recipe (configured per webhook URL)
  ────────────────────────────────────────────────────────────────────────────
  TRADE_LOW_NORMAL                    → asymmetric IC, 0.5× contracts
  TRADE_LOW_BOOST                     → asymmetric IC, 0.75× contracts
  TRADE_NORMAL_NORMAL                 → asymmetric IC, 1.0× contracts
  TRADE_NORMAL_BOOST                  → asymmetric IC, 1.5× contracts
  TRADE_HIGH_NORMAL                   → asymmetric IC, 1.5× contracts
  TRADE_HIGH_BOOST                    → asymmetric IC, 2.25× contracts
  TRADE_EXTREME_NORMAL_HEDGED         → asymmetric IC, 2.0× + long Δ5 put
  TRADE_EXTREME_BOOST_HEDGED          → asymmetric IC, 3.0× + long Δ5 put
  NO_TRADE                            → no position (logs only)

The peak-EV trade per the literature is `TRADE_EXTREME_BOOST_HEDGED` —
high-VVIX Friday entry with maximum sizing AND tail protection.

SKIP precedence (highest first):
  1. Composite signal SKIP (existing 3-factor model says don't trade)
  2. Thursday entry (Co premium thin)
  3. VVIX percentile ≥ 99 (tail-regime circuit breaker)
"""
from typing import Dict

from flask import jsonify
from datetime import datetime
import pytz

from core.config import get_config, get_desk_config
from core.data.market_data import vvix_percentile_bucket, vvix_percentile_252d
from desks.overnight_condors.desk import OvernightCondorsDesk

ET_TZ = pytz.timezone('US/Eastern')

# Day-of-week classification for entry day (Mon=0 ... Fri=4)
DOW_BOOST_DAYS = {0, 4}      # Monday, Friday
DOW_NORMAL_DAYS = {1, 2}     # Tuesday, Wednesday
DOW_SKIP_DAYS = {3}          # Thursday

# Extreme tail circuit breaker
VVIX_TAIL_SKIP_PERCENTILE = 99.0


class OvernightCondorsMaxDesk(OvernightCondorsDesk):
    desk_id = "overnight_condors_max"
    display_name = "Bot F — Thesis-Max Combined (paper)"
    description = (
        "Combined VVIX × DOW × asymmetric-IC × tail-hedge. "
        "Encodes every actionable dimension the literature supports into one "
        "bot. Production candidate."
    )

    structure_label = "asymIC_VVIXpct252d_DOWmult_EXTRhedge"
    config_prefix = "DESK_F_"
    desk_group = "desk1_overnight_vrp"
    desk_group_label = "Desk 1 — Overnight Vol Premium Capture"
    status_label = "paper"

    def get_webhook_urls(self, config: Dict) -> Dict[str, str]:
        """9 webhook URLs: 8 trade variants + 1 no-trade.

        Naming: TRADE_<vvix_bucket>_<dow_variant>[_HEDGED]
        EXTREME bucket variants are always _HEDGED (tail protection mandatory).
        """
        return {
            'TRADE_LOW_NORMAL':              get_desk_config(config, self.config_prefix, 'TRADE_LOW_NORMAL_URL'),
            'TRADE_LOW_BOOST':               get_desk_config(config, self.config_prefix, 'TRADE_LOW_BOOST_URL'),
            'TRADE_NORMAL_NORMAL':           get_desk_config(config, self.config_prefix, 'TRADE_NORMAL_NORMAL_URL'),
            'TRADE_NORMAL_BOOST':            get_desk_config(config, self.config_prefix, 'TRADE_NORMAL_BOOST_URL'),
            'TRADE_HIGH_NORMAL':             get_desk_config(config, self.config_prefix, 'TRADE_HIGH_NORMAL_URL'),
            'TRADE_HIGH_BOOST':              get_desk_config(config, self.config_prefix, 'TRADE_HIGH_BOOST_URL'),
            'TRADE_EXTREME_NORMAL_HEDGED':   get_desk_config(config, self.config_prefix, 'TRADE_EXTREME_NORMAL_HEDGED_URL'),
            'TRADE_EXTREME_BOOST_HEDGED':    get_desk_config(config, self.config_prefix, 'TRADE_EXTREME_BOOST_HEDGED_URL'),
            'NO_TRADE':                      get_desk_config(config, self.config_prefix, 'NO_TRADE_URL'),
            # Map upstream SKIP through the same NO_TRADE URL
            'SKIP':                          get_desk_config(config, self.config_prefix, 'NO_TRADE_URL'),
        }

    def transform_signal_for_routing(self, signal: Dict, ctx: Dict) -> Dict:
        """Combine (composite tier, VVIX bucket, weekday) → 1 of 9 tier labels.

        SKIP precedence (highest first):
          1. Composite signal SKIP (existing 3-factor model SKIP)
          2. Thursday entry (Papagelis Table 4: Co premium insignificant)
          3. VVIX percentile ≥ 99 (extreme tail circuit breaker)

        Otherwise: TRADE_<bucket>_<dow_variant>[_HEDGED] where _HEDGED is
        appended only when bucket == EXTREME.

        Stashes vvix_bucket, vvix_percentile, vvix_bucket_source,
        dow_variant, dow_multiplier, original_tier, hedge_attached,
        skip_reason on the signal dict for logging / debugging.
        """
        original_tier = signal.get('signal', '')
        now = ctx.get('now') or datetime.now(ET_TZ)
        weekday = now.weekday()

        vvix_data = ctx.get('vvix_data')
        vvix_value = vvix_data.get('current') if vvix_data else None

        # Compute VVIX bucket once (used for routing AND skip-by-tail check)
        bucket, percentile, sample_size, source = vvix_percentile_bucket(vvix_value)

        # Always stash VVIX context for logging — even if we end up skipping
        signal['vvix_bucket'] = bucket
        signal['vvix_value'] = vvix_value
        signal['vvix_percentile'] = percentile
        signal['vvix_sample_size'] = sample_size
        signal['vvix_bucket_source'] = source
        signal['original_tier'] = original_tier

        # ── SKIP precedence ─────────────────────────────────────────────────
        # 1. Composite SKIP from upstream signal engine (event gates, GPT
        #    extreme override, contradiction detection, etc.)
        if original_tier == 'SKIP':
            signal['signal'] = 'SKIP'
            signal['dow_variant'] = ''
            signal['dow_multiplier'] = '0_skip_composite'
            signal['hedge_attached'] = False
            signal['skip_reason'] = 'composite_signal_SKIP'
            return signal

        # 2. Thursday entry — Co premium statistically insignificant
        if weekday in DOW_SKIP_DAYS:
            signal['signal'] = 'SKIP'
            signal['dow_variant'] = ''
            signal['dow_multiplier'] = '0_skip_thursday'
            signal['hedge_attached'] = False
            signal['skip_reason'] = 'thursday_thin_premium_papagelis_t4'
            return signal

        # 3. VVIX percentile ≥ 99 — tail-regime circuit breaker
        # Only enforceable when percentile path is available; static fallback
        # cannot distinguish the 99th percentile precisely so we let it pass
        # (the EXTREME bucket + mandatory hedge already provides protection).
        if percentile is not None and percentile >= VVIX_TAIL_SKIP_PERCENTILE:
            signal['signal'] = 'SKIP'
            signal['dow_variant'] = ''
            signal['dow_multiplier'] = '0_skip_vvix_tail'
            signal['hedge_attached'] = False
            signal['skip_reason'] = f'vvix_pct_{percentile:.1f}_above_99'
            return signal

        # ── ROUTE ───────────────────────────────────────────────────────────
        if weekday in DOW_BOOST_DAYS:
            dow_variant = 'BOOST'
            dow_multiplier = '1.5'
        else:
            dow_variant = 'NORMAL'
            dow_multiplier = '1.0'

        # EXTREME bucket → mandatory tail hedge (HEDGED suffix)
        if bucket == 'EXTREME':
            tier_label = f'TRADE_{bucket}_{dow_variant}_HEDGED'
            hedge_attached = True
        else:
            tier_label = f'TRADE_{bucket}_{dow_variant}'
            hedge_attached = False

        signal['signal'] = tier_label
        signal['dow_variant'] = dow_variant
        signal['dow_multiplier'] = dow_multiplier
        signal['hedge_attached'] = hedge_attached
        signal['skip_reason'] = ''
        return signal

    def register_routes(self, app) -> None:
        config = get_config()
        is_local = bool(config.get("_FROM_FILE"))

        @app.route(f"/{self.desk_id}/trigger", methods=["GET", "POST"])
        def overnight_condors_max_trigger():
            now = datetime.now(ET_TZ)
            timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
            print(f"\n[{timestamp}] /{self.desk_id}/trigger called")

            if not is_local and not self.is_within_window(now):
                return jsonify({
                    "status": "outside_window",
                    "message": "Outside trading window (Mon-Fri, 1:30-2:30 PM ET)",
                    "timestamp": timestamp,
                    "desk": self.desk_id,
                }), 200

            try:
                result = self.run_signal_cycle(config)
                if 'error' in result:
                    return jsonify({"status": "error", "message": result['error']}), 500
                signal = result['signal']
                return jsonify({
                    "status": "success",
                    "timestamp": result['timestamp'],
                    "desk": self.desk_id,
                    "structure": self.structure_label,
                    "decision": signal.get('signal'),
                    "original_tier": signal.get('original_tier'),
                    "vvix_bucket": signal.get('vvix_bucket'),
                    "vvix_percentile": signal.get('vvix_percentile'),
                    "vvix_value": signal.get('vvix_value'),
                    "vvix_bucket_source": signal.get('vvix_bucket_source'),
                    "dow_variant": signal.get('dow_variant'),
                    "dow_multiplier": signal.get('dow_multiplier'),
                    "hedge_attached": signal.get('hedge_attached'),
                    "skip_reason": signal.get('skip_reason'),
                    "composite_score": result['composite']['score'],
                    "trade_executed": result['trade_executed'],
                    "webhook_success": result['webhook'].get('success', False),
                }), 200
            except Exception as e:
                print(f"[{timestamp}] [{self.desk_id}] ERROR: {e}")
                import traceback
                traceback.print_exc()
                return jsonify({"status": "error", "message": str(e)}), 500

    def get_dashboard_html(self) -> str:
        return f"""
        <div class="strategy-box">
            <div class="strategy-title">{self.display_name}</div>
            <div class="edge-item">
                <div class="edge-label">Hypothesis:</div>
                <div class="edge-desc">
                    Maximally express the academic thesis: combine asymmetric IC structure
                    (Feunou: downside VRP +3.4% vs upside −4.4%) with VVIX-quartile sizing
                    (Papagelis Table 6: Q4 premium 6× richer than Q1) and DOW conditioning
                    (Papagelis Table 4: Mon/Fri richest, Thu insignificant), plus mandatory
                    tail hedge in the EXTREME bucket.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Structure:</div>
                <div class="edge-desc">
                    Asymmetric IC (short put Δ20, short call Δ10) across all variants.
                    Contracts = VVIX_multiplier × DOW_multiplier:<br>
                    LOW 0.5×, NORMAL 1×, HIGH 1.5×, EXTREME 2× × DOW (1.0 Tue/Wed; 1.5 Mon/Fri).<br>
                    EXTREME variants auto-attach long Δ5 put for tail protection.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Skip rules:</div>
                <div class="edge-desc">
                    Composite SKIP from upstream → SKIP. Thursday entry → SKIP (premium
                    insignificant per Papagelis T4). VVIX percentile ≥ 99 → SKIP
                    (tail-regime circuit breaker; only enforced when percentile path
                    available, not on static fallback).
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Peak-EV trade:</div>
                <div class="edge-desc">
                    <code>TRADE_EXTREME_BOOST_HEDGED</code> — high-VVIX Friday entry with
                    3× contracts and tail hedge. Empirically the richest single label
                    in the entire firm's strategy space.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Status:</div>
                <div class="edge-desc">
                    Paper trading. Production candidate — if Bot F outperforms A/B/C/D/E
                    over 30+ trades, promotes to live and replaces Bot A.
                </div>
            </div>
        </div>
        <div class="section">
            <div class="section-title">Endpoints</div>
            <div class="endpoint"><a href="/{self.desk_id}/trigger">/{self.desk_id}/trigger</a> - Generate signal</div>
        </div>
        """
