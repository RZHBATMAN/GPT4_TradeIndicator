"""AsymmetricCondorsDesk — Bot B in the parallel paper trial.

Same signal pipeline as OvernightCondorsDesk. Only the OA-side recipe and
webhook URL prefix differ. Tests whether asymmetric delta selection (wider
puts, narrower calls) outperforms the symmetric IC, given Feunou et al. (2015)
finding that downside VRP is +3.4% while upside VRP is -4.4% — i.e., the
call-wing premium is structurally negative-EV.

OA-side recipe (configured per webhook URL on OA, not in Python):
    short put  Δ ≈ 20 (wider, captures more rich downside premium)
    long put   Δ ≈ 10
    short call Δ ≈ 10 (narrower, less giveback on negative-EV upside)
    long call  Δ ≈ 5

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


class AsymmetricCondorsDesk(OvernightCondorsDesk):
    desk_id = "asymmetric_condors"
    display_name = "Bot B — Asymmetric IC (paper)"
    description = (
        "Same signal as Bot A but with skew-aware structure: "
        "short put Δ20, short call Δ10. Tests Feunou's downside-VRP finding."
    )

    structure_label = "asymmetric_IC_putΔ20_callΔ10"
    config_prefix = "DESK_B_"

    desk_group = "desk1_overnight_vrp"
    desk_group_label = "Desk 1 — Overnight Vol Premium Capture"
    status_label = "paper"

    # Bot B matches Bot A's contract count per tier (per playbook §3.3 — only
    # the structure tilts asymmetric, sizing is identical for clean comparison).
    CONTRACTS_BY_TIER = {
        'TRADE_AGGRESSIVE':   1,
        'TRADE_NORMAL':       1,
        'TRADE_CONSERVATIVE': 1,
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
        def asymmetric_condors_trigger():
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
                    Feunou, Jahan-Parvar &amp; Okou (2015): downside VRP is +3.4% annualized
                    (sellers earn) while upside VRP is −4.4% (sellers PAY). A symmetric IC
                    handicaps itself by selling negative-EV call wings. Widening puts and
                    narrowing calls should improve risk-adjusted return.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Structure:</div>
                <div class="edge-desc">
                    Short put Δ≈20, long put Δ≈10, short call Δ≈10, long call Δ≈5.
                    Same 1 DTE, same 2PM entry → 10AM exit window.
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
