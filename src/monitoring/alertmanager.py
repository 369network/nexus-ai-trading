"""
NEXUS ALPHA — Advanced Alert Manager
======================================
Singleton AlertManager with multi-channel escalation, deduplication,
rate limiting, alert history persistence, and Black Swan formatters.
"""

from __future__ import annotations

import asyncio
import smtplib
import time
from dataclasses import dataclass, field
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from enum import IntEnum
from typing import Any, ClassVar, Dict, List, Optional

import httpx
import structlog

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Alert levels
# ---------------------------------------------------------------------------


class AlertLevel(IntEnum):
    INFO = 0
    WARNING = 1
    CRITICAL = 2
    EMERGENCY = 3


_LEVEL_EMOJI: Dict[AlertLevel, str] = {
    AlertLevel.INFO: "ℹ️",
    AlertLevel.WARNING: "⚠️",
    AlertLevel.CRITICAL: "🚨",
    AlertLevel.EMERGENCY: "🆘",
}

_DISCORD_COLORS: Dict[AlertLevel, int] = {
    AlertLevel.INFO: 0x3498DB,      # Blue
    AlertLevel.WARNING: 0xF39C12,   # Orange
    AlertLevel.CRITICAL: 0xE74C3C,  # Red
    AlertLevel.EMERGENCY: 0x8B0000, # Dark red
}


# ---------------------------------------------------------------------------
# Alert record
# ---------------------------------------------------------------------------


@dataclass
class Alert:
    """A single alert event."""

    alert_id: str
    level: AlertLevel
    title: str
    message: str
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    channels_sent: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Rate limiter (per-channel)
# ---------------------------------------------------------------------------


class _ChannelRateLimiter:
    """
    Sliding-window rate limiter.
    Allows at most ``max_per_minute`` sends per channel per 60-second window.
    """

    def __init__(self, max_per_minute: int = 10) -> None:
        self._max = max_per_minute
        self._windows: Dict[str, List[float]] = {}

    def can_send(self, channel: str) -> bool:
        now = time.monotonic()
        window = self._windows.setdefault(channel, [])
        # Purge old timestamps
        self._windows[channel] = [t for t in window if now - t < 60]
        if len(self._windows[channel]) >= self._max:
            return False
        self._windows[channel].append(now)
        return True


# ---------------------------------------------------------------------------
# Deduplication cache
# ---------------------------------------------------------------------------


class _DedupeCache:
    """Suppress re-alerting the same condition within a cooldown window."""

    def __init__(self, cooldown_seconds: int = 300) -> None:
        self._cooldown = cooldown_seconds
        self._last_seen: Dict[str, float] = {}

    def is_duplicate(self, key: str) -> bool:
        now = time.monotonic()
        last = self._last_seen.get(key, 0.0)
        if now - last < self._cooldown:
            return True
        self._last_seen[key] = now
        return False

    def force_reset(self, key: str) -> None:
        self._last_seen.pop(key, None)


# ---------------------------------------------------------------------------
# AlertManager singleton
# ---------------------------------------------------------------------------


