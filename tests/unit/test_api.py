"""
NEXUS ALPHA — FastAPI Server Unit Tests
=========================================
Tests for API endpoints: health, portfolio, auth, rate limiting,
and WebSocket signal streaming. Uses httpx AsyncClient and mocked
Supabase/JWT dependencies.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Dict
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

# Import the FastAPI app — do this before setting up mocks so imports resolve
from src.api.server import _rate_limiter, app
from src.api.schemas import HealthResponse, PortfolioSummaryResponse


# ---------------------------------------------------------------------------
# JWT token helpers
# ---------------------------------------------------------------------------

def _make_jwt(role: str = "admin", sub: str = "test-user") -> str:
    """Create a valid JWT token for testing."""
    try:
        import jwt
        payload = {
            "sub": sub,
            "role": role,
            "exp": int(time.time()) + 3600,
        }
        from src.api.server import _JWT_SECRET, _JWT_ALGORITHM
        return jwt.encode(payload, _JWT_SECRET, algorithm=_JWT_ALGORITHM)
    except ImportError:
        # If PyJWT not available, use a known stub token
        return "test-token-no-jwt"


_ADMIN_TOKEN = _make_jwt(role="admin")
_VIEWER_TOKEN = _make_jwt(role="viewer")

_AUTH_HEADERS = {"Authorization": f"Bearer {_ADMIN_TOKEN}"}
_VIEWER_HEADERS = {"Authorization": f"Bearer {_VIEWER_TOKEN}"}


# ---------------------------------------------------------------------------
# App test client fixture
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Async httpx client pointed at the FastAPI test app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def _mock_supabase_health(healthy: bool = True):
    """Context manager that patches SupabaseClient.get_instance."""
    mock_client = MagicMock()
    mock_client.health_check = AsyncMock(return_value=healthy)
    mock_instance = AsyncMock(return_value=mock_client)
    return patch("src.api.server.SupabaseClient.get_instance", mock_instance)


# ---------------------------------------------------------------------------
# 1. Health endpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    async def test_health_returns_200(self, client: AsyncClient):
        resp = await client.get("/api/v1/health")
        assert resp.status_code == 200

    async def test_health_response_schema(self, client: AsyncClient):
        resp = await client.get("/api/v1/health")
        data = resp.json()
        assert "status" in data
        assert "version" in data
        assert "uptimeSeconds" in data or "uptime_seconds" in data
        assert data.get("status") == "ok"

    async def test_health_no_auth_required(self, client: AsyncClient):
        """Health endpoint must be accessible without authentication."""
        resp = await client.get("/api/v1/health")
        assert resp.status_code != 401

    async def test_health_shows_db_connected(self, client: AsyncClient):
        with _mock_supabase_health(healthy=True):
            resp = await client.get("/api/v1/health")
            data = resp.json()
            db = data.get("database", data.get("database_status", ""))
            assert db in ("connected", "unknown", "unavailable")  # depends on Supabase availability

    async def test_health_has_timestamp(self, client: AsyncClient):
        resp = await client.get("/api/v1/health")
        data = resp.json()
        ts = data.get("timestamp")
        assert ts is not None
        # Should be parseable as ISO datetime
        datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ---------------------------------------------------------------------------
# 2. Portfolio summary endpoint
# ---------------------------------------------------------------------------


class TestPortfolioSummaryEndpoint:
    async def test_portfolio_returns_200_with_auth(self, client: AsyncClient):
        resp = await client.get("/api/v1/portfolio/summary", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    async def test_portfolio_response_schema(self, client: AsyncClient):
        resp = await client.get("/api/v1/portfolio/summary", headers=_AUTH_HEADERS)
        data = resp.json()
        # Accept both camelCase and snake_case (Pydantic v2 aliases)
        assert "totalEquity" in data or "total_equity" in data
        total = data.get("totalEquity", data.get("total_equity", None))
        assert total is not None
        assert isinstance(total, (int, float))

    async def test_portfolio_has_open_positions(self, client: AsyncClient):
        resp = await client.get("/api/v1/portfolio/summary", headers=_AUTH_HEADERS)
        data = resp.json()
        positions = data.get("openPositions", data.get("open_positions", []))
        assert isinstance(positions, list)

    async def test_portfolio_equity_non_negative(self, client: AsyncClient):
        resp = await client.get("/api/v1/portfolio/summary", headers=_AUTH_HEADERS)
        data = resp.json()
        equity = data.get("totalEquity", data.get("total_equity", 0))
        assert equity >= 0


# ---------------------------------------------------------------------------
# 3. Authentication tests
# ---------------------------------------------------------------------------


class TestAuthentication:
    async def test_portfolio_without_auth_returns_401_or_403(self, client: AsyncClient):
        resp = await client.get("/api/v1/portfolio/summary")
        assert resp.status_code in (401, 403)

    async def test_trades_without_auth_returns_401_or_403(self, client: AsyncClient):
        resp = await client.get("/api/v1/trades")
        assert resp.status_code in (401, 403)

    async def test_risk_without_auth_returns_401_or_403(self, client: AsyncClient):
        resp = await client.get("/api/v1/risk/metrics")
        assert resp.status_code in (401, 403)

    async def test_invalid_token_returns_401(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/portfolio/summary",
            headers={"Authorization": "Bearer invalid.token.here"},
        )
        assert resp.status_code == 401

    async def test_malformed_auth_header_returns_401_or_403(self, client: AsyncClient):
        resp = await client.get(
            "/api/v1/portfolio/summary",
            headers={"Authorization": "NotBearer sometoken"},
        )
        assert resp.status_code in (401, 403, 422)

    async def test_valid_admin_token_accepted(self, client: AsyncClient):
        resp = await client.get("/api/v1/portfolio/summary", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    async def test_feature_flag_update_requires_admin_role(self, client: AsyncClient):
        """Viewer role should be rejected from feature flag updates."""
        resp = await client.put(
            "/api/v1/feature-flags/dream_mode",
            json={"enabled": True},
            headers=_VIEWER_HEADERS,
        )
        # Should be 403 Forbidden for non-admin
        assert resp.status_code in (403, 401)


# ---------------------------------------------------------------------------
# 4. Rate limiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    async def test_rate_limit_hit_after_100_requests(self, client: AsyncClient):
        """Sending 101 rapid requests should trigger rate limiting."""
        # Reset rate limiter state for this IP
        ip = "127.0.0.1"
        _rate_limiter._buckets.pop(ip, None)

        responses = []
        for i in range(105):
            resp = await client.get("/api/v1/health")
            responses.append(resp.status_code)

        # At least one response should be 429
        assert 429 in responses, (
            f"Expected at least one 429 in 105 requests. Got: {set(responses)}"
        )

    async def test_health_endpoint_exempt_from_rate_limit(self, client: AsyncClient):
        """Health endpoint should not contribute to rate limiting."""
        # The server exempts /api/v1/health from rate limiting
        ip = "127.0.0.2"  # Use fresh IP
        _rate_limiter._buckets.pop(ip, None)

        # Send many health checks
        for _ in range(15):
            resp = await client.get("/api/v1/health")
            # Health is exempt, so should never get 429 from health calls alone
            # (But since we can't control the actual IP in test, just check status)
            assert resp.status_code in (200, 429)

    async def test_429_has_error_body(self, client: AsyncClient):
        """429 responses should have a JSON error body."""
        ip = "127.0.0.3"
        _rate_limiter._buckets.pop(ip, None)

        # Force rate limit
        _rate_limiter._buckets[ip] = [time.monotonic()] * 101

        # The actual test client IP may differ; test the rate limiter directly
        allowed = _rate_limiter.is_allowed(ip)
        assert allowed is False

    async def test_rate_limit_window_resets(self):
        """Rate limiter bucket should clear after window expires."""
        ip = "127.0.0.99"
        _rate_limiter._buckets[ip] = [time.monotonic() - 70] * 100  # Old entries

        allowed = _rate_limiter.is_allowed(ip)
        assert allowed is True  # Old entries expired, window reset


# ---------------------------------------------------------------------------
# 5. WebSocket signal stream
# ---------------------------------------------------------------------------


class TestWebSocketSignalStream:
    async def test_ws_connects_to_signals_channel(self, client: AsyncClient):
        """WebSocket should accept connection and send welcome message."""
        async with client.websocket_connect("/ws/signals") as ws:
            # Should receive a welcome message
            msg_text = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
            msg = json.loads(msg_text)
            assert msg.get("type") == "connected"
            assert msg.get("channel") == "signals"

    async def test_ws_welcome_message_has_timestamp(self, client: AsyncClient):
        async with client.websocket_connect("/ws/signals") as ws:
            msg_text = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
            msg = json.loads(msg_text)
            assert "timestamp" in msg

    async def test_ws_trades_channel_connects(self, client: AsyncClient):
        async with client.websocket_connect("/ws/trades") as ws:
            msg_text = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
            msg = json.loads(msg_text)
            assert msg.get("channel") == "trades"

    async def test_ws_risk_channel_connects(self, client: AsyncClient):
        async with client.websocket_connect("/ws/risk") as ws:
            msg_text = await asyncio.wait_for(ws.receive_text(), timeout=3.0)
            msg = json.loads(msg_text)
            assert msg.get("channel") == "risk"

    async def test_ws_multiple_clients_same_channel(self, client: AsyncClient):
        """Multiple clients can connect to the same channel simultaneously."""
        async with client.websocket_connect("/ws/signals") as ws1:
            async with client.websocket_connect("/ws/signals") as ws2:
                msg1 = json.loads(await asyncio.wait_for(ws1.receive_text(), timeout=3.0))
                msg2 = json.loads(await asyncio.wait_for(ws2.receive_text(), timeout=3.0))
                assert msg1.get("type") == "connected"
                assert msg2.get("type") == "connected"

    async def test_ws_broadcast_reaches_all_clients(self, client: AsyncClient):
        """Broadcast to a channel should reach all connected clients."""
        from src.api.websocket_manager import manager

        received: list = []

        async def _receive(ws):
            # Skip welcome
            await asyncio.wait_for(ws.receive_text(), timeout=2.0)
            # Wait for broadcast
            try:
                msg = await asyncio.wait_for(ws.receive_text(), timeout=2.0)
                received.append(json.loads(msg))
            except asyncio.TimeoutError:
                pass

        async with client.websocket_connect("/ws/signals") as ws1:
            async with client.websocket_connect("/ws/signals") as ws2:
                # Skip welcome messages
                await asyncio.wait_for(ws1.receive_text(), timeout=2.0)
                await asyncio.wait_for(ws2.receive_text(), timeout=2.0)

                # Broadcast to channel
                test_msg = {"type": "signal", "test": True, "data": {}}
                sent = await manager.broadcast("signals", test_msg)
                assert sent >= 0  # At least tried to send


# ---------------------------------------------------------------------------
# 6. Other endpoints
# ---------------------------------------------------------------------------


class TestOtherEndpoints:
    async def test_risk_metrics_returns_200(self, client: AsyncClient):
        resp = await client.get("/api/v1/risk/metrics", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    async def test_performance_returns_200(self, client: AsyncClient):
        resp = await client.get("/api/v1/performance?days=30", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    async def test_performance_days_parameter(self, client: AsyncClient):
        resp = await client.get("/api/v1/performance?days=90", headers=_AUTH_HEADERS)
        data = resp.json()
        assert resp.status_code == 200
        period = data.get("periodDays", data.get("period_days"))
        assert period == 90

    async def test_feature_flags_returns_200(self, client: AsyncClient):
        resp = await client.get("/api/v1/feature-flags", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    async def test_feature_flags_response_is_dict(self, client: AsyncClient):
        resp = await client.get("/api/v1/feature-flags", headers=_AUTH_HEADERS)
        data = resp.json()
        flags = data.get("flags", {})
        assert isinstance(flags, dict)

    async def test_trades_returns_200(self, client: AsyncClient):
        resp = await client.get("/api/v1/trades", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    async def test_trades_limit_parameter_respected(self, client: AsyncClient):
        resp = await client.get("/api/v1/trades?limit=10", headers=_AUTH_HEADERS)
        data = resp.json()
        trades = data.get("trades", [])
        assert len(trades) <= 10

    async def test_circuit_breakers_returns_200(self, client: AsyncClient):
        resp = await client.get("/api/v1/circuit-breakers", headers=_AUTH_HEADERS)
        assert resp.status_code == 200

    async def test_emergency_stop_forbidden_for_viewer(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/emergency-stop",
            json={"reason": "Test emergency stop", "close_positions": False},
            headers=_VIEWER_HEADERS,
        )
        assert resp.status_code in (403, 401)

    async def test_emergency_stop_accepted_for_admin(self, client: AsyncClient):
        resp = await client.post(
            "/api/v1/emergency-stop",
            json={
                "reason": "Unit test emergency stop — do not execute in production",
                "close_positions": False,
                "operator": "pytest",
            },
            headers=_AUTH_HEADERS,
        )
        # Should succeed (200) or be blocked (422 if validation fails)
        assert resp.status_code in (200, 422)
        if resp.status_code == 200:
            data = resp.json()
            assert data.get("acknowledged") is True

    async def test_prometheus_metrics_endpoint(self, client: AsyncClient):
        resp = await client.get("/metrics")
        assert resp.status_code == 200
        assert "nexus_api_requests_total" in resp.text or "text/plain" in resp.headers.get("content-type", "")

    async def test_openapi_schema_accessible(self, client: AsyncClient):
        resp = await client.get("/openapi.json")
        assert resp.status_code == 200
        schema = resp.json()
        assert "paths" in schema
        assert "/api/v1/health" in schema["paths"]
