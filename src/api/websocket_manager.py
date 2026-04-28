"""
NEXUS ALPHA — WebSocket Connection Manager
============================================
Manages WebSocket channel subscriptions, broadcasting, message queuing
for disconnected clients, and auto-reconnect support.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import datetime
from typing import Any, DefaultDict, Dict, List, Optional, Set

import structlog
from fastapi import WebSocket, WebSocketDisconnect

log = structlog.get_logger(__name__)

# Valid channel names
VALID_CHANNELS: Set[str] = {"signals", "trades", "risk", "portfolio"}

# Max queued messages per disconnected client before dropping
_MAX_QUEUE_SIZE = 100
# Seconds to keep a queue for a disconnected client
_QUEUE_TTL_SECONDS = 30.0


class _ClientState:
    """State for a single WebSocket connection."""

    def __init__(self, websocket: WebSocket, channel: str) -> None:
        self.websocket = websocket
        self.channel = channel
        self.connected_at = datetime.utcnow()
        self.message_count = 0
        self.queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue(maxsize=_MAX_QUEUE_SIZE)
        self.is_alive = True


class ConnectionManager:
    """
    WebSocket connection manager supporting named channels.

    Channels:
    - ``signals``   — new trading signals as they are generated
    - ``trades``    — trade execution updates (filled, cancelled, closed)
    - ``risk``      — risk metrics broadcast every 5 seconds
    - ``portfolio`` — portfolio-level updates

    Features:
    - Per-channel broadcast and unicast.
    - Message queuing for temporarily disconnected clients (30-second TTL).
    - Auto-flush of queued messages on reconnect.
    - Heartbeat / ping support.
    - Thread-safe via asyncio.Lock.

    Usage (in FastAPI route)::

        manager = ConnectionManager()

        @app.websocket("/ws/signals")
        async def ws_signals(ws: WebSocket):
            await manager.connect(ws, "signals")
            try:
                async for _ in ws.iter_text():
                    pass  # Client messages ignored; server-push only
            except WebSocketDisconnect:
                await manager.disconnect(ws, "signals")
    """

    def __init__(self) -> None:
        # channel -> set of active client states
        self._channels: DefaultDict[str, List[_ClientState]] = defaultdict(list)
        # websocket id -> state (for fast lookup)
        self._ws_to_state: Dict[int, _ClientState] = {}
        # Disconnected client queues (ws_id -> (queue, timestamp))
        self._pending_queues: Dict[int, tuple[asyncio.Queue, float]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def connect(self, websocket: WebSocket, channel: str) -> None:
        """
        Accept a WebSocket connection and register it to a channel.

        Args:
            websocket: FastAPI WebSocket instance.
            channel: One of ``signals``, ``trades``, ``risk``, ``portfolio``.

        Raises:
            ValueError: If channel is not valid.
        """
        if channel not in VALID_CHANNELS:
            await websocket.close(code=4000, reason=f"Invalid channel: {channel}")
            raise ValueError(f"Channel {channel!r} not valid. Choose from {VALID_CHANNELS}.")

        await websocket.accept()
        state = _ClientState(websocket, channel)

        async with self._lock:
            self._channels[channel].append(state)
            self._ws_to_state[id(websocket)] = state

        log.info(
            "ws_client_connected",
            channel=channel,
            total_in_channel=len(self._channels[channel]),
        )

        # Flush any queued messages from a previous connection
        await self._flush_pending_queue(websocket, state)

        # Send a welcome message
        await self.send_personal_message(
            websocket,
            {
                "type": "connected",
                "channel": channel,
                "timestamp": datetime.utcnow().isoformat(),
                "message": f"Connected to NEXUS ALPHA {channel} stream",
            },
        )

    async def disconnect(self, websocket: WebSocket, channel: str) -> None:
        """
        Remove a WebSocket from a channel.

        Preserves the client's message queue for ``_QUEUE_TTL_SECONDS``
        so messages sent during a brief disconnect can be replayed.

        Args:
            websocket: WebSocket instance to remove.
            channel: Channel it was registered to.
        """
        ws_id = id(websocket)

        async with self._lock:
            state = self._ws_to_state.pop(ws_id, None)
            if state:
                state.is_alive = False
                try:
                    self._channels[channel].remove(state)
                except ValueError:
                    pass
                # Preserve queue for potential reconnect
                if not state.queue.empty():
                    import time
                    self._pending_queues[ws_id] = (state.queue, time.monotonic())

        log.info(
            "ws_client_disconnected",
            channel=channel,
            remaining=len(self._channels.get(channel, [])),
        )

        # Cleanup stale pending queues
        asyncio.create_task(self._cleanup_stale_queues())

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def broadcast(self, channel: str, message: Dict[str, Any]) -> int:
        """
        Broadcast a message to all clients subscribed to a channel.

        Clients that fail to receive are silently disconnected.

        Args:
            channel: Target channel name.
            message: JSON-serialisable dict to broadcast.

        Returns:
            Number of clients successfully reached.
        """
        payload = self._encode(message)
        sent = 0

        async with self._lock:
            clients = list(self._channels.get(channel, []))

        dead: List[_ClientState] = []
        for state in clients:
            if not state.is_alive:
                dead.append(state)
                continue
            try:
                await asyncio.wait_for(
                    state.websocket.send_text(payload), timeout=5.0
                )
                state.message_count += 1
                sent += 1
            except asyncio.TimeoutError:
                log.warning("ws_broadcast_timeout", channel=channel)
                dead.append(state)
            except Exception:
                dead.append(state)

        # Remove dead connections
        if dead:
            async with self._lock:
                for state in dead:
                    state.is_alive = False
                    try:
                        self._channels[channel].remove(state)
                    except ValueError:
                        pass

        if sent > 0:
            log.debug("ws_broadcast", channel=channel, recipients=sent)

        return sent

    async def broadcast_to_all(self, message: Dict[str, Any]) -> Dict[str, int]:
        """Broadcast to every channel. Returns dict of channel -> sent count."""
        results: Dict[str, int] = {}
        for channel in VALID_CHANNELS:
            results[channel] = await self.broadcast(channel, message)
        return results

    # ------------------------------------------------------------------
    # Personal messages
    # ------------------------------------------------------------------

    async def send_personal_message(
        self,
        websocket: WebSocket,
        message: Dict[str, Any],
    ) -> bool:
        """
        Send a message to a single WebSocket client.

        Args:
            websocket: Target WebSocket.
            message: JSON-serialisable dict.

        Returns:
            True if sent successfully, False otherwise.
        """
        try:
            await asyncio.wait_for(
                websocket.send_text(self._encode(message)), timeout=5.0
            )
            return True
        except Exception as exc:
            log.debug("ws_personal_send_failed", error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    async def enqueue_for_channel(
        self,
        channel: str,
        message: Dict[str, Any],
        offline_only: bool = False,
    ) -> None:
        """
        Queue a message for clients that are temporarily disconnected.

        If ``offline_only`` is False, also broadcasts to connected clients.

        Args:
            channel: Target channel.
            message: Message dict.
            offline_only: Only queue; don't broadcast to connected clients.
        """
        if not offline_only:
            await self.broadcast(channel, message)

        # Also queue for disconnected clients with pending queues
        async with self._lock:
            for ws_id, (queue, _ts) in self._pending_queues.items():
                if not queue.full():
                    try:
                        queue.put_nowait(message)
                    except asyncio.QueueFull:
                        pass  # Drop oldest if full

    async def _flush_pending_queue(
        self, websocket: WebSocket, state: _ClientState
    ) -> None:
        """Replay queued messages to a newly reconnected client."""
        ws_id = id(websocket)
        async with self._lock:
            pending = self._pending_queues.pop(ws_id, None)

        if not pending:
            return

        queue, _ = pending
        replayed = 0
        while not queue.empty():
            try:
                msg = queue.get_nowait()
                msg["_replayed"] = True
                success = await self.send_personal_message(websocket, msg)
                if not success:
                    break
                replayed += 1
            except asyncio.QueueEmpty:
                break

        if replayed > 0:
            log.info("ws_queue_flushed", replayed=replayed)

    async def _cleanup_stale_queues(self) -> None:
        """Remove pending queues older than TTL."""
        import time
        now = time.monotonic()
        async with self._lock:
            stale = [
                ws_id
                for ws_id, (_q, ts) in self._pending_queues.items()
                if now - ts > _QUEUE_TTL_SECONDS
            ]
            for ws_id in stale:
                del self._pending_queues[ws_id]

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def connection_count(self, channel: Optional[str] = None) -> int:
        """Return number of active connections, optionally filtered by channel."""
        if channel:
            return len(self._channels.get(channel, []))
        return sum(len(conns) for conns in self._channels.values())

    def get_stats(self) -> Dict[str, Any]:
        """Return connection statistics for monitoring."""
        return {
            "total_connections": self.connection_count(),
            "channels": {
                ch: len(conns) for ch, conns in self._channels.items()
            },
            "pending_queues": len(self._pending_queues),
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _encode(message: Dict[str, Any]) -> str:
        """Serialise message to JSON string."""
        return json.dumps(message, default=str)


# ---------------------------------------------------------------------------
# Global singleton for FastAPI app
# ---------------------------------------------------------------------------

# Import and reuse this instance throughout the API module
manager = ConnectionManager()
