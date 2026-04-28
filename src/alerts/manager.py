"""
NEXUS ALPHA - Alert Manager
=============================
Sends trade, signal, warning, and critical alerts via Telegram and Discord.
Designed for graceful degradation — if a notification provider is unavailable
or not configured, it logs the alert and continues without raising.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Maximum items buffered in the alert queue before dropping
_QUEUE_MAX_SIZE = 500

# How long to wait between retries on a failed send
_RETRY_DELAY_S = 5.0

# Maximum number of send retries per alert
_MAX_RETRIES = 3


class AlertManager:
    """
    Unified alert dispatcher for Telegram and Discord.

    All send methods are fire-and-forget: they put items on an internal
    asyncio.Queue which is drained by a dedicated background loop
    (started by `run()`).

    Parameters
    ----------
    settings : Settings
        Application settings containing notification credentials.
    """

    def __init__(self, settings: Any) -> None:
        self._settings = settings
        self._queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(
            maxsize=_QUEUE_MAX_SIZE
        )
        self._telegram_available = False
        self._discord_available = False
        self._telegram_bot: Optional[Any] = None  # telegram.Bot instance

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Initialise notification providers (non-blocking, graceful)."""
        await self._init_telegram()
        await self._init_discord()
        logger.info(
            "AlertManager initialised: telegram=%s discord=%s",
            self._telegram_available,
            self._discord_available,
        )

    async def run(self) -> None:
        """Background loop that drains the alert queue and dispatches messages."""
        logger.info("AlertManager: background dispatch loop started")
        while True:
            try:
                alert = await self._queue.get()
                await self._dispatch(alert)
                self._queue.task_done()
            except asyncio.CancelledError:
                logger.info("AlertManager: dispatch loop cancelled")
                return
            except Exception as exc:
                logger.error("AlertManager: unexpected error in dispatch loop: %s", exc)

    async def stop(self) -> None:
        """Drain remaining queued alerts then stop."""
        logger.info("AlertManager: draining %d pending alerts", self._queue.qsize())
        # Give it a brief window to flush
        try:
            await asyncio.wait_for(self._queue.join(), timeout=10.0)
        except asyncio.TimeoutError:
            logger.warning("AlertManager: queue drain timed out — some alerts may be lost")

    # ------------------------------------------------------------------
    # Public send methods
    # ------------------------------------------------------------------

    async def send_trade_alert(self, trade: Dict[str, Any]) -> None:
        """Queue a trade execution alert."""
        symbol = trade.get("symbol", "?")
        direction = trade.get("direction", "?")
        quantity = trade.get("quantity", 0.0)
        price = trade.get("entry_price", 0.0)

        message = (
            f"TRADE EXECUTED\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Size: {quantity:.4f}\n"
            f"Entry: {price:.4f}\n"
            f"Time: {datetime.now(timezone.utc).isoformat()}"
        )
        await self._enqueue("TRADE", message)

    async def send_signal_alert(self, signal: Dict[str, Any]) -> None:
        """Queue a signal generation alert."""
        symbol = signal.get("symbol", "?")
        direction = signal.get("direction", "?")
        confidence = signal.get("confidence", 0.0)

        message = (
            f"SIGNAL GENERATED\n"
            f"Symbol: {symbol}\n"
            f"Direction: {direction}\n"
            f"Confidence: {confidence:.2%}\n"
            f"Time: {datetime.now(timezone.utc).isoformat()}"
        )
        await self._enqueue("SIGNAL", message)

    async def send_error_alert(self, error: str) -> None:
        """Queue an error alert."""
        message = (
            f"ERROR\n"
            f"{error}\n"
            f"Time: {datetime.now(timezone.utc).isoformat()}"
        )
        await self._enqueue("ERROR", message)

    async def notify_trade(self, trade: Any, signal: Any) -> None:
        """
        Notify on a completed trade.  Accepts typed objects (with attributes)
        as well as plain dicts.
        """
        trade_dict = trade if isinstance(trade, dict) else _to_dict(trade)
        signal_dict = signal if isinstance(signal, dict) else _to_dict(signal)

        symbol = trade_dict.get("symbol") or getattr(signal, "symbol", "?")
        direction = trade_dict.get("direction") or getattr(signal, "direction", "?")
        quantity = trade_dict.get("quantity", 0.0) or trade_dict.get("filled_qty", 0.0)
        price = trade_dict.get("entry_price", 0.0) or trade_dict.get("avg_fill_price", 0.0)
        conf = signal_dict.get("confidence", 0.0)

        message = (
            f"TRADE\n"
            f"{symbol} {direction}\n"
            f"Size: {quantity:.4f}  Price: {price:.4f}\n"
            f"Confidence: {conf:.2%}\n"
            f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}"
        )
        await self._enqueue("TRADE", message)

    async def notify_warning(self, text: str) -> None:
        """Queue a warning-level notification."""
        message = f"WARNING\n{text}\nTime: {datetime.now(timezone.utc).isoformat()}"
        await self._enqueue("WARNING", message)

    async def notify_critical(self, text: str) -> None:
        """Queue a critical-level notification."""
        message = f"CRITICAL\n{text}\nTime: {datetime.now(timezone.utc).isoformat()}"
        await self._enqueue("CRITICAL", message)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _init_telegram(self) -> None:
        """Attempt to initialise the Telegram bot."""
        token = getattr(self._settings, "telegram_bot_token", "")
        if not token:
            logger.debug("AlertManager: Telegram not configured (no token)")
            return
        try:
            from telegram import Bot  # type: ignore[import]
            self._telegram_bot = Bot(token=token)
            # Smoke-test: get bot info
            bot_info = await self._telegram_bot.get_me()
            logger.info("AlertManager: Telegram bot connected: @%s", bot_info.username)
            self._telegram_available = True
        except ImportError:
            logger.warning(
                "AlertManager: python-telegram-bot not installed — Telegram alerts disabled"
            )
        except Exception as exc:
            logger.warning("AlertManager: Telegram init failed: %s", exc)

    async def _init_discord(self) -> None:
        """Validate the Discord webhook URL."""
        webhook = getattr(self._settings, "discord_webhook_url", "")
        _is_placeholder = not webhook or "your_webhook" in webhook or webhook.endswith("/")
        if webhook and webhook.startswith("https://discord.com/api/webhooks/") and not _is_placeholder:
            self._discord_available = True
            logger.info("AlertManager: Discord webhook configured")
        else:
            logger.debug("AlertManager: Discord not configured (no webhook URL)")

    async def _enqueue(self, level: str, message: str) -> None:
        """Put an alert on the queue, dropping silently if full."""
        alert = {"level": level, "message": message}
        try:
            self._queue.put_nowait(alert)
        except asyncio.QueueFull:
            logger.warning(
                "AlertManager: queue full — dropped %s alert: %s",
                level, message[:80],
            )

    async def _dispatch(self, alert: Dict[str, Any]) -> None:
        """Send a queued alert to all configured providers."""
        level = alert.get("level", "INFO")
        message = alert.get("message", "")

        # Always log it
        log_fn = logger.critical if level == "CRITICAL" else (
            logger.warning if level in ("WARNING", "ERROR") else logger.info
        )
        log_fn("[ALERT/%s] %s", level, message)

        # Telegram
        if self._telegram_available and self._telegram_bot:
            chat_id = getattr(self._settings, "telegram_chat_id", "")
            if chat_id:
                await self._send_telegram(chat_id, message)

        # Discord
        if self._discord_available:
            webhook = getattr(self._settings, "discord_webhook_url", "")
            if webhook:
                await self._send_discord(webhook, level, message)

    async def _send_telegram(self, chat_id: str, message: str) -> None:
        """Send a message to Telegram with retry logic."""
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                await self._telegram_bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode=None,
                )
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    logger.debug(
                        "AlertManager: Telegram send attempt %d failed: %s — retrying",
                        attempt, exc,
                    )
                    await asyncio.sleep(_RETRY_DELAY_S)
                else:
                    logger.warning(
                        "AlertManager: Telegram send failed after %d attempts: %s",
                        _MAX_RETRIES, exc,
                    )

    async def _send_discord(self, webhook_url: str, level: str, message: str) -> None:
        """Send a message to Discord via webhook with retry logic."""
        # Use aiohttp or httpx if available, else skip silently
        payload = {
            "content": f"**[{level}]**\n```\n{message}\n```",
            "username": "NEXUS ALPHA",
        }
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                await self._http_post(webhook_url, payload)
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt < _MAX_RETRIES:
                    logger.debug(
                        "AlertManager: Discord send attempt %d failed: %s — retrying",
                        attempt, exc,
                    )
                    await asyncio.sleep(_RETRY_DELAY_S)
                else:
                    logger.warning(
                        "AlertManager: Discord send failed after %d attempts: %s",
                        _MAX_RETRIES, exc,
                    )

    async def _http_post(self, url: str, payload: Dict[str, Any]) -> None:
        """POST JSON payload to URL using available async HTTP library."""
        try:
            import aiohttp  # type: ignore[import]
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    if resp.status >= 400:
                        text = await resp.text()
                        raise RuntimeError(f"HTTP {resp.status}: {text[:200]}")
            return
        except ImportError:
            pass

        try:
            import httpx  # type: ignore[import]
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = client.post(url, json=payload)
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            return
        except ImportError:
            pass

        logger.debug("AlertManager: no async HTTP library available (aiohttp/httpx) — Discord alert not sent")


def _to_dict(obj: Any) -> Dict[str, Any]:
    """Convert an object with attributes to a dict for uniform handling."""
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {}
