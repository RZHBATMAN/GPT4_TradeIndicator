#!/usr/bin/env python3
"""
Ren's Trading Firm — Multi-Desk Signal System

Slim app.py: Flask app, desk registry, unified tabbed dashboard.
Each desk registers its own routes via desk.register_routes(app).
"""

from flask import Flask, jsonify
from datetime import datetime
from typing import Dict
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

    # ──────────────────────────────────────────────────────────────────
    # Tab building: partition desks by desk_group.
    #   - Desks with desk_group set → grouped together in one tab per group.
    #   - Desks without desk_group → render as their own tab (back-compat).
    # ──────────────────────────────────────────────────────────────────
    tab_buttons = ['<button class="tab-btn active" onclick="switchTab(\'overview\')">Overview</button>']
    tab_contents = []

    # Partition
    grouped: Dict[str, list] = {}      # group_id → [desks]
    ungrouped: list = []               # [desks]
    for desk in ACTIVE_DESKS:
        if getattr(desk, 'desk_group', None):
            grouped.setdefault(desk.desk_group, []).append(desk)
        else:
            ungrouped.append(desk)

    # Group tabs: one per group, with a compact card per bot inside
    for group_id, desks in grouped.items():
        group_label = desks[0].desk_group_label or group_id
        tab_buttons.append(
            f'<button class="tab-btn" onclick="switchTab(\'{group_id}\')">{group_label}</button>'
        )

        # Build compact bot cards for this group
        bot_cards_html = ""
        for desk in desks:
            health = desk.get_health()
            last_signal = health.get('last_signal') or '—'
            score = health.get('last_score')
            score_str = f"{score:.1f}" if score is not None else "—"
            badge_class = {
                'live': 'badge-live',
                'paper': 'badge-paper',
                'oa-native': 'badge-oa',
            }.get(getattr(desk, 'status_label', 'paper'), 'badge-paper')
            structure = getattr(desk, 'structure_label', '') or ''
            bot_cards_html += f"""
            <div class="bot-card">
                <div class="bot-card-header">
                    <span class="bot-name">{desk.display_name}</span>
                    <span class="badge {badge_class}">{desk.status_label}</span>
                </div>
                <div class="bot-card-body">
                    <div class="bot-line"><strong>Strategy:</strong> {desk.description}</div>
                    {f'<div class="bot-line"><strong>Structure:</strong> <code>{structure}</code></div>' if structure else ''}
                    <div class="bot-line"><strong>Last signal:</strong> {last_signal} &nbsp;·&nbsp; <strong>Score:</strong> {score_str} &nbsp;·&nbsp; <strong>Pokes today:</strong> {health.get('poke_count', 0)}</div>
                    <div class="bot-line"><a href="/{desk.desk_id}/trigger">/{desk.desk_id}/trigger</a></div>
                </div>
            </div>
            """

        # Group-specific OA-native add-ons. For Desk 1, this is the ONLY currently-live bot
        # in the group, so it renders at the TOP of the tab (above the paper python-signal bots).
        oa_native_extras = ""
        if group_id == "desk1_overnight_vrp":
            oa_native_extras = """
            <div class="bot-card bot-card-oa bot-card-featured">
                <div class="bot-card-header">
                    <span class="bot-name">Simple Condor (OA-native) — currently the only live bot</span>
                    <span>
                        <span class="badge badge-live">live</span>
                        <span class="badge badge-oa">oa-native</span>
                    </span>
                </div>
                <div class="bot-card-body">
                    <div class="bot-line"><strong>Strategy:</strong> SPX iron condor, OA-managed end-to-end. No Python signal — OA's own scanner picks the entry; this app is not in the loop.</div>
                    <div class="bot-line"><strong>Structure:</strong> <code>simple_IC_+_stop_loss_+_time_exit</code></div>
                    <div class="bot-line"><strong>Role in the firm:</strong> production reference. Every python-signal bot below (Bot A control + B–F paper trial) is being measured against this baseline.</div>
                </div>
            </div>
            """

        tab_contents.append(f"""
        <div class="tab-content" id="tab-{group_id}" style="display:none;">
            <div class="group-header">
                <div class="group-title">{group_label}</div>
                <div class="group-meta">{('1 live (OA-native) · ' if oa_native_extras else '') + str(len(desks)) + ' python-signal bot(s) (paper)'} &nbsp;·&nbsp; signal pipeline shared across all python-signal bots in this group</div>
            </div>
            {oa_native_extras}
            {bot_cards_html}
        </div>
        """)

    # Ungrouped desks: render full HTML in their own tab (back-compat)
    for desk in ungrouped:
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

    # ──────────────────────────────────────────────────────────────────
    # Overview tab: one card per GROUP (collapsed), one card per ungrouped desk.
    # This avoids cluttering the homepage with 6+ overnight bot cards.
    # ──────────────────────────────────────────────────────────────────
    desk_cards = ""

    # Grouped desks: render one summary card per group
    for group_id, desks in grouped.items():
        group_label = desks[0].desk_group_label or group_id
        live_count = sum(1 for d in desks if getattr(d, 'status_label', 'paper') == 'live')
        paper_count = sum(1 for d in desks if getattr(d, 'status_label', 'paper') == 'paper')
        # OA-native bots are managed end-to-end on Option Alpha (no Python signal).
        # In Desk 1 the only OA-native bot (Simple Condor) is currently the firm's
        # only LIVE bot, so we surface that explicitly.
        oa_native_live_extra = 1 if group_id == "desk1_overnight_vrp" else 0
        composition_bits = []
        if oa_native_live_extra:
            composition_bits.append(f"{oa_native_live_extra} live (OA-native)")
        if live_count:
            composition_bits.append(f"{live_count} live (python-signal)")
        if paper_count:
            composition_bits.append(f"{paper_count} paper")
        composition = " · ".join(composition_bits)

        first_description = desks[0].description if desks else ''
        total_in_group = len(desks) + oa_native_live_extra
        desk_cards += f"""
        <div class="desk-card" onclick="switchTabFromCard('{group_id}')" style="cursor:pointer;">
            <div class="desk-card-title">{group_label} <span class="composition-tag">{composition}</span></div>
            <div class="desk-card-desc">{first_description}</div>
            <div class="desk-card-stats">
                <span>Bots in group: <strong>{total_in_group}</strong></span>
                <span>Click to view all bots →</span>
            </div>
        </div>
        """

    # Ungrouped desks: render full per-desk card (back-compat)
    for desk in ungrouped:
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

    # OA-native group cards for overview (existing — unchanged)
    desk_cards += """
        <div class="desk-card" onclick="switchTabFromCard('gex')" style="cursor:pointer; border-left: 3px solid #8b5cf6;">
            <div class="desk-card-title">GEX Desks <span class="oa-badge-inline">OA-Native</span></div>
            <div class="desk-card-desc">Dealer gamma exposure strategies — Wall (sell premium at pin levels) and Snap (mean-revert toward GEX pillars).</div>
            <div class="desk-card-stats"><span>Click to view →</span></div>
        </div>
        <div class="desk-card" onclick="switchTabFromCard('intraday_fly')" style="cursor:pointer; border-left: 3px solid #8b5cf6;">
            <div class="desk-card-title">Intraday Vol Overpricing Fly <span class="oa-badge-inline">OA-Native</span></div>
            <div class="desk-card-desc">Sell intraday vol overpricing via 0DTE iron fly anchored at previous close. Entry 10 AM ET.</div>
            <div class="desk-card-stats"><span>Click to view →</span></div>
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
            /* ── Compact group-tab styling (Desk N tabs) ───────────────── */
            .group-header {{
                background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
                color: white;
                padding: 14px 18px;
                border-radius: 8px;
                margin-bottom: 14px;
            }}
            .group-title {{
                font-size: 16px;
                font-weight: 700;
                margin-bottom: 4px;
            }}
            .group-meta {{
                font-size: 12px;
                color: #cbd5e1;
            }}
            .bot-card {{
                background: white;
                border: 1px solid #e2e8f0;
                border-left: 3px solid #2a5298;
                border-radius: 6px;
                padding: 10px 14px;
                margin: 8px 0;
                font-size: 13px;
            }}
            .bot-card-oa {{
                border-left-color: #8b5cf6;
                background: #faf5ff;
            }}
            /* Featured card: used for the only live bot in a group (Simple Condor in Desk 1). */
            .bot-card-featured {{
                border-left-width: 5px;
                box-shadow: 0 2px 8px rgba(16, 185, 129, 0.12);
            }}
            .bot-card-featured .bot-name {{ color: #0f5132; }}
            .bot-card-header {{
                display: flex;
                justify-content: space-between;
                align-items: center;
                margin-bottom: 6px;
            }}
            .bot-name {{
                font-weight: 700;
                color: #1e3c72;
                font-size: 14px;
            }}
            .bot-card-body {{
                color: #475569;
            }}
            .bot-line {{
                margin: 3px 0;
                line-height: 1.4;
            }}
            .bot-line code {{
                background: #f1f5f9;
                padding: 1px 6px;
                border-radius: 4px;
                font-size: 12px;
                color: #1e293b;
            }}
            .badge {{
                padding: 2px 8px;
                border-radius: 10px;
                font-size: 10px;
                font-weight: 700;
                color: white;
                text-transform: uppercase;
            }}
            .badge-live {{ background: #10b981; }}
            .badge-paper {{ background: #d97706; }}
            .badge-oa {{ background: #8b5cf6; }}
            .composition-tag {{
                font-size: 11px;
                color: #64748b;
                font-weight: 500;
                margin-left: 8px;
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
        // Click-from-overview-card: switch to the matching tab and visually mark the tab button.
        function switchTabFromCard(tabId) {{
            document.querySelectorAll('.tab-content').forEach(el => el.style.display = 'none');
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            const target = document.getElementById('tab-' + tabId);
            if (target) target.style.display = 'block';
            // Mark the corresponding tab button as active
            document.querySelectorAll('.tab-btn').forEach(btn => {{
                if (btn.getAttribute('onclick') && btn.getAttribute('onclick').includes("'" + tabId + "'")) {{
                    btn.classList.add('active');
                }}
            }});
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
