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



# 从环境变量获取 secrets
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
NEWS_API_KEY = os.environ.get("NEWS_API_KEY")
trade_url = os.environ.get("TRADE_URL")
no_trade_url = os.environ.get("NO_TRADE_URL")

# 检查是否存在
if not all([OPENAI_API_KEY, NEWS_API_KEY, trade_url, no_trade_url]):
    raise ValueError("Some environment variables are missing.")

    
API_URL = "https://api.openai.com/v1/chat/completions"
NEWS_API_URL = "https://newsapi.org/v2/top-headlines"
TRADING_TIMEZONE = ZoneInfo("America/New_York")

# Trading window for endpoint access (when the web app accepts requests)
TRADING_WINDOW_START = dt_time(hour=13, minute=30)  # 1:30 PM ET
TRADING_WINDOW_END = dt_time(hour=15, minute=55)    # 3:55 PM ET

# Poke settings
POKE_INTERVAL = 30 * 60  # 30 minutes in seconds
POKE_WINDOW_START = dt_time(hour=13, minute=30)   # 1:30 PM ET
POKE_WINDOW_END = dt_time(hour=15, minute=55)      # 3:55 PM ET



def fetch_breaking_news():
    """ Fetches the latest general news headlines and descriptions from newsapi.org. """ # QUESTION: How to determine latest (timeframe needs to be defined)?
    params = {
        'apiKey': NEWS_API_KEY,
        'language': 'en',
        'sortBy': 'publishedAt',
        'pageSize': 5  # Get the top 5 headlines for context
    }
    response = requests.get(NEWS_API_URL, params=params)
    
    if response.status_code != 200:
        raise Exception(f"News API request failed: {response.text}")
    
    news_data = response.json().get("articles", [])

    # Extract headlines and descriptions
    news_headlines = [article['title'] for article in news_data]
    news_summary = "\n".join([f"- {article['title']}: {article['description']}" for article in news_data])
    
    return news_headlines, news_summary if news_summary else "No breaking news available."

def parse_gpt_response(response_text):
    """Clean and parse GPT response as JSON."""
    try:
        # Remove any backticks and any "json" markers, then strip whitespace
        cleaned_response = response_text.replace("```", "").replace("json", "").strip()

        # Print cleaned response for debugging
        print("Cleaned GPT response:", cleaned_response)

        # Attempt to parse as JSON
        parsed_response = json.loads(cleaned_response)
        return parsed_response
    except json.JSONDecodeError as e:
        print(f"Failed to parse GPT response as JSON. Response was: {response_text}. Error: {e}")
        # Return a fallback to avoid triggering false positives
        return {"impact": "Unknown", "explanation": "Unable to parse ChatGPT JSON response due to formatting issues."}

def ask_gpt(prompt):
    headers = {
        "Authorization": f"Bearer {OPENAI_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": "gpt-4-turbo",  # Adjust model name if necessary
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 150,
        "logprobs": True,  # Log the completion for viewing in the OpenAI API dashboard
    }
    response = requests.post(API_URL, headers=headers, json=data)
    if response.status_code != 200:
        raise Exception(f"OpenAI API request failed: {response.text}")
    
    result_text = response.json()["choices"][0]["message"]["content"].strip()
    
    # Parse response as JSON using parse_gpt_response
    return parse_gpt_response(result_text)

def analyze_impact(news_summary):
    # Refined prompt focusing on volatility and confidence in prediction
    prompt = (
        f"The following is a summary of recent breaking news events:\n{news_summary}\n\n"
        "Please determine if any of these events are likely to increase market volatility in the S&P 500 index (SPX) "
        "by more than 1.5 basis points. Focus on assessing conditions that disrupt a stable market, such as major geopolitical events, "
        "unexpected macroeconomic announcements, or policy decisions.\n\n"
        "When responding, consider:\n"
        "- Historical impact of similar events on SPX volatility.\n"
        "- Likelihood of influencing investor sentiment or causing significant price fluctuations.\n"
        "- Severity and unexpected nature of the event.\n\n"
        "Provide your response in JSON format, including a confidence level:\n"
        "{\"impact\": \"Yes\" or \"No\", \"confidence\": \"High\" or \"Low\", \"explanation\": \"Brief explanation here.\"}"
    )
    return ask_gpt(prompt)

def is_trade_recommended(impact_analysis):
    impact = impact_analysis.get("impact", "").lower()
    confidence = impact_analysis.get("confidence", "").lower()
    
    # Only pause trading if both impact is "Yes" and confidence is "High"
    return not (impact == "yes" and confidence == "high")

