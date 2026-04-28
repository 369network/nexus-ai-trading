"""
src/monitoring/health_check.py
--------------------------------
Health check and readiness probe for NEXUS ALPHA.

HTTP endpoints (via aiohttp):
  GET /health  — returns full HealthReport as JSON (always 200)
  GET /ready   — returns 200 only when all *critical* checks pass;
                 returns 503 otherwise (blocks k8s/systemd startup).

Usage::

    import asyncio
    from src.monitoring.health_check import HealthCheckServer

    asyncio.run(HealthCheckServer().run(host="0.0.0.0", port=9090))
"""

from __future__ import annotations

import asyncio
import os
import shutil
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import aiohttp
import structlog
from aiohttp import web

logger = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Status enumerations
# ---------------------------------------------------------------------------

class ComponentStatus(str, Enum):
    OK       = "ok"
    DEGRADED = "degraded"
    CRITICAL = "critical"
    UNKNOWN  = "unknown"


class OverallStatus(str, Enum):
    HEALTHY  = "healthy"
    DEGRADED = "degraded"
    CRITICAL = "critical"


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ComponentCheck:
    """Result of a single component health check."""
    name: str
    status: ComponentStatus
    message: str
    latency_ms: float
    is_critical: bool
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":        self.name,
            "status":      self.status.value,
            "message":     self.message,
            "latency_ms":  round(self.latency_ms, 2),
            "is_critical": self.is_critical,
            "details":     self.details,
        }


@dataclass
class HealthReport:
    """Aggregated report returned by /health."""
    overall_status: OverallStatus
    component_statuses: dict[str, ComponentCheck]
    timestamp: str
    uptime_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_status":      self.overall_status.value,
            "timestamp":           self.timestamp,
            "uptime_seconds":      round(self.uptime_seconds, 1),
            "component_statuses":  {
                name: check.to_dict()
                for name, check in self.component_statuses.items()
            },
        }

    @property
    def all_critical_ok(self) -> bool:
        return all(
            c.status == ComponentStatus.OK
            for c in self.component_statuses.values()
            if c.is_critical
        )


# ---------------------------------------------------------------------------
# Individual check helpers
# ---------------------------------------------------------------------------

async def _timed_check(coro: Any) -> tuple[Any, float]:
    """Run *coro* and return (result, elapsed_ms)."""
    t0 = time.perf_counter()
    result = await coro
    elapsed = (time.perf_counter() - t0) * 1000
    return result, elapsed


# ---------------------------------------------------------------------------
# HealthChecker
# ---------------------------------------------------------------------------

