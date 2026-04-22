"""
notifier.py — Telegram notification formatter and sender for STF Alert Service.
"""

import logging
from datetime import timezone, timedelta

import requests

import config
from detector import SignalResult

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))


def format_message(sig: SignalResult) -> str:
    """
    Format a clean Telegram alert message for a Cfg1 sweep signal.
    No TP/SL levels — user applies heatmap judgment before entry.
    """
    # Direction emoji and label
    if sig.direction == "LONG":
        dir_emoji  = "🟢"
        sweep_desc = "Longs flushed — fade the flush"
    else:
        dir_emoji  = "🔴"
        sweep_desc = "Shorts squeezed — fade the squeeze"

    # Time in SGT
    bar_sgt  = sig.bar_time.astimezone(SGT)
    time_str = bar_sgt.strftime("%Y-%m-%d %H:%M SGT")

    # Price formatted with commas
    price_str = f"${sig.price:,.0f}"

    # Price move (absolute value + direction word)
    pm_abs = abs(sig.price_move_pct)
    pm_dir = "fell" if sig.price_move_pct < 0 else "rose"

    # Funding rate
    if sig.funding_rate is not None:
        fr_val   = sig.funding_rate
        fr_pct   = fr_val * 100          # convert to percentage display
        fr_sign  = "+" if fr_pct >= 0 else ""
        if fr_pct > 0.005:
            fr_bias = "long-biased"
        elif fr_pct < -0.005:
            fr_bias = "short-biased"
        else:
            fr_bias = "neutral"
        fr_str = f"{fr_sign}{fr_pct:.4f}% ({fr_bias})"
    else:
        fr_str = "n/a"

    # CVD
    cvd_str = "Diverging ✓" if sig.cvd_diverging else "Not diverging"

    msg = (
        f"{dir_emoji} STF SWEEP ALERT — {sig.direction} SETUP\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Time:   {time_str}\n"
        f"Price:  {price_str}\n"
        f"\n"
        f"Signal:\n"
        f"  Vol z-score:         {sig.vol_zscore:.2f}  (min 1.5, 96-bar)\n"
        f"  Price {pm_dir}:        {pm_abs:.2f}%  (2-bar)\n"
        f"  Funding rate:        {fr_str}\n"
        f"\n"
        f"Context:\n"
        f"  CVD:           {cvd_str}\n"
        f"\n"
        f"📋 {sweep_desc}\n"
        f"→ Check heatmap. Cluster present? Enter {sig.direction}."
    )
    return msg


def send_alert(sig: SignalResult) -> bool:
    """
    Send formatted alert to Telegram.
    Returns True on success, False on failure.
    """
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("Telegram credentials not configured")
        return False

    msg = format_message(sig)
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": config.TELEGRAM_CHAT_ID,
        "text":    msg,
    }

    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
        logger.info("Telegram alert sent: %s signal at %s", sig.direction, sig.bar_time)
        return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


def send_startup_message() -> None:
    """Send a startup confirmation message on service boot."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return

    msg = (
        "✅ STF Alert Service started\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        f"Polling every {config.POLL_INTERVAL_MINUTES} min\n"
        f"Signal (Cfg1): vol_z >= {config.VOL_ZSCORE_THRESHOLD} (96-bar) "
        f"+ price >= {config.PRICE_IMPULSE_MIN * 100:.0f}% (2-bar) "
        f"+ fund filter {config.FUND_FILTER * 100:.3f}%\n"
        f"Cooldown: {config.COOLDOWN_HOURS}h per direction\n"
        "Monitoring 24/7 — check heatmap on alert."
    )
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text":    msg,
        }, timeout=10)
    except Exception as e:
        logger.warning("Startup message failed: %s", e)


def send_error_message(context: str, error: str) -> None:
    """Send a brief error notification to Telegram."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        return
    msg = f"⚠️ STF Alert — fetch error\n{context}\n{error}"
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text":    msg,
        }, timeout=10)
    except Exception:
        pass