class AlertManager:
    """
    Central alert dispatcher with escalation, deduplication, and rate limiting.

    Escalation rules:
    - INFO     → no external channels (logs only)
    - WARNING  → Telegram only
    - CRITICAL → Telegram + Discord
    - EMERGENCY → Telegram + Discord + Email + PagerDuty

    Usage::

        am = AlertManager.get_instance()
        await am.send(
            level=AlertLevel.CRITICAL,
            title="Circuit Breaker Tripped",
            message="Drawdown exceeded 15% threshold",
            source="risk_manager",
        )
    """

    _instance: ClassVar[Optional["AlertManager"]] = None
    _lock: ClassVar[asyncio.Lock] = asyncio.Lock()

    # Alert history (in-memory ring buffer, max 1000 entries)
    _MAX_HISTORY = 1000

    def __init__(
        self,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        discord_webhook_url: str = "",
        smtp_host: str = "",
        smtp_port: int = 587,
        smtp_user: str = "",
        smtp_password: str = "",
        smtp_to: str = "",
        pagerduty_routing_key: str = "",
        max_alerts_per_minute: int = 10,
        dedup_cooldown_seconds: int = 300,
    ) -> None:
        self._telegram_token = telegram_token
        self._telegram_chat_id = telegram_chat_id
        self._discord_webhook = discord_webhook_url
        self._smtp_host = smtp_host
        self._smtp_port = smtp_port
        self._smtp_user = smtp_user
        self._smtp_password = smtp_password
        self._smtp_to = smtp_to
        self._pagerduty_routing_key = pagerduty_routing_key

        self._rate_limiter = _ChannelRateLimiter(max_per_minute=max_alerts_per_minute)
        self._dedupe = _DedupeCache(cooldown_seconds=dedup_cooldown_seconds)
        self._history: List[Alert] = []
        self._supabase_persist = True

        log.info(
            "alertmanager_initialized",
            telegram=bool(telegram_token),
            discord=bool(discord_webhook_url),
            email=bool(smtp_host),
            pagerduty=bool(pagerduty_routing_key),
        )

    # ------------------------------------------------------------------
    # Singleton factory
    # ------------------------------------------------------------------

    @classmethod
    async def get_instance(cls) -> "AlertManager":
        """Return (or create) the singleton AlertManager from settings."""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls._from_settings()
            return cls._instance

    @classmethod
    def _from_settings(cls) -> "AlertManager":
        """Build AlertManager from application settings."""
        try:
            from src.config import get_settings
            s = get_settings()
            return cls(
                telegram_token=getattr(s, "telegram_bot_token", ""),
                telegram_chat_id=getattr(s, "telegram_chat_id", ""),
                discord_webhook_url=getattr(s, "discord_webhook_url", ""),
                smtp_host=getattr(s, "smtp_host", ""),
                smtp_port=getattr(s, "smtp_port", 587),
                smtp_user=getattr(s, "smtp_user", ""),
                smtp_password=getattr(s, "smtp_password", ""),
                smtp_to=getattr(s, "alert_email_to", ""),
                pagerduty_routing_key=getattr(s, "pagerduty_routing_key", ""),
            )
        except Exception:
            return cls()

    # ------------------------------------------------------------------
    # Public send API
    # ------------------------------------------------------------------

    async def send(
        self,
        level: AlertLevel,
        title: str,
        message: str,
        source: str = "system",
        metadata: Optional[Dict[str, Any]] = None,
        dedupe_key: Optional[str] = None,
        force: bool = False,
    ) -> Alert:
        """
        Send an alert through appropriate channels based on level.

        Args:
            level: Alert severity level.
            title: Short headline.
            message: Detailed message body.
            source: Module/component originating the alert.
            metadata: Extra key-value pairs to include.
            dedupe_key: Deduplication key (default: title).
            force: Bypass deduplication (for emergency re-alerts).

        Returns:
            Alert record with channels_sent populated.
        """
        import uuid

        key = dedupe_key or title
        if not force and self._dedupe.is_duplicate(key):
            log.debug("alertmanager_deduped", key=key, level=level.name)
            # Still return an Alert object so callers don't break
            return Alert(
                alert_id=str(uuid.uuid4())[:8],
                level=level,
                title=title,
                message=message,
                source=source,
                metadata=metadata or {},
            )

        alert = Alert(
            alert_id=str(uuid.uuid4())[:8],
            level=level,
            title=title,
            message=message,
            source=source,
            metadata=metadata or {},
        )

        log.info(
            "alertmanager_sending",
            level=level.name,
            title=title,
            source=source,
            alert_id=alert.alert_id,
        )

        # Dispatch based on level
        tasks: List[asyncio.Task] = []

        if level >= AlertLevel.WARNING:
            tasks.append(asyncio.create_task(self._send_telegram(alert)))

        if level >= AlertLevel.CRITICAL:
            tasks.append(asyncio.create_task(self._send_discord(alert)))

        if level >= AlertLevel.EMERGENCY:
            tasks.append(asyncio.create_task(self._send_email(alert)))
            tasks.append(asyncio.create_task(self._send_pagerduty(alert)))

        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

        # Persist to history
        self._history.append(alert)
        if len(self._history) > self._MAX_HISTORY:
            self._history = self._history[-self._MAX_HISTORY:]

        # Persist to Supabase async (fire-and-forget)
        if self._supabase_persist:
            asyncio.create_task(self._persist_alert(alert))

        return alert

    # ------------------------------------------------------------------
    # Specialized formatters
    # ------------------------------------------------------------------

    async def circuit_breaker_alert(
        self,
        symbol: str,
        reason: str,
        current_value: float,
        threshold: float,
        source: str = "circuit_breaker",
    ) -> Alert:
        """Format and send a circuit breaker trip alert."""
        title = f"Circuit Breaker Triggered: {symbol}"
        message = (
            f"Circuit breaker tripped for {symbol}.\n"
            f"Reason: {reason}\n"
            f"Current value: {current_value:.4f}\n"
            f"Threshold: {threshold:.4f}\n"
            f"All new orders for {symbol} are BLOCKED."
        )
        return await self.send(
            level=AlertLevel.CRITICAL,
            title=title,
            message=message,
            source=source,
            metadata={"symbol": symbol, "reason": reason, "value": current_value, "threshold": threshold},
            dedupe_key=f"circuit_breaker_{symbol}",
        )

    async def drawdown_alert(
        self,
        current_drawdown_pct: float,
        threshold_pct: float,
        equity: float,
        peak_equity: float,
        source: str = "risk_manager",
    ) -> Alert:
        """Format and send a portfolio drawdown alert."""
        level = AlertLevel.CRITICAL if current_drawdown_pct >= threshold_pct * 1.5 else AlertLevel.WARNING
        title = f"Portfolio Drawdown Alert: {current_drawdown_pct:.1f}%"
        message = (
            f"Portfolio has drawn down {current_drawdown_pct:.2f}% from peak.\n"
            f"Current equity: ${equity:,.2f}\n"
            f"Peak equity: ${peak_equity:,.2f}\n"
            f"Threshold: {threshold_pct:.1f}%\n"
            f"Action: {'EMERGENCY STOP recommended' if level == AlertLevel.CRITICAL else 'Reduce position sizing'}"
        )
        return await self.send(
            level=level,
            title=title,
            message=message,
            source=source,
            metadata={
                "drawdown_pct": current_drawdown_pct,
                "threshold_pct": threshold_pct,
                "equity": equity,
                "peak_equity": peak_equity,
            },
            dedupe_key=f"drawdown_{int(current_drawdown_pct)}",
        )

    async def black_swan_alert(
        self,
        symbol: str,
        price_change_pct: float,
        volume_spike_factor: float,
        source: str = "black_swan_detector",
    ) -> Alert:
        """Format and send a black swan / extreme market move alert."""
        title = f"BLACK SWAN DETECTED: {symbol} {price_change_pct:+.1f}%"
        message = (
            f"Extreme market event detected for {symbol}.\n"
            f"Price change: {price_change_pct:+.2f}% (>{abs(price_change_pct):.0f} sigma move)\n"
            f"Volume spike: {volume_spike_factor:.1f}x normal\n"
            f"ACTION REQUIRED: Review all open positions immediately.\n"
            f"System has activated emergency risk protocols."
        )
        return await self.send(
            level=AlertLevel.EMERGENCY,
            title=title,
            message=message,
            source=source,
            metadata={
                "symbol": symbol,
                "price_change_pct": price_change_pct,
                "volume_spike": volume_spike_factor,
            },
            dedupe_key=f"black_swan_{symbol}",
            force=True,  # Always send black swan alerts
        )

    # ------------------------------------------------------------------
    # History / inspection
    # ------------------------------------------------------------------

    def get_history(
        self,
        level_min: AlertLevel = AlertLevel.INFO,
        limit: int = 100,
    ) -> List[Alert]:
        """Return recent alert history filtered by minimum level."""
        filtered = [a for a in self._history if a.level >= level_min]
        return filtered[-limit:]

    def get_stats(self) -> Dict[str, Any]:
        """Return alert counts by level."""
        return {
            "total": len(self._history),
            "info": sum(1 for a in self._history if a.level == AlertLevel.INFO),
            "warning": sum(1 for a in self._history if a.level == AlertLevel.WARNING),
            "critical": sum(1 for a in self._history if a.level == AlertLevel.CRITICAL),
            "emergency": sum(1 for a in self._history if a.level == AlertLevel.EMERGENCY),
        }

    # ------------------------------------------------------------------
    # Channel senders
    # ------------------------------------------------------------------

    async def _send_telegram(self, alert: Alert) -> None:
        if not self._telegram_token or not self._telegram_chat_id:
            return
        if not self._rate_limiter.can_send("telegram"):
            log.warning("alertmanager_telegram_rate_limited", title=alert.title)
            return

        emoji = _LEVEL_EMOJI[alert.level]
        text = (
            f"{emoji} *{alert.level.name}* — {alert.title}\n\n"
            f"{alert.message}\n\n"
            f"_Source: {alert.source} | ID: {alert.alert_id}_"
        )

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    f"https://api.telegram.org/bot{self._telegram_token}/sendMessage",
                    json={
                        "chat_id": self._telegram_chat_id,
                        "text": text,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                )
                resp.raise_for_status()
                alert.channels_sent.append("telegram")
                log.debug("alertmanager_telegram_sent", alert_id=alert.alert_id)
        except Exception as exc:
            log.warning("alertmanager_telegram_error", alert_id=alert.alert_id, error=str(exc))

    async def _send_discord(self, alert: Alert) -> None:
        if not self._discord_webhook:
            return
        if not self._rate_limiter.can_send("discord"):
            log.warning("alertmanager_discord_rate_limited", title=alert.title)
            return

        embed = {
            "title": f"{_LEVEL_EMOJI[alert.level]} {alert.title}",
            "description": alert.message,
            "color": _DISCORD_COLORS[alert.level],
            "footer": {"text": f"Source: {alert.source} | ID: {alert.alert_id}"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(alert.timestamp)),
            "fields": [
                {"name": k, "value": str(v), "inline": True}
                for k, v in list(alert.metadata.items())[:6]
            ],
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    self._discord_webhook,
                    json={"embeds": [embed]},
                )
                resp.raise_for_status()
                alert.channels_sent.append("discord")
                log.debug("alertmanager_discord_sent", alert_id=alert.alert_id)
        except Exception as exc:
            log.warning("alertmanager_discord_error", alert_id=alert.alert_id, error=str(exc))

    async def _send_email(self, alert: Alert) -> None:
        if not self._smtp_host or not self._smtp_to:
            return
        if not self._rate_limiter.can_send("email"):
            log.warning("alertmanager_email_rate_limited", title=alert.title)
            return

        subject = f"[NEXUS ALPHA {alert.level.name}] {alert.title}"
        body = (
            f"NEXUS ALPHA Alert — {alert.level.name}\n"
            f"{'=' * 50}\n\n"
            f"{alert.message}\n\n"
            f"Source: {alert.source}\n"
            f"Alert ID: {alert.alert_id}\n"
            f"Timestamp: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime(alert.timestamp))}\n"
        )
        if alert.metadata:
            body += "\nMetadata:\n"
            for k, v in alert.metadata.items():
                body += f"  {k}: {v}\n"

        try:
            msg = MIMEMultipart()
            msg["From"] = self._smtp_user
            msg["To"] = self._smtp_to
            msg["Subject"] = subject
            msg.attach(MIMEText(body, "plain"))

            await asyncio.to_thread(self._send_email_sync, msg)
            alert.channels_sent.append("email")
            log.debug("alertmanager_email_sent", alert_id=alert.alert_id)
        except Exception as exc:
            log.warning("alertmanager_email_error", alert_id=alert.alert_id, error=str(exc))

    def _send_email_sync(self, msg: MIMEMultipart) -> None:
        with smtplib.SMTP(self._smtp_host, self._smtp_port) as server:
            server.starttls()
            if self._smtp_user and self._smtp_password:
                server.login(self._smtp_user, self._smtp_password)
            server.send_message(msg)

    async def _send_pagerduty(self, alert: Alert) -> None:
        if not self._pagerduty_routing_key:
            return
        if not self._rate_limiter.can_send("pagerduty"):
            log.warning("alertmanager_pagerduty_rate_limited", title=alert.title)
            return

        payload = {
            "routing_key": self._pagerduty_routing_key,
            "event_action": "trigger",
            "payload": {
                "summary": f"[NEXUS ALPHA] {alert.title}",
                "severity": "critical",
                "source": f"nexus-alpha/{alert.source}",
                "component": alert.source,
                "group": "trading-system",
                "custom_details": {
                    "message": alert.message,
                    "alert_id": alert.alert_id,
                    "level": alert.level.name,
                    **alert.metadata,
                },
            },
            "dedup_key": f"nexus-alpha-{alert.alert_id}",
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(
                    "https://events.pagerduty.com/v2/enqueue",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                resp.raise_for_status()
                alert.channels_sent.append("pagerduty")
                log.info("alertmanager_pagerduty_sent", alert_id=alert.alert_id)
        except Exception as exc:
            log.warning("alertmanager_pagerduty_error", alert_id=alert.alert_id, error=str(exc))

    # ------------------------------------------------------------------
    # Supabase persistence
    # ------------------------------------------------------------------

    async def _persist_alert(self, alert: Alert) -> None:
        """Store alert to Supabase alert_history table (fire-and-forget)."""
        try:
            from src.db.supabase_client import SupabaseClient
            from src.config import get_settings

            settings = get_settings()
            client = await SupabaseClient.get_instance(
                url=settings.supabase_url,
                key=settings.supabase_service_key,
            )

            row = {
                "alert_id": alert.alert_id,
                "level": alert.level.name,
                "title": alert.title,
                "message": alert.message,
                "source": alert.source,
                "metadata": alert.metadata,
                "channels_sent": alert.channels_sent,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(alert.timestamp)),
            }
            await asyncio.to_thread(
                lambda: client._client.table("alert_history").insert(row).execute()  # type: ignore[union-attr]
            )
        except Exception:
            pass  # Best-effort; don't let persistence errors block alerts
