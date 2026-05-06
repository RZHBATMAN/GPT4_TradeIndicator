"""OvernightCondorsDowDesk — Bot E in the parallel paper trial.

Same signal pipeline AND same structural recipe as Bot A. The ONLY difference
is sizing keyed off day-of-week.

Empirical basis: Papagelis & Dotsis (2025) Table 4 Panel B — variance-swap
close-to-open P&L by weekday across 14 underlyings:

    Mon Co (= Friday→Monday weekend hold):  most negative for 12/14 underlyings
    Tue Co, Wed Co:                          significantly negative, similar magnitude
    Thu Co:                                  mostly insignificant — premium thin
    Fri Co:                                  mostly insignificant for non-US;
                                             the *Friday entry* trade actually rolls
                                             into the rich Monday Co

Translation to Bot E sizing logic by *entry day* (the day we open the trade):

    Friday entry  → Monday-morning exit (~64h hold incl. weekend) → BOOST 1.5×
    Monday entry  → Tuesday-morning exit                          → BOOST 1.5×
    Tue / Wed entry                                                → NORMAL 1.0×
    Thursday entry → Friday-morning exit (Thu Co premium thin)     → SKIP

The transform hook rewrites the standard tier label by appending a DOW
suffix (_BOOST or _NORMAL). Thursday entries are forced to SKIP regardless
of the upstream signal, on the grounds that Thursday Co premium is
statistically insignificant per Papagelis Table 4 Panel B.
"""
from typing import Dict

from flask import jsonify
from datetime import datetime
import pytz

from core.config import get_config, get_desk_config
from desks.overnight_condors.desk import OvernightCondorsDesk

ET_TZ = pytz.timezone('US/Eastern')

# Day-of-week classification for entry day (Mon=0 ... Fri=4)
DOW_BOOST_DAYS = {0, 4}      # Monday, Friday
DOW_NORMAL_DAYS = {1, 2}     # Tuesday, Wednesday
DOW_SKIP_DAYS = {3}          # Thursday


class OvernightCondorsDowDesk(OvernightCondorsDesk):
    desk_id = "overnight_condors_dow"
    display_name = "Bot E — DOW-Sized IC (paper)"
    description = (
        "Same signal as Bot A. Sizing scales with day-of-week: "
        "Mon/Fri 1.5× (richest Co windows), Tue/Wed 1× (baseline), "
        "Thursday SKIP (premium thin per Papagelis)."
    )

    structure_label = "IC_25pt_0.16d_DOWsized"
    config_prefix = "DESK_E_"

    desk_group = "desk1_overnight_vrp"
    desk_group_label = "Desk 1 — Overnight Vol Premium Capture"
    status_label = "paper"

    def get_webhook_urls(self, config: Dict) -> Dict[str, str]:
        """Webhook URLs keyed by tier_DOWvariant."""
        urls = {
            'NO_TRADE': get_desk_config(config, self.config_prefix, 'NO_TRADE_URL'),
            'SKIP':     get_desk_config(config, self.config_prefix, 'NO_TRADE_URL'),
        }
        # 3 base tiers × 2 DOW variants = 6 trade URLs
        for base in ['TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE']:
            for variant in ['BOOST', 'NORMAL']:
                key = f'{base}_{variant}'
                urls[key] = get_desk_config(config, self.config_prefix, f'{key}_URL')
        return urls

    def transform_signal_for_routing(self, signal: Dict, ctx: Dict) -> Dict:
        """Map (TRADE_*, weekday) → TRADE_*_BOOST / TRADE_*_NORMAL / SKIP.

        Thursday → forced SKIP regardless of upstream signal.
        """
        original_tier = signal.get('signal', '')
        now = ctx.get('now') or datetime.now(ET_TZ)
        weekday = now.weekday()

        # Upstream SKIP is preserved
        if original_tier == 'SKIP':
            signal['dow_multiplier'] = '0.0_SKIP_signal'
            return signal

        # Thursday entry → forced SKIP (Thursday Co premium is statistically
        # insignificant per Papagelis Table 4 Panel B)
        if weekday in DOW_SKIP_DAYS:
            signal['signal'] = 'SKIP'
            signal['dow_multiplier'] = '0.0_SKIP_thursday'
            signal['dow_skip_reason'] = 'Thursday Co premium thin (Papagelis Table 4)'
            signal['original_tier'] = original_tier
            return signal

        # Mon/Fri → BOOST, Tue/Wed → NORMAL
        if weekday in DOW_BOOST_DAYS:
            variant = 'BOOST'
            multiplier = '1.5'
        else:
            variant = 'NORMAL'
            multiplier = '1.0'

        signal['original_tier'] = original_tier
        signal['signal'] = f'{original_tier}_{variant}'
        signal['dow_multiplier'] = multiplier
        signal['dow_variant'] = variant
        return signal

    def register_routes(self, app) -> None:
        config = get_config()
        is_local = bool(config.get("_FROM_FILE"))

        @app.route(f"/{self.desk_id}/trigger", methods=["GET", "POST"])
        def overnight_condors_dow_trigger():
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
                    "dow_variant": signal.get('dow_variant'),
                    "dow_multiplier": signal.get('dow_multiplier'),
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
                    Papagelis &amp; Dotsis (2025) Table 4 Panel B — Monday close-to-open
                    (= Friday→Monday weekend hold) is the most negative variance-swap return
                    of the week for 12/14 underlyings. Thursday close-to-open is statistically
                    insignificant. Sizing up Mon/Fri entries and skipping Thursday should
                    improve risk-adjusted return.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Structure:</div>
                <div class="edge-desc">
                    Same IC as Bot A. Sizing by entry day:<br>
                    • Mon/Fri entry → 1.5× contracts (BOOST)<br>
                    • Tue/Wed entry → 1.0× contracts (NORMAL)<br>
                    • Thursday entry → SKIP (premium thin)
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
