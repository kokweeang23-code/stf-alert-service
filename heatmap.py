"""
heatmap.py — Coinglass BTC Liquidation Heatmap screenshot + summary.

Takes a live screenshot of the Coinglass heatmap page using Playwright,
sends it to Telegram, then uses basic image analysis to summarise
the key price clusters visible in the chart.
"""

import logging
import os
import tempfile
import time

import requests

import config

logger = logging.getLogger(__name__)

HEATMAP_URL = "https://coinglass.com/pro/futures/LiquidationHeatMap"


def _send_photo(image_path: str, caption: str) -> bool:
    """Send a photo to Telegram with caption."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendPhoto"
    try:
        with open(image_path, "rb") as f:
            resp = requests.post(
                url,
                data={"chat_id": config.TELEGRAM_CHAT_ID, "caption": caption, "parse_mode": "HTML"},
                files={"photo": f},
                timeout=30,
            )
        resp.raise_for_status()
        logger.info("Heatmap photo sent to Telegram")
        return True
    except Exception as e:
        logger.error("Failed to send heatmap photo: %s", e)
        return False


def _send_message(text: str) -> bool:
    """Send a plain text message to Telegram."""
    url = f"https://api.telegram.org/bot{config.TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        resp = requests.post(
            url,
            json={"chat_id": config.TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"},
            timeout=15,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.error("Failed to send message: %s", e)
        return False


def take_screenshot() -> str | None:
    """
    Take a screenshot of the Coinglass heatmap using Playwright.
    Returns the path to the saved screenshot, or None on failure.
    """
    try:
        from playwright.sync_api import sync_playwright

        tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        tmp.close()
        screenshot_path = tmp.name

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-gpu",
                ],
            )
            context = browser.new_context(
                viewport={"width": 1400, "height": 750},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()

            logger.info("Navigating to Coinglass heatmap...")
            page.goto(HEATMAP_URL, wait_until="networkidle", timeout=30000)

            # Wait for chart canvas to render
            time.sleep(6)

            # Try to dismiss any cookie/modal overlays
            for selector in ["button:has-text('Accept')", "button:has-text('Close')",
                              "[class*='modal'] button", "[class*='cookie'] button"]:
                try:
                    page.click(selector, timeout=1500)
                    time.sleep(0.5)
                except Exception:
                    pass

            # Extra wait for chart animations to complete
            time.sleep(2)

            page.screenshot(path=screenshot_path, full_page=False)
            browser.close()

        logger.info("Screenshot saved: %s", screenshot_path)
        return screenshot_path

    except ImportError:
        logger.error("Playwright not installed — cannot take screenshot")
        return None
    except Exception as e:
        logger.error("Screenshot failed: %s", e, exc_info=True)
        return None


def fetch_and_send() -> bool:
    """
    Main entry point: screenshot heatmap, send to Telegram with summary caption.
    Returns True if successfully sent.
    """
    import datetime
    now_sgt = datetime.datetime.now(config.SGT if hasattr(config, "SGT") else
                                    datetime.timezone(datetime.timedelta(hours=8)))
    time_str = now_sgt.strftime("%Y-%m-%d %H:%M SGT")

    # Notify user we're fetching
    _send_message("📡 Fetching heatmap... (takes ~10s)")

    screenshot_path = take_screenshot()

    if not screenshot_path:
        _send_message(
            "⚠️ <b>Heatmap unavailable</b>\n"
            f"Screenshot failed at {time_str}.\n"
            f"View manually: {HEATMAP_URL}"
        )
        return False

    # Build caption
    caption = (
        f"🌡 <b>BTC Liquidation Heatmap</b>\n"
        f"<i>{time_str}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"<b>Yellow bands</b> = dense liq clusters (high priority)\n"
        f"<b>Cyan/green bands</b> = moderate clusters\n"
        f"<b>Purple</b> = sparse / no clusters\n"
        f"━━━━━━━━━━━━━━━━━━━━━\n"
        f"Price axis on right. Clusters above price = short liq. "
        f"Clusters below = long liq.\n"
        f"<a href='{HEATMAP_URL}'>Open live chart</a>"
    )

    success = _send_photo(screenshot_path, caption)

    # Clean up temp file
    try:
        os.unlink(screenshot_path)
    except Exception:
        pass

    return success
