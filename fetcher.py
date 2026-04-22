"""
fetcher.py — Data fetcher for STF Alert Service.

Fetches only what's needed for Cfg1 signal detection:
  - Binance OHLCV (30m, last LOOKBACK_BARS bars)   — price + volume
  - Binance funding rate (latest value)             — funding filter

No Coinglass dependency. Railway must be SE Asia (Singapore) —
Binance fapi returns 451 geo-block from US/EU regions.
"""

import logging
from datetime import datetime, timezone

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)


# ── Binance OHLCV ───────────────────────────────────────────────────────────

def fetch_ohlcv(n_bars: int = config.LOOKBACK_BARS) -> pd.DataFrame:
    """Fetch last n_bars of BTCUSDT 30m candles from Binance futures."""
    url    = config.BINANCE_BASE + config.BINANCE_KLINES
    params = {
        "symbol":   config.SYMBOL,
        "interval": config.INTERVAL,
        "limit":    min(n_bars, 1500),
    }
    resp = requests.get(url, params=params, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    df = pd.DataFrame(data, columns=[
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_vol", "trades", "taker_buy_base",
        "taker_buy_quote", "ignore"
    ])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
    df = df.set_index("open_time")
    for col in ["open", "high", "low", "close", "volume", "taker_buy_base"]:
        df[col] = pd.to_numeric(df[col])

    logger.info("OHLCV: %d bars %s → %s", len(df), df.index[0].date(), df.index[-1].date())
    return df[["open", "high", "low", "close", "volume", "taker_buy_base"]]


# ── Binance funding rate (latest value) ─────────────────────────────────────

def fetch_funding_latest() -> float | None:
    """
    Fetch the most recent funding rate for BTCUSDT from Binance fapi.
    Returns the latest fundingRate as a float (e.g. 0.0001 = 0.01%).
    Returns None on failure.

    Endpoint: GET /fapi/v1/fundingRate?symbol=BTCUSDT&limit=1
    Response: list of {"symbol", "fundingRate", "fundingTime", "markPrice"}
    """
    url    = config.BINANCE_BASE + config.BINANCE_FUNDING_RATE
    params = {
        "symbol": config.SYMBOL,
        "limit":  1,
    }
    try:
        resp = requests.get(url, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        if data and isinstance(data, list):
            val = data[-1].get("fundingRate")
            result = float(val) if val is not None else None
            if result is not None:
                logger.info("Funding rate: %.6f (%.4f%%)", result, result * 100)
            return result
    except Exception as e:
        logger.warning("Funding fetch failed: %s", e)
    return None


# ── Merged dataset ──────────────────────────────────────────────────────────

def fetch_all() -> pd.DataFrame:
    """
    Fetch OHLCV data needed for Cfg1 signal detection.
    Returns a DataFrame with columns:
        open, high, low, close, volume, taker_buy_base

    Funding rate is fetched separately via fetch_funding_latest() and passed
    into detect_signal() directly (not merged into the bar DataFrame).
    """
    df = fetch_ohlcv()
    logger.info("Dataset ready: %d bars, columns: %s", len(df), list(df.columns))
    return df
