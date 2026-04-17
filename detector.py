"""
signal.py — Signal detection for STF Alert Service.

Implements Config B (validated 2026-04-17):
    liq_win=96, thresh=1.5, price_imp=1.0%, oi_drop=2%, cooldown=2h

Signal logic:
    LONG:  long_liq z-score > 1.5  AND  price fell >= 1.0% (2-bar)
           AND  OI dropped >= 2% from 8-bar rolling peak
    SHORT: short_liq z-score > 1.5 AND  price rose >= 1.0% (2-bar)
           AND  OI dropped >= 2% from 8-bar rolling peak

Returns a SignalResult dataclass with all context needed for the Telegram message.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

import numpy as np
import pandas as pd

import config

logger = logging.getLogger(__name__)


@dataclass
class SignalResult:
    direction:        str            # "LONG" or "SHORT"
    bar_time:         datetime       # bar timestamp (UTC)
    price:            float          # close price at signal bar
    liq_zscore:       float          # z-score that fired
    price_move_pct:   float          # 2-bar price move (signed, %)
    oi_drop_pct:      float          # OI drop from rolling peak (%)
    funding_rate:     Optional[float] = None   # latest funding rate (%)
    cvd_diverging:    bool           = False   # CVD diverging from price
    long_liq_usd:     float          = 0.0    # raw long liq USD that bar
    short_liq_usd:    float          = 0.0    # raw short liq USD that bar


def _rolling_zscore(series: pd.Series, window: int) -> pd.Series:
    """Rolling z-score. NaN-safe."""
    m  = series.rolling(window, min_periods=max(2, window // 4)).mean()
    sd = series.rolling(window, min_periods=max(2, window // 4)).std()
    sd = sd.replace(0, np.nan)
    return (series - m) / sd


def _cvd_diverging(df: pd.DataFrame, smooth: int = 4) -> bool:
    """
    True if CVD direction opposes price direction over the last `smooth` bars.
    CVD delta = taker_buy_base - (volume - taker_buy_base)
    """
    if "taker_buy_base" not in df.columns or "volume" not in df.columns:
        return False
    if len(df) < smooth + 1:
        return False

    recent = df.iloc[-(smooth + 1):]
    buy    = recent["taker_buy_base"].fillna(0)
    sell   = recent["volume"].fillna(0) - buy
    cvd_d  = buy - sell

    price_chg = recent["close"].diff().fillna(0)

    cvd_sum   = cvd_d.iloc[1:].sum()
    price_sum = price_chg.iloc[1:].sum()

    # Diverge: price and CVD moving in opposite directions
    return (price_sum > 0 and cvd_sum < 0) or (price_sum < 0 and cvd_sum > 0)


def detect_signal(
    df: pd.DataFrame,
    funding_rate: Optional[float] = None,
    last_long_alert:  Optional[datetime] = None,
    last_short_alert: Optional[datetime] = None,
) -> Optional[SignalResult]:
    """
    Run signal detection on the latest bar of df.

    Args:
        df:                Merged DataFrame from fetcher.fetch_all()
        funding_rate:      Latest funding rate value (optional enrichment)
        last_long_alert:   UTC datetime of last LONG alert sent (for cooldown)
        last_short_alert:  UTC datetime of last SHORT alert sent (for cooldown)

    Returns:
        SignalResult if a signal fires, None otherwise.
    """
    if len(df) < config.LIQ_ZSCORE_WINDOW + 5:
        logger.warning("Not enough bars for signal detection (%d < %d)",
                       len(df), config.LIQ_ZSCORE_WINDOW + 5)
        return None

    long_liq  = df["long_liq_usd"].fillna(0)
    short_liq = df["short_liq_usd"].fillna(0)

    lz_long  = _rolling_zscore(long_liq,  config.LIQ_ZSCORE_WINDOW)
    lz_short = _rolling_zscore(short_liq, config.LIQ_ZSCORE_WINDOW)

    # Price move over last 2 bars
    price_2bar = df["close"].pct_change(2).fillna(0)

    # OI peak drop (live data — meaningful since OI is fresh from API)
    if "oi_close" in df.columns and df["oi_close"].notna().any():
        oi      = df["oi_close"].ffill()
        oi_peak = oi.rolling(config.OI_PEAK_WINDOW, min_periods=2).max()
        oi_drop = ((oi_peak - oi) / oi_peak.replace(0, np.nan)).fillna(0).clip(lower=0)
    else:
        logger.warning("OI not available — OI drop gate bypassed")
        oi_drop = pd.Series(1.0, index=df.index)  # gate always passes if no OI

    # Evaluate on the LAST completed bar (index -1)
    # Signal fires on bar T → we alert immediately (no +1 shift needed for live alert)
    last = df.index[-1]
    i    = len(df) - 1

    lz_l  = lz_long.iloc[i]
    lz_s  = lz_short.iloc[i]
    pm    = price_2bar.iloc[i]
    oi_d  = oi_drop.iloc[i]
    price = df["close"].iloc[i]

    now_utc = datetime.now(timezone.utc)
    cooldown = timedelta(hours=config.COOLDOWN_HOURS)

    # ── LONG signal ────────────────────────────────────────────────────────
    long_fire = (
        lz_l  > config.LIQ_ZSCORE_THRESHOLD and
        pm    < -config.PRICE_IMPULSE_MIN    and
        oi_d  >= config.OI_DROP_MIN
    )
    if long_fire:
        if last_long_alert and (now_utc - last_long_alert) < cooldown:
            logger.info("LONG signal suppressed — cooldown active (last: %s)", last_long_alert)
        else:
            cvd_div = _cvd_diverging(df.iloc[max(0, i-10):i+1])
            logger.info("LONG signal fired — lz=%.2f pm=%.2f%% oi_drop=%.2f%%",
                        lz_l, pm * 100, oi_d * 100)
            return SignalResult(
                direction      = "LONG",
                bar_time       = last.to_pydatetime(),
                price          = price,
                liq_zscore     = round(lz_l, 2),
                price_move_pct = round(pm * 100, 2),
                oi_drop_pct    = round(oi_d * 100, 2),
                funding_rate   = funding_rate,
                cvd_diverging  = cvd_div,
                long_liq_usd   = df["long_liq_usd"].iloc[i],
                short_liq_usd  = df["short_liq_usd"].iloc[i],
            )

    # ── SHORT signal ───────────────────────────────────────────────────────
    short_fire = (
        lz_s  > config.LIQ_ZSCORE_THRESHOLD and
        pm    > config.PRICE_IMPULSE_MIN     and
        oi_d  >= config.OI_DROP_MIN
    )
    if short_fire:
        if last_short_alert and (now_utc - last_short_alert) < cooldown:
            logger.info("SHORT signal suppressed — cooldown active (last: %s)", last_short_alert)
        else:
            cvd_div = _cvd_diverging(df.iloc[max(0, i-10):i+1])
            logger.info("SHORT signal fired — lz=%.2f pm=%.2f%% oi_drop=%.2f%%",
                        lz_s, pm * 100, oi_d * 100)
            return SignalResult(
                direction      = "SHORT",
                bar_time       = last.to_pydatetime(),
                price          = price,
                liq_zscore     = round(lz_s, 2),
                price_move_pct = round(pm * 100, 2),
                oi_drop_pct    = round(oi_d * 100, 2),
                funding_rate   = funding_rate,
                cvd_diverging  = cvd_div,
                long_liq_usd   = df["long_liq_usd"].iloc[i],
                short_liq_usd  = df["short_liq_usd"].iloc[i],
            )

    logger.info(
        "No signal — lz_long=%.2f lz_short=%.2f pm=%.2f%% oi_drop=%.2f%%",
        lz_l if not np.isnan(lz_l) else -99,
        lz_s if not np.isnan(lz_s) else -99,
        pm * 100, oi_d * 100
    )
    return None
