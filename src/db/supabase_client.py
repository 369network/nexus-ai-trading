"""
NEXUS ALPHA — Supabase Async Client Wrapper
=============================================
Singleton async Supabase client with connection reuse, batch inserts,
real-time subscriptions, and retry logic.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Any, Callable, Sequence
from uuid import UUID

from supabase import AsyncClient, AsyncClientOptions, create_async_client

from src.utils.logging import get_logger
from src.utils.retry import retry_with_backoff

log = get_logger(__name__)

# Batch size for bulk insert operations
_BULK_BATCH_SIZE = 500


# ---------------------------------------------------------------------------
# Data Transfer Objects (lightweight dicts for Supabase rows)
# ---------------------------------------------------------------------------


CandeRow = dict[str, Any]
SignalRow = dict[str, Any]
TradeRow = dict[str, Any]
AgentDecisionRow = dict[str, Any]
RiskEventRow = dict[str, Any]
PortfolioSnapshotRow = dict[str, Any]
MemoryEntryRow = dict[str, Any]


# ---------------------------------------------------------------------------
# Supabase Client Singleton
# ---------------------------------------------------------------------------


class SupabaseClient:
    """
    Async Supabase client wrapper with singleton pattern.

    Uses connection reuse — creates the client once and reuses it.
    All public methods are async and include retry logic.
    """

    _instance: SupabaseClient | None = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __init__(self, url: str, key: str) -> None:
        self._url = url
        self._key = key
        self._client: AsyncClient | None = None

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    async def get_instance(cls) -> "SupabaseClient":
        """
        Return the singleton SupabaseClient instance.
        Initializes connection on first call.
        """
        async with cls._lock:
            if cls._instance is None:
                from config.settings import get_settings
                s = get_settings()
                instance = cls(
                    url=s.database.supabase_url,
                    key=s.database.supabase_service_key.get_secret_value(),
                )
                await instance._connect()
                cls._instance = instance
        return cls._instance

    @classmethod
    async def reset(cls) -> None:
        """Reset the singleton (useful in tests)."""
        async with cls._lock:
            if cls._instance is not None:
                cls._instance._client = None
                cls._instance = None

    # ------------------------------------------------------------------
    # Connection management
    # ------------------------------------------------------------------

    async def _connect(self) -> None:
        """Establish connection to Supabase."""
        options = AsyncClientOptions(
            auto_refresh_token=True,
            persist_session=False,
        )
        self._client = await create_async_client(
            self._url,
            self._key,
            options=options,
        )
        log.info("Supabase client connected", url=self._url)

    @property
    def client(self) -> AsyncClient:
        if self._client is None:
            raise RuntimeError("Supabase client not connected. Call connect() first.")
        return self._client

    # ------------------------------------------------------------------
    # Market Data (Candles)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def insert_candle(self, candle: CandeRow) -> dict[str, Any]:
        """
        Insert a single candle record.

        Expected keys: market, symbol, interval, timestamp, open, high, low,
                       close, volume, vwap, trades_count
        """
        result = await self.client.table("market_data").insert(candle).execute()
        return result.data[0] if result.data else {}

    @retry_with_backoff(max_retries=3, base_delay=2.0)
    async def bulk_insert_candles(self, candles: list[CandeRow]) -> int:
        """
        Bulk insert candles in batches of 500.
        Uses upsert to handle duplicates gracefully.

        Args:
            candles: List of candle row dicts.

        Returns:
            Total number of rows inserted/updated.
        """
        if not candles:
            return 0

        total = 0
        for i in range(0, len(candles), _BULK_BATCH_SIZE):
            batch = candles[i : i + _BULK_BATCH_SIZE]
            result = (
                await self.client.table("market_data")
                .upsert(
                    batch,
                    on_conflict="market,symbol,interval,timestamp",
                    ignore_duplicates=False,
                )
                .execute()
            )
            count = len(result.data) if result.data else 0
            total += count
            log.debug(
                "Bulk candle batch inserted",
                batch_num=i // _BULK_BATCH_SIZE + 1,
                batch_size=len(batch),
                inserted=count,
            )

        log.info("Bulk candle insert complete", total_rows=total, symbol=candles[0].get("symbol"))
        return total

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def get_latest_candles(
        self,
        symbol: str,
        interval: str,
        limit: int = 500,
        market: str | None = None,
    ) -> list[dict[str, Any]]:
        """
        Fetch the most recent candles for a symbol.

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT').
            interval: Candle timeframe string (e.g., '15m', '1h').
            limit: Number of candles to fetch.
            market: Optional market filter (e.g., 'crypto').

        Returns:
            List of candle dicts, ordered oldest→newest.
        """
        query = (
            self.client.table("market_data")
            .select("*")
            .eq("symbol", symbol)
            .eq("timeframe", interval)   # DB column is "timeframe", not "interval"
            .order("ts", desc=True)      # DB column is "ts", not "timestamp"
            .limit(limit)
        )
        if market:
            query = query.eq("market", market)

        result = await query.execute()

        # Reverse to get chronological order (oldest first)
        data = result.data or []
        data.reverse()
        return data

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def insert_signal(self, signal: SignalRow) -> dict[str, Any]:
        """
        Insert a trading signal.

        Expected keys: market, symbol, strategy, signal_type, direction,
                       strength, entry_price, stop_loss, take_profit,
                       timeframe, agent_id, model_id, metadata
        """
        result = await self.client.table("signals").insert(signal).execute()
        return result.data[0] if result.data else {}

    # ------------------------------------------------------------------
    # Trades
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def insert_trade(self, trade: TradeRow) -> dict[str, Any]:
        """
        Insert a new trade record.

        Expected keys: signal_id, market, symbol, side, order_type,
                       size, entry_price, stop_loss, take_profit,
                       strategy, exchange, paper_trade
        """
        result = await self.client.table("trades").insert(trade).execute()
        return result.data[0] if result.data else {}

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def update_trade(
        self,
        trade_id: str | UUID,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """
        Update an existing trade (e.g., on close, stop adjustment).

        Args:
            trade_id: UUID of the trade to update.
            updates: Dict of fields to update (e.g., {'status': 'closed', 'exit_price': 1.23}).
        """
        result = (
            await self.client.table("trades")
            .update(updates)
            .eq("id", str(trade_id))
            .execute()
        )
        return result.data[0] if result.data else {}

    async def get_open_trades(
        self, market: str | None = None
    ) -> list[dict[str, Any]]:
        """Fetch all currently open trades."""
        query = (
            self.client.table("trades")
            .select("*")
            .eq("status", "open")
            .order("opened_at", desc=True)
        )
        if market:
            query = query.eq("market", market)
        result = await query.execute()
        return result.data or []

    # ------------------------------------------------------------------
    # Agent Decisions
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def insert_agent_decision(
        self, decision: AgentDecisionRow
    ) -> dict[str, Any]:
        """
        Record an AI agent decision for audit and performance tracking.

        Expected keys: agent_name, model_id, market, symbol, decision_type,
                       input_context, output_reasoning, confidence, action_taken,
                       outcome, llm_cost_usd, latency_ms
        """
        result = await self.client.table("agent_decisions").insert(decision).execute()
        return result.data[0] if result.data else {}

    # ------------------------------------------------------------------
    # Risk Events
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def insert_risk_event(self, event: RiskEventRow) -> dict[str, Any]:
        """
        Record a risk management event (circuit breaker, drawdown, etc.).

        Expected keys: event_type, circuit_breaker_id, market, symbol,
                       severity, trigger_value, threshold_value, action_taken,
                       positions_affected, details
        """
        result = await self.client.table("risk_events").insert(event).execute()
        return result.data[0] if result.data else {}

    # ------------------------------------------------------------------
    # Portfolio Snapshots
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def insert_portfolio_snapshot(
        self, snapshot: PortfolioSnapshotRow
    ) -> dict[str, Any]:
        """
        Insert a portfolio snapshot (called periodically or after trades).

        Expected keys: total_equity, cash_balance, invested_value,
                       unrealized_pnl, realized_pnl_today, daily_return_pct,
                       positions_count, market_exposure (JSONB), drawdown_pct,
                       sharpe_ratio
        """
        result = await self.client.table("portfolio_snapshots").insert(snapshot).execute()
        return result.data[0] if result.data else {}

    async def get_latest_snapshot(self) -> dict[str, Any] | None:
        """Get the most recent portfolio snapshot."""
        result = (
            await self.client.table("portfolio_snapshots")
            .select("*")
            .order("snapshot_at", desc=True)
            .limit(1)
            .execute()
        )
        data = result.data or []
        return data[0] if data else None

    # ------------------------------------------------------------------
    # Memory Entries (Agent Memory)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def insert_memory_entry(self, entry: MemoryEntryRow) -> dict[str, Any]:
        """
        Store an agent memory entry.

        Expected keys: agent_name, memory_tier (short/medium/long),
                       content, summary, market, symbol, importance_score,
                       expires_at, embedding (vector for semantic search)
        """
        result = await self.client.table("memory_entries").insert(entry).execute()
        return result.data[0] if result.data else {}

    async def search_memories(
        self,
        agent_name: str,
        tier: str = "medium",
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Retrieve recent memory entries for an agent."""
        result = (
            await self.client.table("memory_entries")
            .select("*")
            .eq("agent_name", agent_name)
            .eq("memory_tier", tier)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        return result.data or []

    # ------------------------------------------------------------------
    # Real-time subscriptions
    # ------------------------------------------------------------------

    def subscribe_to_signals(
        self,
        callback: Callable[[dict[str, Any]], None],
        market: str | None = None,
    ) -> Any:
        """
        Subscribe to new trading signals via Supabase real-time.

        Args:
            callback: Async or sync function called with each new signal row.
            market: Optional market filter (e.g., 'crypto').

        Returns:
            Supabase channel subscription object.
        """
        channel_name = f"signals_{market or 'all'}"
        channel = self.client.channel(channel_name)

        filter_str = "event=INSERT"
        if market:
            filter_str += f",filter=market=eq.{market}"

        channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table="signals",
            filter=f"market=eq.{market}" if market else None,
            callback=callback,
        )

        asyncio.create_task(channel.subscribe())
        log.info("Subscribed to signals", channel=channel_name, market=market or "all")
        return channel

    def subscribe_to_risk_events(
        self,
        callback: Callable[[dict[str, Any]], None],
    ) -> Any:
        """Subscribe to risk events for real-time monitoring."""
        channel = self.client.channel("risk_events_stream")
        channel.on_postgres_changes(
            event="INSERT",
            schema="public",
            table="risk_events",
            callback=callback,
        )
        asyncio.create_task(channel.subscribe())
        log.info("Subscribed to risk events stream")
        return channel

    # ------------------------------------------------------------------
    # Model Performance (Brier Score tracking)
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=1.0)
    async def insert_model_performance(
        self, record: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Record model performance metrics (Brier scores, accuracy, etc.).

        Expected keys: model_id, agent_name, market, period, brier_score,
                       accuracy, precision, recall, total_predictions,
                       correct_predictions
        """
        result = await self.client.table("model_performance").insert(record).execute()
        return result.data[0] if result.data else {}

    async def get_model_performance_history(
        self,
        model_id: str,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        """Get recent performance history for a model."""
        result = (
            await self.client.table("model_performance")
            .select("*")
            .eq("model_id", model_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        data = result.data or []
        data.reverse()  # Chronological order
        return data

    # ------------------------------------------------------------------
    # Connection lifecycle (public API used by main orchestrator)
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Establish connection to Supabase (public lifecycle method)."""
        if self._client is None:
            try:
                await self._connect()
            except Exception as exc:
                log.warning(
                    "Supabase connect failed (degraded mode): %s", exc
                )
        else:
            log.debug("Supabase already connected")

    async def close(self) -> None:
        """Close the Supabase connection and reset the singleton."""
        try:
            self._client = None
            log.info("Supabase connection closed")
        except Exception as exc:
            log.warning("Supabase close error: %s", exc)

    # ------------------------------------------------------------------
    # Signal / Trade / Agent persistence (used by main orchestrator)
    # ------------------------------------------------------------------

    async def store_signal(
        self,
        signal: Any,
        edge: Any,
        risk: Any,
    ) -> str:
        """Persist a fused signal with edge and risk metadata. Returns signal_id."""
        import uuid as _uuid
        signal_id = str(_uuid.uuid4())
        try:
            row: dict[str, Any] = {
                "id": signal_id,
                "symbol": getattr(signal, "symbol", ""),
                "market": getattr(signal, "market", ""),
                "timeframe": getattr(signal, "timeframe", ""),
                "direction": str(getattr(signal, "direction", "")),
                "confidence": float(getattr(signal, "confidence", 0.0)),
                "expected_value": float(getattr(signal, "expected_value", 0.0)),
                "risk_reward": float(getattr(signal, "risk_reward", 0.0)),
                "edge_detected": bool(getattr(edge, "edge_detected", False)) if edge else False,
                "risk_approved": bool(getattr(risk, "approved", False)) if risk else False,
            }
            await self.insert_signal(row)
        except Exception as exc:
            log.debug("store_signal failed: %s", exc)
        return signal_id

    async def store_trade(self, signal_id: str, trade: Any) -> None:
        """Persist a trade execution record."""
        try:
            row: dict[str, Any] = {
                "signal_id": signal_id,
                "symbol": getattr(trade, "symbol", ""),
                "side": getattr(trade, "direction", ""),
                "order_type": "MARKET",
                "size": float(getattr(trade, "quantity", 0.0)),
                "entry_price": float(getattr(trade, "entry_price", 0.0)),
                "stop_loss": float(getattr(trade, "stop_loss", 0.0)),
                "take_profit": float(getattr(trade, "take_profit", 0.0)),
                "paper_trade": True,
                "exchange": getattr(trade, "executor", "paper"),
            }
            await self.insert_trade(row)
        except Exception as exc:
            log.debug("store_trade failed: %s", exc)

    async def store_agent_decisions(
        self, signal_id: str, decisions: Any
    ) -> None:
        """Persist per-agent vote records for a signal."""
        try:
            if not decisions:
                return
            items = decisions.items() if isinstance(decisions, dict) else enumerate(decisions)
            for agent_name, decision in items:
                row: dict[str, Any] = {
                    "signal_id": signal_id,
                    "agent_name": str(agent_name),
                    "decision_type": str(getattr(decision, "decision", decision)),
                    "confidence": float(getattr(decision, "confidence", 0.5)),
                    "input_context": {},
                    "output_reasoning": str(getattr(decision, "reasoning", ""))[:500],
                    "action_taken": str(getattr(decision, "decision", decision)),
                }
                await self.insert_agent_decision(row)
        except Exception as exc:
            log.debug("store_agent_decisions failed: %s", exc)

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Market Orchestrator helpers
    # ------------------------------------------------------------------

    async def upsert_candle(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        candle: dict[str, Any],
    ) -> None:
        """
        Upsert a single live candle into market_data.
        Silently ignores errors so the pipeline keeps running.

        DB schema: symbol, market, timeframe, ts (timestamptz), open/high/low/close/volume.
        Candle timestamps are expected in UTC milliseconds.
        """
        from datetime import datetime, timezone as _tz

        ts_ms = candle.get("timestamp", 0) or 0
        # Convert ms epoch → ISO-8601 string for timestamptz column
        ts_iso = datetime.fromtimestamp(ts_ms / 1000.0, tz=_tz.utc).isoformat()

        row: CandeRow = {
            "market":    market,
            "symbol":    symbol,
            "timeframe": timeframe,      # DB column is "timeframe"
            "ts":        ts_iso,         # DB column is "ts" (timestamptz)
            "open":      candle.get("open",   0.0),
            "high":      candle.get("high",   0.0),
            "low":       candle.get("low",    0.0),
            "close":     candle.get("close",  0.0),
            "volume":    candle.get("volume", 0.0),
            "source":    "binance",
        }
        try:
            await (
                self.client.table("market_data")
                .upsert(row, on_conflict="symbol,market,timeframe,ts")
                .execute()
            )
        except Exception as exc:
            log.warning(
                "upsert_candle failed",
                market=market, symbol=symbol, interval=timeframe, error=str(exc),
            )

    async def fetch_candles(
        self,
        market: str,
        symbol: str,
        timeframe: str,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """
        Fetch the most recent candles for a symbol/timeframe.
        Returns a list of candle dicts ordered oldest→newest.
        """
        return await self.get_latest_candles(
            symbol=symbol,
            interval=timeframe,
            limit=limit,
            market=market,
        )

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Return True if Supabase is reachable and responding."""
        try:
            await self.client.table("trades").select("id").limit(1).execute()
            return True
        except Exception as exc:
            log.error("Supabase health check failed", error=str(exc))
            return False


# ---------------------------------------------------------------------------
# Module-level accessor
# ---------------------------------------------------------------------------


async def get_supabase() -> SupabaseClient:
    """Return the singleton Supabase client (auto-initializes on first call)."""
    return await SupabaseClient.get_instance()
