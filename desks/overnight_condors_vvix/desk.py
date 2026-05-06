"""OvernightCondorsVvixDesk — Bot D in the parallel paper trial.

Same signal pipeline AND same structural recipe as Bot A (symmetric IC).
The ONLY difference is sizing: contract count scales with VVIX regime.

Empirical basis: Papagelis & Dotsis (2025) Table 6. Variance-swap P&L during
the overnight (close-to-open) period by VVIX quartile (sample 2010-2022):

    Q1 (lowest VVIX):  -3.01    ← thin premium → smaller size (LOW)
    Q2:                -2.82    ← baseline                    (NORMAL)
    Q3:                -6.77    ← ~2× richer                   (HIGH)
    Q4 (highest VVIX): -17.88   ← ~6× richer AND tail-heaviest (EXTREME)

We bucket the *current* VVIX by its quartile rank within the trailing 252
trading days of VVIX closes — fetched from Polygon and cached daily across
all desks. Quartile boundaries are aligned with Papagelis's table directly:

    pct < 25  → LOW       (Q1; 0.5× contracts)
    25 ≤ <50  → NORMAL    (Q2; 1×   contracts)
    50 ≤ <75  → HIGH      (Q3; 1.5× contracts)
    pct ≥ 75  → EXTREME   (Q4; 2×   contracts + OA-side optional tail hedge)

If the Polygon history fetch fails or returns < 60 bars, the bucketer falls
back to a static-threshold path (vvix_static_bucket()) so the bot continues
to trade. The fallback is logged via the `vvix_bucket_source` field as
'static_fallback'.

The transform hook rewrites the standard tier label (TRADE_AGGRESSIVE/
NORMAL/CONSERVATIVE/SKIP) into the VVIX-bucket tier (TRADE_VVIX_LOW/
NORMAL/HIGH/EXTREME or NO_TRADE), routing to a different OA recipe per
bucket so OA can apply the appropriate contract multiplier.

SKIP from the upstream signal is preserved — it overrides VVIX bucketing.
"""
from typing import Dict

from flask import jsonify
from datetime import datetime
import pytz

from core.config import get_config, get_desk_config
from core.data.market_data import vvix_percentile_bucket
from desks.overnight_condors.desk import OvernightCondorsDesk

ET_TZ = pytz.timezone('US/Eastern')


class OvernightCondorsVvixDesk(OvernightCondorsDesk):
    desk_id = "overnight_condors_vvix"
    display_name = "Bot D — VVIX-Sized IC (paper)"
    description = (
        "Same signal as Bot A. Sizing scales with VVIX regime, "
        "bucketed by trailing 252-day percentile rank (quartile-aligned with "
        "Papagelis & Dotsis Table 6). Falls back to static thresholds if "
        "history is unavailable."
    )

    structure_label = "IC_25pt_0.16d_VVIXpct252d"
    config_prefix = "DESK_D_"

    desk_group = "desk1_overnight_vrp"
    desk_group_label = "Desk 1 — Overnight Vol Premium Capture"
    status_label = "paper"

    def get_webhook_urls(self, config: Dict) -> Dict[str, str]:
        """Five webhook URLs — one per VVIX bucket plus NO_TRADE for SKIP."""
        return {
            'TRADE_VVIX_LOW':     get_desk_config(config, self.config_prefix, 'TRADE_VVIX_LOW_URL'),
            'TRADE_VVIX_NORMAL':  get_desk_config(config, self.config_prefix, 'TRADE_VVIX_NORMAL_URL'),
            'TRADE_VVIX_HIGH':    get_desk_config(config, self.config_prefix, 'TRADE_VVIX_HIGH_URL'),
            'TRADE_VVIX_EXTREME': get_desk_config(config, self.config_prefix, 'TRADE_VVIX_EXTREME_URL'),
            'NO_TRADE':           get_desk_config(config, self.config_prefix, 'NO_TRADE_URL'),
            # Also handle SKIP routing in case the original signal label leaks through
            'SKIP':               get_desk_config(config, self.config_prefix, 'NO_TRADE_URL'),
        }

    def transform_signal_for_routing(self, signal: Dict, ctx: Dict) -> Dict:
        """Map (TRADE_*, VVIX_value) → TRADE_VVIX_<bucket>. Preserve SKIP.

        Uses 252-day rolling percentile via vvix_percentile_bucket(), which
        falls back to static thresholds if Polygon history is unavailable.
        Source ('percentile_252d' vs 'static_fallback') is logged for audit.
        """
        original_tier = signal.get('signal', '')
        if original_tier == 'SKIP':
            # SKIP overrides — don't trade regardless of VVIX
            signal['vvix_bucket'] = 'SKIP'
            return signal

        vvix_data = ctx.get('vvix_data')
        vvix_value = vvix_data.get('current') if vvix_data else None

        bucket, percentile, sample_size, source = vvix_percentile_bucket(vvix_value)

        signal['vvix_bucket'] = bucket
        signal['vvix_value'] = vvix_value
        signal['vvix_percentile'] = percentile          # None when fallback
        signal['vvix_sample_size'] = sample_size        # 0 when fallback
        signal['vvix_bucket_source'] = source           # 'percentile_252d' or 'static_fallback'
        signal['original_tier'] = original_tier
        signal['signal'] = f'TRADE_VVIX_{bucket}'
        return signal

    def register_routes(self, app) -> None:
        config = get_config()
        is_local = bool(config.get("_FROM_FILE"))

        @app.route(f"/{self.desk_id}/trigger", methods=["GET", "POST"])
        def overnight_condors_vvix_trigger():
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
                    "vvix_bucket": signal.get('vvix_bucket'),
                    "vvix_value": signal.get('vvix_value'),
                    "vvix_percentile": signal.get('vvix_percentile'),
                    "vvix_sample_size": signal.get('vvix_sample_size'),
                    "vvix_bucket_source": signal.get('vvix_bucket_source'),
                    "original_tier": signal.get('original_tier'),
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
                    Papagelis &amp; Dotsis (2025) Table 6 — Q4 vol-of-vol days produce
                    overnight VRP P&amp;L of -17.88 vs -3.01 for Q1 — a 6× richer premium.
                    Sizing 2× on high-VVIX days should materially improve risk-adjusted return.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Structure:</div>
                <div class="edge-desc">
                    Same IC as Bot A (Δ16/10, 25pt). Contract count varies by VVIX
                    quartile rank within the trailing 252 trading days:<br>
                    Q1 (pct&lt;25) LOW 0.5×, Q2 (25-50) NORMAL 1×,
                    Q3 (50-75) HIGH 1.5×, Q4 (≥75) EXTREME 2×.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Bucketing source:</div>
                <div class="edge-desc">
                    True 252-day rolling percentile via Polygon I:VVIX aggregates,
                    cached daily across all desks. Static threshold fallback if
                    history fetch fails (logged as static_fallback for audit).
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Status:</div>
                <div class="edge-desc">Paper trading. Promotion criteria in plan §3.5.</div>
            </div>
        </div>
        <div class="section">
            <div class="section-title">Endpoints</div>
            <div class="endpoint"><a href="/{self.desk_id}/trigger">/{self.desk_id}/trigger</a> - Generate signal</div>
        </div>
        """
