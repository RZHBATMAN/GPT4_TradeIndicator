#!/usr/bin/env python3
"""
SPX Overnight Vol Premium Bot - Railway Production
Uses Polygon/Massive Indices Starter ($49/mo) - 15-min delayed data
Real SPX + VIX1D data (no more proxies!)

Triple-Layer Filtering: Algo Dedup → Keyword Filter → GPT Analysis
"""

from flask import Flask, jsonify
from datetime import datetime, time as dt_time
import pytz
import os
import threading
import time as time_module
import requests

# Import modular components
from config.loader import get_config
from data.market_data import get_spx_data_with_retry, get_vix1d_with_retry, get_spx_snapshot, get_vix1d_snapshot, get_spx_aggregates
from data.news_fetcher import fetch_news_raw
from processing.pipeline import process_news_pipeline
from signal_engine import run_signal_analysis
from webhooks import send_webhook
from sheets_logger import log_signal as log_signal_to_sheets

app = Flask(__name__)

# Configuration
ET_TZ = pytz.timezone('US/Eastern')

# Trading windows - PRODUCTION: Mon-Fri, 1:30-2:30 PM ET
TRADING_WINDOW_START = dt_time(hour=13, minute=30)
TRADING_WINDOW_END = dt_time(hour=14, minute=30)

# Load config at startup
CONFIG = get_config()
POLYGON_API_KEY = CONFIG.get('POLYGON_API_KEY')

# Derived: True when config was loaded from .config (local), False when env-only (e.g. Railway)
IS_LOCAL = bool(CONFIG.get("_FROM_FILE"))
TRADING_WINDOW_LABEL = "24 hours (local testing)" if IS_LOCAL else "Mon-Fri, 1:30 PM - 2:30 PM ET"
ENVIRONMENT_LABEL = "Local (Test)" if IS_LOCAL else "Railway Production"
POKE_LABEL = "Disabled (local testing — trigger manually)" if IS_LOCAL else "Active (every 20 min in window)"

# ============================================================================
# TRADING WINDOW CHECK
# ============================================================================

def is_within_trading_window(now=None):
    """Check if within 1:30-2:30 PM ET trading window on weekdays (Mon-Fri).
    When config is loaded from .config (local), window is 24hr so you can test any time.
    """
    if now is None:
        now = datetime.now(ET_TZ)

    # Local: config from .config → 24hr window for testing
    if CONFIG.get("_FROM_FILE"):
        return True

    # Production: enforce Mon–Fri 1:30–2:30 PM ET
    if now.weekday() >= 5:  # Saturday or Sunday
        return False
    current_time = now.time()
    return TRADING_WINDOW_START <= current_time <= TRADING_WINDOW_END

# ============================================================================
# FLASK ROUTES
# ============================================================================

