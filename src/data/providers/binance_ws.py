"""
NEXUS ALPHA - Binance WebSocket Manager
=========================================
Manages multiple persistent Binance WebSocket connections, dispatching
stream messages to registered callbacks with automatic reconnect.

Supports both Spot (wss://stream.binance.com:9443) and Futures
(wss://fstream.binance.com) combined stream endpoints.

Usage
-----
::

    cfg = BinanceWSConfig(api_key="...", api_secret="...")
    ws = BinanceWebSocketManager(cfg)
    ws.on("btcusdt@kline_1m", my_callback)
    await ws.connect(["btcusdt@kline_1m", "ethusdt@trade"])
    # ... later
    await ws.close()
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SPOT_WS_BASE     = "wss://stream.binance.com:9443/stream"
_FUTURES_WS_BASE  = "wss://fstream.binance.com/stream"
_MAX_STREAMS_PER_CONN = 200       # Binance hard limit
_STALE_TIMEOUT_S  = 60.0          # no message in 60s → reconnect
_PING_INTERVAL_S  = 20.0          # keepalive ping cadence

StreamCallback = Callable[[str, Dict[str, Any]], None]


# ---------------------------------------------------------------------------
# Config dataclass
# ---------------------------------------------------------------------------

@dataclass
class BinanceWSConfig:
    """Configuration for the Binance WebSocket Manager."""

    api_key: str = ""
    api_secret: str = ""

    # Market type: "spot" or "futures"
    market: str = "spot"

    # Reconnect back-off: initial, cap, jitter
    reconnect_delay_initial: float = 1.0
    reconnect_delay_cap: float     = 60.0
    reconnect_delay_multiplier: float = 2.0

    # How long to wait (s) for a graceful close
    close_timeout: float = 5.0

    # Maximum connections this manager may open simultaneously
    max_connections: int = 10

    # Extra websockets kwargs forwarded to connect()
    ws_kwargs: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal connection handle
# ---------------------------------------------------------------------------

class _Connection:
    """
    Owns one WebSocket connection carrying up to MAX_STREAMS_PER_CONN streams.
    """

    def __init__(
        self,
        conn_id: int,
        streams: List[str],
        ws_url: str,
        config: BinanceWSConfig,
        dispatch: Callable[[str, Dict[str, Any]], None],
    ) -> None:
        self.conn_id      = conn_id
        self.streams      = list(streams)
        self.ws_url       = ws_url
        self.config       = config
        self._dispatch    = dispatch
        self._ws: Optional[Any] = None
        self._running     = False
        self._task: Optional[asyncio.Task] = None

        # Stats
        self.messages_received = 0
        self.reconnections     = 0
        self.errors            = 0
        self._last_message_ts  = time.monotonic()

    # ------------------------------------------------------------------

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(
            self._maintain_connection(),
            name=f"binance-ws-conn-{self.conn_id}",
        )

    async def stop(self) -> None:
        self._running = False
        if self._ws is not None:
            try:
                await asyncio.wait_for(
                    self._ws.close(),
                    timeout=self.config.close_timeout,
                )
            except Exception:
                pass
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------

    async def _maintain_connection(self) -> None:
        """
        Outer reconnect loop with exponential back-off.

        On each iteration:
        1. Open a WebSocket to the combined-stream endpoint.
        2. Run the receive loop until error or stop.
        3. If still running, back off and retry.
        """
        delay = self.config.reconnect_delay_initial

        while self._running:
            streams_param = "/".join(self.streams)
            url = f"{self.ws_url}?streams={streams_param}"
            try:
                logger.info(
                    "[conn-%d] Connecting to %d streams: %s…",
                    self.conn_id, len(self.streams), url[:80],
                )
                async with websockets.connect(
                    url,
                    ping_interval=_PING_INTERVAL_S,
                    ping_timeout=30,
                    **self.config.ws_kwargs,
                ) as ws:
                    self._ws = ws
                    self._last_message_ts = time.monotonic()
                    delay = self.config.reconnect_delay_initial  # reset on success

                    logger.info("[conn-%d] Connected.", self.conn_id)
                    await self._receive_loop(ws)

            except asyncio.CancelledError:
                break
            except (ConnectionClosed, WebSocketException, OSError) as exc:
                self.errors += 1
                logger.warning(
                    "[conn-%d] WS error: %s – retrying in %.1fs",
                    self.conn_id, exc, delay,
                )
            except Exception as exc:
                self.errors += 1
                logger.exception(
                    "[conn-%d] Unexpected error: %s", self.conn_id, exc
                )

            if not self._running:
                break

            self.reconnections += 1
            await asyncio.sleep(delay)
            delay = min(
                delay * self.config.reconnect_delay_multiplier,
                self.config.reconnect_delay_cap,
            )

        logger.info("[conn-%d] Connection loop exited.", self.conn_id)

    # ------------------------------------------------------------------

    async def _receive_loop(self, ws) -> None:
        """Read messages from an open WebSocket and dispatch them."""
        stale_checker = asyncio.create_task(self._stale_guard())
        try:
            async for raw in ws:
                if not self._running:
                    break
                self._last_message_ts = time.monotonic()
                self.messages_received += 1
                try:
                    payload = json.loads(raw)
                    # Combined stream messages wrap data in {"stream":..., "data":...}
                    stream = payload.get("stream", "")
                    data   = payload.get("data", payload)
                    self._dispatch(stream, data)
                except json.JSONDecodeError as exc:
                    logger.debug("[conn-%d] JSON decode error: %s", self.conn_id, exc)
        finally:
            stale_checker.cancel()
            try:
                await stale_checker
            except asyncio.CancelledError:
                pass

    # ------------------------------------------------------------------

    async def _stale_guard(self) -> None:
        """Close stale connections that haven't received data for 60 s."""
        while True:
            await asyncio.sleep(10)
            if time.monotonic() - self._last_message_ts > _STALE_TIMEOUT_S:
                logger.warning(
                    "[conn-%d] No message in %.0fs – closing stale connection.",
                    self.conn_id, _STALE_TIMEOUT_S,
                )
                if self._ws:
                    try:
                        await self._ws.close()
                    except Exception:
                        pass
                break

    @property
    def is_stale(self) -> bool:
        return time.monotonic() - self._last_message_ts > _STALE_TIMEOUT_S


