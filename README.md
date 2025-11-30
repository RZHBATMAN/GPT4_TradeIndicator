# GPT-4 Trade Indicator Bot

An automated trading signal system that analyzes breaking news using GPT-4 to determine market volatility and trigger trading decisions via Option Alpha webhooks.

## 🎯 Overview

This Flask application:
- Monitors breaking news during trading hours (Mon-Fri 1:30-3:55 PM ET)
- Analyzes news impact using GPT-4 to predict SPX volatility
- Automatically triggers Option Alpha webhooks based on analysis
- Runs self-contained with internal scheduling (no external cron needed)

## 🏗️ Architecture

```
┌─────────────────────────────────────────────────┐
│  Railway Container (24/7)                       │
│                                                  │
│  ┌──────────────────────────────────────────┐  │
│  │  Background Thread (Poke Scheduler)      │  │
│  │  • Checks time every 30 minutes          │  │
│  │  • Only active Mon-Fri 1:30-3:55 PM ET   │  │
│  │  • Pokes /option_alpha_trigger endpoint  │  │
│  └──────────────────────────────────────────┘  │
│                                                  │
│  ┌──────────────────────────────────────────┐  │
│  │  Flask Web Server (Main Thread)          │  │
│  │                                           │  │
│  │  Routes:                                  │  │
│  │  • / (homepage)           - Dashboard     │  │
│  │  • /health                - Health check  │  │
│  │  • /option_alpha_trigger  - Main logic    │  │
│  └──────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

## 📋 Features

### 1. Homepage Dashboard (`/`)
- Real-time status display
- Current time and day
- Trading window schedule
- Next poke time
- Visual status indicators (Active/Waiting/Weekend)
- Links to all endpoints

### 2. Health Check (`/health`)
- Returns JSON with system status
- Trading window active/inactive
- Current timestamp
- Quick monitoring endpoint

### 3. Trading Signal Endpoint (`/option_alpha_trigger`)
- Fetches latest news from NewsAPI
- Analyzes impact using GPT-4
- Determines volatility confidence
- Triggers appropriate Option Alpha webhook
- Only processes requests during trading hours

### 4. Automated Scheduling
- Internal poke thread (no external cron needed)
- Runs every 30 minutes during trading window
- Automatic weekend/off-hours detection
- Timezone-aware (Eastern Time)

## ⚙️ Configuration

### Environment Variables

Required environment variables (set in Railway):

```bash
# OpenAI API
OPENAI_API_KEY=sk-...

# News API
NEWS_API_KEY=...

# Option Alpha Webhooks
TRADE_URL=https://...    # Webhook to trigger when trading
NO_TRADE_URL=https://... # Webhook to pause trading

# Port (automatically set by Railway)
PORT=8080
```

### Trading Window Settings

Edit in `app.py`:

```python
# When the app accepts requests
TRADING_WINDOW_START = time(hour=13, minute=30)  # 1:30 PM ET
TRADING_WINDOW_END = time(hour=15, minute=55)    # 3:55 PM ET

# When the internal poke runs
POKE_WINDOW_START = time(hour=13, minute=30)   # 1:30 PM ET
POKE_WINDOW_END = time(hour=15, minute=55)     # 3:55 PM ET
POKE_INTERVAL = 30 * 60  # 30 minutes in seconds
```

## 🚀 Deployment

### Railway Deployment

1. **Connect GitHub Repository**
   - Link your GitHub repo to Railway
   - Railway auto-detects Python app

2. **Set Environment Variables**
   - Go to Variables tab
   - Add all required environment variables

3. **Add Procfile** (in repository root)
   ```
   web: python app.py
   ```

4. **Deploy**
   - Railway automatically deploys on push
   - View logs in Deploy Logs tab

### File Structure

```
your-repo/
├── app.py                 # Main Flask application
├── Procfile              # Railway deployment config
├── requirements.txt      # Python dependencies
├── README.md            # This file
└── verify_schedule.py   # Optional: Local schedule verification
```

### Dependencies (`requirements.txt`)

```
Flask==3.0.0
requests==2.31.0
gunicorn==20.1.0
pytz==2023.3
```

## 📊 How It Works

### Poke Cycle (Every 30 Minutes During Trading Hours)

```
1. Poke thread wakes up
   ↓
2. Check current time and day
   ↓
3. Is it Mon-Fri 1:30-3:55 PM ET?
   ↓
   YES → Continue | NO → Sleep 30 mins
   ↓
4. Call /option_alpha_trigger endpoint
   ↓
5. Fetch latest 5 news headlines (NewsAPI)
   ↓
6. Send to GPT-4 for volatility analysis
   ↓
7. GPT responds with:
   - Impact: Yes/No
   - Confidence: High/Low
   - Explanation
   ↓
8. Decision logic:
   - High confidence volatility → Trigger NO_TRADE_URL
   - Otherwise → Trigger TRADE_URL
   ↓
9. Log result and sleep 30 minutes
```

### Request Flow

```
External Request OR Internal Poke
            ↓
    Flask receives request
            ↓
    Check trading window
            ↓
    Outside window? → Reject with message
            ↓
    Inside window? → Process
            ↓
    Fetch news → GPT analysis → Webhook trigger
            ↓
    Return JSON response
```

## 💰 Cost Breakdown

### Monthly Costs (Approximate)

```
Railway Hosting:        $2-3/month
GPT-4 Turbo API:       $1.50/month (100 calls)
NewsAPI:               $0 (free tier)
───────────────────────────────────────
Total:                 ~$3.50-4.50/month
```

### API Usage

```
Pokes per day:    5 (1:30, 2:00, 2:30, 3:00, 3:30 PM)
Days per week:    5 (Mon-Fri only)
Pokes per week:   25
Pokes per month:  ~100

