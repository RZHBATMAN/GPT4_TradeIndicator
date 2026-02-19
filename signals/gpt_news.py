"""Indicator 3: GPT News Analysis (50% weight)"""
import json
import requests
from datetime import datetime
import pytz
from config.loader import get_config

ET_TZ = pytz.timezone('US/Eastern')


def analyze_gpt_news(news_data):
    """LAYER 3: GPT analysis with significance-based time decay model"""
    
    if news_data['count'] == 0:
        print("\n[LAYER 3] GPT ANALYSIS: Skipped (no news) — defaulting to ELEVATED")
        return {
            'score': 7,
            'raw_score': 7,
            'category': 'ELEVATED',
            'reasoning': 'No actionable news available - defaulting to elevated risk (no data = caution)',
            'direction_risk': 'UNKNOWN',
            'key_risk': 'None',
            'duplicates_found': 'None',
            'token_usage': {'input': 0, 'output': 0, 'total': 0, 'cost': 0.0}
        }
    
    config = get_config()
    minimax_api_key = config.get('MINIMAX_API_KEY')
    minimax_model = (config.get('MINIMAX_MODEL') or '').strip() or 'MiniMax-M2.1'
    
    now = datetime.now(ET_TZ)
    current_time_str = now.strftime("%I:%M %p ET")
    
    prompt = f"""You are an expert overnight volatility risk analyst for SPX iron condor positions.

CURRENT TIME: {current_time_str}

CONTEXT:
- Selling SPX iron condor NOW (2:30-3:30 PM entry)
- Holding OVERNIGHT (~16 hours until 9:30 AM tomorrow)
- Iron condor LOSES MONEY from BIG MOVES in EITHER DIRECTION

⚠️ TRIPLE-LAYER FILTERING SYSTEM:

LAYER 1 (COMPLETED): Algorithmic deduplication
- Removed duplicates using fuzzy matching (85% similarity threshold)
- Kept best version (most recent + best source)

LAYER 2 (COMPLETED): Keyword filter
- Removed obvious clickbait: "secret to", "trick to", "shocking"
- Removed obvious opinion: "why you should", "how to invest"
- Removed old retrospectives: "last week recap"

LAYER 3 (YOUR JOB - FOUR RESPONSIBILITIES):

1️⃣ DUPLICATION SAFETY NET:
If you notice articles covering the SAME EVENT (algo may have missed some):
- Count as ONE event, not multiple
- Examples of duplicates:
  * "Apple earnings beat expectations" (Reuters)
  * "Apple beats Q4 earnings forecast" (Bloomberg)
  * "Apple Q4 results exceed expectations" (Yahoo)
  → These are ONE event (Apple earnings), not three!
  
- How to spot: Same company + same event + similar timeframe = Duplicate
- Don't let duplicates inflate your risk score
- Report in "duplicates_found" field

2️⃣ COMMENTARY/NEWS FILTER:
Filter out sophisticated commentary that keyword filter may have missed:

❌ FILTER OUT:
- Sophisticated commentary disguised as news:
  * "Warren Buffett dumps Apple - what it means" (analysis of known action)
  * "Why Nvidia's earnings matter for your portfolio" (opinion/advice)
  * "Here's how to play Tesla after earnings" (trading advice)
  
- Analysis of OLD events with fresh headlines:
  * "Markets digest yesterday's Fed decision" (old event)
  * "Investors react to last week's CPI print" (old data)
  * "Breaking down Apple's guidance from last quarter" (old news)
  
- Speculation dressed as news:
  * "Apple could announce new product if..." (speculation)
  * "What Tesla might do next quarter" (prediction)
  * "AMD may benefit from Nvidia's stumble" (hypothetical)

✅ ANALYZE:
- Earnings reports released TODAY
- Company announcements made in last few hours
- Analyst upgrades/downgrades issued TODAY
- Actual price moves happening NOW (stock sinks/soars X%)
- Breaking regulatory decisions
- Major product launches TODAY

3️⃣ SIGNIFICANCE CLASSIFICATION (CRITICAL - NEW FRAMEWORK):

Classify each UNIQUE event by its potential SPX impact:

Significance 5 (EXTREME - Can move SPX 1%+ overnight):
  • Mag 7 earnings beats/misses/guidance changes (Apple, Microsoft, Google, Amazon, Nvidia, Tesla, Meta)
  • Fed policy surprises or major central bank actions
  • Geopolitical shocks (war escalation, major crisis)
  • Major economic surprises (CPI miss/beat >0.3%, NFP surprise >100K)

Significance 4 (HIGH - Can move SPX 0.5-1%):
  • Multiple Mag 7 stocks moving together (sector rotation)
  • Large-cap ($500B+) earnings surprises outside Mag 7
  • Major sector-wide news (banking stress, tech regulation)
  • Significant geopolitical developments

Significance 3 (MODERATE - Can move SPX 0.2-0.5%):
  • Non-Mag 7 large-cap earnings (SPX components)
  • Sector-specific regulatory changes
  • Commodity shocks (oil spike/crash >5%)

Significance 2 (LOW - Minimal SPX impact <0.2%):
  • Mid-cap earnings
  • Analyst ratings changes (non-Mag 7)
  • Minor economic data
  • Individual stock analyst calls

Significance 1 (NEGLIGIBLE - Ignore for SPX overnight risk):
  • Small-cap news
  • Individual stock moves (non-SPX components)
  • Opinion/commentary pieces
  • Crypto, forex (unless extreme crisis)

4️⃣ TIME-DECAY ASSESSMENT (CRITICAL - NEW FRAMEWORK):

RISK = f(SIGNIFICANCE, TIME_ELAPSED)

The key insight: High-significance events take LONGER to fully price in.

For Significance 5 (EXTREME):
  • 0-2 hours: <30% priced in → EXTREME OVERNIGHT RISK
  • 2-4 hours: 30-50% priced in → EXTREME OVERNIGHT RISK
  • 4-8 hours: 50-80% priced in → HIGH OVERNIGHT RISK
  • 8-12 hours: 80-95% priced in → MODERATE OVERNIGHT RISK
  • 12+ hours: >95% priced in → LOW RISK

For Significance 4 (HIGH):
  • 0-1 hour: <40% priced in → HIGH OVERNIGHT RISK
  • 1-3 hours: 40-70% priced in → HIGH OVERNIGHT RISK
  • 3-6 hours: 70-90% priced in → MODERATE OVERNIGHT RISK
  • 6+ hours: >90% priced in → LOW RISK

For Significance 3 (MODERATE):
  • 0-1 hour: <50% priced in → MODERATE OVERNIGHT RISK
  • 1-3 hours: 50-85% priced in → MODERATE OVERNIGHT RISK
  • 3+ hours: >85% priced in → LOW RISK

For Significance 2-1 (LOW/NEGLIGIBLE):
  • These don't create overnight risk regardless of timing
  • Market digests instantly or doesn't care

EXAMPLES OF SIGNIFICANCE-TIME INTERACTION:

Example A: Nvidia beats earnings, raises guidance (reported 2 hours ago, after-hours)
  → Significance: 5 (EXTREME - Mag 7 earnings)
  → Time elapsed: 2 hours
  → Price-in status: ~35% priced in (using Sig 5 decay curve)
  → Overnight risk: EXTREME (65% NOT YET PRICED, futures still reacting)

Example B: Random mid-cap beats earnings (reported 30 minutes ago)
  → Significance: 2 (LOW - doesn't move SPX)
  → Time elapsed: 30 minutes
  → Price-in status: Irrelevant (Sig 2 = no SPX impact)
  → Overnight risk: NEGLIGIBLE

Example C: Fed announces surprise rate hold (6 hours ago at 2 PM)
  → Significance: 5 (EXTREME - Fed policy)
  → Time elapsed: 6 hours
  → Price-in status: ~75% priced in (market had 6 hours to react)
  → Overnight risk: MODERATE-HIGH (25% still digesting)

Example D: Tesla stock down 3% on analyst downgrade (1 hour ago)
  → Significance: 4 (HIGH - Mag 7 member, but single analyst call)
  → Time elapsed: 1 hour
  → Price-in status: ~40% priced in
  → Overnight risk: MODERATE-HIGH (60% NOT YET PRICED)

Example E: Multiple Mag 7 stocks down 2-3% intraday (ongoing trend)
  → Significance: 4 (HIGH - sector rotation)
  → Time elapsed: Continuous throughout day
  → Price-in status: Mostly priced, but momentum could continue
  → Overnight risk: MODERATE

Remember: Mag 7 = 30% of SPX weight. Their news has DIRECT SPX impact.

NEWS (may contain duplicates/commentary - YOU filter and classify):
{news_data['summary']}

YOUR ANALYSIS PROCESS:

1. Filter duplicates/commentary → Identify UNIQUE, ACTUAL events
2. For each unique event:
   a. Classify SIGNIFICANCE (1-5)
   b. Determine TIME_ELAPSED since event
   c. Calculate "% PRICED IN" using decay tables
   d. Assess OVERNIGHT RISK based on what's NOT YET PRICED
3. Combine all unique events → Overall overnight risk score

SCORING - Based on UNIQUE events with significance-time weighting:

1-2: VERY_QUIET - No real unique catalysts OR only Sig 1-2 events (no SPX impact)
3-4: QUIET - Minor unique events (Sig 3) mostly priced, or Sig 4-5 events fully priced (8+ hours old)
5-6: MODERATE - Moderate unique events (Sig 3-4) partially priced, or old Sig 5 events
7-8: ELEVATED - Major catalyst (Sig 4-5) NOT fully priced (<70% priced in)
9-10: EXTREME - Multiple major catalysts OR one massive Sig 5 event <50% priced in

In your reasoning, EXPLICITLY mention:
- Any duplicates you found (e.g., "Reuters + Bloomberg both covering Apple earnings = ONE event")
- What you filtered as commentary/old news
- What UNIQUE, ACTUAL events you found
- SIGNIFICANCE classification for each event (1-5)
- TIME_ELAPSED for each event
- Estimated "% PRICED IN" for each event
- Why those events create overnight risk (or don't)

Respond in JSON only (no markdown):
{{
  "overnight_magnitude_risk_score": 1-10,
  "risk_category": "VERY_QUIET/QUIET/MODERATE/ELEVATED/EXTREME",
  "reasoning": "MUST mention: (1) Duplicates found, (2) Commentary filtered, (3) Unique events with SIGNIFICANCE + TIME + % PRICED IN analysis",
  "key_overnight_risk": "Single most important unique catalyst with significance level, or 'None - mostly commentary/duplicates'",
  "direction_risk": "UP/DOWN/BOTH/NONE",
  "duplicates_found": "List any duplicate articles (same event from multiple sources), or 'None'"
}}
"""
    
    print(f"\n[LAYER 3] GPT ANALYSIS: Calling MiniMax ({minimax_model}) with significance-time decay model...")
    
    try:
        headers = {
            "Authorization": f"Bearer {minimax_api_key}",
            "Content-Type": "application/json"
        }
        
        data = {
            "model": minimax_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 1000,
            "temperature": 0.1
        }
        
        response = requests.post(
            "https://api.minimax.io/v1/chat/completions",
            headers=headers,
            json=data,
            timeout=60
        )
        
        if response.status_code != 200:
            print(f"  ❌ MiniMax API error: {response.status_code} — defaulting to ELEVATED")
            return {
                'score': 7,
                'raw_score': 7,
                'category': 'ELEVATED',
                'reasoning': f'API error: {response.status_code} — defaulting to elevated risk (no analysis = caution)',
                'direction_risk': 'UNKNOWN',
                'key_risk': 'API Error — no analysis performed',
                'duplicates_found': 'Error',
                'token_usage': {'input': 0, 'output': 0, 'total': 0, 'cost': 0.0}
            }
        
        result = response.json()
        
        # Extract token usage
        usage = result.get('usage', {})
        input_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('completion_tokens', 0)
        total_tokens = usage.get('total_tokens', 0)
        
        # MiniMax pricing varies by model; log usage only
        total_cost = 0.0
        
        print(f"  📊 TOKEN USAGE:")
        print(f"     Input tokens:  {input_tokens:,}")
        print(f"     Output tokens: {output_tokens:,}")
        print(f"     Total tokens:  {total_tokens:,}")
        
        # Extract response text — MiniMax sometimes returns None or empty content
        raw_content = result['choices'][0]['message'].get('content')
        finish_reason = result['choices'][0].get('finish_reason', 'unknown')

        if not raw_content or not raw_content.strip():
            print(f"  ⚠️  MiniMax returned empty content (finish_reason={finish_reason})")
            print(f"  ⚠️  Full response keys: {list(result.keys())}")

            # Retry once — MiniMax empty responses are usually transient
            print(f"  🔄 Retrying MiniMax call...")
            import time as _time
            _time.sleep(2)
            retry_resp = requests.post(
                "https://api.minimax.io/v1/chat/completions",
                headers=headers,
                json=data,
                timeout=60
            )
            if retry_resp.status_code == 200:
                retry_result = retry_resp.json()
                retry_usage = retry_result.get('usage', {})
                print(f"  📊 RETRY TOKEN USAGE: {retry_usage.get('total_tokens', 0):,} tokens")
                raw_content = retry_result['choices'][0]['message'].get('content')
                finish_reason = retry_result['choices'][0].get('finish_reason', 'unknown')

            if not raw_content or not raw_content.strip():
                print(f"  ❌ MiniMax returned empty content again (finish_reason={finish_reason}) — defaulting to ELEVATED")
                return {
                    'score': 7,
                    'raw_score': 7,
                    'category': 'ELEVATED',
                    'reasoning': f'MiniMax returned empty response (finish_reason={finish_reason}) — defaulting to elevated risk',
                    'direction_risk': 'UNKNOWN',
                    'key_risk': 'Error — empty MiniMax response',
                    'duplicates_found': 'Error',
                    'token_usage': {'input': input_tokens, 'output': output_tokens, 'total': total_tokens, 'cost': 0.0}
                }

        response_text = raw_content.strip()

        if response_text.startswith('```'):
            response_text = response_text.split('```')[1]
            if response_text.startswith('json'):
                response_text = response_text[4:]

        gpt_analysis = json.loads(response_text)
        raw_score = gpt_analysis.get('overnight_magnitude_risk_score', 5)
        raw_score = max(1, min(10, raw_score))
        
        # Calibration (less aggressive now since GPT has better framework)
        if raw_score >= 9:
            calibrated = raw_score
        elif raw_score >= 7:
            calibrated = raw_score - 0.5
        elif raw_score <= 3:
            calibrated = raw_score + 0.5
        else:
            calibrated = raw_score
        
        calibrated = max(1, min(10, round(calibrated)))
        
        print(f"  ✅ MiniMax Risk Score: {raw_score} (calibrated: {calibrated})")
        print(f"  ✅ Category: {gpt_analysis.get('risk_category', 'MODERATE')}")
        
        return {
            'score': calibrated,
            'raw_score': raw_score,
            'category': gpt_analysis.get('risk_category', 'MODERATE'),
            'reasoning': gpt_analysis.get('reasoning', ''),
            'key_risk': gpt_analysis.get('key_overnight_risk', 'None'),
            'direction_risk': gpt_analysis.get('direction_risk', 'UNKNOWN'),
            'duplicates_found': gpt_analysis.get('duplicates_found', 'None'),
            'token_usage': {
                'input': input_tokens,
                'output': output_tokens,
                'total': total_tokens,
                'cost': total_cost
            }
        }
        
    except Exception as e:
        print(f"  ❌ MiniMax error: {e} — defaulting to ELEVATED")
        import traceback
        traceback.print_exc()
        return {
            'score': 7,
            'raw_score': 7,
            'category': 'ELEVATED',
            'reasoning': f'MiniMax error: {str(e)} — defaulting to elevated risk (no analysis = caution)',
            'direction_risk': 'UNKNOWN',
            'key_risk': 'Error — no analysis performed',
            'duplicates_found': 'Error',
            'token_usage': {'input': 0, 'output': 0, 'total': 0, 'cost': 0.0}
        }
