"""
config.py — STF Alert Service configuration.
All sensitive values are loaded from environment variables (Railway env vars).

Signal: Cfg1 (validated 2026-04-22)
    OHLCV + Volume Z-score + Funding Rate filter
    price_imp=1.0%, vol_win=96, vol_thresh=1.5, fund_filter=0.05%
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID   = os.environ.get("TELEGRAM_CHAT_ID",   "")

# ── Signal parameters (Cfg1 — validated 2026-04-22) ──────────────────────
# WF result: 6 folds, 4/6 positive, avg Sharpe 2.57, avg WinRate 40.9%, ~35 trades/30d
# No Coinglass dependency — purely Binance fapi (OHLCV + funding rate)
VOL_ZSCORE_WINDOW    = 96      # 48h rolling baseline for volume z-score (96 × 30m)
VOL_ZSCORE_THRESHOLD = 1.5     # volume z-score threshold for qualifying spike
PRICE_IMPULSE_MIN    = 0.010   # 1.0% 2-bar price move (same direction as sweep)
FUND_FILTER          = 0.0005  # 0.05% — skip if funding extreme in wrong direction

# TP / SL (informational — used in startup message only; execution is manual)
TAKE_PROFIT_PCT = 0.020   # 2.0%
STOP_LOSS_PCT   = 0.007   # 0.7%

# ── Cooldown ────────────────────────────────────────────────────────────────
COOLDOWN_HOURS = 2   # minimum hours between alerts of the same direction

# ── Data fetch ──────────────────────────────────────────────────────────────
SYMBOL        = "BTCUSDT"
INTERVAL      = "30m"
LOOKBACK_BARS = 200   # bars fetched per poll; 200 × 30m = 100h (covers vol_win=96 + buffer)

# ── Binance endpoints ───────────────────────────────────────────────────────
# Railway region MUST be SE Asia (Singapore) — Binance fapi returns 451 from US/EU
BINANCE_BASE            = "https://fapi.binance.com"
BINANCE_KLINES          = "/fapi/v1/klines"
BINANCE_FUNDING_RATE    = "/fapi/v1/fundingRate"

# ── Scheduler ──────────────────────────────────────────────────────────────
POLL_INTERVAL_MINUTES = 30   # how often to check for signals (aligns to 30m bar close)

# ── Flask ───────────────────────────────────────────────────────────────────
PORT = int(os.environ.get("PORT", 5000))
