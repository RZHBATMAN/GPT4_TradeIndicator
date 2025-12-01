from flask import Flask, jsonify
import requests
import os
import json
import threading
import time
from datetime import datetime, time as dt_time, timedelta
try:
    from zoneinfo import ZoneInfo  # type: ignore
except ImportError:
    from pytz import timezone as ZoneInfo  # type: ignore

app = Flask(__name__)

# Environment variables
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
trade_url = os.environ.get("TRADE_URL")
no_trade_url = os.environ.get("NO_TRADE_URL")

# Check required variables
if not all([OPENAI_API_KEY, NEWS_API_KEY, trade_url, no_trade_url]):
    raise ValueError("Some environment variables are missing.")

# Optional: VIX threshold (you can add VIX_API_KEY later)
VIX_THRESHOLD = float(os.environ.get("VIX_THRESHOLD", "25.0"))

# API endpoints
API_URL = "https://api.openai.com/v1/chat/completions"
NEWS_API_URL = "https://newsapi.org/v2/top-headlines"
TRADING_TIMEZONE = ZoneInfo("America/New_York")

# Trading window - matches your Option Alpha scanner (1:30-4:00 PM)
TRADING_WINDOW_START = dt_time(hour=14, minute=30)  # 2:30 PM ET (better news coverage)
TRADING_WINDOW_END = dt_time(hour=15, minute=30)     # 3:30 PM ET (hard stop)

# Poke settings - check every 20 mins during entry window
POKE_INTERVAL = 20 * 60  # 20 minutes (allows 4 checks: 2:30, 2:50, 3:10, 3:30)
POKE_WINDOW_START = dt_time(hour=14, minute=30)  # 2:30 PM ET (first check)
POKE_WINDOW_END = dt_time(hour=15, minute=30)    # 3:30 PM ET (last check at 3:30)


def fetch_breaking_news():
    """
    Fetches market-relevant news with time-weighted relevance
    
    Strategy:
    - Recent news (last 3 hours): Most relevant for overnight risk
    - US business news: Direct SPX impact
    - Global market news: Overnight spillover
    - Geopolitical news: 24/7 shock risk
    """
    try:
        # Calculate time window (last 3 hours - optimal for afternoon trading)
        # Captures: Recent developments without too much already-priced news
        now = datetime.now(TRADING_TIMEZONE)
        three_hours_ago = now - timedelta(hours=3)
        from_time = three_hours_ago.strftime("%Y-%m-%dT%H:%M:%S")
        
        all_articles = []
        
        # Query 1: US Business/Financial (most relevant)
        params_business = {
            'apiKey': NEWS_API_KEY,
            'language': 'en',
            'category': 'business',
            'country': 'us',
            'sortBy': 'publishedAt',
            'from': from_time,  # Last 3 hours
            'pageSize': 10
        }
        
        # Query 2: Market-specific keywords (catches breaking developments)
        params_market = {
            'apiKey': NEWS_API_KEY,
            'language': 'en',
            'sortBy': 'publishedAt',
            'from': from_time,  # Last 3 hours
            'q': 'stock OR market OR fed OR economy OR bank OR "wall street" OR crisis OR volatility',
            'pageSize': 10
        }
        
        # Query 3: Geopolitical/Global (overnight spillover risk)
        params_global = {
            'apiKey': NEWS_API_KEY,
            'language': 'en',
            'sortBy': 'publishedAt',
            'from': from_time,  # Last 3 hours
            'q': 'geopolitical OR war OR conflict OR central bank OR "global markets"',
            'pageSize': 5
        }
        
        # Fetch all three categories
        for params in [params_business, params_market, params_global]:
            try:
                response = requests.get(NEWS_API_URL, params=params, timeout=10)
                if response.status_code == 200:
                    articles = response.json().get("articles", [])
                    all_articles.extend(articles)
            except Exception as e:
                print(f"Error fetching news category: {e}")
                continue
        
        # Deduplicate by title
        seen_titles = set()
        unique_articles = []
        for article in all_articles:
            title = article.get('title', '')
            if title and title not in seen_titles and len(title) > 10:
                seen_titles.add(title)
                unique_articles.append(article)
        
        # Sort by published time (most recent first)
        unique_articles.sort(
            key=lambda x: x.get('publishedAt', ''), 
            reverse=True
        )
        
        # Take top 15 most recent
        unique_articles = unique_articles[:15]
        
        if not unique_articles:
            print("Warning: No recent market news found, using fallback")
            # Fallback: Get top headlines without time filter
            params_fallback = {
                'apiKey': NEWS_API_KEY,
                'language': 'en',
                'category': 'business',
                'country': 'us',
                'pageSize': 5
            }
            response = requests.get(NEWS_API_URL, params=params_fallback, timeout=10)
            if response.status_code == 200:
                unique_articles = response.json().get("articles", [])[:5]
        
        if not unique_articles:
            return [], "No market news available."
        
        # Format for GPT
        news_headlines = [article['title'] for article in unique_articles]
        
        # Create detailed summary with timestamps
        news_summary = ""
        for article in unique_articles:
            title = article.get('title', 'N/A')
            description = article.get('description', 'N/A')
            published = article.get('publishedAt', 'N/A')
            source = article.get('source', {}).get('name', 'Unknown')
            
            # Parse timestamp to show how recent
            try:
                pub_time = datetime.fromisoformat(published.replace('Z', '+00:00'))
                pub_time_et = pub_time.astimezone(TRADING_TIMEZONE)
                time_str = pub_time_et.strftime("%I:%M %p")
            except:
                time_str = "Earlier"
            
            news_summary += f"[{time_str}] {source}: {title}\n   {description}\n\n"
        
        print(f"Fetched {len(unique_articles)} relevant news items from last 3 hours")
        return news_headlines, news_summary.strip()
        
    except Exception as e:
        print(f"Error in fetch_breaking_news: {e}")
        return [], "Unable to fetch news data."