Each poke costs:  ~$0.015 (GPT-4 API)
Monthly GPT cost: ~$1.50
```

## 🔍 Monitoring

### View Logs (Railway Dashboard)

```
Railway Dashboard 
  → Your Project 
  → Service 
  → Deployments 
  → Latest Deployment 
  → Deploy Logs
```

### Log Examples

**Weekend (No Activity):**
```
[2025-11-30 02:30:47 PM EST] Weekend (Saturday), skipping poke.
[2025-11-30 02:30:47 PM EST] Sleeping for 30 minutes until next poke check...
```

**Weekday Outside Window:**
```
[2025-12-02 09:00:00 AM EST] Outside poke window (1:30-3:55 PM ET), skipping poke.
```

**Active Trading Window:**
```
[2025-12-02 01:30:00 PM EST] Poking self at http://127.0.0.1:8080/option_alpha_trigger...
[2025-12-02 01:30:00 PM EST] Received request to /option_alpha_trigger
[2025-12-02 01:30:00 PM EST] Within trading window - processing request
[2025-12-02 01:30:02 PM EST] Fetched 5 news headlines
[2025-12-02 01:30:05 PM EST] GPT analysis complete - Impact: No, Confidence: High
[2025-12-02 01:30:06 PM EST] Trade decision: EXECUTE - Market conditions are stable
[2025-12-02 01:30:06 PM EST] Poke completed - Status: 200
```

## 🧪 Testing

### Test Endpoints Manually

```bash
# Homepage (free, no API calls)
curl https://your-app.up.railway.app/

# Health check (free, no API calls)
curl https://your-app.up.railway.app/health

# Trading endpoint (costs money - calls GPT!)
curl https://your-app.up.railway.app/option_alpha_trigger
```

### Verify Schedule Locally

```bash
python verify_schedule.py
```

This shows:
- Poke times each day
- Cost estimates
- Next scheduled poke
- Current status

## 🛠️ Customization

### Change Poke Interval

Edit `app.py`:
```python
POKE_INTERVAL = 30 * 60  # Change to 15 * 60 for 15 mins
```

### Change Trading Window

Edit `app.py`:
```python
TRADING_WINDOW_START = time(hour=13, minute=30)  # 1:30 PM
TRADING_WINDOW_END = time(hour=15, minute=55)    # 3:55 PM
```

### Modify GPT Prompt

Edit `analyze_impact()` function in `app.py`:
```python
def analyze_impact(news_summary):
    prompt = (
        # Customize your prompt here
        f"Analyze this news: {news_summary}"
    )
    return ask_gpt(prompt)
```

### Change Volatility Threshold

Edit prompt in `analyze_impact()`:
```python
"by more than 1.5 basis points"  # Change to your threshold
```

## 🐛 Troubleshooting

### App Not Starting

**Check environment variables:**
```bash
# In Railway: Variables tab
# Ensure all 4 are set:
OPENAI_API_KEY
NEWS_API_KEY
TRADE_URL
NO_TRADE_URL
```

### Poke Not Working

**Check logs for:**
```
"Weekend, skipping poke"          → Normal on Sat/Sun
"Outside poke window"             → Normal outside 1:30-3:55 PM
"Poking self..."                  → Should see during trading hours
```

### GPT Errors

**Common issues:**
- Invalid API key → Check OPENAI_API_KEY
- Rate limit → Wait or upgrade OpenAI plan
- Parsing error → GPT response not in JSON format

### Webhook Not Triggering

**Check:**
- TRADE_URL and NO_TRADE_URL are correct
- Option Alpha webhooks are active
- Check logs for "Failed to trigger" messages

## 📚 Technical Details

### Threading Model

- **Main Thread:** Runs Flask web server (blocking)
- **Daemon Thread:** Runs poke scheduler (background)
- **Communication:** Daemon thread → HTTP → Flask route

### Why Daemon Thread?

```python
t.daemon = True
```

- Daemon threads automatically exit when main program exits
- Prevents zombie processes
- Clean shutdown when Railway restarts container

### Port Configuration

```python
port = int(os.environ.get("PORT", 8080))
```

- Railway provides PORT via environment variable
- Defaults to 8080 for local testing
- Must listen on 0.0.0.0 (all interfaces) for Railway

### Timezone Handling

```python
TRADING_TIMEZONE = ZoneInfo("America/New_York")
```

- All times calculated in Eastern Time
- Handles DST automatically
- Poke logs show timezone: `-05:00` (EST) or `-04:00` (EDT)

## 🔐 Security Notes

- Never commit API keys to git
- Use Railway environment variables
- Secrets are injected at runtime
- No sensitive data in logs

## 📖 API References

- [OpenAI GPT-4 API](https://platform.openai.com/docs/api-reference)
- [NewsAPI Documentation](https://newsapi.org/docs)
- [Flask Documentation](https://flask.palletsprojects.com/)
- [Railway Documentation](https://docs.railway.app/)

## 📝 License

This project is for personal use. Modify as needed.

## 🤝 Support

For issues or questions:
1. Check Railway Deploy Logs
2. Verify environment variables
3. Test endpoints manually
4. Review this README

---

**Last Updated:** November 30, 2025  
**Version:** 1.0  
**Status:** Production Ready ✅