def is_within_trading_window(now=None):
    """Return True when current time is Mon-Fri between 1:30-3:55 PM Eastern."""
    now = now or datetime.now(TRADING_TIMEZONE)
    if now.weekday() >= 5:  # 5=Saturday, 6=Sunday
        return False
    current_time = now.time()
    return TRADING_WINDOW_START <= current_time <= TRADING_WINDOW_END

def trigger_option_alpha(url):
    try:
        response = requests.post(url)
        return response.status_code == 200
    except Exception as e:
        print(f"Error triggering Option Alpha: {e}")
        return False

@app.route("/", methods=["GET"])
def homepage():
    """Homepage with app information and status"""
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
        status = "WEEKEND - Pokes paused"
    elif not in_window:
        if now.time() < POKE_WINDOW_START:
            next_poke = datetime.combine(now.date(), POKE_WINDOW_START)
            next_poke = next_poke.replace(tzinfo=TRADING_TIMEZONE)
            next_poke_str = f"Today at {next_poke.strftime('%I:%M %p %Z')}"
            status = "WAITING - Before trading window"
        else:
            tomorrow = now + timedelta(days=1)
            while tomorrow.weekday() >= 5:
                tomorrow += timedelta(days=1)
            next_poke = datetime.combine(tomorrow.date(), POKE_WINDOW_START)
            next_poke = next_poke.replace(tzinfo=TRADING_TIMEZONE)
            next_poke_str = next_poke.strftime("%A, %B %d at %I:%M %p %Z")
            status = "DONE - After trading window"
    else:
        next_poke_str = "Active - poking every 30 minutes"
        status = "ACTIVE - Trading window open"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>GPT-4 Trade Indicator</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                max-width: 800px;
                margin: 40px auto;
                padding: 20px;
                background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                color: #333;
            }}
            .container {{
                background: white;
                border-radius: 10px;
                padding: 30px;
                box-shadow: 0 10px 40px rgba(0,0,0,0.2);
            }}
            h1 {{
                color: #667eea;
                margin-top: 0;
                display: flex;
                align-items: center;
                gap: 10px;
            }}
            .status {{
                display: inline-block;
                padding: 8px 16px;
                border-radius: 20px;
                font-weight: bold;
                font-size: 14px;
                margin-bottom: 20px;
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
                gap: 15px;
                margin: 20px 0;
            }}
            .info-item {{
                padding: 15px;
                background: #f9fafb;
                border-radius: 8px;
                border-left: 4px solid #667eea;
            }}
            .info-item strong {{
                display: block;
                color: #667eea;
                margin-bottom: 5px;
                font-size: 12px;
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
                padding: 12px;
                border-radius: 6px;
                margin: 10px 0;
                font-family: 'Courier New', monospace;
                font-size: 14px;
            }}
            .endpoint a {{
                color: #667eea;
                text-decoration: none;
                font-weight: bold;
            }}
            .endpoint a:hover {{
                text-decoration: underline;
            }}
            footer {{
                margin-top: 30px;
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
            <h1>
                📈 GPT-4 Trade Indicator
            </h1>
            
            <div class="status {'active' if in_window and not is_weekend else 'waiting' if not is_weekend else 'weekend'}">
                {status}
            </div>
            
            <div class="info-grid">
                <div class="info-item">
                    <strong>Current Time</strong>
                    <span>{timestamp}</span>
                </div>
                
                <div class="info-item">
                    <strong>Day</strong>
                    <span>{day_name} {'(Weekend)' if is_weekend else '(Weekday)'}</span>
                </div>
                
                <div class="info-item">
                    <strong>Trading Window</strong>
                    <span>Mon-Fri, 1:30-3:55 PM ET</span>
                </div>
                
                <div class="info-item">
                    <strong>Poke Interval</strong>
                    <span>Every 30 minutes</span>
                </div>
                
                <div class="info-item">
                    <strong>Next Poke</strong>
                    <span>{next_poke_str}</span>
                </div>
            </div>
            
            <div class="endpoints">
                <h3>📡 Available Endpoints</h3>
                <div class="endpoint">
                    <a href="/health">/health</a> - System health check
                </div>
                <div class="endpoint">
                    <a href="/option_alpha_trigger">/option_alpha_trigger</a> - Main trading signal endpoint
                </div>
            </div>
            
            <footer>
                🤖 Automated Trading Signal System | Powered by GPT-4 & Railway
            </footer>
        </div>
    </body>
    </html>
    """
    return html

@app.route("/option_alpha_trigger", methods=["GET", "POST"])
def option_alpha_trigger():
    now = datetime.now(TRADING_TIMEZONE)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    
    # Log every request
    print(f"[{timestamp}] Received request to /option_alpha_trigger")
    
    try:
        if not is_within_trading_window(now):
            message = f"Outside trading window (Mon-Fri 1:30-3:55 PM ET); request rejected at {timestamp}"
            print(f"[{timestamp}] {message}")
            return jsonify({
                "status": "rejected",
                "message": message,
                "current_time": timestamp
            }), 200
        
        print(f"[{timestamp}] Within trading window - processing request")
        
        # Step 1: Fetch breaking news headlines and summary
        news_headlines, news_summary = fetch_breaking_news()
        print(f"[{timestamp}] Fetched {len(news_headlines)} news headlines")
        
        # Step 2: Analyze impact of breaking news on SPX
        impact_analysis = analyze_impact(news_summary)
        print(f"[{timestamp}] GPT analysis complete - Impact: {impact_analysis.get('impact')}, Confidence: {impact_analysis.get('confidence')}")
        
        # Step 3: Get explanation for the output and determine trading action
        explanation = impact_analysis.get("explanation", "No explanation provided.")
        if is_trade_recommended(impact_analysis):
            # If GPT suggests stability, trigger trade URL
            success = trigger_option_alpha(trade_url)
            message = "Market conditions are stable; trading triggered." if success else "Failed to trigger trading."
            print(f"[{timestamp}] Trade decision: EXECUTE - {message}")
        else:
            # If GPT suggests high-confidence volatility, trigger no-trade URL
            success = trigger_option_alpha(no_trade_url)
            message = "High confidence of volatility detected; trading paused." if success else "Failed to trigger no-trade."
            print(f"[{timestamp}] Trade decision: PAUSE - {message}")

        # Output the result message, including news and GPT explanation
        return jsonify({
            "status": "success",
            "message": message,
            "timestamp": timestamp,
            "news_headlines": news_headlines,
            "news_summary": news_summary,
            "gpt_explanation": explanation
        }), 200
    except Exception as e:
        error_msg = f"An error occurred: {e}"
        print(f"[{timestamp}] ERROR: {error_msg}")
        return jsonify({"status": "error", "message": str(e), "timestamp": timestamp}), 500

@app.route("/health", methods=["GET"])
def health_check():
    """Health check endpoint to verify app is running"""
    now = datetime.now(TRADING_TIMEZONE)
    timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
    in_window = is_within_trading_window(now)
    
    return jsonify({
        "status": "healthy",
        "timestamp": timestamp,
        "trading_window_active": in_window,
        "trading_window": "Mon-Fri 1:30-3:55 PM ET"
    }), 200

def poke_self():
    """Background thread to poke /option_alpha_trigger every 30 mins strictly during trading window."""
    port = os.environ.get("PORT", "8080")
    print(f"[POKE THREAD] Started - will poke every 30 minutes during Mon-Fri 1:30-3:55 PM ET")
    
    while True:
        now = datetime.now(TRADING_TIMEZONE)
        timestamp = now.strftime("%Y-%m-%d %I:%M:%S %p %Z")
        
        # Only Mon-Fri
        if now.weekday() < 5:  
            current_time = now.time()
            if POKE_WINDOW_START <= current_time <= POKE_WINDOW_END:
                try:
                    url = f"http://127.0.0.1:{port}/option_alpha_trigger"
                    print(f"[{timestamp}] Poking self at {url}...")
                    r = requests.get(url, timeout=30)
                    print(f"[{timestamp}] Poke completed - Status: {r.status_code}, Response: {r.text[:200]}")
                except Exception as e:
                    print(f"[{timestamp}] ERROR poking self: {e}")
            else:
                print(f"[{timestamp}] Outside poke window (1:30-3:55 PM ET), skipping poke.")
        else:
            day_name = now.strftime("%A")
            print(f"[{timestamp}] Weekend ({day_name}), skipping poke.")
        
        # Sleep 30 minutes
        print(f"[{timestamp}] Sleeping for 30 minutes until next poke check...")
        time.sleep(POKE_INTERVAL)

if __name__ == "__main__":
    # Get port from environment variable (Railway sets this)
    port = int(os.environ.get("PORT", 8080))
    
    print(f"=" * 80)
    print(f"Starting GPT4 Trade Indicator App")
    print(f"=" * 80)
    print(f"Port: {port}")
    print(f"Trading Window: Mon-Fri 1:30-3:55 PM ET")
    print(f"Poke Interval: Every 30 minutes")
    print(f"Poke Window: Mon-Fri 1:30-3:55 PM ET")
    print(f"=" * 80)
    
    # 启动后台线程
    t = threading.Thread(target=poke_self, daemon=True)
    t.start()
    
    # 启动 Flask
    print(f"Starting Flask app on port {port}")
    app.run(host="0.0.0.0", port=port)