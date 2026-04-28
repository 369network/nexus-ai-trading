"""
src/monitoring/health_server.py
--------------------------------
Lightweight health + metrics HTTP server.

Serves:
  GET /health                  → 200 JSON {status, uptime_seconds, paper_mode, version}
  GET /metrics                 → Prometheus text format from global REGISTRY
  GET /positions               → 200 JSON {positions, count}
  POST /control/emergency-stop → triggers emergency stop if X-Admin-Key matches ADMIN_API_KEY
  GET /                        → redirect to /health

Runs in a daemon thread so it does not block the asyncio event loop.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Callable, Dict, List, Optional

from prometheus_client import REGISTRY, generate_latest, CONTENT_TYPE_LATEST

logger = logging.getLogger(__name__)

_SERVER_START_TIME = time.monotonic()
_BOT_STATUS: Dict[str, Any] = {
    "version": "1.0.0",
    "paper_mode": True,
}

# ---------------------------------------------------------------------------
# Shared positions state
# ---------------------------------------------------------------------------

_OPEN_POSITIONS: List[Any] = []


def update_positions(positions: List[Any]) -> None:
    """Replace the current open-positions snapshot. Thread-safe (GIL-protected list replace)."""
    global _OPEN_POSITIONS
    _OPEN_POSITIONS = list(positions)


# ---------------------------------------------------------------------------
# Emergency-stop state
# ---------------------------------------------------------------------------

_EMERGENCY_STOP: bool = False
_EMERGENCY_STOP_CALLBACK: Optional[Callable] = None


def register_emergency_stop_callback(fn: Callable) -> None:
    """Register a callable to invoke when the emergency-stop endpoint is triggered."""
    global _EMERGENCY_STOP_CALLBACK
    _EMERGENCY_STOP_CALLBACK = fn


def is_emergency_stop() -> bool:
    """Return True if the emergency stop has been triggered."""
    return _EMERGENCY_STOP


def update_bot_status(**kwargs: Any) -> None:
    """Update fields shown in /health response. Thread-safe."""
    _BOT_STATUS.update(kwargs)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/health", "/"):
            self._serve_health()
        elif self.path == "/metrics":
            self._serve_metrics()
        elif self.path == "/positions":
            self._serve_positions()
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self) -> None:
        if self.path == "/control/emergency-stop":
            self._handle_emergency_stop()
        else:
            self.send_response(404)
            self.end_headers()

    def _serve_health(self) -> None:
        body = json.dumps({
            "status": "healthy",
            "uptime_seconds": int(time.monotonic() - _SERVER_START_TIME),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            **_BOT_STATUS,
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_metrics(self) -> None:
        output = generate_latest(REGISTRY)
        self.send_response(200)
        self.send_header("Content-Type", CONTENT_TYPE_LATEST)
        self.send_header("Content-Length", str(len(output)))
        self.end_headers()
        self.wfile.write(output)

    def _serve_positions(self) -> None:
        positions = list(_OPEN_POSITIONS)
        body = json.dumps({
            "positions": positions,
            "count": len(positions),
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _handle_emergency_stop(self) -> None:
        global _EMERGENCY_STOP, _EMERGENCY_STOP_CALLBACK

        provided_key = self.headers.get("X-Admin-Key", "")
        expected_key = os.environ.get("ADMIN_API_KEY", "")

        # Reject if key is missing or doesn't match
        if not expected_key or provided_key != expected_key:
            body = json.dumps({"error": "Unauthorized"}).encode()
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        _EMERGENCY_STOP = True
        logger.critical("Emergency stop triggered via /control/emergency-stop")

        if _EMERGENCY_STOP_CALLBACK is not None:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(_EMERGENCY_STOP_CALLBACK(), loop)
                else:
                    loop.run_until_complete(_EMERGENCY_STOP_CALLBACK())
            except Exception as exc:
                logger.error("Emergency stop callback failed: %s", exc)

        body = json.dumps({"triggered": True}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass  # suppress per-request access logs


class HealthServer:
    """Runs the health/metrics HTTP server in a daemon thread."""

    def __init__(self, port: int = 8080, host: str = "") -> None:
        self.port = port
        self.host = host
        self._server: Optional[HTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the server in a daemon thread. Returns immediately."""
        self._server = HTTPServer((self.host, self.port), _HealthHandler)
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
            name="nexus-health-server",
        )
        self._thread.start()
        logger.info(
            "Health/metrics server started on :%d  (GET /health  GET /metrics)",
            self.port,
        )

    def stop(self) -> None:
        if self._server:
            self._server.shutdown()
            logger.info("Health/metrics server stopped.")
