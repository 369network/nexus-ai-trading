"""
NEXUS ALPHA — FastAPI REST API Server
=======================================
Dashboard and external integration API with JWT authentication,
rate limiting, Prometheus metrics, and WebSocket streams.
"""

from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import structlog
import uvicorn
from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from prometheus_client import Counter, Gauge, Histogram, generate_latest
from starlette.responses import Response

from src.api.schemas import (
    AgentDebateResponse,
    CircuitBreakersResponse,
    CircuitBreakerStatus,
    DebateListResponse,
    EmergencyStopRequest,
    EmergencyStopResponse,
    ErrorResponse,
    FeatureFlagUpdateRequest,
    FeatureFlagUpdateResponse,
    FeatureFlagsListResponse,
    HealthResponse,
    PerformanceResponse,
    PortfolioSummaryResponse,
    RiskMetricsResponse,
    SignalListResponse,
    TradeListResponse,
)
from src.api.websocket_manager import VALID_CHANNELS, manager

log = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# JWT auth
# ---------------------------------------------------------------------------

_JWT_SECRET = os.environ.get("JWT_SECRET", "nexus-alpha-dev-secret-change-in-prod")
_JWT_ALGORITHM = "HS256"
_bearer = HTTPBearer(auto_error=True)


def _decode_jwt(token: str) -> Dict[str, Any]:
    """Decode and verify a JWT. Raises HTTPException on failure."""
    try:
        import jwt

        payload = jwt.decode(token, _JWT_SECRET, algorithms=[_JWT_ALGORITHM])
        return payload
    except ImportError:
        # If PyJWT not installed, accept any token in development
        log.warning("jwt_module_unavailable", note="Install PyJWT for production use")
        return {"sub": "dev-user", "role": "admin"}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {exc}",
        )


async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(_bearer),
) -> Dict[str, Any]:
    """FastAPI dependency: validate JWT and return payload."""
    return _decode_jwt(credentials.credentials)


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------


class _IPRateLimiter:
    """Simple in-memory sliding-window rate limiter: 100 req/min per IP."""

    def __init__(self, max_requests: int = 100, window_seconds: int = 60) -> None:
        self._max = max_requests
        self._window = window_seconds
        self._buckets: Dict[str, List[float]] = {}

    def is_allowed(self, ip: str) -> bool:
        now = time.monotonic()
        window = self._buckets.setdefault(ip, [])
        self._buckets[ip] = [t for t in window if now - t < self._window]
        if len(self._buckets[ip]) >= self._max:
            return False
        self._buckets[ip].append(now)
        return True


_rate_limiter = _IPRateLimiter(max_requests=100, window_seconds=60)


# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

