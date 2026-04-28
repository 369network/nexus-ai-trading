"""
NEXUS ALPHA — Telegram Alert Notifications
==========================================
Sends real-time trading alerts to a Telegram bot.

Usage:
    from src.monitoring.telegram_alerts import TelegramAlerter

    alerter = TelegramAlerter()   # reads TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID from env
    await alerter.send_trade_alert(trade)
    await alerter.send_signal_alert(signal)
    await alerter.send_daily_summary(portfolio)
    await alerter.send_circuit_breaker(market, reason)
    await alerter.send_emergency_stop()
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


class TelegramAlerter:
    """Async Telegram notification sender for NEXUS ALPHA."""

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        self._token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = chat_id or os.getenv("TELEGRAM_CHAT_ID", "")
        self._enabled = bool(self._token and self._chat_id)
        if not self._enabled:
            logger.info(
                "[Telegram] Disabled — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID to enable."
            )

    @property
    def enabled(self) -> bool:
        return self._enabled

    # ─── Core send ────────────────────────────────────────────────────────────

    async def _send(self, text: str, parse_mode: str = "Markdown") -> bool:
        """Send a raw message. Returns True on success."""
        if not self._enabled:
            return False
        url = TELEGRAM_API.format(token=self._token)
        payload = {
            "chat_id": self._chat_id,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        try:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=10)
            ) as session:
                async with session.post(url, json=payload) as resp:
                    data = await resp.json()
                    if not data.get("ok"):
                        logger.warning(
                            "[Telegram] API error: %s", data.get("description", "unknown")
                        )
                        return False
                    return True
        except asyncio.TimeoutError:
            logger.warning("[Telegram] Send timed out")
            return False
        except Exception as exc:  # noqa: BLE001
            logger.warning("[Telegram] Send failed: %s", exc)
            return False

    # ─── Alert helpers ────────────────────────────────────────────────────────

    def _ts(self) -> str:
        return datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    async def send_trade_alert(self, trade: Dict[str, Any]) -> None:
        """Notify on trade open or close."""
        symbol = trade.get("symbol", "?")
        direction = trade.get("direction", "?")
        entry = trade.get("entry_price", 0.0)
        status = trade.get("status", "OPEN")
        pnl = trade.get("pnl", None)
        strategy = trade.get("strategy", "")

        if status == "OPEN":
            icon = "🟢" if direction == "LONG" else "🔴"
            text = (
                f"{icon} *Trade Opened* — {symbol}\n"
                f"Direction: `{direction}`\n"
                f"Entry: `${entry:,.4f}`\n"
                f"Strategy: {strategy}\n"
                f"_{self._ts()}_"
            )
        else:
            pnl_icon = "✅" if (pnl or 0) >= 0 else "❌"
            pnl_str = f"+${pnl:,.2f}" if (pnl or 0) >= 0 else f"-${abs(pnl or 0):,.2f}"
            exit_price = trade.get("exit_price", 0.0)
            text = (
                f"{pnl_icon} *Trade Closed* — {symbol}\n"
                f"Direction: `{direction}` | P&L: `{pnl_str}`\n"
                f"Entry: `${entry:,.4f}` → Exit: `${exit_price:,.4f}`\n"
                f"Strategy: {strategy}\n"
                f"_{self._ts()}_"
            )
        await self._send(text)

    async def send_signal_alert(
        self, signal: Dict[str, Any], min_strength: int = 75
    ) -> None:
        """Notify on strong signal. Only sends if strength >= min_strength."""
        strength = signal.get("strength", 0)
        if strength < min_strength:
            return

        symbol = signal.get("symbol", "?")
        direction = signal.get("direction", "?")
        market = signal.get("market", "?")
        icon = "🚀" if direction == "LONG" else "🔻"
        text = (
            f"{icon} *Signal* — {symbol} ({market})\n"
            f"Direction: `{direction}` | Strength: `{strength}/100`\n"
            f"_{self._ts()}_"
        )
        await self._send(text)

    async def send_circuit_breaker(
        self, market: str, reason: str, details: str = ""
    ) -> None:
        """Notify on circuit breaker trip."""
        text = (
            f"⚡ *Circuit Breaker Tripped*\n"
            f"Market: `{market}`\n"
            f"Reason: {reason}\n"
            + (f"Details: _{details}_\n" if details else "")
            + f"_{self._ts()}_"
        )
        await self._send(text)

    async def send_emergency_stop(self) -> None:
        """Notify on emergency stop."""
        text = (
            f"🛑 *EMERGENCY STOP TRIGGERED*\n"
            f"All positions closed. Bot halted.\n"
            f"_{self._ts()}_"
        )
        await self._send(text)

    async def send_drawdown_alert(
        self, drawdown_pct: float, portfolio_value: float
    ) -> None:
        """Notify on significant drawdown."""
        text = (
            f"⚠️ *Drawdown Alert*\n"
            f"Current Drawdown: `{drawdown_pct:.1f}%`\n"
            f"Portfolio Value: `${portfolio_value:,.2f}`\n"
            f"_{self._ts()}_"
        )
        await self._send(text)

    async def send_daily_summary(self, portfolio: Dict[str, Any]) -> None:
        """Send end-of-day summary."""
        equity = portfolio.get("equity", 0.0)
        daily_pnl = portfolio.get("daily_pnl", 0.0)
        daily_pnl_pct = portfolio.get("daily_pnl_pct", 0.0)
        total_trades = portfolio.get("total_trades_today", 0)
        win_rate = portfolio.get("win_rate_today", 0.0)
        drawdown = portfolio.get("current_drawdown_pct", 0.0)

        pnl_icon = "✅" if daily_pnl >= 0 else "❌"
        pnl_str = f"+${daily_pnl:,.2f} (+{daily_pnl_pct:.2f}%)" if daily_pnl >= 0 else f"-${abs(daily_pnl):,.2f} ({daily_pnl_pct:.2f}%)"

        text = (
            f"📊 *Daily Summary — NEXUS ALPHA*\n\n"
            f"{pnl_icon} Day P&L: `{pnl_str}`\n"
            f"💰 Equity: `${equity:,.2f}`\n"
            f"📈 Trades Today: `{total_trades}` | Win Rate: `{win_rate:.1f}%`\n"
            f"📉 Max Drawdown: `{drawdown:.1f}%`\n\n"
            f"_{self._ts()}_"
        )
        await self._send(text)

    async def send_test(self) -> bool:
        """Send a connectivity test message. Returns True on success."""
        text = (
            f"🤖 *NEXUS ALPHA* — Test Notification\n\n"
            f"✅ Telegram integration is working correctly.\n"
            f"_{self._ts()}_"
        )
        return await self._send(text)


# ─── Singleton ────────────────────────────────────────────────────────────────

_ALERTER: Optional[TelegramAlerter] = None


def get_alerter() -> TelegramAlerter:
    """Return the global TelegramAlerter singleton."""
    global _ALERTER
    if _ALERTER is None:
        _ALERTER = TelegramAlerter()
    return _ALERTER
