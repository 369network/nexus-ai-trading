"""
src/monitoring/prometheus_metrics.py
-------------------------------------
Prometheus instrumentation for the NEXUS ALPHA trading system.

All metrics are module-level singletons exposed via the ``METRICS`` object so
any module can do::

    from src.monitoring.prometheus_metrics import METRICS

    METRICS.trades_total.labels(market="crypto", side="long", strategy="momentum").inc()

Starting the HTTP server::

    from src.monitoring.prometheus_metrics import MetricsServer
    import asyncio

    asyncio.run(MetricsServer().start_server(port=8000))
"""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from typing import Final

import structlog
from prometheus_client import (
    Counter,
    Gauge,
    Histogram,
    Summary,
    start_http_server,
    REGISTRY,
    CollectorRegistry,
)

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Histogram bucket presets
# ---------------------------------------------------------------------------

# Sub-second latency buckets (0 ms → 10 s)
_LATENCY_BUCKETS: Final = (
    0.001, 0.005, 0.01, 0.025, 0.05,
    0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0,
)

# Order execution is slightly slower; include up to 30 s
_EXECUTION_BUCKETS: Final = (
    0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0, 30.0,
)

# LLM calls can be slow; up to 2 minutes
_LLM_BUCKETS: Final = (
    0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0, 60.0, 120.0,
)

# P&L buckets in percentage points (-20 % … +20 %)
_PNL_BUCKETS: Final = (
    -20.0, -10.0, -5.0, -2.0, -1.0, -0.5,
    0.0,
    0.5, 1.0, 2.0, 5.0, 10.0, 20.0,
)


# ---------------------------------------------------------------------------
# Metrics container
# ---------------------------------------------------------------------------