def parse_gpt_response(response_text):
    """Clean and parse GPT response as JSON"""
    try:
        cleaned_response = response_text.replace("```json", "").replace("```", "").strip()
        print("Cleaned GPT response:", cleaned_response)
        parsed_response = json.loads(cleaned_response)
        return parsed_response
    except json.JSONDecodeError as e:
        print(f"Failed to parse GPT response as JSON. Response was: {response_text}. Error: {e}")
        return {
            "overnight_risk_score": 5,
            "recommendation": "REDUCE_SIZE",
            "reasoning": "Unable to parse GPT response due to formatting issues."
        }


def ask_gpt(prompt):
    """Send prompt to GPT-4 and get response"""
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "gpt-4-turbo",
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 300,
        "temperature": 0.3,  # Lower temperature for more consistent analysis
    }
    response = requests.post(API_URL, headers=headers, json=data)
    
    if response.status_code != 200:
        raise Exception(f"OpenAI API request failed: {response.text}")
    
    result_text = response.json()["choices"][0]["message"]["content"].strip()
    return parse_gpt_response(result_text)


def analyze_overnight_risk(news_summary):
    """
    GPT as PREDICTION MODEL: Forward-looking overnight escalation risk
    Focus: Will recent developments lead to overnight gaps + IV spikes?
    """
    current_time = datetime.now(TRADING_TIMEZONE).strftime("%A, %B %d, %Y %I:%M %p %Z")
    
    prompt = f"""You are a PREDICTIVE risk model for overnight SPX options exposure.

CURRENT TIME: {current_time}

MY POSITION:
- Selling SPX iron condor NOW (late afternoon entry)
- Holding position OVERNIGHT (~16 hours)
- Exiting tomorrow morning at target

MY DUAL RISKS (occur together):
1. Price Gap Risk: SPX gaps ±1-2% overnight → strikes breached
2. IV Spike Risk: VIX/IV jumps overnight → expensive to exit
→ Same shock events trigger BOTH simultaneously

ALREADY FILTERED (by my system):
✓ Scheduled FOMC, CPI, NFP, GDP releases
✓ Known earnings (mega-caps after hours)
✓ Pre-announced Fed speeches

YOUR PREDICTION TASK:
Based on recent news, predict: "Will there be an UNEXPECTED shock overnight that causes BOTH price gaps AND IV spikes?"

RECENT NEWS (last 3 hours, time-stamped):
{news_summary}

PREDICTION FRAMEWORK - Ask these questions:

1. ESCALATION POTENTIAL:
   - Are any situations ACTIVELY DEVELOPING right now?
   - Could they get WORSE between now and tomorrow morning?
   - Is this the START of something bigger or RESOLUTION of something?

2. TIMING RISK:
   - Did critical news just break in last 1-2 hours?
   - Is there INSUFFICIENT market reaction time before close?
   - Could after-hours/overnight bring NEW developments?

3. CONTAGION RISK:
   - Is this ISOLATED or could it SPREAD?
   - Are there SYSTEMIC implications?
   - Could one problem trigger others overnight?

4. SURPRISE FACTOR:
   - Was this EXPECTED or UNEXPECTED?
   - Is the market COMPLACENT about this risk?
   - Could morning bring SHOCKING updates?

THREAT CATEGORIES (Unscheduled shocks only):

🚨 GEOPOLITICAL ESCALATION:
- Wars/conflicts suddenly intensifying
- Major attacks or military action
- Regime changes, coups
- Trade war surprise escalations

🏦 FINANCIAL CONTAGION:
- Bank failures or liquidity crises
- Credit market freezes
- Major bankruptcy filings (systemic)
- Margin calls / forced liquidations

⚡ POLICY SHOCKS:
- Emergency Fed actions (unscheduled)
- Surprise regulatory moves
- Major policy U-turns
- Government shutdowns / debt ceiling

📰 BREAKING CRISES:
- Major corporate frauds revealed
- Cyber attacks on financial systems
- Natural disasters (major economic impact)
- Pandemic developments

PREDICTION SCALE (1-10):

1-3: VERY LOW OVERNIGHT RISK → TRADE
→ Normal stable news flow
→ No developing crises
→ Market has digested all information
→ Expected: Quiet overnight, normal theta/IV decay
→ Prediction: 95%+ chance of calm overnight

4-6: MODERATE RISK → TRADE (with caution)
→ Some developments but manageable
→ Market awareness is adequate
→ Low probability of major shock
→ Expected: Likely calm, possible minor movement
→ Prediction: 80-90% chance of manageable overnight

7-8: HIGH RISK → SKIP
→ Active crisis unfolding RIGHT NOW
→ High probability of overnight deterioration
→ Market showing stress signals
→ Expected: Likely gap and/or IV spike
→ Prediction: 40-60% chance of significant shock

9-10: EXTREME RISK → SKIP
→ Major crisis in full swing
→ Very high certainty of overnight chaos
→ Systemic implications clear
→ Expected: Almost certain gap + IV explosion
→ Prediction: 70%+ chance of major overnight shock

DECISION THRESHOLD:
• Scores 1-6: TRADE (overnight risk acceptable)
• Scores 7-10: SKIP (overnight risk too high)

RESPOND IN JSON (no markdown, no backticks):
{{
  "developing_threats": "List specific situations that could ESCALATE overnight, or 'None - stable conditions'",
  "late_breaking_news": "Any news from last 1-2 hours that market hasn't fully absorbed? Or 'None'",
  "overnight_escalation_probability": "Low/Moderate/High - likelihood things get WORSE overnight",
  "gap_risk_score": <1-10>,
  "iv_spike_risk_score": <1-10>,
  "overall_risk_score": <1-10>,
  "recommendation": "TRADE" or "SKIP",
  "reasoning": "2-3 sentences: What could happen overnight? Why this risk level? What's the FORWARD-LOOKING concern?"
}}

CRITICAL CALIBRATION:
• BASELINE = 1-2 (70% of days are quiet, nothing brewing)
• Only elevate if there's FORWARD-LOOKING escalation risk
• "News happened" ≠ risk (if fully absorbed by market already)
• "News developing" = risk (if could worsen overnight)
• Ask: "Between now and 9:30 AM tomorrow, could this explode?"
• Be realistic: Stable situations = low scores, not moderate

EXAMPLES:

Score 2: "Markets closed mixed on light volume, no major developments"
→ Nothing brewing, normal overnight expected

Score 3: "Fed official reiterates rates stance, tech stocks slightly down"  
→ Already priced, no surprise element

Score 6: "Regional bank reports Q4 loss, stock down 8%, analysts monitoring"
→ Developing situation, could see more bank issues tomorrow, moderate escalation risk

Score 8: "Major bank halts withdrawals, regulators in emergency talks, contagion fears"
→ Active crisis, high probability of overnight deterioration, systemic implications"""

    return ask_gpt(prompt)


