#!/usr/bin/env python3
"""
Ren's Trading Firm — Multi-Desk Signal System

Slim app.py: Flask app, desk registry, unified tabbed dashboard.
Each desk registers its own routes via desk.register_routes(app).
"""

from flask import Flask, jsonify
from datetime import datetime
import pytz
import os

from core.config import get_config
from core.alerting import get_alert_status
from core.scheduler import start_scheduler
from core.data.market_data import get_spx_snapshot, get_vix1d_snapshot, get_vix_snapshot, get_spx_aggregates
from desks import ACTIVE_DESKS

app = Flask(__name__)

# Configuration
ET_TZ = pytz.timezone('US/Eastern')
CONFIG = get_config()
POLYGON_API_KEY = CONFIG.get('POLYGON_API_KEY')
IS_LOCAL = bool(CONFIG.get("_FROM_FILE"))
ENVIRONMENT_LABEL = "Local (Test)" if IS_LOCAL else "Railway Production"

# Register all desk routes
for desk in ACTIVE_DESKS:
    desk.register_routes(app)


# ============================================================================
# SHARED ROUTES
# ============================================================================

@app.route("/", methods=["GET"])
def homepage():
    """Tabbed firm dashboard."""
    now = datetime.now(ET_TZ)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    status_class = "status status-local" if IS_LOCAL else "status status-production"
    status_text = "LOCAL (TEST)" if IS_LOCAL else "PRODUCTION"

    # Build desk tabs and content
    tab_buttons = ['<button class="tab-btn active" onclick="switchTab(\'overview\')">Overview</button>']
    tab_contents = []

    for desk in ACTIVE_DESKS:
        tab_id = desk.desk_id
        tab_buttons.append(
            f'<button class="tab-btn" onclick="switchTab(\'{tab_id}\')">{desk.display_name}</button>'
        )
        tab_contents.append(f"""
        <div class="tab-content" id="tab-{tab_id}" style="display:none;">
            {desk.get_dashboard_html()}
        </div>
        """)

    # OA-native desk tabs (not signal-driven from this app)
    tab_buttons.append('<button class="tab-btn" onclick="switchTab(\'gex\')">GEX Desks</button>')
    tab_buttons.append('<button class="tab-btn" onclick="switchTab(\'intraday_fly\')">Intraday Fly</button>')
    tab_contents.append("""
        <div class="tab-content" id="tab-gex" style="display:none;">
            <div class="oa-badge">OA-Native</div>
            <div class="strategy-box" style="border-color: #8b5cf6;">
                <div class="strategy-title" style="color: #7c3aed;">GEX Wall Desk</div>
                <div class="edge-item">
                    <div class="edge-label">Thesis:</div>
                    <div class="edge-desc">
                        When dealers are net long gamma at a strike, their delta hedging suppresses price movement
                        — buying dips and selling rallies. High-GEX strikes act as walls that pin price,
                        creating a predictable range-bound environment for premium selling.
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Structure:</div>
                    <div class="edge-desc">
                        Sell premium around identified GEX wall levels where dealer hedging
                        is expected to suppress movement. Strike selection based on where
                        gamma exposure is concentrated.
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Risk:</div>
                    <div class="edge-desc">
                        GEX levels shift as options are traded throughout the day. A catalyst can
                        overwhelm the dealer hedging flow, breaking through the wall. Requires
                        accurate, timely GEX data to identify true wall levels.
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Execution:</div>
                    <div class="edge-desc">
                        Fully managed on Option Alpha. OA handles signal generation, strike selection,
                        entry timing, and exit management. No webhook from this app.
                    </div>
                </div>
            </div>
            <div class="strategy-box" style="border-color: #8b5cf6;">
                <div class="strategy-title" style="color: #7c3aed;">GEX Snap Desk</div>
                <div class="edge-item">
                    <div class="edge-label">Thesis:</div>
                    <div class="edge-desc">
                        High-GEX strikes act as gravitational pillars — when price moves away,
                        dealer hedging creates a force that pulls it back toward the pin.
                        Trade the snap-back toward the GEX level, not the breakout away from it.
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Structure:</div>
                    <div class="edge-desc">
                        Mean-reversion positions toward the GEX pillar. When price pulls away from
                        a high-GEX strike, position for the snap back as dealer hedging
                        attracts price toward the pin level.
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Risk:</div>
                    <div class="edge-desc">
                        A catalyst strong enough to overwhelm dealer hedging flow — price moves away
                        from the GEX level and keeps going. Requires accurate identification of
                        true GEX pillars vs weak levels that won't hold.
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Execution:</div>
                    <div class="edge-desc">
                        Fully managed on Option Alpha. OA handles signal generation, strike selection,
                        entry timing, and exit management. No webhook from this app.
                    </div>
                </div>
            </div>
        </div>
        """)

    # Intraday Fly tab (OA-native)
    tab_contents.append("""
        <div class="tab-content" id="tab-intraday_fly" style="display:none;">
            <div class="oa-badge">OA-Native</div>
            <div class="strategy-box" style="border-color: #8b5cf6;">
                <div class="strategy-title" style="color: #7c3aed;">Intraday Vol Overpricing Fly</div>
                <div class="edge-item">
                    <div class="edge-label">Thesis:</div>
                    <div class="edge-desc">
                        Selling the gap between the intraday move the market prices in and the intraday move
                        that actually occurs, anchored to yesterday's close. 0DTE options systematically
                        overprice realized intraday movement due to structural demand for portfolio hedging
                        (variance risk premium).
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Structure:</div>
                    <div class="edge-desc">
                        0DTE iron butterfly centered at previous day's closing price. Entry at 10:00 AM ET
                        (after opening auction noise settles and spreads tighten). Expire same day.
                        Previous close is a natural anchor — last consensus price where all participants agreed,
                        and dealer hedging / open interest concentrates around it, creating a magnet effect.
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Risk:</div>
                    <div class="edge-desc">
                        Tail days — CPI surprises, Fed announcements, or breaking events that push SPX 2-3%
                        in one direction. Iron fly max loss = width - credit. Backtest looks good because
                        most days are range-bound, but tail losses can be large.
                    </div>
                </div>
                <div class="edge-item">
                    <div class="edge-label">Execution:</div>
                    <div class="edge-desc">
                        Fully managed on Option Alpha. OA handles entry at 10 AM, strike selection
                        (centered at previous close), and exit management. No webhook from this app.
                        Same vol overpricing thesis as Desk 1 (overnight condors) but different time window.
                    </div>
                </div>
            </div>
        </div>
        """)

    # Overview tab: desk cards
    desk_cards = ""
    for desk in ACTIVE_DESKS:
        health = desk.get_health()
        last_signal = health.get('last_signal') or 'None today'
        last_score = health.get('last_score')
        score_str = f"{last_score:.1f}" if last_score is not None else "-"
        desk_cards += f"""
        <div class="desk-card">
            <div class="desk-card-title">{desk.display_name}</div>
            <div class="desk-card-desc">{desk.description}</div>
            <div class="desk-card-stats">
                <span>Last Signal: <strong>{last_signal}</strong></span>
                <span>Score: <strong>{score_str}</strong></span>
                <span>Pokes today: <strong>{health.get('poke_count', 0)}</strong></span>
            </div>
        </div>
        """

    # OA-native desk cards for overview
    desk_cards += """
        <div class="desk-card" style="border-left: 3px solid #8b5cf6;">
            <div class="desk-card-title">GEX Desks <span class="oa-badge-inline">OA-Native</span></div>
            <div class="desk-card-desc">Dealer gamma exposure strategies — Wall (sell premium at pin levels) and Snap (mean-revert toward GEX pillars).</div>
            <div class="desk-card-stats"></div>
        </div>
        <div class="desk-card" style="border-left: 3px solid #8b5cf6;">
            <div class="desk-card-title">Intraday Vol Overpricing Fly <span class="oa-badge-inline">OA-Native</span></div>
            <div class="desk-card-desc">Sell intraday vol overpricing via 0DTE iron fly anchored at previous close. Entry 10 AM ET.</div>
            <div class="desk-card-stats"></div>
        </div>
        """

    poke_label = "Disabled (local testing)" if IS_LOCAL else "Active (multi-desk scheduler)"
    trading_windows = ", ".join(
        f"{d.display_name}: {d.window_start.strftime('%I:%M %p')}-{d.window_end.strftime('%I:%M %p')} ET"
        for d in ACTIVE_DESKS
    )

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ren's Trading Firm</title>
        <style>
            body {{
                font-family: 'Segoe UI', sans-serif;
                max-width: 1100px;
                margin: 40px auto;
                padding: 20px;
                background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
            }}
            .container {{
                background: white;
                border-radius: 12px;
                padding: 40px;
                box-shadow: 0 15px 50px rgba(0,0,0,0.3);
            }}
            .header {{
                border-bottom: 3px solid #2a5298;
                padding-bottom: 20px;
                margin-bottom: 30px;
                display: flex;
                justify-content: space-between;
                align-items: center;
            }}
            h1 {{
                color: #1e3c72;
                margin: 0;
                font-size: 28px;
            }}
            .status {{
                display: inline-block;
                padding: 8px 16px;
                border-radius: 20px;
                font-weight: bold;
                color: white;
                font-size: 13px;
            }}
            .status-production {{ background: #10b981; }}
            .status-local {{ background: #d97706; border: 2px solid #b45309; }}
            .tab-bar {{
                display: flex;
                gap: 4px;
                margin-bottom: 24px;
                border-bottom: 2px solid #e2e8f0;
                padding-bottom: 0;
            }}
            .tab-btn {{
                padding: 10px 20px;
                border: none;
                background: #f1f5f9;
                color: #475569;
                font-size: 14px;
                font-weight: 600;
                cursor: pointer;
                border-radius: 8px 8px 0 0;
                transition: all 0.2s;
            }}
            .tab-btn:hover {{ background: #e2e8f0; }}
            .tab-btn.active {{
                background: #2a5298;
                color: white;
            }}
            .section {{
                margin: 20px 0;
                padding: 16px 20px;
                background: #f8fafc;
                border-radius: 8px;
                border-left: 4px solid #2a5298;
            }}
            .section-title {{
                font-size: 16px;
                font-weight: 700;
                color: #1e3c72;
                margin: 0 0 12px 0;
            }}
            .info-item {{
                margin: 6px 0;
                padding: 6px 10px;
                background: white;
                border-radius: 6px;
                font-size: 14px;
            }}
            .info-label {{
                font-weight: 600;
                color: #475569;
                display: inline-block;
                min-width: 180px;
            }}
            .info-value {{ color: #1e293b; }}
            .strategy-box {{
                background: #eff6ff;
                border: 2px solid #3b82f6;
                padding: 20px;
                border-radius: 8px;
                margin: 20px 0;
            }}
            .strategy-title {{
                font-size: 18px;
                font-weight: 700;
                color: #1e40af;
                margin: 0 0 12px 0;
            }}
            .edge-item {{
                padding: 8px 0;
                border-bottom: 1px solid #cbd5e1;
            }}
            .edge-item:last-child {{ border-bottom: none; }}
            .edge-label {{ font-weight: 600; color: #1e40af; }}
            .edge-desc {{ color: #475569; margin-top: 4px; font-size: 14px; }}
            .endpoint {{
                background: #f3f4f6;
                padding: 12px;
                margin: 8px 0;
                border-radius: 6px;
                font-family: monospace;
                font-size: 14px;
            }}
            .endpoint a {{ color: #2a5298; text-decoration: none; font-weight: bold; }}
            .endpoint a:hover {{ text-decoration: underline; }}
            .desk-card {{
                background: #f8fafc;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                padding: 20px;
                margin: 12px 0;
            }}
            .desk-card-title {{
                font-size: 16px;
                font-weight: 700;
                color: #1e3c72;
                margin-bottom: 6px;
            }}
            .desk-card-desc {{
                font-size: 13px;
                color: #64748b;
                margin-bottom: 10px;
            }}
            .desk-card-stats {{
                display: flex;
                gap: 24px;
                font-size: 13px;
                color: #334155;
            }}
            .oa-badge {{
                display: inline-block;
                background: #8b5cf6;
                color: white;
                padding: 4px 12px;
                border-radius: 12px;
                font-size: 11px;
                font-weight: 700;
                margin-bottom: 16px;
            }}
            .oa-badge-inline {{
                background: #8b5cf6;
                color: white;
                padding: 2px 8px;
                border-radius: 10px;
                font-size: 10px;
                font-weight: 600;
                margin-left: 8px;
                vertical-align: middle;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>Ren's Trading Firm</h1>
                <span class="{status_class}">{status_text}</span>
                <div style="font-size: 13px; color: #64748b; margin-top: 8px; width: 100%;">All desks and strategies are under active development.</div>
            </div>

            <div class="tab-bar">
                {''.join(tab_buttons)}
            </div>

            <div class="tab-content" id="tab-overview">
                {desk_cards}
                <div class="section">
                    <div class="section-title">System Information</div>
                    <div class="info-item"><span class="info-label">Current Time:</span> <span class="info-value">{timestamp}</span></div>
                    <div class="info-item"><span class="info-label">Trading Windows:</span> <span class="info-value">{trading_windows}</span></div>
                    <div class="info-item"><span class="info-label">Environment:</span> <span class="info-value">{ENVIRONMENT_LABEL}</span></div>
                    <div class="info-item"><span class="info-label">Scheduler:</span> <span class="info-value">{poke_label}</span></div>
                </div>
                <div class="section">
                    <div class="section-title">Shared Endpoints</div>
                    <div class="endpoint"><a href="/health">/health</a> - Health check (all desks)</div>
                    <div class="endpoint"><a href="/test_polygon_delayed">/test_polygon_delayed</a> - Test Polygon data</div>
                    <div class="endpoint"><a href="/test_slack">/test_slack</a> - Send test Slack alert</div>
                </div>
            </div>

            {''.join(tab_contents)}
        </div>

        <script>
        function switchTab(tabId) {{
            document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById('tab-' + tabId).style.display = 'block';
            event.target.classList.add('active');
        }}
        </script>
    </body>
    </html>
    """
    return html


@app.route("/health", methods=["GET"])
def health_check():
    """Health check for all desks."""
    now = datetime.now(ET_TZ)
    return jsonify({
        "status": "healthy",
        "timestamp": now.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
        "environment": "local" if IS_LOCAL else "production",
        "desks": {desk.desk_id: desk.get_health() for desk in ACTIVE_DESKS},
        "alerting": get_alert_status(),
    }), 200


@app.route("/test_polygon_delayed", methods=["GET"])
def test_polygon_delayed():
    """Test Polygon Indices Starter - SPX and VIX1D (15-min delayed)"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'plan': 'Indices Starter ($49/mo) - 15-min delayed',
    }

    if not POLYGON_API_KEY:
        return jsonify({'error': 'No API key'}), 500

    spx_snapshot = get_spx_snapshot()
    results['spx_snapshot'] = {
        'status': 'SUCCESS' if spx_snapshot else 'FAILED',
        'data': spx_snapshot
    }

    vix1d_snapshot = get_vix1d_snapshot()
    results['vix1d_snapshot'] = {
        'status': 'SUCCESS' if vix1d_snapshot else 'FAILED',
        'data': vix1d_snapshot
    }

    vix_snapshot = get_vix_snapshot()
    results['vix_snapshot'] = {
        'status': 'SUCCESS' if vix_snapshot else 'FAILED',
        'data': vix_snapshot
    }

    spx_agg = get_spx_aggregates()
    results['spx_aggregates'] = {
        'status': 'SUCCESS' if spx_agg else 'FAILED',
        'days_returned': len(spx_agg['closes']) if spx_agg else 0,
        'sample_closes': spx_agg['closes'][:5] if spx_agg else []
    }

    if spx_snapshot and vix1d_snapshot and spx_agg:
        results['status'] = 'READY'
    else:
        results['status'] = 'PARTIAL'

    return jsonify(results), 200


@app.route("/test_slack", methods=["GET"])
def test_slack():
    """Send a test alert to Slack to verify webhook configuration."""
    from core.alerting import _send_alert, _get_webhook_url

    now = datetime.now(ET_TZ)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")

    success = _send_alert(
        "Test Alert",
        "This is a test alert from Ren's Trading Firm. "
        "If you see this in Slack, alerting is working correctly!",
        level='info',
    )

    if success:
        return jsonify({
            "status": "success",
            "message": "Test alert sent to Slack successfully!",
            "timestamp": timestamp,
        }), 200
    else:
        url = _get_webhook_url()
        if not url:
            return jsonify({
                "status": "error",
                "message": "ALERT_WEBHOOK_URL is not configured.",
                "timestamp": timestamp,
            }), 400
        else:
            return jsonify({
                "status": "error",
                "message": "Webhook is configured but the alert failed to send.",
                "webhook_url_prefix": url[:40] + "...",
                "timestamp": timestamp,
            }), 500


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))

    print("=" * 80)
    print("Ren's Trading Firm — Multi-Desk Signal System")
    print("=" * 80)
    print(f"Port: {PORT}")
    print(f"Environment: {ENVIRONMENT_LABEL}")
    print(f"Active Desks: {len(ACTIVE_DESKS)}")
    for desk in ACTIVE_DESKS:
        print(f"  - {desk.display_name} ({desk.desk_id})")
        print(f"    Window: {desk.window_start.strftime('%I:%M %p')}-{desk.window_end.strftime('%I:%M %p')} ET")
    print("=" * 80)

    # Start multi-desk scheduler
    start_scheduler(ACTIVE_DESKS, is_local=IS_LOCAL)

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
