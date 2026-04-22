"""
detector.py — Signal detection for STF Alert Service.

Implements Cfg1 (validated 2026-04-22):
    vol_win=96, vol_thresh=1.5, price_imp=1.0%, fund_filter=0.05%, cooldown=2h
    WF result: 6 folds, 4/6 positive, avg Sharpe 2.57, avg WinRate 40.9%

Signal logic:
    LONG:  price 2-bar <= -1.0%  AND  vol_z(96) >= 1.5  AND  funding >= -0.0005
    SHORT: price 2-bar >= +1.0%  AND  vol_z(96) >= 1.5  AND  funding <=  0.0005

No Coinglass dependency — purely Binance fapi (OHLCV + funding rate).
Returns a SignalResult dataclass with all context needed for the Telegram message.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    direction:       str             # "LONG" or "SHORT"
    bar_time:        datetime        # bar timestamp (UTC)
    price:           float           # close price at signal bar
    vol_zscore:      float           # volume z-score that fired
    price_move_pct:  float           # 2-bar price move (signed, %)
    funding_rate:    Optional[float] = None   # latest funding rate (raw, e.g. 0.0001)
    cvd_diverging:   bool            = False  # CVD diverging from price direction


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    """Rolling z-score. NaN-safe."""
    m  = series.rolling(window, min_periods=max(2, window // 4)).mean()
    sd = series.rolling(window, min_periods=max(2, window // 4)).std()
    sd = sd.replace(0, np.nan)
    return (series - m) / sd


def _cvd_diverging(df: pd.DataFrame, smooth: int = 4) -> bool:
    """
    True if CVD direction opposes price direction over the last `smooth` bars.
    CVD delta = taker_buy_base - (volume - taker_buy_base) = 2×taker_buy_base - volume
    """
    if "taker_buy_base" not in df.columns or "volume" not in df.columns:
        return False
    if len(df) < smooth + 1:
        return False

    recent    = df.iloc[-(smooth + 1):]
    buy       = recent["taker_buy_base"].fillna(0)
    sell      = recent["volume"].fillna(0) - buy
    cvd_delta = buy - sell

    price_chg = recent["close"].diff().fillna(0)
    cvd_sum   = cvd_delta.iloc[1:].sum()
    price_sum = price_chg.iloc[1:].sum()

    # Diverging: price and net order flow moving in opposite directions
    return (price_sum > 0 and cvd_sum < 0) or (price_sum < 0 and cvd_sum > 0)


def detect_signal(
    df: pd.DataFrame,
    funding_rate:     Optional[float] = None,
    last_long_alert:  Optional[datetime] = None,
    last_short_alert: Optional[datetime] = None,
) -> Optional[SignalResult]:
    """
    Run Cfg1 signal detection on the latest bar of df.

    Args:
        df:                OHLCV DataFrame from fetcher.fetch_all()
        funding_rate:      Latest Binance funding rate (float, e.g. 0.0001 = 0.01%)
        last_long_alert:   UTC datetime of last LONG alert sent (for cooldown)
        last_short_alert:  UTC datetime of last SHORT alert sent (for cooldown)

    Returns:
        SignalResult if a signal fires, None otherwise.
    """
    min_bars = config.VOL_ZSCORE_WINDOW + 5
    if len(df) < min_bars:
        logger.warning("Not enough bars for signal detection (%d < %d)", len(df), min_bars)
        return None

    # ── Volume z-score ─────────────────────────────────────────────────────
    vol_z = _rolling_zscore(df["volume"], config.VOL_ZSCORE_WINDOW)

    # ── 2-bar price impulse ─────────────────────────────────────────────────
    price_2bar = df["close"].pct_change(2).fillna(0)

    # ── Evaluate on the last completed bar ─────────────────────────────────
    i     = len(df) - 1
    last  = df.index[-1]
    vz    = vol_z.iloc[i]
    pm    = price_2bar.iloc[i]
    price = float(df["close"].iloc[i])

    now_utc  = datetime.now(timezone.utc)
    cooldown = timedelta(hours=config.COOLDOWN_HOURS)

    # ── Funding filter ──────────────────────────────────────────────────────
    # fund_filter=0.0005 → skip LONG if funding < -0.0005 (heavily short-biased market)
    #                     skip SHORT if funding > +0.0005 (heavily long-biased market)
    # If funding unavailable, gate passes (non-fatal)
    fund_ok_long  = True
    fund_ok_short = True
    if funding_rate is not None:
        fund_ok_long  = funding_rate >= -config.FUND_FILTER
        fund_ok_short = funding_rate <=  config.FUND_FILTER

    logger.debug(
        "Bar %s | vz=%.2f | pm=%.2f%% | fund=%s",
        last, vz if not np.isnan(vz) else float("nan"),
        pm * 100,
        f"{funding_rate:.6f}" if funding_rate is not None else "n/a",
    )

    # ── LONG signal ─────────────────────────────────────────────────────────
    # Price dropped >= 1% (2-bar) — longs flushed — fade the flush
    long_fire = (
        not np.isnan(vz)                  and
        vz  >= config.VOL_ZSCORE_THRESHOLD and
        pm  <= -config.PRICE_IMPULSE_MIN   and
        fund_ok_long
    )
    if long_fire:
        if last_long_alert and (now_utc - last_long_alert) < cooldown:
            logger.info("LONG signal suppressed — cooldown active (last: %s)", last_long_alert)
        else:
            cvd_div = _cvd_diverging(df.iloc[max(0, i - 10): i + 1])
            logger.info(
                "LONG signal fired — vz=%.2f pm=%.2f%% fund=%s",
                vz, pm * 100,
                f"{funding_rate:.6f}" if funding_rate is not None else "n/a",
            )
            return SignalResult(
                direction      = "LONG",
                bar_time       = last.to_pydatetime(),
                price          = price,
                vol_zscore     = round(float(vz), 2),
                price_move_pct = round(pm * 100, 2),
                funding_rate   = funding_rate,
                cvd_diverging  = cvd_div,
            )

    # ── SHORT signal ────────────────────────────────────────────────────────
    # Price rose >= 1% (2-bar) — shorts squeezed — fade the squeeze
    short_fire = (
        not np.isnan(vz)                  and
        vz  >= config.VOL_ZSCORE_THRESHOLD and
        pm  >= config.PRICE_IMPULSE_MIN    and
        fund_ok_short
    )
    if short_fire:
        if last_short_alert and (now_utc - last_short_alert) < cooldown:
            logger.info("SHORT signal suppressed — cooldown active (last: %s)", last_short_alert)
        else:
            cvd_div = _cvd_diverging(df.iloc[max(0, i - 10): i + 1])
            logger.info(
                "SHORT signal fired — vz=%.2f pm=%.2f%% fund=%s",
                vz, pm * 100,
                f"{funding_rate:.6f}" if funding_rate is not None else "n/a",
            )
            return SignalResult(
                direction      = "SHORT",
                bar_time       = last.to_pydatetime(),
                price          = price,
                vol_zscore     = round(float(vz), 2),
                price_move_pct = round(pm * 100, 2),
                funding_rate   = funding_rate,
                cvd_diverging  = cvd_div,
            )

    logger.info(
        "No signal — vz=%.2f pm=%.2f%% fund=%s",
        vz if not np.isnan(vz) else -99,
        pm * 100,
        f"{funding_rate:.6f}" if funding_rate is not None else "n/a",
    )
    return None