def is_trade_recommended(risk_analysis):
    """
    Binary decision: Trade or Skip (no half-sizing for 1-contract trading)
    Returns: (should_trade: bool, position_size: float, reason: str)
    """
    recommendation = risk_analysis.get("recommendation", "TRADE_HALF").upper()
    overall_risk = risk_analysis.get("overall_risk_score", 5)
    gap_risk = risk_analysis.get("gap_risk_score", 5)
    iv_risk = risk_analysis.get("iv_spike_risk_score", 5)
    reasoning = risk_analysis.get("reasoning", "No reasoning provided")
    threats = risk_analysis.get("threats_detected", "Unknown")
    
    # Simple binary decision: Trade if low-moderate risk, Skip if high risk
    # Threshold: Score 7+ = Skip, Score <7 = Trade
    if overall_risk >= 7:
        return (
            False, 
            0.0, 
            f"HIGH RISK - Skip trade (Overall: {overall_risk}, Gap: {gap_risk}, IV: {iv_risk}). "
            f"Threats: {threats}. {reasoning}"
        )
    else:
        return (
            True, 
            1.0, 
            f"TRADE - Risk acceptable (Overall: {overall_risk}, Gap: {gap_risk}, IV: {iv_risk}). "
            f"{reasoning}"
        )


def is_within_trading_window(now=None):
    """Check if current time is within trading window"""
    now = now or datetime.now(TRADING_TIMEZONE)
    if now.weekday() >= 5:  # Weekend
        return False
    current_time = now.time()
    return TRADING_WINDOW_START <= current_time <= TRADING_WINDOW_END


