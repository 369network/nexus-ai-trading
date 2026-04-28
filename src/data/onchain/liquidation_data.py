"""
NEXUS ALPHA - Liquidation Monitor
=====================================
Streams and analyses perpetual swap liquidation events from Binance.

A liquidation cascade occurs when a large wave of stop-outs forces
the price further in one direction, triggering more liquidations.

Environment variables:
    None required (public Binance WebSocket stream).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)

_BINANCE_FUTURES_WS = "wss://fstream.binance.com/ws"
_RECONNECT_INITIAL  = 1.0
_RECONNECT_CAP      = 60.0
_RECONNECT_MULT     = 2.0

# Window for cascade detection (seconds)
_CASCADE_WINDOW_S = 60
# USD threshold per second to consider a cascade is underway
_CASCADE_THRESHOLD_USD_PER_S = 5_000_000  # $5M per minute


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class LiquidationEvent:
    """A single perpetual swap liquidation order."""

    symbol:    str
    side:      str          # "BUY" (short squeeze) or "SELL" (long liquidation)
    quantity:  float
    price:     float
    amount_usd: float
    timestamp: float        # Unix seconds


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class LiquidationMonitor:
    """
    Streams Binance liquidation orders via WebSocket and provides
    cascade risk scoring.
    """

    def __init__(self) -> None:
        self._running        = False
        self._ws_task: Optional[asyncio.Task] = None
        self._callbacks: List[Callable[[LiquidationEvent], None]] = []

        # Rolling window of recent liquidations for cascade detection
        # Deque stores (timestamp, amount_usd) tuples
        self._recent: Deque[tuple] = deque(maxlen=10_000)

        # Per-symbol rolling liquidation USD (last 60s)
        self._symbol_usd: Dict[str, Deque] = {}

    # ------------------------------------------------------------------

    async def stream_liquidations(
        self,
        callback: Callable[[LiquidationEvent], None],
        symbols: Optional[List[str]] = None,
    ) -> None:
        """
        Start streaming liquidation events.

        Spawns a background WebSocket task; returns immediately.
        All symbols are streamed unless *symbols* is specified.

        Parameters
        ----------
        callback:
            Called with each :class:`LiquidationEvent`.
        symbols:
            Optional list of symbols to subscribe to (e.g. ``["BTCUSDT"]``).
            If None, subscribes to ``!forceOrder@arr`` (all symbols).
        """
        self._callbacks.append(callback)
        if self._running:
            return  # already streaming

        self._running = True
        stream = "!forceOrder@arr"
        if symbols:
            # Multi-stream format
            stream = "/".join(
                f"{s.lower()}@forceOrder" for s in symbols
            )

        self._ws_task = asyncio.create_task(
            self._ws_loop(stream),
            name="binance-liquidation-stream",
        )

    # ------------------------------------------------------------------

    async def _ws_loop(self, stream: str) -> None:
        """Outer reconnect loop for the liquidation stream."""
        delay = _RECONNECT_INITIAL
        url = f"{_BINANCE_FUTURES_WS}/{stream}"

        while self._running:
            try:
                async with websockets.connect(url, ping_interval=20) as ws:
                    delay = _RECONNECT_INITIAL
                    logger.info("LiquidationMonitor: connected to %s", url[:80])

                    async for raw in ws:
                        if not self._running:
                            return
                        try:
                            payload = json.loads(raw)
                            self._handle_message(payload)
                        except json.JSONDecodeError:
                            continue

            except asyncio.CancelledError:
                return
            except (ConnectionClosed, WebSocketException, OSError) as exc:
                logger.warning(
                    "LiquidationMonitor: WS error: %s – retrying in %.1fs", exc, delay
                )
            except Exception as exc:
                logger.exception("LiquidationMonitor: unexpected: %s", exc)

            if not self._running:
                return
            await asyncio.sleep(delay)
            delay = min(delay * _RECONNECT_MULT, _RECONNECT_CAP)

    # ------------------------------------------------------------------

    def _handle_message(self, payload: Dict[str, Any]) -> None:
        """Parse a raw liquidation WebSocket message."""
        # Binance wraps the event under "o" key
        data = payload.get("o", payload)

        symbol   = data.get("s", "")
        side     = data.get("S", "")           # "BUY" or "SELL"
        quantity = float(data.get("q", 0))
        price    = float(data.get("ap", 0))    # average price
        ts_ms    = int(data.get("T", 0))
        ts       = ts_ms / 1_000.0 if ts_ms > 1e9 else time.time()

        amount_usd = quantity * price

        event = LiquidationEvent(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            amount_usd=amount_usd,
            timestamp=ts,
        )

        # Record for cascade detection
        self._recent.append((ts, amount_usd))
        self._symbol_usd.setdefault(symbol, deque(maxlen=1_000)).append(
            (ts, amount_usd)
        )

        # Dispatch to callbacks
        for cb in self._callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(event))
                else:
                    cb(event)
            except Exception as exc:
                logger.exception("Liquidation callback error: %s", exc)

    # ------------------------------------------------------------------

    def get_liquidation_cascade_risk(
        self,
        symbol: Optional[str] = None,
    ) -> float:
        """
        Return a cascade risk score in [0, 1].

        The score is computed as the ratio of liquidated USD in the last
        60 seconds to the :data:`_CASCADE_THRESHOLD_USD_PER_S` × 60.

        Parameters
        ----------
        symbol:
            If provided, compute risk only for this symbol.
            If None, compute across all symbols.

        Returns
        -------
        float
            Risk score in [0.0, 1.0].  Values > 0.7 indicate cascade risk.
        """
        now = time.time()
        cutoff = now - _CASCADE_WINDOW_S

        if symbol:
            recent_entries = list(self._symbol_usd.get(symbol, []))
        else:
            recent_entries = list(self._recent)

        # Filter to the window
        window_usd = sum(
            usd for ts, usd in recent_entries if ts >= cutoff
        )

        threshold_usd = _CASCADE_THRESHOLD_USD_PER_S * (_CASCADE_WINDOW_S / 60.0)
        score = min(window_usd / threshold_usd, 1.0) if threshold_usd > 0 else 0.0

        if score > 0.7:
            logger.warning(
                "CASCADE RISK for %s: score=%.2f (%.0f USD in last %ds)",
                symbol or "ALL", score, window_usd, _CASCADE_WINDOW_S,
            )

        return score

    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Stop streaming and clean up."""
        self._running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
