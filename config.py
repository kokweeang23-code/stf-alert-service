"""
config.py — STF Alert Service configuration.
All sensitive values are loaded from environment variables (Railway env vars).
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ───────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")

# ── Coinglass API ──────────────────────────────────────────────────────────
CG_API_KEY = os.environ.get("CG_API_KEY", "")

# ── Signal parameters (Config B — validated 2026-04-17) ───────────────────
# liq_win=96, thresh=1.5, price_imp=1.0%
# WF result: 4/4 folds positive, avg Sharpe 5.66, avg WinRate 48.5%
LIQ_ZSCORE_WINDOW    = 96     # 48-bar rolling baseline for z-score (48h)
LIQ_ZSCORE_THRESHOLD = 1.5    # z-score threshold for qualifying liq spike
PRICE_IMPULSE_MIN    = 0.010  # 1.0% price move over 2 bars (same direction as liq)
OI_DROP_MIN          = 0.02   # 2% OI drop from 8-bar rolling peak (live data only)
OI_PEAK_WINDOW       = 8      # bars to look back for OI peak (4h)

# ── Cooldown ───────────────────────────────────────────────────────────────
COOLDOWN_HOURS = 2   # minimum hours between alerts of the same direction

# ── Data fetch ─────────────────────────────────────────────────────────────
SYMBOL           = "BTCUSDT"
CG_SYMBOL        = "BTC"
INTERVAL         = "30m"
LOOKBACK_BARS    = 200        # bars to fetch for z-score baseline (100h)
BARS_PER_REQUEST = 500

# ── Coinglass endpoints ────────────────────────────────────────────────────
CG_BASE              = "https://open-api-v4.coinglass.com"
CG_LIQ_ENDPOINT      = "/api/futures/liquidation/aggregated-history"
CG_OI_ENDPOINT       = "/api/futures/open-interest/aggregated-history"
CG_FUNDING_ENDPOINT  = "/api/futures/funding-rate/history"

# ── Binance endpoint ───────────────────────────────────────────────────────
# Spot API used (not futures/fapi) — fapi.binance.com returns 451 geo-block on Railway
BINANCE_BASE         = "https://api.binance.com"
BINANCE_KLINES       = "/api/v3/klines"

# ── Scheduler ─────────────────────────────────────────────────────────────
POLL_INTERVAL_MINUTES = 30   # how often to check for signals

# ── Flask ─────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 5000))
