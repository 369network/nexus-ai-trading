"""
NEXUS ALPHA — Alert Manager
============================
Sends structured alerts to Telegram and Discord.
Provides trade notification formatting and alert rate limiting
(max 1 alert per minute per alert type).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import httpx

from src.utils.logging import get_logger

log = get_logger(__name__)


class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Level → emoji + formatting
# ---------------------------------------------------------------------------

_LEVEL_EMOJI: dict[AlertLevel, str] = {
    AlertLevel.INFO: "ℹ️",
    AlertLevel.WARNING: "⚠️",
    AlertLevel.CRITICAL: "🚨",
}

_LEVEL_COLOR: dict[AlertLevel, int] = {
    AlertLevel.INFO: 0x3498DB,      # Blue
    AlertLevel.WARNING: 0xF39C12,  # Orange
    AlertLevel.CRITICAL: 0xE74C3C, # Red
}


# ---------------------------------------------------------------------------
# Alert rate limiter (per-type, not per-message)
# ---------------------------------------------------------------------------


class _AlertRateLimiter:
    """Simple in-memory rate limiter: max 1 alert per type per window."""

    def __init__(self, window_seconds: int = 60) -> None:
        self._last_sent: dict[str, float] = {}
        self._window = window_seconds

    def should_send(self, alert_key: str) -> bool:
        """Return True if this alert type can be sent now."""
        now = time.monotonic()
        last = self._last_sent.get(alert_key, 0.0)
        if now - last >= self._window:
            self._last_sent[alert_key] = now
            return True
        return False

    def force_allow(self, alert_key: str) -> None:
        """Reset rate limiter for a specific key (e.g., for critical alerts)."""
        self._last_sent.pop(alert_key, None)


# ---------------------------------------------------------------------------
# Notification data models
# ---------------------------------------------------------------------------


@dataclass
class TradeNotification:
    """Structured data for a trade alert."""

    event: str          # entry | exit | stop_hit | liquidation
    symbol: str
    market: str         # crypto | forex | commodities | indian_stocks | us_stocks
    side: str           # long | short
    entry_price: float
    current_price: float | None = None
    exit_price: float | None = None
    size: float = 0.0
    pnl_usd: float | None = None
    pnl_pct: float | None = None
    stop_loss: float | None = None
    take_profit: float | None = None
    strategy: str = ""
    trade_id: str = ""
    reason: str = ""        # Why trade was taken / exited
    holding_hours: float | None = None


@dataclass
class RiskNotification:
    """Structured data for a risk event alert."""

    event: str              # circuit_breaker | drawdown | position_limit | correlation
    circuit_breaker: str = ""
    drawdown_pct: float | None = None
    affected_market: str = ""
    action_taken: str = ""
    details: str = ""


# ---------------------------------------------------------------------------
# Alert Manager
# ---------------------------------------------------------------------------


class AlertManager:
    """
    Sends alerts to Telegram and Discord with rate limiting.

    Args:
        telegram_token: Telegram bot token. If empty, Telegram is disabled.
        telegram_chat_id: Target chat ID for Telegram messages.
        discord_webhook_url: Discord webhook URL. If empty, Discord is disabled.
        rate_limit_seconds: Minimum seconds between same-type alerts.
        http_timeout: HTTP request timeout in seconds.
    """

    def __init__(
        self,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        discord_webhook_url: str = "",
        rate_limit_seconds: int = 60,
        http_timeout: float = 10.0,
    ) -> None:
        self._telegram_token = telegram_token
        self._telegram_chat_id = telegram_chat_id
        self._discord_webhook_url = discord_webhook_url
        self._rate_limiter = _AlertRateLimiter(window_seconds=rate_limit_seconds)
        self._http_timeout = http_timeout
        self._telegram_enabled = bool(telegram_token) and bool(telegram_chat_id)
        self._discord_enabled = bool(discord_webhook_url)

        if not self._telegram_enabled:
            log.warning("Telegram alerts disabled (token or chat_id missing)")
        if not self._discord_enabled:
            log.warning("Discord alerts disabled (webhook_url missing)")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def send_info(
        self, message: str, title: str = "Info", alert_key: str | None = None
    ) -> None:
        """Send an informational alert."""
        await self._dispatch(
            message=message,
            title=title,
            level=AlertLevel.INFO,
            alert_key=alert_key or f"info:{title}",
        )

    async def send_warning(
        self, message: str, title: str = "Warning", alert_key: str | None = None
    ) -> None:
        """Send a warning alert."""
        await self._dispatch(
            message=message,
            title=title,
            level=AlertLevel.WARNING,
            alert_key=alert_key or f"warning:{title}",
        )

    async def send_critical(
        self,
        message: str,
        title: str = "Critical",
        alert_key: str | None = None,
        bypass_rate_limit: bool = True,
    ) -> None:
        """Send a critical alert. Bypasses rate limiting by default."""
        key = alert_key or f"critical:{title}"
        if bypass_rate_limit:
            self._rate_limiter.force_allow(key)
        await self._dispatch(
            message=message,
            title=title,
            level=AlertLevel.CRITICAL,
            alert_key=key,
        )

    async def send_trade_entry(self, trade: TradeNotification) -> None:
        """Format and send a trade entry notification."""
        title = f"Trade Entry — {trade.symbol}"
        msg = self._format_trade_entry(trade)
        await self._dispatch(
            message=msg,
            title=title,
            level=AlertLevel.INFO,
            alert_key=f"entry:{trade.trade_id}",
            bypass_rate_limit=True,   # Always send trade entries
        )

    async def send_trade_exit(self, trade: TradeNotification) -> None:
        """Format and send a trade exit notification."""
        level = (
            AlertLevel.WARNING
            if (trade.pnl_usd is not None and trade.pnl_usd < 0)
            else AlertLevel.INFO
        )
        title = f"Trade Exit — {trade.symbol}"
        msg = self._format_trade_exit(trade)
        await self._dispatch(
            message=msg,
            title=title,
            level=level,
            alert_key=f"exit:{trade.trade_id}",
            bypass_rate_limit=True,
        )

    async def send_stop_hit(self, trade: TradeNotification) -> None:
        """Format and send a stop-loss hit notification."""
        title = f"Stop Loss Hit — {trade.symbol}"
        msg = self._format_stop_hit(trade)
        await self._dispatch(
            message=msg,
            title=title,
            level=AlertLevel.WARNING,
            alert_key=f"stop:{trade.trade_id}",
            bypass_rate_limit=True,
        )

    async def send_circuit_breaker(self, risk: RiskNotification) -> None:
        """Send a circuit breaker triggered alert."""
        title = f"Circuit Breaker — {risk.circuit_breaker}"
        msg = self._format_risk_event(risk)
        await self._dispatch(
            message=msg,
            title=title,
            level=AlertLevel.CRITICAL,
            alert_key=f"cb:{risk.circuit_breaker}",
            bypass_rate_limit=True,
        )

    async def send_daily_pnl(
        self,
        total_pnl_usd: float,
        total_pnl_pct: float,
        trades_count: int,
        win_rate_pct: float,
    ) -> None:
        """Send end-of-day P&L summary."""
        emoji = "📈" if total_pnl_usd >= 0 else "📉"
        sign = "+" if total_pnl_usd >= 0 else ""
        msg = (
            f"{emoji} Daily Summary\n\n"
            f"P&L: {sign}${total_pnl_usd:,.2f} ({sign}{total_pnl_pct:.2f}%)\n"
            f"Trades: {trades_count}\n"
            f"Win Rate: {win_rate_pct:.1f}%"
        )
        await self._dispatch(
            message=msg,
            title="Daily P&L Summary",
            level=AlertLevel.INFO,
            alert_key="daily_pnl",
            bypass_rate_limit=True,
        )

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    def _format_trade_entry(self, t: TradeNotification) -> str:
        side_arrow = "↑ LONG" if t.side == "long" else "↓ SHORT"
        lines = [
            f"{side_arrow} {t.symbol} ({t.market.upper()})",
            f"Entry: ${t.entry_price:,.5g}",
            f"Size: {t.size}",
        ]
        if t.stop_loss:
            lines.append(f"Stop: ${t.stop_loss:,.5g}")
        if t.take_profit:
            lines.append(f"Target: ${t.take_profit:,.5g}")
        if t.strategy:
            lines.append(f"Strategy: {t.strategy}")
        if t.reason:
            lines.append(f"Reason: {t.reason}")
        if t.trade_id:
            lines.append(f"ID: {t.trade_id}")
        return "\n".join(lines)

    def _format_trade_exit(self, t: TradeNotification) -> str:
        pnl_str = ""
        if t.pnl_usd is not None:
            sign = "+" if t.pnl_usd >= 0 else ""
            pnl_str = f"{sign}${t.pnl_usd:,.2f}"
            if t.pnl_pct is not None:
                pnl_str += f" ({sign}{t.pnl_pct:.2f}%)"
        exit_price = t.exit_price or t.current_price or 0
        lines = [
            f"{'✅' if (t.pnl_usd or 0) >= 0 else '❌'} {t.symbol} EXIT",
            f"Side: {t.side.upper()}",
            f"Entry: ${t.entry_price:,.5g} → Exit: ${exit_price:,.5g}",
        ]
        if pnl_str:
            lines.append(f"P&L: {pnl_str}")
        if t.holding_hours is not None:
            lines.append(f"Held: {t.holding_hours:.1f}h")
        if t.reason:
            lines.append(f"Reason: {t.reason}")
        return "\n".join(lines)

    def _format_stop_hit(self, t: TradeNotification) -> str:
        pnl_str = ""
        if t.pnl_usd is not None:
            pnl_str = f"Loss: -${abs(t.pnl_usd):,.2f}"
            if t.pnl_pct is not None:
                pnl_str += f" (-{abs(t.pnl_pct):.2f}%)"
        lines = [
            f"🛑 STOP HIT — {t.symbol}",
            f"Side: {t.side.upper()}",
            f"Entry: ${t.entry_price:,.5g}",
            f"Stop: ${t.stop_loss:,.5g}" if t.stop_loss else "",
        ]
        if pnl_str:
            lines.append(pnl_str)
        return "\n".join(l for l in lines if l)

    def _format_risk_event(self, r: RiskNotification) -> str:
        lines = [f"Circuit Breaker: {r.circuit_breaker}"]
        if r.affected_market:
            lines.append(f"Market: {r.affected_market}")
        if r.drawdown_pct is not None:
            lines.append(f"Drawdown: {r.drawdown_pct:.2f}%")
        if r.action_taken:
            lines.append(f"Action: {r.action_taken}")
        if r.details:
            lines.append(f"Details: {r.details}")
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Dispatch internals
    # ------------------------------------------------------------------

    async def _dispatch(
        self,
        message: str,
        title: str,
        level: AlertLevel,
        alert_key: str,
        bypass_rate_limit: bool = False,
    ) -> None:
        """Rate-limit and send to all configured channels concurrently."""
        if not bypass_rate_limit and not self._rate_limiter.should_send(alert_key):
            log.debug("Alert rate limited", key=alert_key, level=level.value)
            return

        tasks = []
        if self._telegram_enabled:
            tasks.append(self._send_telegram(message, title, level))
        if self._discord_enabled:
            tasks.append(self._send_discord(message, title, level))

        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    log.error("Alert delivery failed", error=str(result))

    async def _send_telegram(
        self, message: str, title: str, level: AlertLevel
    ) -> None:
        emoji = _LEVEL_EMOJI[level]
        full_text = f"{emoji} <b>{title}</b>\n\n{message}"
        url = f"https://api.telegram.org/bot{self._telegram_token}/sendMessage"
        payload = {
            "chat_id": self._telegram_chat_id,
            "text": full_text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()

    async def _send_discord(
        self, message: str, title: str, level: AlertLevel
    ) -> None:
        color = _LEVEL_COLOR[level]
        emoji = _LEVEL_EMOJI[level]
        payload = {
            "embeds": [
                {
                    "title": f"{emoji} {title}",
                    "description": message,
                    "color": color,
                }
            ]
        }
        async with httpx.AsyncClient(timeout=self._http_timeout) as client:
            resp = await client.post(self._discord_webhook_url, json=payload)
            resp.raise_for_status()


# ---------------------------------------------------------------------------
# Module-level factory
# ---------------------------------------------------------------------------


def create_alert_manager_from_settings() -> AlertManager:
    """Build an AlertManager from application settings."""
    from config.settings import get_settings
    s = get_settings()
    return AlertManager(
        telegram_token=s.alerts.telegram.bot_token.get_secret_value(),
        telegram_chat_id=s.alerts.telegram.chat_id,
        discord_webhook_url=s.alerts.discord.webhook_url.get_secret_value(),
        rate_limit_seconds=s.alerts.rate_limit_seconds,
    )
