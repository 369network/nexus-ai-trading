"""
src/monitoring/health_server.py
--------------------------------
Lightweight health + metrics HTTP server.

Serves:
  GET /health   → 200 JSON {status, uptime_seconds, paper_mode, version}
  GET /metrics  → Prometheus text format from global REGISTRY
  GET /          → redirect to /health

Runs in a daemon thread so it does not block the asyncio event loop.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, Optional

from prometheus_client import REGISTRY, generate_latest, CONTENT_TYPE_LATEST

logger = logging.getLogger(__name__)

_SERVER_START_TIME = time.monotonic()
_BOT_STATUS: Dict[str, Any] = {
    "version": "1.0.0",
    "paper_mode": True,
}


def update_bot_status(**kwargs: Any) -> None:
    """Update fields shown in /health response. Thread-safe."""
    _BOT_STATUS.update(kwargs)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path in ("/health", "/"):
            self._serve_health()
        elif self.path == "/metrics":
            self._serve_metrics()
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