def trigger_option_alpha(url, position_size=1.0):
    """
    Trigger Option Alpha webhook
    Note: You might want to modify Option Alpha automation to accept position sizing parameter
    """
    try:
        # If your Option Alpha allows, you could pass position_size as parameter
        # For now, just trigger the webhook
        response = requests.post(url)
        return response.status_code == 200
    except Exception as e:
        print(f"Error triggering Option Alpha: {e}")
        return False


@app.route("/", methods=["GET"])
def homepage():
    """Homepage with strategy information and status"""
    now = datetime.now(TRADING_TIMEZONE)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    in_window = is_within_trading_window(now)
    day_name = now.strftime("%A")
    is_weekend = now.weekday() >= 5
    
    # Calculate next poke time
    if is_weekend:
        days_until_monday = (7 - now.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 1
        next_poke_date = now + timedelta(days=days_until_monday)
        next_poke = datetime.combine(next_poke_date.date(), POKE_WINDOW_START)
        next_poke = next_poke.replace(tzinfo=TRADING_TIMEZONE)
        next_poke_str = next_poke.strftime("%A, %B %d at %I:%M %p %Z")
        status = "WEEKEND - No trading"
    elif not in_window:
        if now.time() < POKE_WINDOW_START:
            next_poke = datetime.combine(now.date(), POKE_WINDOW_START)
            next_poke = next_poke.replace(tzinfo=TRADING_TIMEZONE)
            next_poke_str = f"Today at {next_poke.strftime('%I:%M %p %Z')}"
            status = "PRE-MARKET - Waiting for entry window"
        else:
            tomorrow = now + timedelta(days=1)
            while tomorrow.weekday() >= 5:
                tomorrow += timedelta(days=1)
            next_poke = datetime.combine(tomorrow.date(), POKE_WINDOW_START)
            next_poke = next_poke.replace(tzinfo=TRADING_TIMEZONE)
            next_poke_str = next_poke.strftime("%A, %B %d at %I:%M %p %Z")
            status = "MARKET CLOSED - Done for today"
    else:
        next_poke_str = "Active - checking every 30 minutes"
        status = "ACTIVE - Entry window open!"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>SPX Overnight Iron Condor Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                max-width: 900px;
                margin: 40px auto;
                padding: 20px;
                background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%);
                color: #333;
            }}
            .container {{
                background: white;
                border-radius: 12px;
                padding: 35px;
                box-shadow: 0 15px 50px rgba(0,0,0,0.3);
            }}
            h1 {{
                color: #1e3c72;
                margin-top: 0;
                font-size: 28px;
                border-bottom: 3px solid #2a5298;
                padding-bottom: 15px;
            }}
            .strategy-box {{
                background: #f0f7ff;
                border-left: 5px solid #2a5298;
                padding: 20px;
                margin: 20px 0;
                border-radius: 5px;
            }}
            .strategy-box h3 {{
                margin-top: 0;
                color: #1e3c72;
            }}
            .status {{
                display: inline-block;
                padding: 10px 20px;
                border-radius: 25px;
                font-weight: bold;
                font-size: 15px;
                margin: 15px 0;
            }}
            .status.active {{
                background: #10b981;
                color: white;
            }}
            .status.waiting {{
                background: #f59e0b;
                color: white;
            }}
            .status.weekend {{
                background: #6b7280;
                color: white;
            }}
            .info-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
                gap: 15px;
                margin: 25px 0;
            }}
            .info-item {{
                padding: 18px;
                background: #f9fafb;
                border-radius: 8px;
                border-left: 4px solid #2a5298;
            }}
            .info-item strong {{
                display: block;
                color: #1e3c72;
                margin-bottom: 8px;
                font-size: 13px;
                text-transform: uppercase;
                letter-spacing: 0.5px;
            }}
            .info-item span {{
                font-size: 18px;
                color: #1f2937;
            }}
            .endpoints {{
                margin-top: 30px;
                padding-top: 20px;
                border-top: 2px solid #e5e7eb;
            }}
            .endpoint {{
                background: #f3f4f6;
                padding: 14px;
                border-radius: 6px;
                margin: 12px 0;
                font-family: 'Courier New', monospace;
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
            .warning {{
                background: #fef3c7;
                border-left: 5px solid #f59e0b;
                padding: 15px;
                margin: 20px 0;
                border-radius: 5px;
            }}
            footer {{
                margin-top: 35px;
                padding-top: 20px;
                border-top: 1px solid #e5e7eb;
                color: #6b7280;
                font-size: 14px;
                text-align: center;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>📊 SPX Overnight Iron Condor Bot</h1>
            
            <div class="strategy-box">
                <h3>🎯 Strategy Overview</h3>
                <p><strong>Entry:</strong> 2:30-3:30 PM ET (afternoon positioning)</p>
                <p><strong>Product:</strong> SPX Iron Condor (1 DTE)</p>
                <p><strong>Hold:</strong> Overnight</p>
                <p><strong>Exit:</strong> Next morning at 15% profit target</p>
                <p><strong>Edge:</strong> Capture overnight risk premium decay + IV crush</p>
            </div>
            
            <div class="status {'active' if in_window and not is_weekend else 'waiting' if not is_weekend else 'weekend'}">
                {status}
            </div>
            
            <div class="info-grid">
                <div class="info-item">
                    <strong>Current Time</strong>
                    <span>{timestamp}</span>
                </div>
                
                <div class="info-item">
                    <strong>Trading Day</strong>
                    <span>{day_name} {'(Weekend)' if is_weekend else '(Weekday)'}</span>
                </div>
                
                <div class="info-item">
                    <strong>Entry Window</strong>
                    <span>Mon-Fri, 2:30-3:30 PM ET</span>
                </div>
                
                <div class="info-item">
                    <strong>Check Interval</strong>
                    <span>Every 20 minutes</span>
                </div>
                
                <div class="info-item">
                    <strong>Next Check</strong>
                    <span>{next_poke_str}</span>
                </div>
            </div>
            
            <div class="warning">
                <strong>⚠️ Risk Management Active</strong><br>
                Bot analyzes overnight gap risk using GPT-4 before each trade decision.
                Skips high-risk days automatically.
            </div>
            
            <div class="endpoints">
                <h3>📡 Available Endpoints</h3>
                <div class="endpoint">
                    <a href="/health">/health</a> - Quick system health check
                </div>
                <div class="endpoint">
                    <a href="/option_alpha_trigger">/option_alpha_trigger</a> - Trade decision endpoint
                </div>
            </div>
            
            <footer>
                🤖 Overnight Premium Capture System | GPT-4 Risk Analysis | Railway Hosting
            </footer>
        </div>
    </body>
    </html>
    """
    return html


@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint"""
    now = datetime.now(TRADING_TIMEZONE)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    in_window = is_within_trading_window(now)
    
    return jsonify({
        "status": "healthy",
        "timestamp": timestamp,
        "entry_window_active": in_window,
        "entry_window": "Mon-Fri 2:30-3:30 PM ET",
        "strategy": "SPX overnight iron condor"
    }), 200


@app.route("/option_alpha_trigger", methods=["GET", "POST"])
def option_alpha_trigger():
    """
    Main trading decision endpoint
    Analyzes overnight risk and triggers appropriate webhook
    """
    now = datetime.now(TRADING_TIMEZONE)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    
    print(f"[{timestamp}] Received request to /option_alpha_trigger")
    
    try:
        # Check if within trading window
        if not is_within_trading_window(now):
            current_time_str = now.strftime("%I:%M %p")
            day_name = now.strftime("%A")
            is_weekend = now.weekday() >= 5
            
            if is_weekend:
                reason = f"Weekend ({day_name}) - Our strategy only trades Mon-Fri"
            else:
                reason = f"Outside our desired trading window (2:30-3:30 PM ET). Current time: {current_time_str}"
            
            message = f"Request not processed. {reason}"
            print(f"[{timestamp}] {message}")
            return jsonify({
                "status": "outside_window",
                "message": message,
                "reason": "outside_desired_window" if not is_weekend else "weekend",
                "our_trading_window": "Mon-Fri 2:30-3:30 PM ET",
                "current_time": timestamp,
                "note": "Market may be open, but we only enter positions during our specific window"
            }), 200
        
        print(f"[{timestamp}] Within entry window - analyzing overnight risk...")
        
        # Fetch latest news
        news_headlines, news_summary = fetch_breaking_news()
        print(f"[{timestamp}] Fetched {len(news_headlines)} news headlines")
        
        # Analyze overnight gap risk with GPT
        risk_analysis = analyze_overnight_risk(news_summary)
        print(f"[{timestamp}] GPT Risk Analysis: {json.dumps(risk_analysis, indent=2)}")
        
        # Determine trade decision
        should_trade, position_size, reason = is_trade_recommended(risk_analysis)
        
        # Execute decision
        if should_trade:
            success = trigger_option_alpha(trade_url, position_size)
            action = "EXECUTE TRADE" if position_size == 1.0 else f"EXECUTE TRADE ({int(position_size*100)}% size)"
            message = f"{action} - {reason}" if success else f"Failed to trigger: {reason}"
            print(f"[{timestamp}] Decision: {action}")
            print(f"[{timestamp}] Webhook trigger {'successful' if success else 'failed'}")
        else:
            success = trigger_option_alpha(no_trade_url)
            message = f"SKIP TRADE - {reason}"
            print(f"[{timestamp}] Decision: SKIP - {reason}")
            print(f"[{timestamp}] No-trade webhook trigger {'successful' if success else 'failed'}")
        
        # Return comprehensive response
        return jsonify({
            "status": "success",
            "timestamp": timestamp,
            "decision": "TRADE" if should_trade else "SKIP",
            "position_size": f"{int(position_size*100)}%",
            "message": message,
            "risk_analysis": {
                "overall_risk_score": risk_analysis.get("overall_risk_score"),
                "gap_risk_score": risk_analysis.get("gap_risk_score"),
                "iv_spike_risk_score": risk_analysis.get("iv_spike_risk_score"),
                "threats_detected": risk_analysis.get("threats_detected"),
                "recommendation": risk_analysis.get("recommendation"),
                "reasoning": risk_analysis.get("reasoning")
            },
            "news_count": len(news_headlines),
            "webhook_triggered": success
        }), 200
        
    except Exception as e:
        error_msg = f"Error: {str(e)}"
        print(f"[{timestamp}] ERROR: {error_msg}")
        import traceback
        traceback.print_exc()
        return jsonify({
            "status": "error",
            "message": error_msg,
            "timestamp": timestamp
        }), 500


def poke_self():
    """
    Background thread for smart trade checking:
    - Waits for Flask to be ready (with health check)
    - Checks immediately after ready (if in window)
    - Then aligns to 30-minute intervals: 2:30, 3:00, 3:30 PM ET
    """
    port = os.environ.get("PORT", "8080")
    
    # Wait for Flask to be ready with health checks
    print(f"[POKE THREAD] Started - waiting for Flask to be ready...")
    max_wait = 30  # Maximum 30 seconds
    wait_interval = 2  # Check every 2 seconds
    elapsed = 0
    
    while elapsed < max_wait:
        try:
            # Try to hit health endpoint
            health_url = f"http://127.0.0.1:{port}/health"
            response = requests.get(health_url, timeout=2)
            if response.status_code == 200:
                print(f"[POKE THREAD] ✓ Flask is ready (health check passed after {elapsed}s)")
                break
        except:
            # Flask not ready yet
            pass
        
        time.sleep(wait_interval)
        elapsed += wait_interval
    
    if elapsed >= max_wait:
        print(f"[POKE THREAD] ⚠ Flask health check timeout after {max_wait}s, proceeding anyway...")
    
    print(f"[POKE THREAD] Will check immediately, then at 2:30, 2:50, 3:10, 3:30 PM ET on Mon-Fri")
    
    first_check = True  # Flag for immediate first check
    
    while True:
        now = datetime.now(TRADING_TIMEZONE)
        timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
        
        # First check: immediate (no sleep)
        if first_check:
            print(f"[{timestamp}] First check - executing immediately...")
            first_check = False
            # Don't sleep, proceed to check below
        else:
            # Subsequent checks: calculate smart sleep to next target time
            current_minute = now.hour * 60 + now.minute
            
            # Target minutes: 2:30 PM (870), 2:50 PM (890), 3:10 PM (910), 3:30 PM (930)
            target_minutes = [870, 890, 910, 930]
            
            # Find next target
            next_target = None
            for target in target_minutes:
                if current_minute < target - 1:  # -1 to ensure we don't miss it
                    next_target = target
                    break
            
            # Calculate next check time
            if next_target is None:
                # No target today, next is tomorrow at 2:30 PM
                tomorrow = now + timedelta(days=1)
                next_check_time = datetime.combine(
                    tomorrow.date(), 
                    dt_time(hour=14, minute=30),
                    tzinfo=TRADING_TIMEZONE
                )
            else:
                # Next target today
                target_hour = next_target // 60
                target_minute = next_target % 60
                next_check_time = datetime.combine(
                    now.date(),
                    dt_time(hour=target_hour, minute=target_minute),
                    tzinfo=TRADING_TIMEZONE
                )
            
            # Calculate sleep duration
            sleep_seconds = (next_check_time - now).total_seconds()
            
            # Add 5 second buffer to ensure we wake after target
            sleep_seconds = max(sleep_seconds + 5, 60)  # Minimum 60 seconds
            
            next_check_str = next_check_time.strftime("%I:%M:%S %p")
            print(f"[{timestamp}] Sleeping {int(sleep_seconds)}s until next check at {next_check_str}...")
            time.sleep(sleep_seconds)
            
            # Update timestamp after sleep
            now = datetime.now(TRADING_TIMEZONE)
            timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
        
        # Perform the check
        if now.weekday() < 5:  # Weekday
            current_time = now.time()
            
            if POKE_WINDOW_START <= current_time <= POKE_WINDOW_END:
                # Within window - execute check
                try:
                    url = f"http://127.0.0.1:{port}/option_alpha_trigger"
                    print(f"[{timestamp}] ✓ Within window (2:30-3:30 PM) - Checking trade decision at {url}...")
                    r = requests.get(url, timeout=60)
                    print(f"[{timestamp}] Check completed - Status: {r.status_code}")
                    
                    # Log the decision
                    if r.status_code == 200:
                        result = r.json()
                        decision = result.get('decision', 'UNKNOWN')
                        pos_size = result.get('position_size', 'N/A')
                        print(f"[{timestamp}] Trade Decision: {decision} (Size: {pos_size})")
                    
                except Exception as e:
                    print(f"[{timestamp}] ERROR checking trade decision: {e}")
            else:
                # Outside window
                current_time_str = now.strftime("%I:%M %p")
                print(f"[{timestamp}] ✗ Outside window (2:30-3:30 PM ET) - Current: {current_time_str}, skipping check.")
        else:
            # Weekend
            day_name = now.strftime("%A")
            print(f"[{timestamp}] Weekend ({day_name}), no checks needed.")




if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    
    print("=" * 80)
    print("SPX Overnight Iron Condor Strategy Bot")
    print("=" * 80)
    print(f"Port: {port}")
    print(f"Entry Window: Mon-Fri 2:30-3:30 PM ET")
    print(f"Check Interval: Every 20 minutes")
    print(f"Check Window: Mon-Fri 2:30-3:30 PM ET (checks at 2:30, 2:50, 3:10, 3:30)")
    print(f"VIX Threshold: {VIX_THRESHOLD} (above this = auto skip)")
    print("Strategy: Sell overnight premium, capture IV crush")
    print("=" * 80)
    
    # Start background poke thread
    t = threading.Thread(target=poke_self, daemon=True)
    t.start()
    
    # Start Flask
    print(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)