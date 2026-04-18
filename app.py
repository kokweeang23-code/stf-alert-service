"""
app.py — STF Alert Service main application.

Flask web server + APScheduler for Railway deployment.
Polls for sweep signals every 30 minutes and sends Telegram alerts.

Endpoints:
    GET /          — health check + last run status
    GET /run       — manual trigger (fetch + check + alert if signal)
    GET /status    — JSON status: last check, last signal, cooldown state
"""

import logging
import os
from datetime import datetime, timezone, timedelta
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, jsonify

import config
import fetcher
import detector as sig_module
import notifier
import heatmap as heatmap_module

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# ── App state ──────────────────────────────────────────────────────────────
app = Flask(__name__)

state = {
    "last_check":        None,   # UTC datetime of last scheduled run
    "last_signal":       None,   # direction of last alert sent
    "last_signal_time":  None,   # UTC datetime of last alert sent
    "last_long_alert":   None,   # UTC datetime of last LONG alert (cooldown)
    "last_short_alert":  None,   # UTC datetime of last SHORT alert (cooldown)
    "last_price":        None,   # BTC price at last check
    "checks_total":      0,
    "alerts_total":      0,
    "last_error":        None,
}

SGT = timezone(timedelta(hours=8))


# ── Core check function ────────────────────────────────────────────────────

def run_check(source: str = "scheduler") -> dict:
    """
    Fetch latest data, run signal detection, send alert if triggered.
    Returns a status dict.
    """
    logger.info("Running check (source=%s)...", source)
    state["checks_total"] += 1
    state["last_check"]    = datetime.now(timezone.utc)

    try:
        # Fetch all data
        df = fetcher.fetch_all()
        state["last_price"] = float(df["close"].iloc[-1])

        # Fetch funding rate (enrichment — failure is non-fatal)
        try:
            funding = fetcher.fetch_funding_latest()
        except Exception as e:
            logger.warning("Funding fetch failed (non-fatal): %s", e)
            funding = None

        # Detect signal
        result = sig_module.detect_signal(
            df            = df,
            funding_rate  = funding,
            last_long_alert  = state["last_long_alert"],
            last_short_alert = state["last_short_alert"],
        )

        if result:
            # Send Telegram alert
            sent = notifier.send_alert(result)
            if sent:
                state["alerts_total"]   += 1
                state["last_signal"]     = result.direction
                state["last_signal_time"] = datetime.now(timezone.utc)
                if result.direction == "LONG":
                    state["last_long_alert"]  = datetime.now(timezone.utc)
                else:
                    state["last_short_alert"] = datetime.now(timezone.utc)
            return {"status": "alert_sent", "direction": result.direction,
                    "price": state["last_price"]}
        else:
            return {"status": "no_signal", "price": state["last_price"]}

    except Exception as e:
        logger.error("Check failed: %s", e, exc_info=True)
        state["last_error"] = str(e)
        notifier.send_error_message(f"Source: {source}", str(e))
        return {"status": "error", "error": str(e)}


# ── Flask routes ───────────────────────────────────────────────────────────

@app.route("/")
def health():
    last_check = state["last_check"]
    last_check_sgt = last_check.astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT") if last_check else "never"
    last_sig_time  = state["last_signal_time"]
    last_sig_sgt   = last_sig_time.astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT") if last_sig_time else "none"

    return (
        f"<h3>STF Alert Service — Running</h3>"
        f"<p><b>Last check:</b> {last_check_sgt}</p>"
        f"<p><b>Last signal:</b> {state['last_signal'] or 'none'} @ {last_sig_sgt}</p>"
        f"<p><b>BTC price:</b> ${state['last_price']:,.0f}</p>"
        f"<p><b>Checks:</b> {state['checks_total']} | <b>Alerts sent:</b> {state['alerts_total']}</p>"
        f"<p><b>Last error:</b> {state['last_error'] or 'none'}</p>"
        f"<hr><p>Signal config: liq_z &gt; {config.LIQ_ZSCORE_THRESHOLD} (96-bar) "
        f"+ price &gt; {config.PRICE_IMPULSE_MIN*100:.0f}% "
        f"+ OI drop &gt; {config.OI_DROP_MIN*100:.0f}% "
        f"| Cooldown: {config.COOLDOWN_HOURS}h</p>"
    ) if state["last_price"] else "<h3>STF Alert Service — Starting up...</h3>"