@app.route("/", methods=["GET"])
def homepage():
    """Homepage - Concise, Professional, Holistic"""
    now = datetime.now(ET_TZ)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    status_class = "status status-local" if IS_LOCAL else "status status-production"
    status_text = "LOCAL (TEST) · 24hr trading window" if IS_LOCAL else "PRODUCTION · Mon–Fri 1:30–2:30 PM ET"

    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Ren's SPX Vol Signal</title>
        <style>
            body {{
                font-family: 'Segoe UI', sans-serif;
                max-width: 1000px;
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
            }}
            h1 {{ 
                color: #1e3c72; 
                margin: 0 0 10px 0;
                font-size: 32px;
            }}
            .subtitle {{
                color: #64748b;
                font-size: 16px;
                font-weight: 500;
            }}
            .status {{
                display: inline-block;
                padding: 10px 20px;
                border-radius: 25px;
                font-weight: bold;
                color: white;
                margin-top: 15px;
            }}
            .status-production {{
                background: #10b981;
            }}
            .status-local {{
                background: #d97706;
                border: 2px solid #b45309;
            }}
            .section {{
                margin: 25px 0;
                padding: 20px;
                background: #f8fafc;
                border-radius: 8px;
                border-left: 4px solid #2a5298;
            }}
            .section-title {{
                font-size: 18px;
                font-weight: 700;
                color: #1e3c72;
                margin: 0 0 15px 0;
            }}
            .info-item {{
                margin: 8px 0;
                padding: 8px 12px;
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
            .info-value {{
                color: #1e293b;
            }}
            .strategy-box {{
                background: #eff6ff;
                border: 2px solid #3b82f6;
                padding: 20px;
                border-radius: 8px;
                margin: 25px 0;
            }}
            .strategy-title {{
                font-size: 20px;
                font-weight: 700;
                color: #1e40af;
                margin: 0 0 15px 0;
            }}
            .edge-item {{
                padding: 10px 0;
                border-bottom: 1px solid #cbd5e1;
            }}
            .edge-item:last-child {{
                border-bottom: none;
            }}
            .edge-label {{
                font-weight: 600;
                color: #1e40af;
            }}
            .edge-desc {{
                color: #475569;
                margin-top: 5px;
                font-size: 14px;
            }}
            .endpoint {{
                background: #f3f4f6;
                padding: 14px;
                margin: 10px 0;
                border-radius: 6px;
                font-family: monospace;
                font-size: 14px;
            }}
            .endpoint a {{ 
                color: #2a5298; 
                text-decoration: none; 
                font-weight: bold; 
            }}
            .endpoint a:hover {{
                text-decoration: underline;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>📊 Ren's SPX Vol Signal</h1>
                <div class="subtitle">Automated SPX Overnight Iron Condor Decision System</div>
                <div class="{status_class}">{status_text}</div>
            </div>
            
            <div class="strategy-box">
                <div class="strategy-title">🎯 Trading Strategy: Overnight Vol Premium Capture</div>
                
                <div class="edge-item">
                    <div class="edge-label">📈 Core Edge:</div>
                    <div class="edge-desc">
                        Sell SPX iron condors (1:30-2:30 PM entry, 1 DTE) when implied volatility is rich relative to realized volatility 
                        and overnight news risk is manageable. Capture theta decay + vol premium during the ~16-hour overnight period.
                    </div>
                </div>
                
                <div class="edge-item">
                    <div class="edge-label">🔍 Trading Factors (3 Factors):</div>
                    <div class="edge-desc">
                        <strong>1. IV/RV Ratio (30%):</strong> Real VIX1D (1-day forward IV) vs 10-day realized vol.<br>
                        <strong>2. Market Trend (20%):</strong> Analyzes momentum and intraday volatility.<br>
                        <strong>3. GPT News Analysis (50%):</strong> Triple-layer filtering (Algo dedup → Keyword → GPT).
                    </div>
                </div>
                
                <div class="edge-item">
                    <div class="edge-label">⚡ Trade Sizing Logic:</div>
                    <div class="edge-desc">
                        <strong>AGGRESSIVE:</strong> Score &lt;3.5 → 20pt width, 0.18 delta<br>
                        <strong>NORMAL:</strong> Score 3.5-5.0 → 25pt width, 0.16 delta<br>
                        <strong>CONSERVATIVE:</strong> Score 5.0-7.5 → 30pt width, 0.14 delta<br>
                        <strong>SKIP:</strong> Score ≥7.5 → No trade
                    </div>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">⚙️ System Information</div>
                <div class="info-item">
                    <span class="info-label">Current Time:</span>
                    <span class="info-value">{timestamp}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Trading Window:</span>
                    <span class="info-value">{TRADING_WINDOW_LABEL}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Environment:</span>
                    <span class="info-value">{ENVIRONMENT_LABEL}</span>
                </div>
                <div class="info-item">
                    <span class="info-label">Scheduler (POKE):</span>
                    <span class="info-value">{POKE_LABEL}</span>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">📡 Data Sources</div>
                <div class="info-item">
                    <span class="info-label">Market Data Provider:</span>
                    <span class="info-value">Polygon/Massive Indices Starter ($49/mo)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">SPX Data:</span>
                    <span class="info-value">Real I:SPX snapshot + aggregates (15-min delayed)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">VIX1D Data:</span>
                    <span class="info-value">Real I:VIX1D snapshot (15-min delayed, 1-day forward IV)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">News Sources:</span>
                    <span class="info-value">Yahoo Finance RSS + Google News RSS (FREE)</span>
                </div>
                <div class="info-item">
                    <span class="info-label">AI Analysis:</span>
                    <span class="info-value">GPT-4 Turbo (OpenAI)</span>
                </div>
            </div>
            
            <div class="section">
                <div class="section-title">🔗 API Endpoints</div>
                <div class="endpoint"><a href="/health">/health</a> - Health check</div>
                <div class="endpoint"><a href="/option_alpha_trigger">/option_alpha_trigger</a> - Generate trading signal</div>
                <div class="endpoint"><a href="/test_polygon_delayed">/test_polygon_delayed</a> - Test Polygon data</div>
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.route("/health", methods=["GET"])
def health_check():
    """Health check"""
    now = datetime.now(ET_TZ)
    return jsonify({
        "status": "healthy",
        "timestamp": now.strftime("%Y-%m-%d %I:%M:%S %p %Z"),
        "environment": "local" if IS_LOCAL else "production",
        "trading_window": TRADING_WINDOW_LABEL,
        "filtering": "Triple-layer (Algo dedup → Keyword → GPT)",
        "market_data_source": "Polygon/Massive Indices Starter ($49/mo)",
        "news_sources": "Yahoo Finance RSS + Google News RSS (FREE)",
        "spx_data": "Real I:SPX (15-min delayed)",
        "vix_data": "Real I:VIX1D (15-min delayed, 1-day forward IV)"
    }), 200

@app.route("/option_alpha_trigger", methods=["GET", "POST"])
def option_alpha_trigger():
    """Main trading decision endpoint"""
    now = datetime.now(ET_TZ)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    
    print(f"\n[{timestamp}] /option_alpha_trigger called")
    
    # Check trading window
    if not is_within_trading_window(now):
        return jsonify({
            "status": "outside_window",
            "message": "Outside trading window (" + ("24hr on local" if IS_LOCAL else "Mon-Fri, 1:30-2:30 PM ET") + ")",
            "timestamp": timestamp,
            "environment": "local" if IS_LOCAL else "production",
        }), 200
    
    try:
        print(f"[{timestamp}] Fetching market data from Polygon...")
        
        # Fetch SPX data (snapshot + aggregates)
        spx_data = get_spx_data_with_retry(max_retries=3)
        if not spx_data:
            return jsonify({"status": "error", "message": "SPX data failed after 3 retries (Polygon)"}), 500
        
        # Fetch VIX1D data
        vix1d_data = get_vix1d_with_retry(max_retries=3)
        if not vix1d_data:
            return jsonify({"status": "error", "message": "VIX1D data failed after 3 retries (Polygon)"}), 500
        
        # Fetch and process news
        print(f"[{timestamp}] Fetching news from RSS sources...")
        raw_articles = fetch_news_raw()
        
        print(f"[{timestamp}] Processing news (deduplication + filtering)...")
        news_data = process_news_pipeline(raw_articles)
        
        print(f"[{timestamp}] Analyzing factors...")
        
        # Use the signal engine to run all analysis
        analysis_result = run_signal_analysis(spx_data, vix1d_data, news_data)
        
        factors = analysis_result['indicators']  # Internal: signal_engine uses 'indicators' key
        composite = analysis_result['composite']
        signal = analysis_result['signal']
        
        iv_rv = factors['iv_rv']
        trend = factors['trend']
        gpt = factors['gpt']
        
        # Detailed logging for each factor
        print(f"\n[{timestamp}] ========== FACTOR ANALYSIS ==========")
        
        # Factor 1: IV/RV Ratio (30% weight)
        print(f"[{timestamp}] FACTOR 1: IV/RV Ratio (Weight: 30%)")
        print(f"[{timestamp}]   - VIX1D (Implied Vol): {iv_rv['implied_vol']:.2f}%")
        print(f"[{timestamp}]   - Realized Vol (10-day): {iv_rv['realized_vol']:.2f}%")
        print(f"[{timestamp}]   - IV/RV Ratio: {iv_rv['iv_rv_ratio']:.3f}")
        if 'rv_change' in iv_rv:
            print(f"[{timestamp}]   - RV Change: {iv_rv['rv_change']*100:+.2f}%")
        print(f"[{timestamp}]   - Factor Score: {iv_rv['score']:.1f}/10")
        print(f"[{timestamp}]   - Weighted Contribution: {iv_rv['score'] * 0.30:.2f}")
        
        # Factor 2: Market Trend (20% weight)
        print(f"[{timestamp}] FACTOR 2: Market Trend (Weight: 20%)")
        print(f"[{timestamp}]   - SPX Current: {spx_data['current']:.2f}")
        print(f"[{timestamp}]   - SPX High Today: {spx_data['high_today']:.2f}")
        print(f"[{timestamp}]   - SPX Low Today: {spx_data['low_today']:.2f}")
        print(f"[{timestamp}]   - 5-Day Change: {trend['change_5d']*100:+.2f}%")
        print(f"[{timestamp}]   - Intraday Range: {trend['intraday_range']*100:.2f}%")
        print(f"[{timestamp}]   - Factor Score: {trend['score']:.1f}/10")
        print(f"[{timestamp}]   - Weighted Contribution: {trend['score'] * 0.20:.2f}")
        
        # Factor 3: GPT News Analysis (50% weight)
        print(f"[{timestamp}] FACTOR 3: GPT News Analysis (Weight: 50%)")
        print(f"[{timestamp}]   - News Pipeline Stats:")
        filter_stats = news_data.get('filter_stats', {})
        print(f"[{timestamp}]     * Raw Articles Fetched: {filter_stats.get('raw_articles', 0)}")
        print(f"[{timestamp}]     * Duplicates Removed: {filter_stats.get('duplicates_removed', 0)}")
        print(f"[{timestamp}]     * Unique Articles: {filter_stats.get('unique_articles', 0)}")
        print(f"[{timestamp}]     * Junk Filtered: {filter_stats.get('junk_filtered', 0)}")
        print(f"[{timestamp}]     * Sent to GPT: {filter_stats.get('sent_to_gpt', 0)}")
        print(f"[{timestamp}]   - GPT Analysis:")
        print(f"[{timestamp}]     * Category: {gpt.get('category', 'UNKNOWN')}")
        print(f"[{timestamp}]     * Key Risk: {gpt.get('key_risk', 'None')}")
        print(f"[{timestamp}]     * Direction Risk: {gpt.get('direction_risk', 'UNKNOWN')}")
        if 'duplicates_found' in gpt:
            print(f"[{timestamp}]     * Duplicates Found by GPT: {gpt['duplicates_found']}")
        print(f"[{timestamp}]   - Factor Score: {gpt['score']:.1f}/10")
        print(f"[{timestamp}]   - Weighted Contribution: {gpt['score'] * 0.50:.2f}")
        print(f"[{timestamp}]   - GPT Reasoning: {gpt.get('reasoning', 'N/A')[:200]}...")
        
        # Composite Score
        print(f"\n[{timestamp}] ========== COMPOSITE SCORE ==========")
        print(f"[{timestamp}] Composite Score: {composite['score']:.1f}/10")
        print(f"[{timestamp}] Category: {composite['category']}")
        print(f"[{timestamp}] Breakdown: ({iv_rv['score']:.1f} × 0.30) + ({trend['score']:.1f} × 0.20) + ({gpt['score']:.1f} × 0.50) = {composite['score']:.1f}")
        
        # Final Signal
        print(f"\n[{timestamp}] ========== FINAL SIGNAL ==========")
        print(f"[{timestamp}] Signal: {signal['signal']}")
        print(f"[{timestamp}] Should Trade: {signal['should_trade']}")
        print(f"[{timestamp}] Reason: {signal['reason']}")
        print(f"[{timestamp}] ======================================\n")
        
        # Send webhook
        webhook = send_webhook(signal)

        # Log to Google Sheet for history/backtesting (optional; no-op if not configured)
        log_signal_to_sheets(
            timestamp=timestamp,
            signal=signal,
            composite=composite,
            iv_rv=iv_rv,
            trend=trend,
            gpt=gpt,
            spx_current=spx_data["current"],
            vix1d_current=vix1d_data["current"],
            filter_stats=news_data.get("filter_stats", {}),
            webhook_success=webhook.get("success", False),
        )

        # Format news headlines
        news_headlines = []
        if news_data.get('articles'):
            for article in news_data['articles'][:25]:
                time_str = article['published_time'].strftime("%I:%M %p")
                hours_ago = article['hours_ago']
                
                if hours_ago < 1:
                    recency = "⚠️"
                elif hours_ago < 3:
                    recency = "🔸"
                else:
                    recency = "•"
                
                priority = "🔥" if article.get('priority') == 'HIGH' else ""
                
                news_headlines.append(f"{recency} [{time_str}] {priority}{article['title']}")
        
        # Get filter stats
        filter_stats = news_data.get('filter_stats', {
            'raw_articles': 0,
            'duplicates_removed': 0,
            'unique_articles': 0,
            'junk_filtered': 0,
            'sent_to_gpt': 0
        })
        
        # Response
        return jsonify({
            "status": "success",
            "timestamp": timestamp,
            "environment": "local" if IS_LOCAL else "production",
            
            "decision": signal['signal'],
            "composite_score": composite['score'],
            "category": composite['category'],
            "reason": signal['reason'],
            
            "market_data": {
                "spx_current": spx_data['current'],
                "spx_high": spx_data['high_today'],
                "spx_low": spx_data['low_today'],
                "vix1d_current": vix1d_data['current'],
                "data_source": "Polygon/Massive Indices Starter ($49/mo)",
                "timeframe": spx_data.get('timeframe', 'DELAYED')
            },
            
            "factor_1_iv_rv": {
                "weight": "30%",
                "score": iv_rv['score'],
                "iv_rv_ratio": iv_rv['iv_rv_ratio'],
                "realized_vol": f"{iv_rv['realized_vol']}%",
                "implied_vol": f"{iv_rv['implied_vol']}%",
                "vix1d_value": iv_rv['vix1d_value'],
                "tenor": "1-day (VIX1D)",
                "source": "Polygon VIX1D (real data)"
            },
            
            "factor_2_trend": {
                "weight": "20%",
                "score": trend['score'],
                "trend_change_5d": f"{trend['change_5d'] * 100:+.2f}%",
                "intraday_range": f"{trend['intraday_range'] * 100:.2f}%"
            },
            
            "factor_3_news_gpt": {
                "weight": "50%",
                
                "triple_layer_pipeline": {
                    "layer_1_algo_dedup": {
                        "raw_articles_fetched": filter_stats['raw_articles'],
                        "duplicates_removed": filter_stats['duplicates_removed'],
                        "unique_articles": filter_stats['unique_articles']
                    },
                    "layer_2_keyword_filter": {
                        "junk_filtered": filter_stats['junk_filtered'],
                        "sent_to_gpt": filter_stats['sent_to_gpt']
                    },
                    "layer_3_gpt": {
                        "duplicates_found_by_gpt": gpt.get('duplicates_found', 'None'),
                        "description": "GPT triple-duty: duplication safety + commentary filter + risk analysis"
                    }
                },
                
                "headlines_analyzed": news_headlines,
                
                "gpt_analysis": {
                    "score": gpt['score'],
                    "category": gpt['category'],
                    "key_risk": gpt.get('key_risk', 'None'),
                    "direction": gpt.get('direction_risk', 'UNKNOWN'),
                    "reasoning": gpt['reasoning']
                }
            },
            
            "webhook_success": webhook.get('success', False)
            
        }), 200
        
    except Exception as e:
        print(f"[{timestamp}] ERROR: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route("/test_polygon_delayed", methods=["GET"])
def test_polygon_delayed():
    """Test Polygon Indices Starter - SPX and VIX1D (15-min delayed)"""
    results = {
        'test_time': datetime.now(ET_TZ).strftime('%Y-%m-%d %I:%M:%S %p %Z'),
        'plan': 'Indices Starter ($49/mo) - 15-min delayed',
        'note': 'Using official v3 snapshot + v2 aggregates endpoints'
    }
    
    if not POLYGON_API_KEY:
        return jsonify({'error': 'No API key'}), 500
    
    # Test SPX snapshot
    spx_snapshot = get_spx_snapshot()
    results['spx_snapshot'] = {
        'status': '✅ SUCCESS' if spx_snapshot else '❌ FAILED',
        'data': spx_snapshot
    }
    
    # Test VIX1D snapshot
    vix1d_snapshot = get_vix1d_snapshot()
    results['vix1d_snapshot'] = {
        'status': '✅ SUCCESS' if vix1d_snapshot else '❌ FAILED',
        'data': vix1d_snapshot
    }
    
    # Test SPX aggregates
    spx_agg = get_spx_aggregates()
    results['spx_aggregates'] = {
        'status': '✅ SUCCESS' if spx_agg else '❌ FAILED',
        'days_returned': len(spx_agg) if spx_agg else 0,
        'sample_closes': spx_agg[:5] if spx_agg else []
    }
    
    # Summary
    if spx_snapshot and vix1d_snapshot and spx_agg:
        results['recommendation'] = '✅ POLYGON FULLY READY!'
        results['status'] = 'READY'
    else:
        results['recommendation'] = '⚠️ Some data failed'
        results['status'] = 'PARTIAL'
    
    return jsonify(results), 200

# ============================================================================
# BACKGROUND THREAD
# ============================================================================

def poke_self():
    """Background thread: Trigger analysis every 20 minutes during trading hours.
    Not started when IS_LOCAL so one manual click = one run when testing.
    """
    print("[POKE] Background thread started")
    # Use same host/port as the app so it works when PORT is overridden (e.g. Railway)
    base_url = os.environ.get("POKE_BASE_URL", "http://localhost:8080")
    timeout_sec = int(os.environ.get("POKE_TIMEOUT", "300"))  # 5 min; GPT can be slow

    while True:
        try:
            now = datetime.now(ET_TZ)
            current_time = now.time()

            if is_within_trading_window(now):
                if current_time.minute in [30, 50, 10] and current_time.second < 30:
                    print(f"\n[POKE] Triggering at {now.strftime('%I:%M %p ET')}")
                    try:
                        requests.get(f"{base_url}/option_alpha_trigger", timeout=timeout_sec)
                    except Exception as e:
                        print(f"[POKE] Error: {e}")

            time_module.sleep(30)

        except Exception as e:
            print(f"[POKE] Background error: {e}")
            time_module.sleep(60)

# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    PORT = int(os.environ.get("PORT", 8080))

    print("=" * 80)
    print("Ren's SPX Vol Signal - Production (Polygon/Massive)")
    print("=" * 80)
    print(f"Port: {PORT}")
    print(f"Trading Window: {TRADING_WINDOW_LABEL}")
    print(f"Environment: {ENVIRONMENT_LABEL}")
    print(f"Market Data: Polygon/Massive Indices Starter ($49/mo)")
    print(f"News Sources: Yahoo Finance RSS + Google News RSS (FREE)")
    print(f"SPX: Real I:SPX (15-min delayed)")
    print(f"VIX1D: Real I:VIX1D (15-min delayed, 1-day forward IV)")
    print("=" * 80)

    # Start POKE thread only in production so local = one click = one run
    if not IS_LOCAL:
        t = threading.Thread(target=poke_self, daemon=True)
        t.start()
        print("[POKE] Scheduler started (production)")
    else:
        print("[POKE] Scheduler disabled (local); trigger manually via /option_alpha_trigger")

    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
