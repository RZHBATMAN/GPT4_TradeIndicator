from flask import Flask, jsonify
import requests
import os
import json
from datetime import datetime, time
try:
    from zoneinfo import ZoneInfo  # type: ignore
except ImportError:
    from pytz import timezone as ZoneInfo  # type: ignore

app = Flask(__name__)

# Paths to secret files
API_KEY_FILE_PATH = "C:\\secrets\\IndicatorKey.txt"
NEWS_API_KEY_FILE_PATH = "C:\\secrets\\NewsKey.txt"
WEBHOOK_URLS_FILE_PATH = "C:\\secrets\\WebhookURLs.txt"

# Load the OpenAI API key from file
try:
    with open(API_KEY_FILE_PATH, 'r') as file:
        OPENAI_API_KEY = file.read().strip()
except FileNotFoundError:
    raise ValueError(f"API key file not found at {API_KEY_FILE_PATH}")

# Load the Trade and No Trade webhook URLs from file
trade_url = None
no_trade_url = None
try:
    with open(WEBHOOK_URLS_FILE_PATH, 'r') as file:
        for line in file:
            key, value = line.strip().split('=')
            if key == 'TRADE_URL':
                trade_url = value
            elif key == 'NO_TRADE_URL':
                no_trade_url = value
except FileNotFoundError:
    raise ValueError(f"Webhook URLs file not found at {WEBHOOK_URLS_FILE_PATH}")

# Ensure both URLs were loaded
if not trade_url or not no_trade_url:
    raise ValueError("Webhook URLs not properly configured in the secret file.")
    
API_URL = "https://api.openai.com/v1/chat/completions"
NEWS_API_URL = "https://newsapi.org/v2/top-headlines"
TRADING_TIMEZONE = ZoneInfo("America/New_York")
TRADING_WINDOW_START = time(hour=13, minute=30)
TRADING_WINDOW_END = time(hour=15, minute=55)

# Load the News API key
with open(NEWS_API_KEY_FILE_PATH, 'r') as file:
    NEWS_API_KEY = file.read().strip()

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
    """Return True when current time is Mon-Fri between 1:30-4:00 PM Eastern."""
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

@app.route("/option_alpha_trigger", methods=["GET", "POST"])
def option_alpha_trigger():
    try:
        if not is_within_trading_window():
            return jsonify({
                "status": "skipped",
                "message": "Outside trading window (Mon-Fri 1:30-4:00 PM ET); action not taken."
            }), 200
        # Step 1: Fetch breaking news headlines and summary
        news_headlines, news_summary = fetch_breaking_news()
        
        # Step 2: Analyze impact of breaking news on SPX
        impact_analysis = analyze_impact(news_summary)
        
        # Step 3: Get explanation for the output and determine trading action
        explanation = impact_analysis.get("explanation", "No explanation provided.")
        if is_trade_recommended(impact_analysis):
            # If GPT suggests stability, trigger trade URL
            success = trigger_option_alpha(trade_url)
            message = "Market conditions are stable; trading triggered." if success else "Failed to trigger trading."
        else:
            # If GPT suggests high-confidence volatility, trigger no-trade URL
            success = trigger_option_alpha(no_trade_url)
            message = "High confidence of volatility detected; trading paused." if success else "Failed to trigger no-trade."

        # Output the result message, including news and GPT explanation
        return jsonify({
            "status": "success",
            "message": message,
            "news_headlines": news_headlines,
            "news_summary": news_summary,
            "gpt_explanation": explanation
        }), 200
    except Exception as e:
        print(f"An error occurred: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == "__main__":
    app.run(port=5000)