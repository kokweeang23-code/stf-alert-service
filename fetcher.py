"""
fetcher.py — Data fetcher for STF Alert Service.

Fetches only what's needed for signal detection:
  - Binance OHLCV (30m, last LOOKBACK_BARS bars)
  - Coinglass liquidations (30m, last LOOKBACK_BARS bars)
  - Coinglass OI (single page — live data, always fresh)
  - Coinglass funding rate (latest value only)

All fetches are live — no cache. Designed for Railway 24/7 operation.
"""

import logging
import time
from datetime import datetime, timezone

import pandas as pd
import requests

import config

logger = logging.getLogger(__name__)

HEADERS = {"coinglassSecret": config.CG_API_KEY}


# ── Binance OHLCV ──────────────────────────────────────────────────────────

def fetch_ohlcv(n_bars: int = config.LOOKBACK_BARS) -> pd.DataFrame:
    """Fetch last n_bars of BTC/USDT 30m candles from Binance futures."""
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


# ── Coinglass liquidations ─────────────────────────────────────────────────

def fetch_liquidations(n_bars: int = config.LOOKBACK_BARS) -> pd.DataFrame:
    """Fetch last n_bars of aggregated liquidation history from Coinglass."""
    end_ms  = int(datetime.now(timezone.utc).timestamp() * 1000)
    url     = config.CG_BASE + config.CG_LIQ_ENDPOINT
    params  = {
        "symbol":    config.CG_SYMBOL,
        "interval":  config.INTERVAL,
        "limit":     min(n_bars, 500),
        "end_time":  end_ms,
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    body = resp.json()

    if not body.get("success") or not body.get("data"):
        logger.warning("Liquidations: empty or error response")
        return pd.DataFrame(columns=["long_liq_usd", "short_liq_usd"])

    records = body["data"]
    rows = []
    for r in records:
        ts = pd.to_datetime(r["time"], unit="ms", utc=True)
        rows.append({
            "time":          ts,
            "long_liq_usd":  float(r.get("aggregated_long_liquidation_usd",  0) or 0),
            "short_liq_usd": float(r.get("aggregated_short_liquidation_usd", 0) or 0),
        })

    df = pd.DataFrame(rows).set_index("time").sort_index()
    logger.info("Liquidations: %d bars %s → %s",
                len(df), df.index[0].date(), df.index[-1].date())
    return df


# ── Coinglass OI (single page — live only) ────────────────────────────────

def fetch_oi(n_bars: int = config.OI_PEAK_WINDOW * 4) -> pd.DataFrame:
    """
    Fetch recent OI bars from Coinglass (single page, most recent ~500 bars).
    OI endpoint has no time-travel — returns the most recent bars only.
    For the alert tool this is fine: we only need the last 8 bars for the
    OI peak drop gate.
    """
    url    = config.CG_BASE + config.CG_OI_ENDPOINT
    params = {
        "symbol":   config.CG_SYMBOL,
        "interval": config.INTERVAL,
        "limit":    500,
    }
    resp = requests.get(url, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    body = resp.json()

    if not body.get("success") or not body.get("data"):
        logger.warning("OI: empty or error response")
        return pd.DataFrame(columns=["oi_close"])

    records = body["data"]
    rows = []
    for r in records:
        ts = pd.to_datetime(r["time"], unit="ms", utc=True)
        rows.append({
            "time":     ts,
            "oi_close": float(r.get("close", 0) or 0),
        })

    df = pd.DataFrame(rows).set_index("time").sort_index()
    logger.info("OI: %d bars (live, most recent)", len(df))
    return df.tail(n_bars)


# ── Coinglass funding rate (latest value) ─────────────────────────────────

def fetch_funding_latest() -> float | None:
    """
    Fetch the most recent 8h funding rate for BTCUSDT.
    Returns the latest close value as a float (e.g. 0.0082 = 0.0082%).
    Returns None on failure.
    """
    url    = config.CG_BASE + config.CG_FUNDING_ENDPOINT
    params = {
        "symbol":   "BTCUSDT",
        "interval": "8h",
        "limit":    1,
    }
    try:
        resp = requests.get(url, params=params, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        body = resp.json()
        if body.get("success") and body.get("data"):
            val = body["data"][-1].get("close")
            return float(val) if val is not None else None
    except Exception as e:
        logger.warning("Funding fetch failed: %s", e)
    return None


# ── Merged dataset ─────────────────────────────────────────────────────────

def fetch_all() -> pd.DataFrame:
    """
    Fetch and merge all data needed for signal detection.
    Returns a DataFrame with columns:
        open, high, low, close, volume, taker_buy_base,
        long_liq_usd, short_liq_usd, oi_close
    """
    ohlcv = fetch_ohlcv()
    liq   = fetch_liquidations()
    oi    = fetch_oi()

    # Merge on index (30m bars)
    df = ohlcv.copy()
    df = df.join(liq,  how="left")
    df = df.join(oi,   how="left")

    # Fill missing liq with 0 (no liquidation event that bar)
    df["long_liq_usd"]  = df["long_liq_usd"].fillna(0)
    df["short_liq_usd"] = df["short_liq_usd"].fillna(0)

    # OI: forward fill (most recent real value), then backfill for early bars
    df["oi_close"] = df["oi_close"].ffill().bfill()

    logger.info("Merged dataset: %d bars, columns: %s", len(df), list(df.columns))
    return df