@app.route("/run")
def manual_run():
    """Manual trigger — useful for testing after deploy."""
    result = run_check(source="manual")
    return jsonify(result)


@app.route("/heatmap")
def heatmap_route():
    """Manually trigger heatmap screenshot + Telegram send."""
    import threading
    threading.Thread(target=heatmap_module.fetch_and_send, daemon=True).start()
    return jsonify({"status": "heatmap_requested", "note": "Screenshot being taken, Telegram message incoming..."})


@app.route("/telegram", methods=["POST"])
def telegram_webhook():
    """
    Telegram webhook — handles bot commands sent by the user.
    Commands supported:
      /heatmap  — screenshot + send heatmap
      /status   — current service status
      /run      — manual signal check
    """
    from flask import request
    import threading

    data = request.get_json(force=True, silent=True) or {}
    message = data.get("message", {})
    text = message.get("text", "").strip().lower()

    logger.info("Telegram webhook: %s", text)

    if text.startswith("/heatmap"):
        threading.Thread(target=heatmap_module.fetch_and_send, daemon=True).start()

    elif text.startswith("/status"):
        def _fmt(dt):
            return dt.astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT") if dt else "N/A"
        msg = (
            f"📊 <b>STF Alert Service Status</b>\n"
            f"Last check: {_fmt(state['last_check'])}\n"
            f"Last signal: {state['last_signal'] or 'none'}\n"
            f"BTC price: ${state['last_price']:,.0f}\n"
            f"Checks: {state['checks_total']} | Alerts: {state['alerts_total']}\n"
            f"Last error: {state['last_error'] or 'none'}"
        )
        notifier._send_message(msg) if hasattr(notifier, '_send_message') else None

    elif text.startswith("/run"):
        threading.Thread(target=lambda: run_check("telegram"), daemon=True).start()

    return jsonify({"ok": True})


@app.route("/status")
def status():
    def _fmt(dt):
        return dt.astimezone(SGT).strftime("%Y-%m-%d %H:%M SGT") if dt else None

    long_cooldown_remaining  = None
    short_cooldown_remaining = None
    now = datetime.now(timezone.utc)
    cd  = timedelta(hours=config.COOLDOWN_HOURS)

    if state["last_long_alert"]:
        remaining = cd - (now - state["last_long_alert"])
        long_cooldown_remaining = max(0, int(remaining.total_seconds() / 60))

    if state["last_short_alert"]:
        remaining = cd - (now - state["last_short_alert"])
        short_cooldown_remaining = max(0, int(remaining.total_seconds() / 60))

    return jsonify({
        "last_check":               _fmt(state["last_check"]),
        "last_signal":              state["last_signal"],
        "last_signal_time":         _fmt(state["last_signal_time"]),
        "last_price":               state["last_price"],
        "checks_total":             state["checks_total"],
        "alerts_total":             state["alerts_total"],
        "long_cooldown_remaining_min":  long_cooldown_remaining,
        "short_cooldown_remaining_min": short_cooldown_remaining,
        "last_error":               state["last_error"],
    })


# ── Scheduler ──────────────────────────────────────────────────────────────

def start_scheduler():
    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        func     = lambda: run_check("scheduler"),
        trigger  = "interval",
        minutes  = config.POLL_INTERVAL_MINUTES,
        id       = "stf_alert_check",
        max_instances = 1,   # prevent overlap if a run takes too long
    )
    scheduler.start()
    logger.info("Scheduler started — polling every %d min", config.POLL_INTERVAL_MINUTES)
    return scheduler


# ── Startup (runs on import — works with Gunicorn) ─────────────────────────

try:
    logger.info("STF Alert Service starting (module load)...")
    notifier.send_startup_message()
    _scheduler = start_scheduler()

    # Run an immediate first check so we don't wait 30 min for first data point
    import threading
    threading.Thread(target=lambda: run_check("startup"), daemon=True).start()
    logger.info("STF Alert Service startup complete.")
except Exception as _startup_exc:
    import traceback
    logger.error("STARTUP FAILED: %s", _startup_exc)
    logger.error(traceback.format_exc())


# ── Entry point (local dev only) ────────────────────────────────────────────

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=config.PORT, debug=False)