@dataclass
class NexusMetrics:
    """
    Container for all Prometheus metrics used by the NEXUS ALPHA system.

    Counters  — monotonically increasing event counts
    Gauges    — current snapshot values (can go up or down)
    Histograms — distribution of a measured value
    Summaries — sliding-window quantiles (client-side)
    """

    # ------------------------------------------------------------------
    # Counters
    # ------------------------------------------------------------------

    trades_total: Counter = field(init=False)
    """Total number of trades executed. Labels: market, side, strategy."""

    signals_generated_total: Counter = field(init=False)
    """Total number of trading signals generated. Labels: market."""

    errors_total: Counter = field(init=False)
    """Total number of errors encountered. Labels: component."""

    llm_requests_total: Counter = field(init=False)
    """Total LLM API calls made. Labels: model, status (success/error)."""

    circuit_breaker_trips_total: Counter = field(init=False)
    """Total number of circuit breaker activations. Labels: name."""

    # ------------------------------------------------------------------
    # Gauges
    # ------------------------------------------------------------------

    portfolio_value: Gauge = field(init=False)
    """Current portfolio net asset value in USD."""

    daily_pnl: Gauge = field(init=False)
    """Today's realized + unrealized P&L in USD."""

    open_positions: Gauge = field(init=False)
    """Number of currently open positions."""

    circuit_breakers_active: Gauge = field(init=False)
    """Count of circuit breakers that are currently triggered."""

    llm_cost_daily: Gauge = field(init=False)
    """Estimated LLM API spend for the current UTC day in USD."""

    portfolio_heat: Gauge = field(init=False)
    """Portfolio heat — fraction of capital at risk (0–1)."""

    current_drawdown_pct: Gauge = field(init=False)
    """Current drawdown from equity peak as a negative percentage."""

    # ------------------------------------------------------------------
    # Histograms
    # ------------------------------------------------------------------

    signal_latency_seconds: Histogram = field(init=False)
    """End-to-end latency from data ingestion to signal emission. Labels: market."""

    order_execution_latency_seconds: Histogram = field(init=False)
    """Latency between order submission and exchange confirmation."""

    llm_response_time_seconds: Histogram = field(init=False)
    """LLM response time per request. Labels: model."""

    # ------------------------------------------------------------------
    # Summaries
    # ------------------------------------------------------------------

    trade_pnl_percent: Summary = field(init=False)
    """Distribution of trade P&L as a percentage of entry value."""

    def __post_init__(self) -> None:
        self._init_counters()
        self._init_gauges()
        self._init_histograms()
        self._init_summaries()

    # ------------------------------------------------------------------
    # Initializers (kept separate so __post_init__ stays readable)
    # ------------------------------------------------------------------

    def _init_counters(self) -> None:
        self.trades_total = Counter(
            "nexus_trades_total",
            "Total number of trades executed",
            labelnames=["market", "side", "strategy"],
        )
        self.signals_generated_total = Counter(
            "nexus_signals_generated_total",
            "Total number of trading signals generated",
            labelnames=["market"],
        )
        self.errors_total = Counter(
            "nexus_errors_total",
            "Total number of errors encountered by component",
            labelnames=["component"],
        )
        self.llm_requests_total = Counter(
            "nexus_llm_requests_total",
            "Total LLM API calls",
            labelnames=["model", "status"],
        )
        self.circuit_breaker_trips_total = Counter(
            "nexus_circuit_breaker_trips_total",
            "Total circuit breaker activations",
            labelnames=["name"],
        )

    def _init_gauges(self) -> None:
        self.portfolio_value = Gauge(
            "nexus_portfolio_value_usd",
            "Current portfolio net asset value in USD",
        )
        self.daily_pnl = Gauge(
            "nexus_daily_pnl_usd",
            "Today's realized and unrealized P&L in USD",
        )
        self.open_positions = Gauge(
            "nexus_open_positions",
            "Number of currently open positions",
        )
        self.circuit_breakers_active = Gauge(
            "nexus_circuit_breakers_active",
            "Count of circuit breakers currently triggered",
        )
        self.llm_cost_daily = Gauge(
            "nexus_llm_cost_daily_usd",
            "Estimated LLM API spend for the current UTC day in USD",
        )
        self.portfolio_heat = Gauge(
            "nexus_portfolio_heat",
            "Portfolio heat — fraction of capital at risk (0–1)",
        )
        self.current_drawdown_pct = Gauge(
            "nexus_current_drawdown_pct",
            "Current drawdown from equity peak as a percentage (negative)",
        )

    def _init_histograms(self) -> None:
        self.signal_latency_seconds = Histogram(
            "nexus_signal_latency_seconds",
            "End-to-end latency from data ingestion to signal emission",
            labelnames=["market"],
            buckets=_LATENCY_BUCKETS,
        )
        self.order_execution_latency_seconds = Histogram(
            "nexus_order_execution_latency_seconds",
            "Latency between order submission and exchange confirmation",
            buckets=_EXECUTION_BUCKETS,
        )
        self.llm_response_time_seconds = Histogram(
            "nexus_llm_response_time_seconds",
            "LLM response time per request",
            labelnames=["model"],
            buckets=_LLM_BUCKETS,
        )

    def _init_summaries(self) -> None:
        self.trade_pnl_percent = Summary(
            "nexus_trade_pnl_percent",
            "Distribution of trade P&L as a percentage of entry notional",
        )


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

METRICS: Final[NexusMetrics] = NexusMetrics()


# ---------------------------------------------------------------------------
# MetricsServer
# ---------------------------------------------------------------------------

class MetricsServer:
    """
    Starts a Prometheus /metrics HTTP endpoint.

    Usage (synchronous context)::

        MetricsServer().start_server(port=8000)

    Usage (async context, non-blocking)::

        server = MetricsServer()
        await server.start_server_async(port=8000)
    """

    def start_server(self, port: int = 8000, addr: str = "") -> None:
        """
        Start the prometheus_client built-in HTTP server on *port*.

        This call blocks the current thread's event loop so it should be
        called from a dedicated thread or before ``asyncio.run()``.
        """
        logger.info("prometheus_metrics_server_starting", port=port, addr=addr or "0.0.0.0")
        start_http_server(port=port, addr=addr)
        logger.info("prometheus_metrics_server_started", port=port)

    def start_server_in_thread(self, port: int = 8000, addr: str = "") -> threading.Thread:
        """
        Start the metrics HTTP server in a daemon thread.

        Returns the thread so the caller can join it if needed.
        """
        t = threading.Thread(
            target=self.start_server,
            args=(port, addr),
            daemon=True,
            name="prometheus-metrics",
        )
        t.start()
        logger.info(
            "prometheus_metrics_thread_started",
            port=port,
            thread_id=t.ident,
        )
        return t

    async def start_server_async(self, port: int = 8000, addr: str = "") -> None:
        """
        Start the metrics server in the default thread-pool executor so the
        async event loop is not blocked.
        """
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, self.start_server, port, addr)
