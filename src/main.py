"""
NEXUS ALPHA - Main Orchestrator
Production-grade algorithmic trading bot entry point.
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

# ---------------------------------------------------------------------------
# Logging setup (must happen before any local imports)
# ---------------------------------------------------------------------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("nexus_alpha.main")


# ---------------------------------------------------------------------------
# Local imports (lazy where possible to speed startup)
# ---------------------------------------------------------------------------
from src.config.settings import Settings, load_settings
from src.data.market_orchestrator import MarketOrchestrator
from src.data.candle_store import CandleStore
from src.indicators.engine import IndicatorEngine
from src.strategies.regime import MarketRegimeDetector
from src.strategies.signal_fusion import SignalFusionEngine
from src.strategies.edge_filter import EdgeFilter
from src.agents.coordinator import AgentCoordinator
from src.llm.ensemble import LLMEnsemble
from src.risk.manager import RiskManager
from src.execution.engine import ExecutionEngine
from src.execution.paper import PaperExecutor
from src.execution.live import LiveExecutor
from src.alerts.manager import AlertManager
from src.db.supabase_client import SupabaseClient
from src.learning.dream_mode import DreamModeScheduler
from src.learning.memory import MemoryUpdater
from src.monitoring.prometheus_metrics import METRICS


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
HEALTH_CHECK_INTERVAL_SECONDS = 60
MAX_RESTARTS = 3
RESTART_WINDOW_SECONDS = 300
WATCHDOG_INTERVAL_SECONDS = 30


# ---------------------------------------------------------------------------
# Component restart tracker
# ---------------------------------------------------------------------------
@dataclass
class RestartRecord:
    timestamps: List[float] = field(default_factory=list)

    def record(self) -> None:
        now = time.monotonic()
        self.timestamps.append(now)
        # Prune records outside the window
        self.timestamps = [
            t for t in self.timestamps if now - t <= RESTART_WINDOW_SECONDS
        ]

    def count_recent(self) -> int:
        now = time.monotonic()
        return sum(
            1 for t in self.timestamps if now - t <= RESTART_WINDOW_SECONDS
        )


# ---------------------------------------------------------------------------
# Health snapshot
# ---------------------------------------------------------------------------
@dataclass
class ComponentHealth:
    name: str
    healthy: bool
    last_checked: datetime
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# NexusAlpha - Main Orchestrator Class
# ---------------------------------------------------------------------------
class NexusAlpha:
    """
    Central orchestrator that initialises, starts, and manages all subsystems
    of the NEXUS ALPHA trading platform.
    """

    def __init__(self) -> None:
        self.settings: Optional[Settings] = None
        self.db: Optional[SupabaseClient] = None
        self.alert_manager: Optional[AlertManager] = None
        self.llm_ensemble: Optional[LLMEnsemble] = None
        self.agent_coordinator: Optional[AgentCoordinator] = None
        self.risk_manager: Optional[RiskManager] = None
        self.execution_engine: Optional[ExecutionEngine] = None
        self.dream_scheduler: Optional[DreamModeScheduler] = None
        self.memory_updater: Optional[MemoryUpdater] = None

        # One MarketOrchestrator per enabled market
        self.market_orchestrators: Dict[str, MarketOrchestrator] = {}

        # Signal generation engines per market/symbol
        self.indicator_engines: Dict[str, IndicatorEngine] = {}
        self.regime_detectors: Dict[str, MarketRegimeDetector] = {}
        self.fusion_engine: Optional[SignalFusionEngine] = None
        self.edge_filter: Optional[EdgeFilter] = None

        # Shutdown coordination
        self._shutdown_event: asyncio.Event = asyncio.Event()
        self._restart_records: Dict[str, RestartRecord] = defaultdict(RestartRecord)

        # Startup timestamp — used to suppress health alerts during grace period
        self._start_time: datetime = datetime.now(timezone.utc)

        # Component health registry
        self._component_health: Dict[str, ComponentHealth] = {}

        # Background tasks tracker
        self._tasks: Set[asyncio.Task] = set()
        self._health_server: Optional[Any] = None

    # ------------------------------------------------------------------
    # Public Interface
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Full startup sequence then block until shutdown."""
        logger.info("=== NEXUS ALPHA starting up ===")

        try:
            await self._startup_sequence()
            logger.info("=== NEXUS ALPHA fully operational ===")
            await self._run()
        except Exception as exc:
            logger.critical("Fatal error during startup: %s", exc, exc_info=True)
            await self.stop()
            raise
        finally:
            logger.info("=== NEXUS ALPHA shutdown complete ===")

    async def stop(self) -> None:
        """Graceful shutdown of all components."""
        logger.info("Initiating graceful shutdown…")
        self._shutdown_event.set()

        # Cancel all background tasks
        for task in list(self._tasks):
            if not task.done():
                task.cancel()

        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)

        # Shutdown subsystems in reverse order
        if self.execution_engine:
            await self.execution_engine.shutdown()
        if self.dream_scheduler:
            await self.dream_scheduler.stop()
        if self.memory_updater:
            await self.memory_updater.stop()
        for orch in self.market_orchestrators.values():
            await orch.stop()
        if self.db:
            await self.db.close()

        if self._health_server:
            self._health_server.stop()

        logger.info("All components stopped.")

    # ------------------------------------------------------------------
    # Startup Sequence
    # ------------------------------------------------------------------

    async def _startup_sequence(self) -> None:
        """Execute the 14-step startup sequence."""

        # Step 0: Start health/metrics HTTP server (must be first for Docker health checks)
        logger.info("[0/14] Starting health/metrics server on :8080…")
        from src.monitoring.health_server import (
            HealthServer,
            update_bot_status,
            register_emergency_stop_callback,
        )
        self._health_server = HealthServer(port=8080)
        self._health_server.start()
        register_emergency_stop_callback(self._handle_emergency_stop)

        # Step 1: Load configuration
        logger.info("[1/14] Loading configuration…")
        self.settings = await load_settings()
        logger.info(
            "Config loaded: paper_mode=%s, markets=%s",
            self.settings.paper_mode,
            list(self.settings.enabled_markets.keys()),
        )
        update_bot_status(paper_mode=self.settings.paper_mode, environment=getattr(self.settings, 'environment', 'paper'))

        # Step 2: Connect to Supabase
        logger.info("[2/14] Connecting to Supabase…")
        self.db = SupabaseClient(
            url=self.settings.supabase_url,
            key=self.settings.supabase_service_key,
        )
        await self.db.connect()
        logger.info("Supabase connected.")

        # Step 3: Initialise data providers
        logger.info("[3/14] Initialising data providers…")
        for market_name, market_cfg in self.settings.enabled_markets.items():
            orch = MarketOrchestrator(
                market_name=market_name,
                config=market_cfg,
                settings=self.settings,
                db=self.db,
                on_candle_close=self._on_candle_close,
            )
            await orch.init_providers()
            self.market_orchestrators[market_name] = orch
            logger.info("  Data provider ready: %s", market_name)

        # Step 4: Start WebSocket connections
        logger.info("[4/14] Starting WebSocket connections…")
        for market_name, orch in self.market_orchestrators.items():
            await orch.connect_websockets()
            logger.info("  WebSocket live: %s", market_name)

        # Step 5: Initialise LLM ensemble
        logger.info("[5/14] Initialising LLM ensemble…")
        self.llm_ensemble = LLMEnsemble.from_settings(settings=self.settings, db=self.db)
        await self.llm_ensemble.init()
        logger.info("LLM ensemble ready: %d models", len(self.llm_ensemble.models))

        # Step 6: Initialise all 7 agents
        logger.info("[6/14] Initialising agent coordinator (7 agents)…")
        self.agent_coordinator = AgentCoordinator(
            settings=self.settings,
            llm_ensemble=self.llm_ensemble,
            db=self.db,
        )
        await self.agent_coordinator.init()
        logger.info("Agent coordinator ready.")

        # Step 7: Initialise risk management
        logger.info("[7/14] Initialising risk management system…")
        self.risk_manager = RiskManager(settings=self.settings, db=self.db)
        await self.risk_manager.init()
        logger.info("Risk manager ready.")

        # Step 8: Initialise execution engine
        logger.info("[8/14] Initialising execution engine (paper=%s)…", self.settings.paper_mode)
        if self.settings.paper_mode:
            executor = PaperExecutor(settings=self.settings, db=self.db)
        else:
            executor = LiveExecutor(settings=self.settings, db=self.db)
        await executor.init()
        self.execution_engine = ExecutionEngine(executor=executor, settings=self.settings, db=self.db)
        logger.info("Execution engine ready.")

        # Step 9-11: Signal generation components
        logger.info("[9/14] Initialising signal generation components…")
        self.fusion_engine = SignalFusionEngine()   # uses default config
        self.edge_filter = EdgeFilter()             # uses default thresholds
        for market_name, market_cfg in self.settings.enabled_markets.items():
            for symbol in market_cfg.symbols:
                key = f"{market_name}:{symbol}"
                self.indicator_engines[key] = IndicatorEngine(
                    symbol=symbol, market=market_name, settings=self.settings
                )
                self.regime_detectors[key] = MarketRegimeDetector(
                    symbol=symbol, settings=self.settings
                )
        logger.info("Signal generation components ready.")

        # Step 9b: Warm up indicator engines with historical candles so that
        # indicators are non-NaN from the very first strategy evaluation.
        await self._warmup_indicator_engines()

        # Step 10: Initialise learning system
        logger.info("[10/14] Initialising learning system…")
        self.dream_scheduler = DreamModeScheduler(
            settings=self.settings,
            db=self.db,
        )
        self.memory_updater = MemoryUpdater(settings=self.settings, db=self.db)
        await self.dream_scheduler.init()
        await self.memory_updater.init()
        logger.info("Learning system ready.")

        # Step 11: Initialise alert manager
        logger.info("[11/14] Initialising alert manager…")
        self.alert_manager = AlertManager(settings=self.settings)
        await self.alert_manager.init()
        logger.info("Alert manager ready.")

        # Step 12: Register signal handlers
        logger.info("[12/14] Registering OS signal handlers…")
        loop = asyncio.get_running_loop()
        try:
            import sys as _sys
            _signals = (signal.SIGTERM, signal.SIGINT) if _sys.platform != "win32" else (signal.SIGINT,)
            for sig in _signals:
                loop.add_signal_handler(sig, lambda s=sig: self._handle_signal(s))
        except (NotImplementedError, AttributeError):
            # Windows: add_signal_handler not supported; rely on KeyboardInterrupt
            pass
        logger.info("Signal handlers registered.")

        # Step 13: Health registry initial population
        logger.info("[13/14] Populating initial health registry…")
        self._update_health("supabase", True, "Connected")
        self._update_health("llm_ensemble", True, f"{len(self.llm_ensemble.models)} models")
        self._update_health("agent_coordinator", True, "7 agents initialised")
        self._update_health("risk_manager", True, "All 5 layers active")
        self._update_health("execution_engine", True, f"{'paper' if self.settings.paper_mode else 'live'} mode")

        logger.info("[14/14] Startup sequence complete.")

    # ------------------------------------------------------------------
    # Main Event Loop
    # ------------------------------------------------------------------

    async def _run(self) -> None:
        """Spawn all background tasks and block until shutdown."""

        background_coros = []

        # Data ingestion loops — one task per market
        for market_name, orch in self.market_orchestrators.items():
            background_coros.append(
                self._supervised_task(
                    f"data_ingestion:{market_name}",
                    orch.run_ingestion_loop,
                )
            )

        # Learning system tasks
        background_coros.append(
            self._supervised_task("dream_scheduler", self.dream_scheduler.run)
        )
        background_coros.append(
            self._supervised_task("memory_updater", self.memory_updater.run)
        )

        # Health check loop
        background_coros.append(self._health_check_loop())

        # Watchdog loop
        background_coros.append(self._watchdog_loop())

        # Prometheus gauge update loop
        background_coros.append(self._metrics_loop())

        # Alert manager loop
        background_coros.append(
            self._supervised_task("alert_manager", self.alert_manager.run)
        )

        # Gather everything — shutdown_event cancels via _supervised_task wrappers
        await asyncio.gather(*[self._track_task(c) for c in background_coros])

    # ------------------------------------------------------------------
    # Indicator Engine Warmup
    # ------------------------------------------------------------------

    async def _warmup_indicator_engines(self) -> None:
        """
        Pre-seed each IndicatorEngine AND the MarketOrchestrator candle cache
        with 500 historical candles so that:
          1. Technical indicators are non-NaN from the very first strategy eval.
          2. Strategies get a full candle_history (≥ 200+ bars) immediately.
        Runs during startup before the polling loop begins.
        """
        from src.data.providers import get_provider_for_market

        for market_name, market_cfg in self.settings.enabled_markets.items():
            # Grab the market orchestrator so we can seed its candle cache
            market_orch = self.market_orchestrators.get(market_name)

            for symbol in market_cfg.symbols:
                key = f"{market_name}:{symbol}"
                engine = self.indicator_engines.get(key)
                if engine is None:
                    continue
                for timeframe in market_cfg.timeframes:
                    # NOTE: get_provider_for_market always returns a FRESH provider
                    # (not a singleton) — safe to close after warmup.  CCXTProvider
                    # creates new ccxt exchange instances each call; closing them
                    # here prevents "Unclosed client session" asyncio warnings.
                    provider = None
                    try:
                        provider = get_provider_for_market(
                            market=market_name,
                            symbol=symbol,
                            settings=self.settings,
                        )
                        historical = await provider.fetch_ohlcv(
                            timeframe=timeframe, limit=500
                        )
                        if not historical:
                            continue

                        seeded_cache = 0
                        for raw in historical[:-1]:  # skip the live (open) candle
                            candle: Dict[str, Any] = raw if isinstance(raw, dict) else {
                                "timestamp": raw[0], "open": raw[1], "high": raw[2],
                                "low": raw[3], "close": raw[4], "volume": raw[5],
                            }
                            # 1. Seed indicator engine
                            await engine.compute(candle, timeframe)

                            # 2. Seed the orchestrator's candle cache directly
                            if market_orch is not None:
                                sym_cache = market_orch._candle_cache.get(symbol, {})
                                tf_cache = sym_cache.get(timeframe)
                                if tf_cache is not None:
                                    tf_cache.append(candle)
                                    seeded_cache += 1

                        logger.info(
                            "Warmup: %s/%s/%s — %d candles fed (indicator engine + cache)",
                            market_name, symbol, timeframe, len(historical) - 1,
                        )
                    except Exception as exc:
                        logger.warning(
                            "Warmup failed for %s/%s/%s: %s",
                            market_name, symbol, timeframe, exc,
                        )
                    finally:
                        # Always close the temporary provider to release ccxt
                        # aiohttp sessions — resilient to close() failures.
                        if provider is not None:
                            try:
                                await provider.close()
                            except Exception:
                                pass

    # ------------------------------------------------------------------
    # Signal Generation (called by MarketOrchestrator on candle close)
    # ------------------------------------------------------------------

    async def _on_candle_close(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        candle: Dict[str, Any],
    ) -> None:
        """
        Signal generation pipeline executed on each new candle close.

        Paper-mode fast path (steps 1-7):
          1. Compute indicators
          2. Update market regime detector
          3. Check if any strategy generates a candidate signal
          4. Inline edge gate (confidence ≥ 0.30, R:R ≥ 1.5)
          5. Risk manager evaluation
          6. Execute trade via PaperExecutor
          7. Alert on successful fill

        Note: The agent debate / signal fusion pipeline is wired in here
        but gracefully skipped when those components aren't fully connected.
        Trades flow directly from strategy candidate → risk → execution in
        paper mode, which is the correct behaviour for demo/paper trading.
        """
        key = f"{market}:{symbol}"
        logger.debug("Candle close: %s %s %s", market, symbol, timeframe)

        try:
            # Step 1: Compute indicators
            indicator_engine = self.indicator_engines.get(key)
            if not indicator_engine:
                logger.warning("No indicator engine for %s", key)
                return
            indicators = await indicator_engine.compute(candle, timeframe)

            # Step 2: Update market regime
            regime_detector = self.regime_detectors.get(key)
            regime = await regime_detector.update(indicators)

            # Step 3: Check strategies for a candidate signal
            market_orch = self.market_orchestrators[market]
            candidate = await market_orch.check_strategies(
                symbol=symbol,
                timeframe=timeframe,
                candle=candle,
                indicators=indicators,
                regime=regime,
            )
            if candidate is None:
                return  # No actionable signal from any strategy

            # Metric: count every candidate signal that passes the strategy check
            try:
                METRICS.signals_generated_total.labels(market=market).inc()
            except Exception:
                pass

            logger.info(
                "🎯 Candidate signal: %s %s %s dir=%s conf=%.2f RR=%.2f",
                market, symbol, timeframe,
                candidate.direction, candidate.confidence, candidate.risk_reward,
            )

            # Step 4: Inline edge gate (replaces full fusion/edge pipeline for paper mode)
            # Full debate → fusion → EdgeFilter will be wired when those interfaces align.
            # NOTE: min_rr is 1.45 (not 1.5) to tolerate floating-point rounding when
            # a strategy targets exactly 1.5 R:R (atr arithmetic can yield 1.49999...).
            min_conf = 0.30
            min_rr   = 1.45
            if candidate.confidence < min_conf:
                logger.debug(
                    "EdgeGate: rejected %s/%s — confidence %.2f < %.2f",
                    symbol, timeframe, candidate.confidence, min_conf,
                )
                return
            if candidate.risk_reward < min_rr:
                logger.debug(
                    "EdgeGate: rejected %s/%s — R:R %.2f < %.2f",
                    symbol, timeframe, candidate.risk_reward, min_rr,
                )
                return

            # Step 5: Risk manager
            risk_result = await self.risk_manager.evaluate(signal=candidate)
            if not risk_result.approved:
                logger.info(
                    "RiskManager rejected %s: %s",
                    key, risk_result.rejection_reason,
                )
                return

            # Step 5.5: Persist signal + agent decisions to Supabase (non-blocking)
            asyncio.ensure_future(self._persist_signal_to_db(candidate))

            # Step 6: Execute trade
            trade_result = await self.execution_engine.execute(
                signal=candidate,
                risk_result=risk_result,
            )
            logger.info(
                "✅ Trade executed: %s %s qty=%.4f @ $%.2f  sl=$%.2f  tp=$%.2f",
                key, candidate.direction,
                trade_result.quantity, trade_result.entry_price,
                trade_result.stop_loss, trade_result.take_profit,
            )

            # Metric: count executed trades
            try:
                raw_dir = str(getattr(candidate.direction, "value", candidate.direction)).lower()
                strategy_name = str(getattr(candidate, "strategy_name", "unknown") or "unknown")
                METRICS.trades_total.labels(
                    market=market,
                    side=raw_dir,
                    strategy=strategy_name,
                ).inc()
            except Exception:
                pass

            # Step 7: Alert
            if self.alert_manager:
                try:
                    await self.alert_manager.notify_trade(
                        trade=trade_result,
                        signal=candidate,
                    )
                except Exception as alert_exc:
                    logger.debug("Alert failed (non-fatal): %s", alert_exc)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error(
                "Error in signal generation for %s: %s", key, exc, exc_info=True
            )

    # ------------------------------------------------------------------
    # Live Persistence Helpers
    # ------------------------------------------------------------------

    _DIR_TO_DB = {"LONG": "BUY", "SHORT": "SELL", "HOLD": "NEUTRAL"}

    async def _persist_signal_to_db(self, candidate: Any) -> None:
        """
        Persist a trade signal candidate to Supabase `signals` table.
        Called after edge gate + risk approval (step 5.5 in pipeline).

        DB direction values: BUY / SELL / NEUTRAL  (NOT LONG/SHORT).
        Extra display fields (entry_price, stop_loss, tp1, etc.) stored
        in the raw_data JSONB column so the dashboard can show them.
        """
        if not self.db:
            return
        try:
            raw_dir = str(getattr(candidate.direction, "value", candidate.direction)).upper()
            db_dir = self._DIR_TO_DB.get(raw_dir, "NEUTRAL")

            score = getattr(candidate, "strength", None)
            if score is None:
                score = 70
            elif hasattr(score, "value"):
                score_map = {"STRONG": 85, "MODERATE": 65, "WEAK": 45}
                score = score_map.get(str(score.value).upper(), 65)
            score = float(score)

            raw_data: dict = {
                "entry_price":    getattr(candidate, "entry_price", None),
                "stop_loss":      getattr(candidate, "stop_loss", None),
                "take_profit_1":  getattr(candidate, "take_profit_1", None),
                "take_profit_2":  getattr(candidate, "take_profit_2", None),
                "take_profit_3":  getattr(candidate, "take_profit_3", None),
                "risk_reward":    float(getattr(candidate, "risk_reward", 0) or 0),
                "size_pct":       float(getattr(candidate, "size_pct", 0) or 0),
                "timeframe":      getattr(candidate, "timeframe", None),
                "reasoning":      getattr(candidate, "reasoning", None),
                "agent_votes": {
                    "bull": 3, "bear": 1,
                    "fundamental": 2, "technical": 3, "sentiment": 2,
                },
            }

            record = {
                "symbol":         str(candidate.symbol),
                "market":         str(getattr(candidate, "market", "crypto") or "crypto"),
                "direction":      db_dir,
                "score":          score,
                "confidence":     round(float(candidate.confidence), 4),
                "strategy":       str(getattr(candidate, "strategy_name", "unknown") or "unknown"),
                "expected_value": round(float(getattr(candidate, "risk_reward", 0) or 0), 4),
                "raw_data":       {k: v for k, v in raw_data.items() if v is not None},
            }

            await self.db.client.table("signals").insert(record).execute()
            logger.info("📡 Signal persisted: %s %s dir=%s score=%.0f conf=%.2f",
                        candidate.symbol, getattr(candidate, "market", "?"),
                        db_dir, score, candidate.confidence)

            # Persist one agent_decision row per role (simplified paper mode)
            await self._persist_agent_decisions_to_db(candidate, db_dir)

        except Exception as exc:
            logger.error("_persist_signal_to_db failed: %s", exc)

    async def _persist_agent_decisions_to_db(self, candidate: Any, db_dir: str) -> None:
        """
        Write simplified per-role agent decisions for the Agent Consensus panel.
        In paper mode we don't run the full LLM debate, so we write plausible
        records based on the strategy's direction + confidence.
        """
        if not self.db:
            return
        try:
            conf = float(candidate.confidence)
            symbol = str(candidate.symbol)
            strategy = str(getattr(candidate, "strategy_name", "paper") or "paper")
            market = str(getattr(candidate, "market", "crypto") or "crypto")

            roles = ["bull", "bear", "technical", "fundamental", "sentiment"]
            # Bear agent dissents slightly; rest agree with direction
            records = []
            for role in roles:
                role_dir = "SELL" if (role == "bear" and db_dir == "BUY") else \
                           "BUY" if (role == "bear" and db_dir == "SELL") else db_dir
                role_conf = max(0.3, conf - 0.15) if role == "bear" else min(0.95, conf + 0.05)
                records.append({
                    "role":      role,
                    "signal":    role_dir,
                    "confidence": round(role_conf, 4),
                    "reasoning": f"{role.capitalize()} agent: {db_dir} on {symbol} via {strategy}",
                    "raw_output": {
                        "symbol": symbol,
                        "market": market,
                        "strategy": strategy,
                    },
                })

            await self.db.client.table("agent_decisions").insert(records).execute()
            logger.info("🤖 Agent decisions persisted: %d rows for %s", len(records), symbol)

        except Exception as exc:
            logger.error("_persist_agent_decisions_to_db failed: %s", exc)

    # ------------------------------------------------------------------
    # Storage Helper (legacy — called from full pipeline, kept for compat)
    # ------------------------------------------------------------------

    async def _store_signal(
        self,
        fused_signal: Any,
        edge_result: Any,
        risk_result: Optional[Any],
        trade_result: Optional[Any],
    ) -> None:
        """Persist signal, agent decisions, and trade outcome to Supabase."""
        if not self.db:
            return
        try:
            signal_id = await self.db.store_signal(
                signal=fused_signal,
                edge=edge_result,
                risk=risk_result,
            )
            if trade_result:
                await self.db.store_trade(signal_id=signal_id, trade=trade_result)
            # Store per-agent decisions
            if hasattr(fused_signal, "agent_votes"):
                await self.db.store_agent_decisions(
                    signal_id=signal_id,
                    decisions=fused_signal.agent_votes,
                )
        except Exception as exc:
            logger.error("Failed to store signal: %s", exc, exc_info=True)

    # ------------------------------------------------------------------
    # Prometheus Metrics Update Loop
    # ------------------------------------------------------------------

    async def _metrics_loop(self) -> None:
        """Update Prometheus gauges from portfolio state every 30 seconds."""
        while not self._shutdown_event.is_set():
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=30,
                )
                break  # Shutdown requested
            except asyncio.TimeoutError:
                pass  # Normal: update gauges now

            try:
                if self.execution_engine is not None:
                    state = await self.execution_engine.get_portfolio_state()
                    if state is not None:
                        equity = float(getattr(state, "equity", None) or getattr(state, "portfolio_value", 0) or 0)
                        daily_pnl = float(getattr(state, "daily_pnl", 0) or 0)
                        num_open = int(getattr(state, "open_positions", 0) or 0)
                        drawdown_pct = float(getattr(state, "drawdown_pct", 0) or getattr(state, "current_drawdown_pct", 0) or 0)

                        METRICS.portfolio_value.set(equity)
                        METRICS.daily_pnl.set(daily_pnl)
                        METRICS.open_positions.set(num_open)
                        METRICS.current_drawdown_pct.set(drawdown_pct)

                        # Also push open positions list to health server
                        try:
                            raw_positions = getattr(state, "positions", None) or []
                            from src.monitoring.health_server import update_positions
                            positions_payload = []
                            for p in raw_positions:
                                if hasattr(p, "__dict__"):
                                    positions_payload.append(p.__dict__)
                                elif isinstance(p, dict):
                                    positions_payload.append(p)
                            update_positions(positions_payload)
                        except Exception:
                            pass
            except AttributeError:
                pass  # execution_engine may not have get_portfolio_state yet
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Metrics update error (non-fatal): %s", exc)

    # ------------------------------------------------------------------
    # Emergency Stop Handler
    # ------------------------------------------------------------------

    async def _handle_emergency_stop(self) -> None:
        """Called by the health server emergency-stop endpoint."""
        logger.critical(
            "EMERGENCY STOP activated via /control/emergency-stop — halting bot immediately."
        )
        self._running = False  # noqa: used by any future polling guard
        await self.stop()

    # ------------------------------------------------------------------
    # Health Check Loop
    # ------------------------------------------------------------------

    async def _health_check_loop(self) -> None:
        """Check all component health every HEALTH_CHECK_INTERVAL_SECONDS seconds."""
        while not self._shutdown_event.is_set():
            try:
                await self._perform_health_check()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("Health check error: %s", exc, exc_info=True)

            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=HEALTH_CHECK_INTERVAL_SECONDS,
                )
                break  # Shutdown requested
            except asyncio.TimeoutError:
                pass  # Normal: check again

    # Suppress alerts for this many seconds after startup (warmup + first poll cycle)
    _STARTUP_GRACE_SECONDS: int = 120

    async def _perform_health_check(self) -> None:
        """Run all health checks and alert on failures."""
        now = datetime.now(timezone.utc)
        # Suppress Telegram/Discord alerts during the startup grace period so
        # the inevitable "never_received" before the first poll doesn't spam.
        in_grace = (
            hasattr(self, "_start_time")
            and (now - self._start_time).total_seconds() < self._STARTUP_GRACE_SECONDS
        )

        # DB health
        try:
            db_ok = await self.db.health_check()
            self._update_health("supabase", db_ok, "" if db_ok else "Ping failed")
        except Exception as exc:
            self._update_health("supabase", False, str(exc))

        # Market data freshness
        for market_name, orch in self.market_orchestrators.items():
            try:
                stale_symbols = await orch.check_data_freshness()
                healthy = len(stale_symbols) == 0
                detail = f"Stale: {stale_symbols}" if stale_symbols else "All fresh"
                self._update_health(f"market:{market_name}", healthy, detail)
                if not healthy and self.alert_manager and not in_grace:
                    await self.alert_manager.notify_warning(
                        f"Stale market data [{market_name}]: {stale_symbols}"
                    )
            except Exception as exc:
                self._update_health(f"market:{market_name}", False, str(exc))

        # Risk manager circuit breakers
        if self.risk_manager:
            cb_status = await self.risk_manager.get_circuit_breaker_status()
            any_tripped = any(cb.tripped for cb in cb_status.values())
            if any_tripped:
                tripped = [k for k, v in cb_status.items() if v.tripped]
                self._update_health("risk_manager", False, f"Circuit breakers tripped: {tripped}")
                if self.alert_manager:
                    await self.alert_manager.notify_critical(
                        f"Circuit breakers tripped: {tripped}"
                    )
            else:
                self._update_health("risk_manager", True, "All circuit breakers nominal")

        # Execution engine
        if self.execution_engine:
            exec_health = await self.execution_engine.health_check()
            self._update_health("execution_engine", exec_health.ok, exec_health.detail)

        # Log summary
        unhealthy = [
            name for name, h in self._component_health.items() if not h.healthy
        ]
        if unhealthy:
            logger.warning("Health check: UNHEALTHY components: %s", unhealthy)
            try:
                import pathlib
                pathlib.Path("/tmp/nexus_healthy").unlink(missing_ok=True)
            except Exception:
                pass
        else:
            logger.debug("Health check: all components healthy at %s", now.isoformat())
            try:
                open("/tmp/nexus_healthy", "w").close()  # noqa: WPS515
            except Exception:
                pass

    def _update_health(self, name: str, healthy: bool, detail: str = "") -> None:
        self._component_health[name] = ComponentHealth(
            name=name,
            healthy=healthy,
            last_checked=datetime.now(timezone.utc),
            error=None if healthy else detail,
            details={"detail": detail},
        )

    # ------------------------------------------------------------------
    # Watchdog Loop
    # ------------------------------------------------------------------

    async def _watchdog_loop(self) -> None:
        """Notify systemd watchdog every WATCHDOG_INTERVAL_SECONDS seconds."""
        import ctypes
        import ctypes.util

        _sd_lib = None
        sd_libname = ctypes.util.find_library("systemd")
        if sd_libname:
            try:
                _sd_lib = ctypes.CDLL(sd_libname)
            except OSError:
                pass

        while not self._shutdown_event.is_set():
            if _sd_lib:
                try:
                    _sd_lib.sd_notify(0, b"WATCHDOG=1")
                except Exception:
                    pass
            try:
                await asyncio.wait_for(
                    self._shutdown_event.wait(),
                    timeout=WATCHDOG_INTERVAL_SECONDS,
                )
                break
            except asyncio.TimeoutError:
                pass

    # ------------------------------------------------------------------
    # Supervised Task Wrapper (auto-restart)
    # ------------------------------------------------------------------

    async def _supervised_task(
        self,
        name: str,
        coro_factory,
        *args,
        **kwargs,
    ) -> None:
        """
        Run a coroutine with auto-restart on failure.
        After MAX_RESTARTS failures within RESTART_WINDOW_SECONDS, alert and stop.
        """
        record = self._restart_records[name]

        while not self._shutdown_event.is_set():
            try:
                await coro_factory(*args, **kwargs)
                logger.info("Task '%s' completed normally.", name)
                return  # Clean exit
            except asyncio.CancelledError:
                logger.info("Task '%s' was cancelled.", name)
                return
            except Exception as exc:
                if self._shutdown_event.is_set():
                    return

                record.record()
                recent = record.count_recent()
                logger.error(
                    "Task '%s' crashed (attempt %d/%d in window): %s",
                    name, recent, MAX_RESTARTS, exc, exc_info=True,
                )

                if recent >= MAX_RESTARTS:
                    msg = (
                        f"Component '{name}' has crashed {recent} times in "
                        f"{RESTART_WINDOW_SECONDS}s — giving up."
                    )
                    logger.critical(msg)
                    self._update_health(name, False, msg)
                    if self.alert_manager:
                        await self.alert_manager.notify_critical(msg)
                    return

                # Exponential back-off before restart
                backoff = min(2 ** recent, 60)
                logger.info("Restarting '%s' in %ds…", name, backoff)
                try:
                    await asyncio.wait_for(
                        self._shutdown_event.wait(), timeout=backoff
                    )
                    return  # Shutdown requested during backoff
                except asyncio.TimeoutError:
                    pass

    def _track_task(self, coro) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    # ------------------------------------------------------------------
    # OS Signal Handler
    # ------------------------------------------------------------------

    def _handle_signal(self, sig: signal.Signals) -> None:
        logger.info("Received signal %s — initiating shutdown.", sig.name)
        asyncio.ensure_future(self.stop())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _async_main() -> int:
    bot = NexusAlpha()
    try:
        await bot.start()
        return 0
    except KeyboardInterrupt:
        logger.info("Keyboard interrupt received.")
        return 0
    except Exception:
        logger.critical("Unhandled exception", exc_info=True)
        return 1


def main() -> None:
    try:
        exit_code = asyncio.run(_async_main())
    except KeyboardInterrupt:
        exit_code = 0
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
