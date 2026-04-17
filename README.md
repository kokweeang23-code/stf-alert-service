# STF Alert Service

Real-time liquidation sweep alert tool. Polls BTC 30m data every 30 minutes
and sends a Telegram message when a qualifying sweep + exhaustion setup is detected.

## Signal Logic (Config B — validated 2026-04-17)

- Long-liq OR short-liq z-score > 1.5 (96-bar rolling baseline = 48h)
- Price moved >= 1.0% in the same direction (2-bar window)
- OI dropped >= 2% from 8-bar rolling peak (4h)
- Cooldown: 2 hours per direction (no repeat alerts)

**This tool surfaces the mechanical signal only. Always check the Coinglass
liquidation heatmap before entering — the cluster must be present and large.**

## Files

| File | Purpose |
|------|---------|
| app.py | Flask server + APScheduler (main entry point) |
| config.py | All parameters (loaded from env vars) |
| fetcher.py | Data fetching: Binance OHLCV + CG liq/OI/funding |
| signal.py | Signal detection logic |
| notifier.py | Telegram message formatting + sending |
| requirements.txt | Python dependencies |
| Procfile | Railway/Gunicorn entry point |
| railway.json | Railway deployment config |

## Deployment (Railway)

### Step 1 — Create GitHub repo
Create a new repo named `stf-alert-service` (or similar).
Upload all files from this folder.

### Step 2 — Deploy on Railway
1. Go to [railway.app](https://railway.app) → New Project → Deploy from GitHub
2. Select your `stf-alert-service` repo
3. Railway auto-detects Python + Procfile

### Step 3 — Set environment variables
In Railway dashboard → Variables, add:

| Variable | Value |
|----------|-------|
| TELEGRAM_BOT_TOKEN | Your bot token (same as CSSI/STF) |
| TELEGRAM_CHAT_ID | Your chat ID (same as CSSI/STF) |
| CG_API_KEY | Your Coinglass Standard API key |

### Step 4 — Deploy
Railway auto-deploys. On first boot:
- Sends startup confirmation to Telegram
- Runs an immediate first check
- Starts 30-minute polling loop

## Endpoints

| Endpoint | Description |
|----------|-------------|
| GET / | Health check + last run status |
| GET /run | Manual trigger (useful for testing) |
| GET /status | JSON status: last check, last signal, cooldown state |

## Sample Alert

```
🟢 STF SWEEP ALERT — LONG SETUP
━━━━━━━━━━━━━━━━━━━━━
Time:   2026-04-17 16:30 SGT
Price:  $84,250

Signal:
  Long-liq z-score:  2.34  (min 1.5)
  Price fell:        1.23%  (2-bar)
  OI drop:           -2.8%  (4h peak)

Context:
  Funding rate:  +0.0082% (long-biased)
  CVD:           Diverging ✓

📋 Longs flushed — fade the flush
→ Check heatmap. Cluster present? Enter LONG.
```

## Isolation
This service is completely independent of your other Railway deployments:
- btc-cssi-daily
- tv-telegram-webhook

If this service goes down, neither of the above is affected.
