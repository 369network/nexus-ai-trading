"""
NEXUS ALPHA - Market Orchestrator
Manages all data subscription, candle caching, and signal triggering for a single market.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from datetime import datetime, time as dt_time, timezone
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple

logger = logging.getLogger("nexus_alpha.market_orchestrator")

# Maximum candles stored per (symbol, timeframe) key
CANDLE_CACHE_SIZE = 500

# Staleness threshold per market class (seconds)
STALENESS_THRESHOLDS = {
    "crypto": 300,      # 5 minutes
    "forex": 600,       # 10 minutes (market hours only)
    "indian_stocks": 900,
    "us_stocks": 900,
}


class MarketOrchestrator:
    """
    Per-market orchestrator that:
      - Manages data subscription for all symbols in the market
      - Routes incoming candles to indicator computation
      - Maintains a candle cache (last 500 candles per symbol per timeframe)
      - Triggers signal generation on new candle close
      - Checks market hours before triggering signals
    """

    def __init__(
        self,
        market_name: str,
        config: Any,            # MarketConfig dataclass
        settings: Any,          # Settings
        db: Any,                # SupabaseClient
        on_candle_close: Callable[..., Coroutine],
    ) -> None:
        self.market_name = market_name
        self.config = config
        self.settings = settings
        self.db = db
        self._on_candle_close = on_candle_close

        # candle_cache[symbol][timeframe] = deque of candle dicts
        self._candle_cache: Dict[str, Dict[str, deque]] = {}

        # Data provider instances
        self._providers: Dict[str, Any] = {}

        # WebSocket connections
        self._ws_connections: Dict[str, Any] = {}

        # Last candle timestamp per (symbol, timeframe)
        self._last_candle_ts: Dict[Tuple[str, str], float] = {}

        # Strategy instances for this market
        self._strategies: List[Any] = []

        self._running = False
        self._ingestion_task: Optional[asyncio.Task] = None
        self._ws_tasks: List[asyncio.Task] = []

        # Candle arrival queue: items are (symbol, timeframe, candle_dict)
        self._candle_queue: asyncio.Queue = asyncio.Queue(maxsize=10_000)

        # Initialise cache structure
        for symbol in self.config.symbols:
            self._candle_cache[symbol] = {
                tf: deque(maxlen=CANDLE_CACHE_SIZE)
                for tf in self.config.timeframes
            }

    # ------------------------------------------------------------------
    # Initialisation
    # ------------------------------------------------------------------

    async def init_providers(self) -> None:
        """Instantiate and connect REST data providers for the market."""
        from src.data.providers import get_provider_for_market

        for symbol in self.config.symbols:
            provider = get_provider_for_market(
                market=self.market_name,
                symbol=symbol,
                settings=self.settings,
            )
            self._providers[symbol] = provider
            logger.debug("[%s] Provider ready: %s", self.market_name, symbol)

        # Load strategy instances
        from src.strategies.registry import StrategyRegistry
        self._strategies = StrategyRegistry.get_strategies_for_market(
            market=self.market_name,
            config=self.config,
            settings=self.settings,
        )
        logger.info(
            "[%s] %d strategies loaded for %d symbols",
            self.market_name,
            len(self._strategies),
            len(self.config.symbols),
        )

    async def connect_websockets(self) -> None:
        """Open WebSocket connections for real-time candle streaming."""
        from src.data.websockets import get_ws_connector_for_market

        for symbol in self.config.symbols:
            ws_conn = get_ws_connector_for_market(
                market=self.market_name,
                symbol=symbol,
                settings=self.settings,
                on_candle=self._handle_raw_candle,
                timeframes=list(self.config.timeframes),
            )
            self._ws_connections[symbol] = ws_conn
            logger.debug("[%s] WebSocket connector created: %s", self.market_name, symbol)

    # ------------------------------------------------------------------
    # Ingestion Loop
    # ------------------------------------------------------------------

    async def run_ingestion_loop(self) -> None:
        """
        Main ingestion loop: start all WebSocket connections and
        process incoming candles from the queue until stopped.
        """
        self._running = True
        logger.info("[%s] Ingestion loop starting.", self.market_name)

        # Start WebSocket connections as tasks
        for symbol, ws_conn in self._ws_connections.items():
            task = asyncio.ensure_future(ws_conn.run())
            self._ws_tasks.append(task)
            logger.info("[%s] WebSocket started: %s", self.market_name, symbol)

        # Process candle queue
        while self._running:
            try:
                item = await asyncio.wait_for(self._candle_queue.get(), timeout=5.0)
                symbol, timeframe, candle, is_closed = item
                await self._process_candle(symbol, timeframe, candle, is_closed)
                self._candle_queue.task_done()
            except asyncio.TimeoutError:
                continue  # No candles; check running flag
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("[%s] Error processing candle: %s", self.market_name, exc, exc_info=True)

        logger.info("[%s] Ingestion loop stopped.", self.market_name)

    async def stop(self) -> None:
        """Stop ingestion and WebSocket connections."""
        self._running = False
        for task in self._ws_tasks:
            task.cancel()
        for ws_conn in self._ws_connections.values():
            try:
                await ws_conn.close()
            except Exception:
                pass
        if self._ws_tasks:
            await asyncio.gather(*self._ws_tasks, return_exceptions=True)
        logger.info("[%s] Market orchestrator stopped.", self.market_name)

    # ------------------------------------------------------------------
    # Raw Candle Handler (called by WebSocket connector)
    # ------------------------------------------------------------------

    async def _handle_raw_candle(
        self,
        symbol: str,
        timeframe: str,
        raw_candle: Dict[str, Any],
        is_closed: bool,
    ) -> None:
        """Normalise and enqueue a raw candle from the WebSocket stream."""
        from src.data.normalizer import normalize_candle

        try:
            candle = normalize_candle(
                raw=raw_candle,
                market=self.market_name,
                symbol=symbol,
                timeframe=timeframe,
            )
            await self._candle_queue.put((symbol, timeframe, candle, is_closed))
        except Exception as exc:
            logger.error(
                "[%s] Failed to normalise candle for %s/%s: %s",
                self.market_name, symbol, timeframe, exc,
            )

    # ------------------------------------------------------------------
    # Candle Processing
    # ------------------------------------------------------------------

    async def _process_candle(
        self,
        symbol: str,
        timeframe: str,
        candle: Dict[str, Any],
        is_closed: bool,
    ) -> None:
        """Update cache, persist to DB, and (on close) trigger signal generation."""
        import time as _time

        # Update cache
        cache = self._candle_cache.get(symbol, {}).get(timeframe)
        if cache is not None:
            # If same timestamp as last candle, replace (update in-progress candle)
            if cache and cache[-1].get("timestamp") == candle.get("timestamp"):
                cache[-1] = candle
            else:
                cache.append(candle)
        else:
            logger.warning(
                "[%s] No cache for %s/%s — skipping.", self.market_name, symbol, timeframe
            )
            return

        # Record wall-clock receive time (not candle open time) so the stale
        # check correctly measures "how long since we last got any data".
        self._last_candle_ts[(symbol, timeframe)] = _time.time()

        # Persist live candle to Supabase (upsert)
        try:
            await self.db.upsert_candle(
                market=self.market_name,
                symbol=symbol,
                timeframe=timeframe,
                candle=candle,
            )
        except Exception as exc:
            logger.error("[%s] DB upsert error for %s/%s: %s", self.market_name, symbol, timeframe, exc)

        # Only trigger signal generation on candle close
        if not is_closed:
            return

        # Check market hours
        if not self._is_market_open():
            logger.debug("[%s] Market closed — skipping signal generation.", self.market_name)
            return

        # Trigger signal generation callback
        await self._on_candle_close(
            market=self.market_name,
            symbol=symbol,
            timeframe=timeframe,
            candle=candle,
        )

    # ------------------------------------------------------------------
    # Strategy Check
    # ------------------------------------------------------------------

    async def check_strategies(
        self,
        symbol: str,
        timeframe: str,
        candle: Dict[str, Any],
        indicators: Dict[str, Any],
        regime: Any,
    ) -> Optional[Any]:
        """
        Evaluate all strategies and return the first candidate signal, or None.
        Strategies are evaluated in priority order; the first LONG or SHORT wins.
        """
        candle_history = list(self._candle_cache.get(symbol, {}).get(timeframe, []))

        for strategy in self._strategies:
            try:
                if not strategy.is_applicable(
                    symbol=symbol, timeframe=timeframe, regime=regime
                ):
                    continue

                candidate = await strategy.evaluate(
                    symbol=symbol,
                    timeframe=timeframe,
                    candle=candle,
                    candle_history=candle_history,
                    indicators=indicators,
                    regime=regime,
                )

                if candidate is not None and candidate.direction in ("LONG", "SHORT"):
                    logger.info(
                        "[%s] Strategy '%s' generated %s signal for %s/%s",
                        self.market_name,
                        strategy.name,
                        candidate.direction,
                        symbol,
                        timeframe,
                    )
                    return candidate

            except Exception as exc:
                logger.error(
                    "[%s] Strategy '%s' error for %s/%s: %s",
                    self.market_name, strategy.name, symbol, timeframe, exc,
                )

        return None

    # ------------------------------------------------------------------
    # Candle Cache Access
    # ------------------------------------------------------------------

    def get_candles(
        self,
        symbol: str,
        timeframe: str,
        n: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Return the last `n` candles from cache (all if n is None)."""
        cache = self._candle_cache.get(symbol, {}).get(timeframe, deque())
        candles = list(cache)
        if n is not None:
            candles = candles[-n:]
        return candles

    def get_latest_candle(self, symbol: str, timeframe: str) -> Optional[Dict[str, Any]]:
        """Return the most recent candle."""
        cache = self._candle_cache.get(symbol, {}).get(timeframe)
        if cache:
            return cache[-1]
        return None

    # ------------------------------------------------------------------
    # Data Freshness Check
    # ------------------------------------------------------------------

    async def check_data_freshness(self) -> List[str]:
        """
        Return a list of symbol/timeframe keys where the latest candle
        is stale (older than the threshold for this market class).
        """
        import time

        threshold = STALENESS_THRESHOLDS.get(self._market_class(), 600)
        now = time.time()
        stale: List[str] = []

        for symbol in self.config.symbols:
            for timeframe in self.config.timeframes:
                key = (symbol, timeframe)
                last_ts = self._last_candle_ts.get(key)
                if last_ts is None:
                    stale.append(f"{symbol}/{timeframe}:never_received")
                elif now - last_ts > threshold:
                    age = int(now - last_ts)
                    stale.append(f"{symbol}/{timeframe}:stale_{age}s")

        return stale

    # ------------------------------------------------------------------
    # Market Hours
    # ------------------------------------------------------------------

    def _is_market_open(self) -> bool:
        """Return True if the market is currently open for trading."""
        market_class = self._market_class()

        if market_class == "crypto":
            return True  # 24/7

        now_utc = datetime.now(timezone.utc)
        weekday = now_utc.weekday()  # 0=Monday, 6=Sunday

        if market_class == "forex":
            # Forex: Mon 00:00 UTC — Fri 22:00 UTC (approximately)
            if weekday == 6:
                return False  # Sunday
            if weekday == 5 and now_utc.hour >= 22:
                return False  # Saturday after 22:00 UTC
            return True

        if market_class == "indian_stocks":
            # NSE/BSE: Mon-Fri 03:45-10:00 UTC (IST 09:15-15:30)
            if weekday >= 5:
                return False
            market_open = dt_time(3, 45)
            market_close = dt_time(10, 0)
            current_time = now_utc.time()
            return market_open <= current_time <= market_close

        if market_class == "us_stocks":
            # NYSE/NASDAQ: Mon-Fri 14:30-21:00 UTC
            if weekday >= 5:
                return False
            market_open = dt_time(14, 30)
            market_close = dt_time(21, 0)
            current_time = now_utc.time()
            return market_open <= current_time <= market_close

        return True  # Unknown market class: assume open

    def _market_class(self) -> str:
        """Classify this market (crypto/forex/indian_stocks/us_stocks)."""
        return getattr(self.config, "market_class", self.market_name.split("_")[0])

    # ------------------------------------------------------------------
    # Candle History Loader (on startup)
    # ------------------------------------------------------------------

    async def load_candle_history(self) -> None:
        """
        On startup, fill the candle cache from Supabase (last 500 candles
        per symbol/timeframe) so strategies have enough history immediately.
        """
        for symbol in self.config.symbols:
            for timeframe in self.config.timeframes:
                try:
                    candles = await self.db.fetch_candles(
                        market=self.market_name,
                        symbol=symbol,
                        timeframe=timeframe,
                        limit=CANDLE_CACHE_SIZE,
                    )
                    cache = self._candle_cache[symbol][timeframe]
                    for c in sorted(candles, key=lambda x: x["timestamp"]):
                        cache.append(c)
                    logger.debug(
                        "[%s] Loaded %d historical candles for %s/%s",
                        self.market_name, len(candles), symbol, timeframe,
                    )
                except Exception as exc:
                    logger.error(
                        "[%s] Failed to load history for %s/%s: %s",
                        self.market_name, symbol, timeframe, exc,
                    )

    # ------------------------------------------------------------------
    # REST Backfill (for gaps)
    # ------------------------------------------------------------------

    async def backfill_gaps(self, symbol: str, timeframe: str) -> int:
        """
        Detect gaps in the candle cache and backfill them via REST API.
        Returns the number of candles added.
        """
        cache = self._candle_cache.get(symbol, {}).get(timeframe)
        if not cache or len(cache) < 2:
            return 0

        added = 0
        candles = list(cache)
        tf_seconds = _timeframe_to_seconds(timeframe)

        for i in range(len(candles) - 1):
            gap_start = candles[i]["timestamp"] + tf_seconds * 1000
            gap_end = candles[i + 1]["timestamp"]
            if gap_end - gap_start > tf_seconds * 1000 * 1.5:
                # Gap detected
                provider = self._providers.get(symbol)
                if not provider:
                    continue
                try:
                    fill_candles = await provider.fetch_candles(
                        timeframe=timeframe,
                        since=gap_start,
                        until=gap_end,
                    )
                    for fc in fill_candles:
                        cache.append(fc)
                        await self.db.upsert_candle(
                            market=self.market_name,
                            symbol=symbol,
                            timeframe=timeframe,
                            candle=fc,
                        )
                        added += 1
                except Exception as exc:
                    logger.error(
                        "[%s] Backfill error for %s/%s: %s",
                        self.market_name, symbol, timeframe, exc,
                    )

        return added


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _timeframe_to_seconds(tf: str) -> int:
    """Convert a timeframe string (e.g. '1m', '4h', '1d') to seconds."""
    unit = tf[-1].lower()
    value = int(tf[:-1])
    multipliers = {"m": 60, "h": 3600, "d": 86400, "w": 604800}
    return value * multipliers.get(unit, 60)