_REQUEST_COUNT = Counter(
    "nexus_api_requests_total",
    "Total API requests",
    ["method", "endpoint", "status"],
)
_REQUEST_LATENCY = Histogram(
    "nexus_api_request_latency_seconds",
    "API request latency",
    ["endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5],
)
_ACTIVE_WS = Gauge(
    "nexus_api_websocket_connections",
    "Active WebSocket connections",
    ["channel"],
)

# ---------------------------------------------------------------------------
# Startup / lifespan
# ---------------------------------------------------------------------------

_startup_time = time.time()


@asynccontextmanager
async def _lifespan(app: FastAPI):
    """Application lifespan: startup and shutdown tasks."""
    log.info("nexus_api_starting")

    # Start background risk broadcast task
    risk_task = asyncio.create_task(_risk_broadcast_loop())

    yield

    log.info("nexus_api_stopping")
    risk_task.cancel()
    try:
        await risk_task
    except asyncio.CancelledError:
        pass


async def _risk_broadcast_loop() -> None:
    """Broadcast risk metrics over WebSocket every 5 seconds."""
    while True:
        try:
            await asyncio.sleep(5)
            risk_data = await _get_risk_data()
            await manager.broadcast("risk", {
                "type": "risk_update",
                "data": risk_data,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            # Update Prometheus gauge
            for ch in VALID_CHANNELS:
                _ACTIVE_WS.labels(channel=ch).set(manager.connection_count(ch))
        except asyncio.CancelledError:
            break
        except Exception as exc:
            log.warning("risk_broadcast_error", error=str(exc))


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="NEXUS ALPHA API",
    description="REST API for the NEXUS ALPHA multi-market AI trading system",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=_lifespan,
)

# CORS — allow dashboard origin
_DASHBOARD_ORIGINS = [
    os.environ.get("DASHBOARD_ORIGIN", "http://localhost:3000"),
    "http://localhost:5173",   # Vite dev server
    "http://localhost:8080",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=_DASHBOARD_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Middleware: rate limiting + metrics
# ---------------------------------------------------------------------------


@app.middleware("http")
async def _middleware(request: Request, call_next):
    # Rate limiting (skip for health and metrics endpoints)
    if request.url.path not in ("/api/v1/health", "/metrics"):
        ip = request.client.host if request.client else "unknown"
        if not _rate_limiter.is_allowed(ip):
            return JSONResponse(
                status_code=429,
                content={"error": "Rate limit exceeded", "detail": "Max 100 requests/minute"},
            )

    # Prometheus timing
    endpoint = request.url.path
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed = time.perf_counter() - t0

    _REQUEST_COUNT.labels(
        method=request.method,
        endpoint=endpoint,
        status=response.status_code,
    ).inc()
    _REQUEST_LATENCY.labels(endpoint=endpoint).observe(elapsed)

    return response


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------


@app.exception_handler(HTTPException)
async def _http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": exc.detail,
            "code": exc.status_code,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# Prometheus metrics endpoint (no auth)
# ---------------------------------------------------------------------------


@app.get("/metrics", include_in_schema=False)
async def metrics():
    return Response(generate_latest(), media_type="text/plain; version=0.0.4")


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/health",
    response_model=HealthResponse,
    tags=["System"],
    summary="Health check",
)
async def health_check():
    """Check system health. No authentication required."""
    db_status = "unknown"
    try:
        from src.db.supabase_client import SupabaseClient
        from src.config import get_settings
        s = get_settings()
        client = await SupabaseClient.get_instance(url=s.supabase_url, key=s.supabase_service_key)
        db_status = "connected" if await client.health_check() else "degraded"
    except Exception:
        db_status = "unavailable"

    return HealthResponse(
        status="ok",
        version="0.1.0",
        uptime_seconds=round(time.time() - _startup_time, 1),
        database=db_status,
        timestamp=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/portfolio/summary",
    response_model=PortfolioSummaryResponse,
    tags=["Portfolio"],
    summary="Portfolio overview",
)
async def portfolio_summary(
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """Return current portfolio equity, positions, and P&L summary."""
    try:
        data = await _get_portfolio_data()
        return data
    except Exception as exc:
        log.exception("portfolio_summary_error", error=str(exc))
        raise HTTPException(500, "Failed to fetch portfolio data")


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/trades",
    response_model=TradeListResponse,
    tags=["Trades"],
    summary="List trades",
)
async def list_trades(
    market: Optional[str] = Query(None, examples=["crypto"]),
    trade_status: Optional[str] = Query(None, alias="status", examples=["closed"]),
    limit: int = Query(50, ge=1, le=500),
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """List trades with optional filters by market and status."""
    try:
        from src.db.supabase_client import SupabaseClient
        from src.config import get_settings
        s = get_settings()
        client = await SupabaseClient.get_instance(url=s.supabase_url, key=s.supabase_service_key)

        query_result = await asyncio.to_thread(
            lambda: _build_trade_query(client, market, trade_status, limit)
        )
        trades = [_row_to_trade_response(r) for r in query_result]
        return TradeListResponse(trades=trades, total=len(trades), page=1, limit=limit)
    except Exception as exc:
        log.exception("list_trades_error", error=str(exc))
        raise HTTPException(500, "Failed to fetch trades")


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/signals",
    response_model=SignalListResponse,
    tags=["Signals"],
    summary="List signals",
)
async def list_signals(
    market: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """List recent trading signals, optionally filtered by market."""
    try:
        rows = await _fetch_signals(market=market, limit=limit)
        return SignalListResponse(signals=rows, total=len(rows), limit=limit)
    except Exception as exc:
        log.exception("list_signals_error", error=str(exc))
        raise HTTPException(500, "Failed to fetch signals")


# ---------------------------------------------------------------------------
# Agent debates
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/agents/debates",
    response_model=DebateListResponse,
    tags=["Agents"],
    summary="List agent debates",
)
async def list_debates(
    limit: int = Query(20, ge=1, le=100),
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """List recent multi-agent debate sessions."""
    try:
        debates = await _fetch_debates(limit=limit)
        return DebateListResponse(debates=debates, total=len(debates), limit=limit)
    except Exception as exc:
        log.exception("list_debates_error", error=str(exc))
        raise HTTPException(500, "Failed to fetch debates")


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/risk/metrics",
    response_model=RiskMetricsResponse,
    tags=["Risk"],
    summary="Current risk metrics",
)
async def risk_metrics(
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """Return current portfolio risk metrics, drawdown, and circuit breaker states."""
    try:
        return await _get_risk_data()
    except Exception as exc:
        log.exception("risk_metrics_error", error=str(exc))
        raise HTTPException(500, "Failed to fetch risk metrics")


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/performance",
    response_model=PerformanceResponse,
    tags=["Performance"],
    summary="Performance statistics",
)
async def performance(
    days: int = Query(30, ge=1, le=365),
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """Return rolling performance statistics over the specified number of days."""
    try:
        return await _get_performance_data(days=days)
    except Exception as exc:
        log.exception("performance_error", error=str(exc))
        raise HTTPException(500, "Failed to fetch performance data")


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/feature-flags",
    response_model=FeatureFlagsListResponse,
    tags=["System"],
    summary="List feature flags",
)
async def list_feature_flags(
    _user: Dict[str, Any] = Depends(get_current_user),
):
    flags = await _get_feature_flags()
    return FeatureFlagsListResponse(flags=flags, count=len(flags))


@app.put(
    "/api/v1/feature-flags/{flag}",
    response_model=FeatureFlagUpdateResponse,
    tags=["System"],
    summary="Update a feature flag",
)
async def update_feature_flag(
    flag: str,
    body: FeatureFlagUpdateRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """Enable or disable a named feature flag. Requires admin role."""
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient privileges")

    try:
        await _set_feature_flag(flag, body.enabled)
        log.info("feature_flag_updated", flag=flag, enabled=body.enabled, user=user.get("sub"))
        return FeatureFlagUpdateResponse(
            flag_name=flag,
            enabled=body.enabled,
            updated_at=datetime.now(timezone.utc),
            message=f"Flag '{flag}' {'enabled' if body.enabled else 'disabled'} successfully",
        )
    except KeyError:
        raise HTTPException(404, f"Feature flag '{flag}' not found")
    except Exception as exc:
        raise HTTPException(500, f"Failed to update flag: {exc}")


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------


@app.post(
    "/api/v1/emergency-stop",
    response_model=EmergencyStopResponse,
    tags=["System"],
    summary="Activate emergency stop",
)
async def emergency_stop(
    body: EmergencyStopRequest,
    user: Dict[str, Any] = Depends(get_current_user),
):
    """
    Activate system-wide emergency stop.
    Optionally closes all open positions.
    Requires admin or operator role.
    """
    if user.get("role") not in ("admin", "operator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Insufficient privileges")

    log.critical(
        "emergency_stop_activated",
        reason=body.reason,
        operator=body.operator or user.get("sub"),
        close_positions=body.close_positions,
    )

    # Notify via alert manager
    try:
        from src.monitoring.alertmanager import AlertManager, AlertLevel
        am = await AlertManager.get_instance()
        await am.send(
            level=AlertLevel.EMERGENCY,
            title="EMERGENCY STOP ACTIVATED",
            message=f"Reason: {body.reason}\nOperator: {body.operator or user.get('sub')}\nPositions will be closed: {body.close_positions}",
            source="api/emergency-stop",
            force=True,
        )
    except Exception:
        pass

    positions_closed = 0
    if body.close_positions:
        positions_closed = await _close_all_positions(reason=body.reason)

    # Broadcast to all WS clients
    await manager.broadcast_to_all({
        "type": "emergency_stop",
        "reason": body.reason,
        "positions_closed": positions_closed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    return EmergencyStopResponse(
        acknowledged=True,
        reason=body.reason,
        positions_closed=positions_closed,
        timestamp=datetime.now(timezone.utc),
        message=f"Emergency stop activated. {positions_closed} positions closed.",
    )


# ---------------------------------------------------------------------------
# Circuit breakers
# ---------------------------------------------------------------------------


@app.get(
    "/api/v1/circuit-breakers",
    response_model=CircuitBreakersResponse,
    tags=["Risk"],
    summary="Circuit breaker states",
)
async def circuit_breakers(
    _user: Dict[str, Any] = Depends(get_current_user),
):
    """Return current state of all symbol-level circuit breakers."""
    try:
        cbs = await _get_circuit_breakers()
        any_tripped = any(cb.is_tripped for cb in cbs)
        return CircuitBreakersResponse(
            circuit_breakers=cbs,
            any_tripped=any_tripped,
            timestamp=datetime.now(timezone.utc),
        )
    except Exception as exc:
        raise HTTPException(500, f"Failed to fetch circuit breakers: {exc}")


# ---------------------------------------------------------------------------
# WebSocket endpoints
# ---------------------------------------------------------------------------


@app.websocket("/ws/signals")
async def ws_signals(websocket: WebSocket):
    """Stream new trading signals in real-time."""
    await manager.connect(websocket, "signals")
    try:
        while True:
            # Keep alive — server-push only, ignore client messages
            await asyncio.sleep(30)
            await manager.send_personal_message(websocket, {"type": "ping"})
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "signals")
    except Exception:
        await manager.disconnect(websocket, "signals")


@app.websocket("/ws/trades")
async def ws_trades(websocket: WebSocket):
    """Stream trade execution updates in real-time."""
    await manager.connect(websocket, "trades")
    try:
        while True:
            await asyncio.sleep(30)
            await manager.send_personal_message(websocket, {"type": "ping"})
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "trades")
    except Exception:
        await manager.disconnect(websocket, "trades")


@app.websocket("/ws/risk")
async def ws_risk(websocket: WebSocket):
    """Stream risk metric updates every 5 seconds (pushed by background task)."""
    await manager.connect(websocket, "risk")
    try:
        while True:
            await asyncio.sleep(30)
            await manager.send_personal_message(websocket, {"type": "ping"})
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "risk")
    except Exception:
        await manager.disconnect(websocket, "risk")


@app.websocket("/ws/portfolio")
async def ws_portfolio(websocket: WebSocket):
    """Stream portfolio-level updates."""
    await manager.connect(websocket, "portfolio")
    try:
        while True:
            await asyncio.sleep(30)
            await manager.send_personal_message(websocket, {"type": "ping"})
    except WebSocketDisconnect:
        await manager.disconnect(websocket, "portfolio")
    except Exception:
        await manager.disconnect(websocket, "portfolio")


# ---------------------------------------------------------------------------
# Data helpers (stub implementations — replace with real DB queries)
# ---------------------------------------------------------------------------


async def _get_portfolio_data() -> PortfolioSummaryResponse:
    """Fetch portfolio data from Supabase/in-memory portfolio tracker."""
    try:
        from src.db.supabase_client import SupabaseClient
        from src.config import get_settings
        s = get_settings()
        client = await SupabaseClient.get_instance(url=s.supabase_url, key=s.supabase_service_key)
        snapshot = await asyncio.to_thread(
            lambda: client._client.table("portfolio_snapshots")  # type: ignore[union-attr]
            .select("*").order("created_at", desc=True).limit(1).execute()
        )
        if snapshot.data:
            row = snapshot.data[0]
            return _row_to_portfolio_response(row)
    except Exception:
        pass

    # Fallback stub
    return PortfolioSummaryResponse(
        total_equity=100_000.0,
        cash=100_000.0,
        positions_value=0.0,
        unrealized_pnl=0.0,
        unrealized_pnl_pct=0.0,
        realized_pnl_today=0.0,
        total_pnl=0.0,
        total_pnl_pct=0.0,
        peak_equity=100_000.0,
        drawdown_pct=0.0,
        open_positions=[],
        n_open_positions=0,
        daily_pnl_series=[],
        mode="paper",
        timestamp=datetime.now(timezone.utc),
    )


async def _get_risk_data() -> RiskMetricsResponse:
    """Fetch current risk metrics."""
    return RiskMetricsResponse(
        total_equity=100_000.0,
        peak_equity=100_000.0,
        current_drawdown_pct=0.0,
        max_drawdown_pct=0.0,
        daily_pnl=0.0,
        daily_pnl_pct=0.0,
        daily_loss_limit_pct=3.0,
        daily_loss_remaining_pct=3.0,
        weekly_pnl_pct=0.0,
        open_positions_count=0,
        max_position_size_pct=10.0,
        total_exposure_pct=0.0,
        var_95_pct=None,
        sharpe_rolling_30d=None,
        circuit_breakers=[],
        system_status="normal",
        emergency_stop_active=False,
        timestamp=datetime.now(timezone.utc),
    )


async def _get_performance_data(days: int) -> PerformanceResponse:
    """Fetch rolling performance statistics."""
    return PerformanceResponse(
        period_days=days,
        total_return_pct=0.0,
        sharpe_ratio=0.0,
        sortino_ratio=0.0,
        max_drawdown_pct=0.0,
        win_rate_pct=0.0,
        total_trades=0,
        profit_factor=0.0,
        data_points=[],
    )


async def _get_feature_flags() -> Dict[str, bool]:
    """Load feature flags from config or Supabase."""
    defaults: Dict[str, bool] = {
        "dream_mode": False,
        "live_trading": False,
        "paper_trading": True,
        "multi_agent_debate": True,
        "ml_signals": True,
        "auto_position_sizing": True,
        "news_sentiment": False,
        "social_sentiment": False,
    }
    try:
        from src.db.supabase_client import SupabaseClient
        from src.config import get_settings
        s = get_settings()
        client = await SupabaseClient.get_instance(url=s.supabase_url, key=s.supabase_service_key)
        result = await asyncio.to_thread(
            lambda: client._client.table("feature_flags").select("*").execute()  # type: ignore[union-attr]
        )
        for row in result.data:
            defaults[row["name"]] = row.get("enabled", False)
    except Exception:
        pass
    return defaults


async def _set_feature_flag(flag: str, enabled: bool) -> None:
    """Persist a feature flag change to Supabase."""
    try:
        from src.db.supabase_client import SupabaseClient
        from src.config import get_settings
        s = get_settings()
        client = await SupabaseClient.get_instance(url=s.supabase_url, key=s.supabase_service_key)
        await asyncio.to_thread(
            lambda: client._client.table("feature_flags")  # type: ignore[union-attr]
            .upsert({"name": flag, "enabled": enabled, "updated_at": datetime.now(timezone.utc).isoformat()})
            .execute()
        )
    except Exception as exc:
        log.warning("feature_flag_persist_failed", flag=flag, error=str(exc))


async def _get_circuit_breakers() -> List[CircuitBreakerStatus]:
    """Fetch circuit breaker states."""
    return []


async def _close_all_positions(reason: str) -> int:
    """Close all open positions. Returns count of positions closed."""
    log.warning("emergency_close_all_positions", reason=reason)
    return 0


async def _fetch_signals(market: Optional[str], limit: int) -> list:
    """Fetch recent signals from Supabase."""
    return []


async def _fetch_debates(limit: int) -> list:
    """Fetch recent debate records from Supabase."""
    return []


def _build_trade_query(client: Any, market: Optional[str], status: Optional[str], limit: int) -> list:
    q = client._client.table("trades").select("*").order("entry_time", desc=True).limit(limit)  # type: ignore[union-attr]
    if market:
        q = q.eq("market", market)
    if status:
        q = q.eq("status", status)
    return q.execute().data


def _row_to_trade_response(row: Dict[str, Any]):
    from src.api.schemas import TradeResponse
    return TradeResponse(
        trade_id=row.get("id", ""),
        symbol=row.get("symbol", ""),
        market=row.get("market", ""),
        direction=row.get("direction", ""),
        status=row.get("status", ""),
        entry_time=row.get("entry_time", datetime.now(timezone.utc)),
        exit_time=row.get("exit_time"),
        entry_price=row.get("entry_price", 0.0),
        exit_price=row.get("exit_price"),
        size=row.get("size", 0.0),
        realized_pnl=row.get("realized_pnl"),
        commission=row.get("commission", 0.0),
        slippage=row.get("slippage", 0.0),
        exit_reason=row.get("exit_reason"),
        signal_id=row.get("signal_id"),
    )


def _row_to_portfolio_response(row: Dict[str, Any]) -> PortfolioSummaryResponse:
    return PortfolioSummaryResponse(
        total_equity=row.get("equity", 100_000.0),
        cash=row.get("cash", 100_000.0),
        positions_value=row.get("positions_value", 0.0),
        unrealized_pnl=row.get("unrealized_pnl", 0.0),
        unrealized_pnl_pct=row.get("unrealized_pnl_pct", 0.0),
        realized_pnl_today=row.get("realized_pnl_today", 0.0),
        total_pnl=row.get("total_pnl", 0.0),
        total_pnl_pct=row.get("total_pnl_pct", 0.0),
        peak_equity=row.get("peak_equity", 100_000.0),
        drawdown_pct=row.get("drawdown_pct", 0.0),
        open_positions=[],
        n_open_positions=row.get("n_positions", 0),
        daily_pnl_series=[],
        mode=row.get("mode", "paper"),
        timestamp=row.get("created_at", datetime.now(timezone.utc)),
    )


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def run_server(host: str = "0.0.0.0", port: int = 8000, reload: bool = False) -> None:
    """Launch the uvicorn ASGI server."""
    try:
        import uvloop
        uvloop.install()
        log.info("api_uvloop_installed")
    except ImportError:
        log.info("api_uvloop_unavailable", note="Install uvloop for better performance")

    uvicorn.run(
        "src.api.server:app",
        host=host,
        port=port,
        reload=reload,
        log_level="info",
        access_log=True,
    )


if __name__ == "__main__":
    run_server()