class HealthChecker:
    """
    Runs all component health checks and returns a :class:`HealthReport`.

    Configuration is read from environment variables so nothing is hard-coded.
    """

    def __init__(self) -> None:
        self._start_time: float = time.time()

        # Connection strings / URLs from environment
        self._db_url:      str = os.getenv("DATABASE_URL", "")
        self._redis_url:   str = os.getenv("REDIS_URL", "redis://localhost:6379")
        self._ollama_url:  str = os.getenv("OLLAMA_URL", "http://localhost:11434")
        self._ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3")

        # Exchange API health endpoints (comma-separated list of URLs)
        self._exchange_urls: list[str] = [
            u.strip()
            for u in os.getenv(
                "EXCHANGE_HEALTH_URLS",
                "https://api.binance.com/api/v3/ping,"
                "https://api.coinbase.com/v2/time",
            ).split(",")
            if u.strip()
        ]

        # Thresholds
        self._min_disk_gb:   float = float(os.getenv("HEALTH_MIN_DISK_GB", "1.0"))
        self._max_memory_pct: float = float(os.getenv("HEALTH_MAX_MEMORY_PCT", "90.0"))

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def check_all(self) -> HealthReport:
        """Run all checks concurrently and return an aggregated report."""
        checks_coros = [
            self._check_database(),
            self._check_redis(),
            self._check_exchange_apis(),
            self._check_ollama(),
            self._check_disk_space(),
            self._check_memory(),
        ]

        results: list[ComponentCheck] = await asyncio.gather(
            *checks_coros, return_exceptions=False
        )

        component_statuses = {c.name: c for c in results}
        overall = self._compute_overall(component_statuses)

        import datetime
        report = HealthReport(
            overall_status=overall,
            component_statuses=component_statuses,
            timestamp=datetime.datetime.utcnow().isoformat() + "Z",
            uptime_seconds=time.time() - self._start_time,
        )

        logger.info(
            "health_check_complete",
            overall=overall.value,
            components={k: v.status.value for k, v in component_statuses.items()},
        )
        return report

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    async def _check_database(self) -> ComponentCheck:
        """Verify PostgreSQL connectivity via asyncpg."""
        if not self._db_url:
            return ComponentCheck(
                name="database",
                status=ComponentStatus.UNKNOWN,
                message="DATABASE_URL not set",
                latency_ms=0,
                is_critical=True,
            )
        try:
            import asyncpg  # type: ignore[import]

            async def _ping() -> None:
                conn = await asyncpg.connect(self._db_url, timeout=5)
                await conn.fetchval("SELECT 1")
                await conn.close()

            _, latency = await _timed_check(_ping())
            return ComponentCheck(
                name="database",
                status=ComponentStatus.OK,
                message="PostgreSQL reachable",
                latency_ms=latency,
                is_critical=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("health_check_database_failed", error=str(exc))
            return ComponentCheck(
                name="database",
                status=ComponentStatus.CRITICAL,
                message=f"Database unreachable: {exc}",
                latency_ms=0,
                is_critical=True,
            )

    async def _check_redis(self) -> ComponentCheck:
        """Verify Redis connectivity via aioredis / redis-py async."""
        try:
            import redis.asyncio as aioredis  # type: ignore[import]

            async def _ping() -> None:
                r = aioredis.from_url(self._redis_url, socket_connect_timeout=3)
                await r.ping()
                await r.aclose()

            _, latency = await _timed_check(_ping())
            return ComponentCheck(
                name="redis",
                status=ComponentStatus.OK,
                message="Redis reachable",
                latency_ms=latency,
                is_critical=True,
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("health_check_redis_failed", error=str(exc))
            return ComponentCheck(
                name="redis",
                status=ComponentStatus.CRITICAL,
                message=f"Redis unreachable: {exc}",
                latency_ms=0,
                is_critical=True,
            )

    async def _check_exchange_apis(self) -> ComponentCheck:
        """Ping each configured exchange health endpoint."""
        failures: list[str] = []
        latencies: list[float] = []

        timeout = aiohttp.ClientTimeout(total=5)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            for url in self._exchange_urls:
                t0 = time.perf_counter()
                try:
                    async with session.get(url) as resp:
                        elapsed = (time.perf_counter() - t0) * 1000
                        latencies.append(elapsed)
                        if resp.status >= 500:
                            failures.append(f"{url} → HTTP {resp.status}")
                except Exception as exc:  # noqa: BLE001
                    failures.append(f"{url} → {exc}")

        avg_latency = sum(latencies) / len(latencies) if latencies else 0

        if not failures:
            return ComponentCheck(
                name="exchange_apis",
                status=ComponentStatus.OK,
                message=f"All {len(self._exchange_urls)} exchanges reachable",
                latency_ms=avg_latency,
                is_critical=False,
                details={"checked": self._exchange_urls},
            )

        status = (
            ComponentStatus.CRITICAL
            if len(failures) == len(self._exchange_urls)
            else ComponentStatus.DEGRADED
        )
        return ComponentCheck(
            name="exchange_apis",
            status=status,
            message=f"{len(failures)} exchange(s) unreachable",
            latency_ms=avg_latency,
            is_critical=False,
            details={"failures": failures},
        )

    async def _check_ollama(self) -> ComponentCheck:
        """Verify the Ollama model is loaded and responding."""
        url = f"{self._ollama_url}/api/show"
        timeout = aiohttp.ClientTimeout(total=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                t0 = time.perf_counter()
                async with session.post(
                    url, json={"name": self._ollama_model}
                ) as resp:
                    elapsed = (time.perf_counter() - t0) * 1000
                    if resp.status == 200:
                        body = await resp.json()
                        return ComponentCheck(
                            name="ollama",
                            status=ComponentStatus.OK,
                            message=f"Model '{self._ollama_model}' loaded",
                            latency_ms=elapsed,
                            is_critical=False,
                            details={"model": body.get("modelfile", "")[:80]},
                        )
                    return ComponentCheck(
                        name="ollama",
                        status=ComponentStatus.DEGRADED,
                        message=f"Ollama returned HTTP {resp.status}",
                        latency_ms=elapsed,
                        is_critical=False,
                    )
        except Exception as exc:  # noqa: BLE001
            logger.warning("health_check_ollama_failed", error=str(exc))
            return ComponentCheck(
                name="ollama",
                status=ComponentStatus.DEGRADED,
                message=f"Ollama unreachable: {exc}",
                latency_ms=0,
                is_critical=False,
            )

    async def _check_disk_space(self) -> ComponentCheck:
        """Ensure at least *_min_disk_gb* GB of free disk space remains."""
        usage = shutil.disk_usage("/")
        free_gb = usage.free / (1024 ** 3)

        if free_gb >= self._min_disk_gb:
            return ComponentCheck(
                name="disk_space",
                status=ComponentStatus.OK,
                message=f"{free_gb:.1f} GB free",
                latency_ms=0,
                is_critical=False,
                details={"free_gb": round(free_gb, 2), "threshold_gb": self._min_disk_gb},
            )
        return ComponentCheck(
            name="disk_space",
            status=ComponentStatus.CRITICAL,
            message=f"Only {free_gb:.1f} GB free (threshold {self._min_disk_gb} GB)",
            latency_ms=0,
            is_critical=True,
            details={"free_gb": round(free_gb, 2), "threshold_gb": self._min_disk_gb},
        )

    async def _check_memory(self) -> ComponentCheck:
        """Warn when system memory usage exceeds *_max_memory_pct* %."""
        try:
            import psutil  # type: ignore[import]

            vm = psutil.virtual_memory()
            used_pct = vm.percent

            status = (
                ComponentStatus.OK
                if used_pct < self._max_memory_pct
                else ComponentStatus.DEGRADED
            )
            return ComponentCheck(
                name="memory",
                status=status,
                message=f"Memory usage {used_pct:.1f}%",
                latency_ms=0,
                is_critical=False,
                details={
                    "used_pct":   round(used_pct, 1),
                    "threshold":  self._max_memory_pct,
                    "used_gb":    round(vm.used / (1024 ** 3), 2),
                    "total_gb":   round(vm.total / (1024 ** 3), 2),
                },
            )
        except ImportError:
            return ComponentCheck(
                name="memory",
                status=ComponentStatus.UNKNOWN,
                message="psutil not installed; memory check skipped",
                latency_ms=0,
                is_critical=False,
            )

    # ------------------------------------------------------------------
    # Aggregation
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_overall(
        checks: dict[str, ComponentCheck],
    ) -> OverallStatus:
        statuses = {c.status for c in checks.values()}
        if ComponentStatus.CRITICAL in statuses:
            return OverallStatus.CRITICAL
        if ComponentStatus.DEGRADED in statuses:
            return OverallStatus.DEGRADED
        return OverallStatus.HEALTHY


# ---------------------------------------------------------------------------
# aiohttp web application
# ---------------------------------------------------------------------------

class HealthCheckServer:
    """
    Exposes /health and /ready HTTP endpoints via aiohttp.

    /health  — always 200; returns full JSON report
    /ready   — 200 when all critical checks pass, 503 otherwise
    """

    def __init__(self) -> None:
        self._checker = HealthChecker()

    async def handle_health(self, request: web.Request) -> web.Response:  # noqa: ARG002
        report = await self._checker.check_all()
        return web.json_response(report.to_dict(), status=200)

    async def handle_ready(self, request: web.Request) -> web.Response:  # noqa: ARG002
        report = await self._checker.check_all()
        if report.all_critical_ok:
            return web.json_response({"ready": True}, status=200)
        return web.json_response(
            {
                "ready": False,
                "failing_checks": [
                    c.name
                    for c in report.component_statuses.values()
                    if c.is_critical and c.status != ComponentStatus.OK
                ],
            },
            status=503,
        )

    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get("/health", self.handle_health)
        app.router.add_get("/ready",  self.handle_ready)
        return app

    async def run(self, host: str = "0.0.0.0", port: int = 9090) -> None:
        app = self.build_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, host=host, port=port)
        await site.start()
        logger.info("health_check_server_started", host=host, port=port)
        # Run until cancelled
        try:
            await asyncio.Event().wait()
        finally:
            await runner.cleanup()


# ---------------------------------------------------------------------------
# Entrypoint (python -m src.monitoring.health_check)
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NEXUS ALPHA health check server")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()

    asyncio.run(HealthCheckServer().run(host=args.host, port=args.port))
