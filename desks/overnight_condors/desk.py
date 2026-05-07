"""OvernightCondorsDesk — SPX overnight iron condor signal desk.

Extracts the trigger handler from the original monolithic app.py.
"""
from datetime import time as dt_time, datetime
from typing import Dict, List
import time as time_module

import pytz
from flask import jsonify

from core.desk import Desk
from core.config import get_config
from core.data.market_data import (
    get_spx_data_with_retry, get_vix1d_with_retry,
    get_vix_with_retry, get_vvix_with_retry,
    get_spx_snapshot, get_vix1d_snapshot, get_vix_snapshot,
    get_vvix_snapshot, get_spx_aggregates,
)
from core.data.news_fetcher import fetch_news_raw
from core.data.oa_event_calendar import check_oa_event_gates, format_gate_reasons
from core.processing.pipeline import process_news_pipeline
from core.webhooks import send_webhook
from core.alerting import record_signal_success, record_api_failure, _send_alert
from sheets_logger import log_signal as log_signal_to_sheets

from desks.overnight_condors.signal_engine import run_signal_analysis

ET_TZ = pytz.timezone('US/Eastern')

# Option Alpha VIX gate: OA will not open positions when VIX >= this value
OA_VIX_GATE = 25


class OvernightCondorsDesk(Desk):
    desk_id = "overnight_condors"
    display_name = "Bot A — Symmetric IC (control)"
    description = "Sell SPX iron condors (1:30-2:30 PM entry, 1 DTE) when IV is rich relative to RV and overnight news risk is manageable. Python signal control bot for the parallel paper trial."

    # Multi-bot trial: structure tag for log attribution. Subclasses override.
    structure_label = "IC_25pt_0.16d_symmetric"

    # Desk 1 group membership (the firm's overnight VRP capture group)
    desk_group = "desk1_overnight_vrp"
    desk_group_label = "Desk 1 — Overnight Vol Premium Capture"
    # paper, not live — the only live bot in this group is the OA-native Simple Condor
    status_label = "paper"

    # 1 contract across all tiers — Bot A's historical baseline.
    CONTRACTS_BY_TIER = {
        'TRADE_AGGRESSIVE':   1,
        'TRADE_NORMAL':       1,
        'TRADE_CONSERVATIVE': 1,
    }

    window_start = dt_time(13, 30)
    window_end = dt_time(14, 30)
    window_days = [0, 1, 2, 3, 4]
    poke_minutes = [30, 50, 10]

    config_prefix = ""
    sheet_tab = "Sheet1"

    def get_webhook_urls(self, config: Dict) -> Dict[str, str]:
        return {
            'TRADE_AGGRESSIVE': config.get('TRADE_AGGRESSIVE_URL'),
            'TRADE_NORMAL': config.get('TRADE_NORMAL_URL'),
            'TRADE_CONSERVATIVE': config.get('TRADE_CONSERVATIVE_URL'),
            'NO_TRADE': config.get('NO_TRADE_URL'),
        }

    def transform_signal_for_routing(self, signal: Dict, ctx: Dict) -> Dict:
        """Hook: rewrite signal['signal'] tier label before webhook routing.

        Default is identity. Subclasses (e.g. VVIX-conditional, DOW-conditional bots)
        override to map the standard tier into a structure-specific tier that routes
        to a different OA recipe. ctx contains vvix_data, vix_data, now, etc.
        """
        return signal

    def run_signal_cycle(self, config: Dict) -> Dict:
        """Full pipeline: fetch market data -> news -> analyze -> signal."""
        now = datetime.now(ET_TZ)
        timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")

        print(f"[{timestamp}] Fetching market data from Polygon...")

        # Fetch SPX data
        spx_data = get_spx_data_with_retry(max_retries=3)
        if not spx_data:
            record_api_failure('Polygon_SPX', desk_id=self.desk_id)
            return {'error': 'SPX data failed after 3 retries (Polygon)'}

        # Fetch VIX1D
        vix1d_data = get_vix1d_with_retry(max_retries=3)
        if not vix1d_data:
            record_api_failure('Polygon_VIX1D', desk_id=self.desk_id)
            return {'error': 'VIX1D data failed after 3 retries (Polygon)'}

        # Fetch VIX (30-day) — non-critical
        vix_data = get_vix_with_retry(max_retries=2)

        # Fetch VVIX — non-critical
        vvix_data = get_vvix_with_retry(max_retries=2)

        # Fetch and process news
        print(f"[{timestamp}] Fetching news from RSS sources...")
        raw_articles = fetch_news_raw()
        print(f"[{timestamp}] Processing news (deduplication + filtering)...")
        news_data = process_news_pipeline(raw_articles)

        print(f"[{timestamp}] Analyzing factors...")
        analysis_result = run_signal_analysis(spx_data, vix1d_data, news_data, vix_data, vvix_data)

        factors = analysis_result['indicators']
        composite = analysis_result['composite']
        signal = analysis_result['signal']
        contradictions = analysis_result.get('contradictions')

        iv_rv = factors['iv_rv']
        trend = factors['trend']
        gpt = factors['gpt']

        # Log factor details
        self._log_factors(timestamp, iv_rv, trend, gpt, spx_data, news_data, composite)

        # Confirmation pass
        print(f"\n[{timestamp}] ========== CONFIRMATION PASS ==========")
        print(f"[{timestamp}] Running second analysis for signal confirmation (temp=0.4)...")
        time_module.sleep(2)

        analysis_result_2 = run_signal_analysis(spx_data, vix1d_data, news_data, vix_data, vvix_data, gpt_temperature=0.4)
        composite_2 = analysis_result_2['composite']
        signal_2 = analysis_result_2['signal']
        contradictions_2 = analysis_result_2.get('contradictions')

        tier_order = ['TRADE_AGGRESSIVE', 'TRADE_NORMAL', 'TRADE_CONSERVATIVE', 'SKIP']
        idx_1 = tier_order.index(signal['signal']) if signal['signal'] in tier_order else 3
        idx_2 = tier_order.index(signal_2['signal']) if signal_2['signal'] in tier_order else 3

        confirmation_pass_data = {
            'pass1_composite': composite['score'],
            'pass1_signal': signal['signal'],
            'pass2_composite': composite_2['score'],
            'pass2_signal': signal_2['signal'],
            'passes_agreed': 'YES' if idx_1 == idx_2 else 'NO',
        }

        if idx_2 > idx_1:
            print(f"[{timestamp}] Pass 1: {signal['signal']} (score={composite['score']:.1f})")
            print(f"[{timestamp}] Pass 2: {signal_2['signal']} (score={composite_2['score']:.1f})")
            print(f"[{timestamp}] -> Using more conservative: {signal_2['signal']}")
            signal = signal_2
            composite = composite_2
            contradictions = contradictions_2
        elif idx_1 > idx_2:
            print(f"[{timestamp}] Pass 1: {signal['signal']} (score={composite['score']:.1f})")
            print(f"[{timestamp}] Pass 2: {signal_2['signal']} (score={composite_2['score']:.1f})")
            print(f"[{timestamp}] -> Using more conservative: {signal['signal']}")
        else:
            print(f"[{timestamp}] Both passes agree: {signal['signal']}")
        print(f"[{timestamp}] ========================================\n")

        # Once-per-day webhook
        today_str = now.strftime('%Y-%m-%d')
        webhook_skipped = False

        if self._daily_signal_cache['date'] != today_str:
            self._daily_signal_cache['date'] = today_str
            self._daily_signal_cache['webhook_sent'] = False
            self._daily_signal_cache['signal'] = None
            self._daily_signal_cache['score'] = None
            self._daily_signal_cache['poke_count'] = 0

        self._daily_signal_cache['poke_count'] += 1
        poke_number = self._daily_signal_cache['poke_count']

        print(f"\n[{timestamp}] ========== FINAL SIGNAL (Poke #{poke_number}) ==========")
        print(f"[{timestamp}] Signal: {signal['signal']}")
        print(f"[{timestamp}] Should Trade: {signal['should_trade']}")
        print(f"[{timestamp}] Reason: {signal['reason']}")

        vix_current = iv_rv.get('vix_30d')
        vix_blocked = vix_current is not None and vix_current >= OA_VIX_GATE
        oa_event_gates = check_oa_event_gates(now)

        if self._daily_signal_cache['webhook_sent']:
            webhook_skipped = True
            trade_executed = "NO_DUPLICATE"
            prior = self._daily_signal_cache['signal']
            print(f"[{timestamp}] Webhook already sent today ({prior}). Logging only, no duplicate webhook.")
            webhook = {'success': True, 'skipped': True}
        else:
            webhook_urls = self.get_webhook_urls(config)
            signal = self.transform_signal_for_routing(signal, ctx={
                'vvix_data': vvix_data,
                'vix_data': vix_data,
                'spx_data': spx_data,
                'now': now,
            })
            webhook = send_webhook(signal, webhook_urls)

            if webhook.get('success'):
                self._daily_signal_cache['webhook_sent'] = True
                self._daily_signal_cache['signal'] = signal['signal']
                self._daily_signal_cache['score'] = composite['score']
                print(f"[{timestamp}] Webhook fired: {signal['signal']} (attempts: {webhook.get('attempts', 1)})")
            else:
                error_msg = webhook.get('error', 'Unknown error')
                print(f"[{timestamp}] WEBHOOK FAILED after {webhook.get('attempts', 0)} attempts: {error_msg}")
                _send_alert(
                    "Webhook Failed",
                    f"Signal {signal['signal']} (score={composite['score']:.1f}) webhook failed "
                    f"after {webhook.get('attempts', 0)} retries. Error: {error_msg}. "
                    f"Next poke will retry.",
                    level='critical',
                    desk_id=self.desk_id,
                )

            if signal['signal'] == 'SKIP':
                trade_executed = "NO_SKIP"
            elif not webhook.get('success'):
                trade_executed = f"NO_WEBHOOK_FAIL ({webhook.get('error', 'unknown')[:40]})"
            elif vix_blocked:
                trade_executed = f"NO_VIX_GATE (VIX={vix_current:.1f})"
                print(f"[{timestamp}] VIX={vix_current:.1f} >= {OA_VIX_GATE} -- OA will block this trade")
            elif oa_event_gates:
                trade_executed = f"NO_OA_EVENT ({format_gate_reasons(oa_event_gates)})"
                print(f"[{timestamp}] OA event gate active: {format_gate_reasons(oa_event_gates)}")
            else:
                trade_executed = "YES"

        print(f"[{timestamp}] Trade Executed: {trade_executed}")
        print(f"[{timestamp}] ======================================\n")

        if contradictions and contradictions.get('contradiction_flags'):
            print(f"\n[{timestamp}] ========== CONTRADICTION DETECTION ==========")
            for flag in contradictions['contradiction_flags']:
                print(f"[{timestamp}]   - {flag}")
            if contradictions.get('override_signal'):
                print(f"[{timestamp}]   >>> OVERRIDE: {contradictions['override_signal']}")
            if contradictions.get('score_adjustment'):
                print(f"[{timestamp}]   >>> ADJUSTMENT: +{contradictions['score_adjustment']}")
            print(f"[{timestamp}] =============================================\n")

        # Log to Google Sheet (unified "live" tab, name-keyed write).
        # Conditional dimensions (vvix_bucket, dow_multiplier, hedge_attached,
        # vvix_percentile, vvix_bucket_source, skip_reason, original_tier) ride
        # on the `signal` dict — set by transform_signal_for_routing() in subclasses.
        routed_tier = signal.get('signal', '')
        contracts = self.CONTRACTS_BY_TIER.get(routed_tier)
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
            contradictions=contradictions,
            vix_current=vix_current,
            trade_executed=trade_executed,
            poke_number=poke_number,
            earnings=analysis_result.get('earnings'),
            confirmation_pass=confirmation_pass_data,
            desk_id=self.desk_id,
            structure_label=self.structure_label,
            contracts=contracts,
        )

        record_signal_success(desk_id=self.desk_id)

        return {
            'timestamp': timestamp,
            'signal': signal,
            'composite': composite,
            'factors': factors,
            'contradictions': contradictions,
            'webhook': webhook,
            'trade_executed': trade_executed,
            'poke_number': poke_number,
            'news_data': news_data,
            'spx_data': spx_data,
            'vix1d_data': vix1d_data,
            'filter_stats': news_data.get("filter_stats", {}),
            'confirmation_pass': confirmation_pass_data,
        }

    def _log_factors(self, timestamp, iv_rv, trend, gpt, spx_data, news_data, composite):
        """Detailed console logging for factor analysis."""
        print(f"\n[{timestamp}] ========== FACTOR ANALYSIS ==========")

        print(f"[{timestamp}] FACTOR 1: IV/RV Ratio (Weight: 30%)")
        print(f"[{timestamp}]   - VIX1D (Implied Vol): {iv_rv['implied_vol']:.2f}%")
        print(f"[{timestamp}]   - Realized Vol (10-day): {iv_rv['realized_vol']:.2f}%")
        print(f"[{timestamp}]   - IV/RV Ratio: {iv_rv['iv_rv_ratio']:.3f}")
        if 'rv_change' in iv_rv:
            print(f"[{timestamp}]   - RV Change: {iv_rv['rv_change']*100:+.2f}%")
        if 'term_structure_ratio' in iv_rv:
            print(f"[{timestamp}]   - VIX (30-day): {iv_rv.get('vix_30d', 'N/A')}")
            print(f"[{timestamp}]   - Term Structure: {iv_rv.get('term_structure', 'N/A')} (VIX1D/VIX = {iv_rv['term_structure_ratio']:.3f})")
            if iv_rv.get('term_modifier', 0) > 0:
                print(f"[{timestamp}]   - Term Structure Modifier: +{iv_rv['term_modifier']}")
        print(f"[{timestamp}]   - Factor Score: {iv_rv['score']:.1f}/10")
        print(f"[{timestamp}]   - Weighted Contribution: {iv_rv['score'] * 0.30:.2f}")

        print(f"[{timestamp}] FACTOR 2: Market Trend (Weight: 20%)")
        print(f"[{timestamp}]   - SPX Current: {spx_data['current']:.2f}")
        print(f"[{timestamp}]   - SPX High Today: {spx_data['high_today']:.2f}")
        print(f"[{timestamp}]   - SPX Low Today: {spx_data['low_today']:.2f}")
        print(f"[{timestamp}]   - 5-Day Change: {trend['change_5d']*100:+.2f}%")
        print(f"[{timestamp}]   - Intraday Range: {trend['intraday_range']*100:.2f}%")
        print(f"[{timestamp}]   - Factor Score: {trend['score']:.1f}/10")
        print(f"[{timestamp}]   - Weighted Contribution: {trend['score'] * 0.20:.2f}")

        print(f"[{timestamp}] FACTOR 3: GPT News Analysis (Weight: 50%)")
        filter_stats = news_data.get('filter_stats', {})
        print(f"[{timestamp}]   - News Pipeline Stats:")
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

        print(f"\n[{timestamp}] ========== COMPOSITE SCORE ==========")
        print(f"[{timestamp}] Composite Score: {composite['score']:.1f}/10")
        print(f"[{timestamp}] Category: {composite['category']}")
        print(f"[{timestamp}] Breakdown: ({iv_rv['score']:.1f} x 0.30) + ({trend['score']:.1f} x 0.20) + ({gpt['score']:.1f} x 0.50) = {composite['score']:.1f}")

    def register_routes(self, app) -> None:
        """Register Flask routes for this desk."""
        config = get_config()
        is_local = bool(config.get("_FROM_FILE"))

        # Canonical /{desk_id}/trigger route — convention enforced firm-wide
        # (see memory/feedback_url_conventions.md).
        @app.route("/overnight_condors/trigger", methods=["GET", "POST"])
        def overnight_condors_trigger():
            """Main trading decision endpoint for overnight condors."""
            now = datetime.now(ET_TZ)
            timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
            print(f"\n[{timestamp}] /overnight_condors/trigger called")

            # Check trading window
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
                composite = result['composite']
                factors = result['factors']
                iv_rv = factors['iv_rv']
                trend = factors['trend']
                gpt = factors['gpt']
                news_data = result['news_data']
                webhook = result['webhook']
                filter_stats = result['filter_stats']

                # Format news headlines
                news_headlines = []
                if news_data.get('articles'):
                    for article in news_data['articles'][:25]:
                        time_str = article['published_time'].strftime("%I:%M %p")
                        hours_ago = article['hours_ago']
                        recency = "!" if hours_ago < 1 else ("~" if hours_ago < 3 else "-")
                        priority = "*" if article.get('priority') == 'HIGH' else ""
                        news_headlines.append(f"{recency} [{time_str}] {priority}{article['title']}")

                return jsonify({
                    "status": "success",
                    "timestamp": result['timestamp'],
                    "desk": self.desk_id,
                    "environment": "local" if is_local else "production",
                    "decision": signal['signal'],
                    "composite_score": composite['score'],
                    "category": composite['category'],
                    "reason": signal['reason'],
                    "market_data": {
                        "spx_current": result['spx_data']['current'],
                        "spx_high": result['spx_data']['high_today'],
                        "spx_low": result['spx_data']['low_today'],
                        "vix1d_current": result['vix1d_data']['current'],
                        "data_source": "Polygon/Massive Indices Starter ($49/mo)",
                        "timeframe": result['spx_data'].get('timeframe', 'DELAYED'),
                    },
                    "factor_1_iv_rv": {
                        "weight": "30%",
                        "score": iv_rv['score'],
                        "iv_rv_ratio": iv_rv['iv_rv_ratio'],
                        "realized_vol": f"{iv_rv['realized_vol']}%",
                        "implied_vol": f"{iv_rv['implied_vol']}%",
                        "vix1d_value": iv_rv['vix1d_value'],
                        "vix_30d": iv_rv.get('vix_30d'),
                        "term_structure": iv_rv.get('term_structure'),
                        "term_structure_ratio": iv_rv.get('term_structure_ratio'),
                    },
                    "factor_2_trend": {
                        "weight": "20%",
                        "score": trend['score'],
                        "trend_change_5d": f"{trend['change_5d'] * 100:+.2f}%",
                        "intraday_range": f"{trend['intraday_range'] * 100:.2f}%",
                    },
                    "factor_3_news_gpt": {
                        "weight": "50%",
                        "triple_layer_pipeline": {
                            "layer_1_algo_dedup": {
                                "raw_articles_fetched": filter_stats['raw_articles'],
                                "duplicates_removed": filter_stats['duplicates_removed'],
                                "unique_articles": filter_stats['unique_articles'],
                            },
                            "layer_2_keyword_filter": {
                                "junk_filtered": filter_stats['junk_filtered'],
                                "sent_to_gpt": filter_stats['sent_to_gpt'],
                            },
                        },
                        "headlines_analyzed": news_headlines,
                        "gpt_analysis": {
                            "score": gpt['score'],
                            "category": gpt['category'],
                            "key_risk": gpt.get('key_risk', 'None'),
                            "direction": gpt.get('direction_risk', 'UNKNOWN'),
                            "reasoning": gpt['reasoning'],
                        },
                    },
                    "webhook_success": webhook.get('success', False),
                }), 200

            except Exception as e:
                print(f"[{timestamp}] ERROR: {e}")
                import traceback
                traceback.print_exc()
                return jsonify({"status": "error", "message": str(e)}), 500

    def get_dashboard_html(self) -> str:
        """Return HTML for this desk's tab content."""
        config = get_config()
        openai_model = (config.get("OPENAI_MODEL") or "").strip() or "gpt-4o-mini"

        return f"""
        <div class="strategy-box">
            <div class="strategy-title">SPX Overnight Iron Condors</div>
            <div class="edge-item">
                <div class="edge-label">Thesis:</div>
                <div class="edge-desc">
                    Selling the gap between the overnight move the market prices in and the overnight move
                    that actually occurs. Implied vol systematically overprices realized overnight movement
                    due to structural demand for portfolio protection.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Structure:</div>
                <div class="edge-desc">
                    SPX iron condors, 1 DTE. Entry 1:30-2:30 PM ET, exit 10:00 AM ET next day (~19.5-20.5 hour hold).
                    Width and delta determined by signal tier:
                    AGGRESSIVE (&lt;3.5) 20pt/0.18d,
                    NORMAL (3.5-5.0) 25pt/0.16d,
                    CONSERVATIVE (5.0-7.5) 30pt/0.14d,
                    SKIP (&gt;=7.5) no trade.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Risk:</div>
                <div class="edge-desc">
                    Overnight tail events — Mag 7 earnings, Fed surprises, geopolitical shocks.
                    Weekend holds (Friday entry → Monday exit) carry additional exposure.
                    Mitigated by 3-factor signal (IV/RV 30%, Trend 20%, GPT news 50%),
                    contradiction detection, confirmation pass, Mag 7 earnings calendar,
                    and FOMC/CPI/NFP event gates.
                </div>
            </div>
            <div class="edge-item">
                <div class="edge-label">Execution:</div>
                <div class="edge-desc">
                    Signal-driven from this app via webhook to Option Alpha.
                    OA handles strike selection, entry, and exit management
                    (profit target, stop loss, time exit, touch monitor).
                    One webhook per day; subsequent pokes log only.
                </div>
            </div>
        </div>
        <div class="section">
            <div class="section-title">Data Sources</div>
            <div class="info-item"><span class="info-label">Market Data:</span> <span class="info-value">Polygon/Massive Indices Starter ($49/mo)</span></div>
            <div class="info-item"><span class="info-label">SPX:</span> <span class="info-value">Real I:SPX snapshot + aggregates (15-min delayed)</span></div>
            <div class="info-item"><span class="info-label">VIX1D:</span> <span class="info-value">Real I:VIX1D snapshot (15-min delayed)</span></div>
            <div class="info-item"><span class="info-label">VIX (30-day):</span> <span class="info-value">I:VIX for term structure analysis</span></div>
            <div class="info-item"><span class="info-label">News:</span> <span class="info-value">Yahoo Finance RSS + Google News RSS (FREE)</span></div>
            <div class="info-item"><span class="info-label">AI:</span> <span class="info-value">OpenAI ({openai_model}), temperature=0.1</span></div>
            <div class="info-item"><span class="info-label">Earnings:</span> <span class="info-value">Polygon ticker events API (Mag 7)</span></div>
        </div>
        <div class="section">
            <div class="section-title">Endpoints</div>
            <div class="endpoint"><a href="/overnight_condors/trigger">/overnight_condors/trigger</a> - Generate signal</div>
        </div>
        """
