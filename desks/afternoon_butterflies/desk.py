"""AfternoonButterfliesDesk — 0DTE SPX afternoon iron butterfly signal desk.

Simple VIX-level based signal. Intentionally minimal — infrastructure placeholder.
"""
from datetime import time as dt_time, datetime
from typing import Dict, List

import pytz
from flask import jsonify

from core.desk import Desk
from core.config import get_config, get_desk_config
from core.data.market_data import get_spx_snapshot, get_vix_with_retry
from core.webhooks import send_webhook
from core.sheets import log_signal as core_log_signal
from core.alerting import record_signal_success

from desks.afternoon_butterflies.signal_engine import run_signal_analysis
from desks.afternoon_butterflies.config import (
    CONFIG_PREFIX, SHEET_TAB, SHEET_HEADERS,
    WINDOW_START, WINDOW_END, WINDOW_DAYS, POKE_MINUTES,
)

ET_TZ = pytz.timezone('US/Eastern')


class AfternoonButterfliesDesk(Desk):
    desk_id = "afternoon_butterflies"
    display_name = "0DTE Afternoon Butterflies"
    description = "ATM iron butterflies on SPX (0DTE), VIX-level sizing. Entry ~2:00 PM, expire same day."

    window_start = WINDOW_START
    window_end = WINDOW_END
    window_days = WINDOW_DAYS
    poke_minutes = POKE_MINUTES

    config_prefix = CONFIG_PREFIX
    sheet_tab = SHEET_TAB
    sheet_headers = SHEET_HEADERS

    def get_webhook_urls(self, config: Dict) -> Dict[str, str]:
        return {
            'TRADE_AGGRESSIVE': get_desk_config(config, self.config_prefix, 'TRADE_AGGRESSIVE_URL'),
            'TRADE_NORMAL': get_desk_config(config, self.config_prefix, 'TRADE_NORMAL_URL'),
            'TRADE_CONSERVATIVE': get_desk_config(config, self.config_prefix, 'TRADE_CONSERVATIVE_URL'),
            'NO_TRADE': get_desk_config(config, self.config_prefix, 'NO_TRADE_URL'),
        }

    def run_signal_cycle(self, config: Dict) -> Dict:
        """Fetch SPX + VIX -> run signal -> webhook -> log."""
        now = datetime.now(ET_TZ)
        timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")

        print(f"[{timestamp}] [{self.desk_id}] Running signal cycle...")

        # Fetch SPX snapshot for current price
        spx_snapshot = get_spx_snapshot()
        spx_current = spx_snapshot['current'] if spx_snapshot else None

        # Fetch VIX (30-day) for signal
        vix_data = get_vix_with_retry(max_retries=2)
        vix_value = vix_data['current'] if vix_data else None

        # Run signal
        result = run_signal_analysis(vix_value)
        signal = result

        print(f"[{timestamp}] [{self.desk_id}] Signal: {signal['signal']} (VIX={vix_value})")

        # Once-per-day webhook
        today_str = now.strftime('%Y-%m-%d')
        if self._daily_signal_cache['date'] != today_str:
            self._daily_signal_cache['date'] = today_str
            self._daily_signal_cache['webhook_sent'] = False
            self._daily_signal_cache['signal'] = None
            self._daily_signal_cache['score'] = None
            self._daily_signal_cache['poke_count'] = 0

        self._daily_signal_cache['poke_count'] += 1

        # Check if webhook URLs are configured
        webhook_urls = self.get_webhook_urls(config)
        has_urls = any(v for v in webhook_urls.values())

        if not has_urls:
            trade_executed = "NO_CONFIG"
            webhook = {'success': True, 'skipped': True}
            print(f"[{timestamp}] [{self.desk_id}] No webhook URLs configured — signal logged only")
        elif self._daily_signal_cache['webhook_sent']:
            trade_executed = "NO_DUPLICATE"
            webhook = {'success': True, 'skipped': True}
        elif now.weekday() == 4:
            trade_executed = "NO_FRIDAY"
            webhook = {'success': True, 'skipped': True}
        else:
            webhook = send_webhook(signal, webhook_urls)
            if webhook.get('success'):
                self._daily_signal_cache['webhook_sent'] = True
                self._daily_signal_cache['signal'] = signal['signal']
                self._daily_signal_cache['score'] = signal['score']

            if signal['signal'] == 'SKIP':
                trade_executed = "NO_SKIP"
            elif not webhook.get('success'):
                trade_executed = f"NO_WEBHOOK_FAIL"
            else:
                trade_executed = "YES"

        # Log to Sheets
        row = [
            timestamp,
            signal['signal'],
            signal['score'],
            vix_value if vix_value is not None else "",
            spx_current if spx_current is not None else "",
            webhook.get('success', False),
            trade_executed,
            signal['reason'],
            signal.get('wing_width', ''),
            signal.get('exit_strategy', ''),
            "",  # SPX_Expiry (backfill later)
            "",  # Move_Pct
            "",  # Outcome
        ]
        core_log_signal(SHEET_TAB, SHEET_HEADERS, row)

        record_signal_success(desk_id=self.desk_id)

        return {
            'timestamp': timestamp,
            'signal': signal,
            'webhook': webhook,
            'trade_executed': trade_executed,
            'vix_value': vix_value,
            'spx_current': spx_current,
        }

    def register_routes(self, app) -> None:
        """Register Flask routes for this desk."""
        config = get_config()
        is_local = bool(config.get("_FROM_FILE"))

        @app.route("/butterflies/trigger", methods=["GET", "POST"])
        def afternoon_butterflies_trigger():
            """Signal endpoint for 0DTE butterflies."""
            now = datetime.now(ET_TZ)
            timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")

            print(f"\n[{timestamp}] /butterflies/trigger called")

            if not is_local and not self.is_within_window(now):
                return jsonify({
                    "status": "outside_window",
                    "message": "Outside trading window (Mon-Fri, 1:45-2:15 PM ET)",
                    "timestamp": timestamp,
                    "desk": self.desk_id,
                }), 200

            try:
                result = self.run_signal_cycle(config)

                return jsonify({
                    "status": "success",
                    "timestamp": result['timestamp'],
                    "desk": self.desk_id,
                    "decision": result['signal']['signal'],
                    "score": result['signal']['score'],
                    "reason": result['signal']['reason'],
                    "vix": result['vix_value'],
                    "spx_current": result['spx_current'],
                    "trade_executed": result['trade_executed'],
                    "webhook_success": result['webhook'].get('success', False),
                }), 200

            except Exception as e:
                print(f"[{timestamp}] [{self.desk_id}] ERROR: {e}")
                import traceback
                traceback.print_exc()
                return jsonify({"status": "error", "message": str(e)}), 500

    def get_dashboard_html(self) -> str:
        """Return HTML for this desk's tab content."""
        return """
        <div class="strategy-box">
            <div class="strategy-title">0DTE Afternoon Iron Butterflies</div>
            <div class="edge-item">
                <div class="edge-label">Thesis:</div>
                <div class="edge-desc">
                    Selling end-of-day theta collapse on 0DTE options. As expiration approaches,
                    time value decays rapidly — iron butterflies capture this accelerating decay
                    when VIX indicates manageable intraday risk.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Structure:</div>
                <div class="edge-desc">
                    ATM SPX iron butterfly, 0DTE. Entry ~2:00 PM ET, expire same day.
                    Wing width sized by VIX level:
                    AGGRESSIVE (VIX &lt;15) 5pt,
                    NORMAL (15-20) 10pt,
                    CONSERVATIVE (20-25) 15pt,
                    SKIP (VIX &gt;25) no trade.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Risk:</div>
                <div class="edge-desc">
                    Late-day momentum moves or news-driven spikes in the final 2 hours.
                    No overnight risk — all positions are 0DTE. VIX gate at 25 skips
                    high-vol environments entirely.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Execution:</div>
                <div class="edge-desc">
                    Signal-driven from this app via webhook to Option Alpha.
                    OA handles strike selection and exit (expire or 3:50 PM close).
                    One webhook per day.
                </div>
            </div>
        </div>
        <div class="section">
            <div class="section-title">Endpoints</div>
            <div class="endpoint"><a href="/butterflies/trigger">/butterflies/trigger</a> - Generate signal</div>
        </div>
        """