# ---------------------------------------------------------------------------
# Public manager
# ---------------------------------------------------------------------------

class BinanceWebSocketManager:
    """
    Multiplexed Binance WebSocket manager.

    * Splits any number of streams across connections of max 200 each.
    * Automatically reconnects with exponential back-off.
    * Provides per-stream callback registration via :meth:`on`.
    * Tracks aggregate statistics across all connections.

    Parameters
    ----------
    config:
        :class:`BinanceWSConfig` instance.
    """

    def __init__(self, config: Optional[BinanceWSConfig] = None) -> None:
        self.config = config or BinanceWSConfig()
        self._callbacks: Dict[str, List[StreamCallback]] = defaultdict(list)
        self._connections: List[_Connection] = []
        self._next_conn_id = 0
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on(self, stream: str, callback: StreamCallback) -> None:
        """
        Register *callback* to be invoked when a message arrives on *stream*.

        Multiple callbacks per stream are supported.

        Parameters
        ----------
        stream:
            Binance stream name, e.g. ``"btcusdt@kline_1m"``.
        callback:
            Async or sync callable with signature
            ``(stream: str, data: dict) -> None``.
        """
        self._callbacks[stream].append(callback)
        logger.debug("Registered callback for stream %r", stream)

    # ------------------------------------------------------------------

    async def connect(self, streams: List[str]) -> None:
        """
        Open connections for the given list of *streams*.

        Streams are batched into groups of :data:`_MAX_STREAMS_PER_CONN`
        and each batch gets its own :class:`_Connection`.

        Parameters
        ----------
        streams:
            List of Binance stream names to subscribe to.
        """
        if not streams:
            return

        ws_base = (
            _FUTURES_WS_BASE
            if self.config.market == "futures"
            else _SPOT_WS_BASE
        )

        async with self._lock:
            # Deduplicate
            unique_streams = list(dict.fromkeys(streams))

            # Partition into batches
            batches = [
                unique_streams[i : i + _MAX_STREAMS_PER_CONN]
                for i in range(0, len(unique_streams), _MAX_STREAMS_PER_CONN)
            ]

            for batch in batches:
                conn = _Connection(
                    conn_id=self._next_conn_id,
                    streams=batch,
                    ws_url=ws_base,
                    config=self.config,
                    dispatch=self._dispatch,
                )
                self._next_conn_id += 1
                self._connections.append(conn)
                conn.start()

        logger.info(
            "BinanceWS: opened %d connection(s) for %d streams.",
            len(batches), len(unique_streams),
        )

    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Gracefully shut down all connections."""
        async with self._lock:
            tasks = [conn.stop() for conn in self._connections]
        await asyncio.gather(*tasks, return_exceptions=True)
        self._connections.clear()
        logger.info("BinanceWebSocketManager closed.")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    @property
    def messages_received(self) -> int:
        """Total messages received across all connections."""
        return sum(c.messages_received for c in self._connections)

    @property
    def reconnections(self) -> int:
        """Total reconnection attempts across all connections."""
        return sum(c.reconnections for c in self._connections)

    @property
    def errors(self) -> int:
        """Total errors encountered across all connections."""
        return sum(c.errors for c in self._connections)

    @property
    def stale_connections(self) -> int:
        """Number of connections that have not received data in 60 s."""
        return sum(1 for c in self._connections if c.is_stale)

    def stats(self) -> Dict[str, Any]:
        """Return a snapshot of aggregate statistics."""
        return {
            "connections":       len(self._connections),
            "messages_received": self.messages_received,
            "reconnections":     self.reconnections,
            "errors":            self.errors,
            "stale_connections": self.stale_connections,
        }

    # ------------------------------------------------------------------
    # Internal dispatch
    # ------------------------------------------------------------------

    def _dispatch(self, stream: str, data: Dict[str, Any]) -> None:
        """Fan-out a received message to all registered callbacks."""
        callbacks = self._callbacks.get(stream, [])
        if not callbacks:
            # Try wildcard-style prefix matching (e.g. "btcusdt@*")
            for pattern, cbs in self._callbacks.items():
                if stream.startswith(pattern.rstrip("*")):
                    callbacks.extend(cbs)

        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    asyncio.create_task(cb(stream, data))
                else:
                    cb(stream, data)
            except Exception as exc:
                logger.exception(
                    "Callback error on stream %r: %s", stream, exc
                )
