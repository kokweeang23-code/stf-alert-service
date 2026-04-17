"""
notifier.py — Telegram notification formatter and sender for STF Alert Service.
"""

import logging
from datetime import timezone, timedelta

import requests

import config
from signal import SignalResult

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))


def format_message(sig: SignalResult) -> str:
    """
    Format a clean Telegram alert message for a sweep signal.
    No TP/SL levels — user applies heatmap judgment before entry.
    """
    # Direction emoji and label
    if sig.direction == "LONG":
        dir_emoji = "🟢"
        liq_label = "Long-liq z-score"
        sweep_desc = "Longs flushed — fade the flush"
    else:
        dir_emoji = "🔴"
        liq_label = "Short-liq z-score"
        sweep_desc = "Shorts squeezed — fade the squeeze"

    # Time in SGT
    bar_sgt = sig.bar_time.astimezone(SGT)
    time_str = bar_sgt.strftime("%Y-%m-%d %H:%M SGT")

    # Price formatted with commas
    price_str = f"${sig.price:,.0f}"

    # Price move (show as absolute value with direction word)
    pm_abs = abs(sig.price_move_pct)
    pm_dir = "fell" if sig.price_move_pct < 0 else "rose"

    # OI drop
    oi_str = f"-{sig.oi_drop_pct:.1f}%" if sig.oi_drop_pct > 0 else "n/a"

    # Funding rate
    if sig.funding_rate is not None:
        fr_val  = sig.funding_rate
        fr_sign = "+" if fr_val >= 0 else ""
        fr_bias = "long-biased" if fr_val > 0.005 else ("short-biased" if fr_val < -0.005 else "neutral")
        fr_str  = f"{fr_sign}{fr_val:.4f}% ({fr_bias})"
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
        f"  {liq_label}:  {sig.liq_zscore:.2f}  (min 1.5)\n"
        f"  Price {pm_dir}:        {pm_abs:.2f}%  (2-bar)\n"
        f"  OI drop:           {oi_str}  (4h peak)\n"
        f"\n"
        f"Context:\n"
        f"  Funding rate:  {fr_str}\n"
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
        "chat_id":    config.TELEGRAM_CHAT_ID,
        "text":       msg,
        "parse_mode": "HTML",
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
        f"Signal: liq z>{config.LIQ_ZSCORE_THRESHOLD} "
        f"(96-bar) + price >{config.PRICE_IMPULSE_MIN*100:.0f}% "
        f"+ OI drop >{config.OI_DROP_MIN*100:.0f}%\n"
        f"Cooldown: {config.COOLDOWN_HOURS}h per direction\n"
        "Monitoring 24/7 — check heatmap on alert."
    )
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={
            "chat_id":    config.TELEGRAM_CHAT_ID,
            "text":       msg,
            "parse_mode": "HTML",
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
