"""
NEXUS ALPHA - Candle Store
============================
In-memory rolling candle store with optional Supabase persistence.
Provides fast per-symbol, per-timeframe access to recent OHLCV data
without requiring a database round-trip on every candle.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Default maximum candles retained per (symbol, timeframe) key
DEFAULT_MAX_CANDLES = 500

# Minimum candles before to_dataframe returns a non-empty result
MIN_CANDLES_FOR_DF = 2


class CandleStore:
    """
    In-memory rolling candle store with Supabase persistence.

    Thread-safety note: this class is designed for use in a single-threaded
    asyncio event loop.  It is NOT thread-safe.

    Parameters
    ----------
    max_candles : int
        Maximum number of candles retained per (symbol, timeframe) window.
        Older candles are pruned as new ones arrive.
    db : optional
        SupabaseClient instance.  If provided, candles are persisted on add.
    """

    def __init__(
        self,
        max_candles: int = DEFAULT_MAX_CANDLES,
        db: Optional[Any] = None,
    ) -> None:
        self._max_candles = max_candles
        self._db = db

        # {(symbol, timeframe): [candle_dict, ...]}
        self._store: Dict[Tuple[str, str], List[Dict[str, Any]]] = defaultdict(list)

    # ------------------------------------------------------------------
    # Write interface
    # ------------------------------------------------------------------

    def add(
        self,
        symbol: str,
        timeframe: str,
        candle: Dict[str, Any],
    ) -> None:
        """
        Append a candle to the rolling store.

        Parameters
        ----------
        symbol : str
            Trading symbol (e.g. "BTC/USDT").
        timeframe : str
            Candle interval (e.g. "15m", "1h").
        candle : dict
            OHLCV candle dict.  Required keys: open, high, low, close, volume.
            Optional: timestamp, trades.
        """
        key = (symbol, timeframe)
        # Normalise to lowercase keys
        normalised = {k.lower(): v for k, v in candle.items()}
        self._store[key].append(normalised)

        # Prune if over limit
        if len(self._store[key]) > self._max_candles:
            self._store[key] = self._store[key][-self._max_candles:]

        logger.debug(
            "CandleStore.add: %s/%s — now %d candles",
            symbol, timeframe, len(self._store[key]),
        )

        # Persist closed candles only — skip live (open) candles to avoid
        # flooding the DB with partial-bar updates on every tick.
        if self._db is not None:
            is_closed = normalised.get("is_closed", normalised.get("closed", True))
            if is_closed is not False:
                import asyncio
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.ensure_future(
                            self.persist_candle(symbol, timeframe, normalised)
                        )
                    else:
                        loop.run_until_complete(
                            self.persist_candle(symbol, timeframe, normalised)
                        )
                except Exception as exc:
                    logger.debug("CandleStore.add: persistence scheduling failed: %s", exc)

    # ------------------------------------------------------------------
    # Read interface
    # ------------------------------------------------------------------

    def get(
        self,
        symbol: str,
        timeframe: str,
        n: int = 200,
    ) -> List[Dict[str, Any]]:
        """
        Return the *n* most recent candles for (symbol, timeframe).

        Parameters
        ----------
        symbol : str
        timeframe : str
        n : int
            Maximum number of candles to return (most recent first if > available).

        Returns
        -------
        list[dict]
            Candle list ordered oldest → newest.  Empty if no data.
        """
        key = (symbol, timeframe)
        candles = self._store.get(key, [])
        if not candles:
            return []
        return candles[-n:] if n < len(candles) else list(candles)

    def get_latest(
        self,
        symbol: str,
        timeframe: str,
    ) -> Optional[Dict[str, Any]]:
        """Return the single most recent candle, or None."""
        key = (symbol, timeframe)
        candles = self._store.get(key, [])
        return candles[-1] if candles else None

    def count(self, symbol: str, timeframe: str) -> int:
        """Return how many candles are stored for (symbol, timeframe)."""
        return len(self._store.get((symbol, timeframe), []))

    def symbols(self) -> List[str]:
        """Return all unique symbols that have at least one candle."""
        return list({s for s, _ in self._store.keys() if self._store[(s, _)]})

    def timeframes(self, symbol: str) -> List[str]:
        """Return all timeframes stored for *symbol*."""
        return [tf for s, tf in self._store.keys() if s == symbol and self._store[(s, tf)]]

    # ------------------------------------------------------------------
    # DataFrame conversion
    # ------------------------------------------------------------------

    def to_dataframe(self, symbol: str, timeframe: str) -> "pd.DataFrame":
        """
        Convert stored candles for (symbol, timeframe) to a pandas DataFrame.

        Returns an empty DataFrame if fewer than MIN_CANDLES_FOR_DF candles
        are available.

        Returns
        -------
        pd.DataFrame
            Columns: open, high, low, close, volume (+ any extras in stored dicts).
            Index is set to DatetimeIndex from 'timestamp' if present.
        """
        import pandas as pd

        candles = self.get(symbol, timeframe)
        if len(candles) < MIN_CANDLES_FOR_DF:
            return pd.DataFrame()

        df = pd.DataFrame(candles)

        # Ensure OHLCV columns are float
        for col in ("open", "high", "low", "close", "volume"):
            if col in df.columns:
                df[col] = df[col].astype(float)

        # Set DatetimeIndex if timestamp column present
        if "timestamp" in df.columns:
            try:
                df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
                df = df.drop(columns=["timestamp"], errors="ignore")
            except Exception:
                try:
                    df.index = pd.to_datetime(df["timestamp"], utc=True)
                    df = df.drop(columns=["timestamp"], errors="ignore")
                except Exception:
                    pass  # Keep integer index

        return df

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    async def persist_candle(
        self,
        symbol: str,
        timeframe: str,
        candle: Dict[str, Any],
        market: str = "crypto",
    ) -> None:
        """Persist a single candle to Supabase asynchronously."""
        if self._db is None:
            return
        try:
            row = {
                "market": market,
                "symbol": symbol,
                "interval": timeframe,
                "timestamp": candle.get("timestamp"),
                "open": float(candle.get("open", 0.0)),
                "high": float(candle.get("high", 0.0)),
                "low": float(candle.get("low", 0.0)),
                "close": float(candle.get("close", 0.0)),
                "volume": float(candle.get("volume", 0.0)),
                "vwap": float(candle.get("vwap", 0.0)),
                "trades_count": int(candle.get("trades", 0)),
            }
            await self._db.insert_candle(row)
        except Exception as exc:
            logger.debug("CandleStore.persist_candle failed: %s", exc)

    # ------------------------------------------------------------------
    # Housekeeping
    # ------------------------------------------------------------------

    def clear(self, symbol: Optional[str] = None, timeframe: Optional[str] = None) -> None:
        """
        Clear stored candles.

        If both symbol and timeframe are given, clears that specific key.
        If only symbol is given, clears all timeframes for that symbol.
        If neither, clears all data.
        """
        if symbol and timeframe:
            self._store.pop((symbol, timeframe), None)
        elif symbol:
            keys_to_remove = [(s, tf) for s, tf in self._store if s == symbol]
            for k in keys_to_remove:
                del self._store[k]
        else:
            self._store.clear()

    def __repr__(self) -> str:
        total = sum(len(v) for v in self._store.values())
        return (
            f"CandleStore(keys={len(self._store)}, "
            f"total_candles={total}, "
            f"max_per_key={self._max_candles})"
        )
