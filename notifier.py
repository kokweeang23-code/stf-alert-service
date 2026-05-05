"""
notifier.py — Telegram notification formatter and sender for STF Alert Service.

Error-alert behavior (added 2026-05-05):
  - First failure of a kind: alert immediately
  - Same error repeating: re-alert at most once per ERROR_REALERT_MINUTES
  - Different error: alert immediately (new problem)
  - Recovery: "fetch recovered" alert after a failure streak ends
"""

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests

import config
from detector import SignalResult

logger = logging.getLogger(__name__)

SGT = timezone(timedelta(hours=8))

# ── Error rate-limiting state ──────────────────────────────────────────────
ERROR_REALERT_MINUTES = 60   # repeat reminder for same error every N min

_err_state = {
    "last_error_signature": None,    # normalized error string
    "last_error_time":      None,    # UTC datetime of last error sent
    "failure_streak":       0,       # consecutive failures (resets on success)
}


def _err_signature(error: str) -> str:
    """
    Normalize an error string so 'same kind' errors collapse together.
    Strips digits, URLs, timestamps so '418 ...' always matches '418 ...'.
    """
    sig = re.sub(r"https?://\S+", "<url>", error)
    sig = re.sub(r"\b\d+\b", "<num>", sig)
    return sig.strip()[:200]


def format_message(sig: SignalResult) -> str:
    """
    Format a clean Telegram alert message for a Cfg1 sweep signal.
    No TP/SL levels — user applies heatmap judgment before entry.
    """
    if sig.direction == "LONG":
        dir_emoji  = "🟢"
        sweep_desc = "Longs flushed — fade the flush"
    else:
        dir_emoji  = "🔴"
        sweep_desc = "Shorts squeezed — fade the squeeze"

    bar_sgt  = sig.bar_time.astimezone(SGT)
    time_str = bar_sgt.strftime("%Y-%m-%d %H:%M SGT")
    price_str = f"${sig.price:,.0f}"

    pm_abs = abs(sig.price_move_pct)
    pm_dir = "fell" if sig.price_move_pct < 0 else "rose"

    if sig.funding_rate is not None:
        fr_val   = sig.funding_rate
        fr_pct   = fr_val * 100
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


def _send_message(text: str) -> bool:
    """Internal helper — POSTs raw text to Telegram. Returns success bool."""
    if not config.TELEGRAM_BOT_TOKEN or not config.TELEGRAM_CHAT_ID:
        logger.error("Telegram credentials not configured")
        return False
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(url, json={
            "chat_id": config.TELEGRAM_CHAT_ID,
            "text":    text,
        }, timeout=10)
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Telegram send failed: %s", e)
        return False


def send_alert(sig: SignalResult) -> bool:
    """Send formatted alert to Telegram. Returns True on success."""
    msg = format_message(sig)
    ok = _send_message(msg)
    if ok:
        logger.info("Telegram alert sent: %s signal at %s", sig.direction, sig.bar_time)
    return ok


def send_startup_message() -> None:
    """Send a startup confirmation message on service boot."""
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
    _send_message(msg)


def send_error_message(context: str, error: str) -> None:
    """
    Send error notification with rate-limiting:
      - First failure → alert immediately
      - Same error within ERROR_REALERT_MINUTES → suppress
      - Different error → alert immediately
      - On Nth repeat after window → re-alert (reminder it's still broken)
    """
    now = datetime.now(timezone.utc)
    sig = _err_signature(error)

    _err_state["failure_streak"] += 1

    last_sig  = _err_state["last_error_signature"]
    last_time = _err_state["last_error_time"]

    # Same error & within mute window → suppress
    if last_sig == sig and last_time is not None:
        elapsed = (now - last_time).total_seconds() / 60
        if elapsed < ERROR_REALERT_MINUTES:
            logger.info("Suppressing duplicate error alert (%d min since last, streak=%d)",
                        int(elapsed), _err_state["failure_streak"])
            return

    # Send the alert
    streak = _err_state["failure_streak"]
    streak_note = f" (#{streak} consecutive)" if streak > 1 else ""
    msg = f"⚠️ STF Alert — fetch error{streak_note}\n{context}\n{error}"
    if _send_message(msg):
        _err_state["last_error_signature"] = sig
        _err_state["last_error_time"]      = now


def send_recovery_message() -> None:
    """
    Call after a successful fetch. If we were in a failure streak,
    send a 'recovered' alert and reset state. No-op otherwise.
    """
    streak = _err_state["failure_streak"]
    if streak == 0:
        return  # nothing to recover from

    msg = (
        f"✅ STF Alert — fetch recovered\n"
        f"Resumed normal polling after {streak} consecutive failures."
    )
    _send_message(msg)
    _err_state["last_error_signature"] = None
    _err_state["last_error_time"]      = None
    _err_state["failure_streak"]       = 0
