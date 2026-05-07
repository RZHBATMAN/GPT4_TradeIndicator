"""OvernightPutspreadDesk — Bot C in the parallel paper trial.

Same signal pipeline as OvernightCondorsDesk. Only the OA-side recipe differs:
short put-spread only — no call leg. Tests the strongest theoretical claim
from Feunou et al. (2015): if upside VRP is structurally negative (-4.4%),
then dropping the call side entirely should improve risk-adjusted edge.

OA-side recipe (configured per webhook URL on OA, not in Python):
    short put  Δ ≈ 16
    long put   Δ ≈ 10
    (no call side)

Note: per-trade credit is roughly half of the IC's, so OA recipe should size
contracts ~2× to match Bot A's gross margin commitment for cleaner comparison.

All other logic — signal engine, factor weights, contradiction detection,
confirmation pass, gates — is inherited unchanged from OvernightCondorsDesk.
"""
from typing import Dict

from flask import jsonify
from datetime import datetime
import pytz

from core.config import get_config, get_desk_config
from desks.overnight_condors.desk import OvernightCondorsDesk

ET_TZ = pytz.timezone('US/Eastern')


class OvernightPutspreadDesk(OvernightCondorsDesk):
    desk_id = "overnight_putspread"
    display_name = "Bot C — Put-Spread Only (paper)"
    description = (
        "Same signal as Bot A but trades short put-spread only "
        "(no call side). Tests Feunou's pure-downside-harvest hypothesis."
    )

    structure_label = "putspread_putΔ16_2x_size"
    config_prefix = "DESK_C_"

    desk_group = "desk1_overnight_vrp"
    desk_group_label = "Desk 1 — Overnight Vol Premium Capture"
    status_label = "paper"

    # Bot C: 2× Bot A contracts (per playbook §4.3 — put-spread uses ~half the
    # margin per contract, doubling normalises gross margin commitment).
    CONTRACTS_BY_TIER = {
        'TRADE_AGGRESSIVE':   2,
        'TRADE_NORMAL':       2,
        'TRADE_CONSERVATIVE': 2,
    }

    def get_webhook_urls(self, config: Dict) -> Dict[str, str]:
        return {
            'TRADE_AGGRESSIVE':   get_desk_config(config, self.config_prefix, 'TRADE_AGGRESSIVE_URL'),
            'TRADE_NORMAL':       get_desk_config(config, self.config_prefix, 'TRADE_NORMAL_URL'),
            'TRADE_CONSERVATIVE': get_desk_config(config, self.config_prefix, 'TRADE_CONSERVATIVE_URL'),
            'NO_TRADE':           get_desk_config(config, self.config_prefix, 'NO_TRADE_URL'),
        }

    def register_routes(self, app) -> None:
        config = get_config()
        is_local = bool(config.get("_FROM_FILE"))

        @app.route(f"/{self.desk_id}/trigger", methods=["GET", "POST"])
        def overnight_putspread_trigger():
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
                return jsonify({
                    "status": "success",
                    "timestamp": result['timestamp'],
                    "desk": self.desk_id,
                    "structure": self.structure_label,
                    "decision": result['signal']['signal'],
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
                    Pure downside-VRP harvest. Feunou et al. (2015) decomposes total VRP into
                    V_RP_D (+3.4%, sellers earn) and V_RP_U (−4.4%, sellers PAY). A put-spread
                    captures only the positive-EV leg. Bondarenko (2019) PUT/WPUT 32-year
                    data supports this on a longer timescale.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Structure:</div>
                <div class="edge-desc">
                    Short put Δ≈16, long put Δ≈10. No call side. Sized 2× contracts to match
                    Bot A's gross margin for clean comparison. Same 1 DTE, 2PM → 10AM hold.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Risk:</div>
                <div class="edge-desc">
                    Loses on sharp upside rallies (no call leg to offset). But theory says we
                    weren't earning on those calls anyway. Asymmetric P&L profile.
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
