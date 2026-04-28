"""
Short-Term Memory for NEXUS ALPHA Learning System.

Stores the last 24 hours of: trades, signals, news events, indicator snapshots.
Auto-expires entries older than 24h via a background thread.
Backed by Supabase with a local dictionary cache for fast reads.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_EXPIRY_HOURS = 24
_GC_INTERVAL_SECONDS = 300  # Run garbage collection every 5 minutes


class ShortTermMemory:
    """
    In-memory store with 24-hour TTL and optional Supabase persistence.

    Stores four categories of entries:
        - trades: completed trade records
        - signals: generated signals (including rejected ones)
        - news: news events with market impact tags
        - indicators: indicator snapshot dictionaries

    Each entry is tagged with a UTC timestamp and auto-expires after 24 hours.

    Parameters
    ----------
    supabase_client : optional
        Supabase client instance. If None, operates in local-only mode.
    expiry_hours : int
        How long entries live (default: 24).
    """

    def __init__(
        self,
        supabase_client: Optional[Any] = None,
        expiry_hours: int = _EXPIRY_HOURS,
    ) -> None:
        self._supabase = supabase_client
        self._expiry_hours = expiry_hours
        self._expiry_delta = timedelta(hours=expiry_hours)

        # Local cache: category → list of {data, timestamp}
        self._cache: Dict[str, List[Dict[str, Any]]] = {
            "trades": [],
            "signals": [],
            "news": [],
            "indicators": [],
        }
        self._lock = threading.RLock()

        # Start background GC thread
        self._gc_thread = threading.Thread(
            target=self._gc_loop, daemon=True, name="ShortTermMemory-GC"
        )
        self._gc_thread.start()
        logger.info("ShortTermMemory initialised (expiry=%dh)", expiry_hours)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def store_trade_result(self, trade: Dict[str, Any]) -> None:
        """
        Store a completed trade result.

        Parameters
        ----------
        trade : dict
            Must include 'symbol' and 'market'. Timestamp is auto-added.
        """
        entry = self._make_entry(trade)
        with self._lock:
            self._cache["trades"].append(entry)
        self._persist("short_term_trades", entry)
        logger.debug("Stored trade: %s/%s", trade.get("market"), trade.get("symbol"))

    def store_signal(self, signal: Dict[str, Any]) -> None:
        """Store a generated (or rejected) trading signal."""
        entry = self._make_entry(signal)
        with self._lock:
            self._cache["signals"].append(entry)
        self._persist("short_term_signals", entry)

    def store_news_event(self, news: Dict[str, Any]) -> None:
        """Store a news event with optional market impact rating."""
        entry = self._make_entry(news)
        with self._lock:
            self._cache["news"].append(entry)
        self._persist("short_term_news", entry)

    def store_indicator_snapshot(self, snapshot: Dict[str, Any]) -> None:
        """Store a snapshot of indicator values for a symbol/timeframe."""
        entry = self._make_entry(snapshot)
        with self._lock:
            self._cache["indicators"].append(entry)
        self._persist("short_term_indicators", entry)

    def get_recent_trades(
        self, market: Optional[str] = None, hours: int = 24
    ) -> List[Dict[str, Any]]:
        """
        Return trades within the last `hours` hours.

        Parameters
        ----------
        market : str, optional
            Filter by market ('crypto', 'forex', etc.). None = all markets.
        hours : int
            Look-back window in hours.

        Returns
        -------
        List[dict]
            Trade records sorted oldest-first.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self._lock:
            entries = [
                e for e in self._cache["trades"]
                if e["_ts"] >= cutoff
                and (market is None or e["data"].get("market") == market)
            ]
        return [e["data"] for e in entries]

    def get_failed_setups(
        self, hours: int = 12, market: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Return trades that resulted in a loss (pnl < 0) within `hours`.

        Used to avoid repeating mistakes in the short term.

        Parameters
        ----------
        hours : int
            Look-back window.
        market : str, optional
            Filter by market.

        Returns
        -------
        List[dict]
            Failed trade records.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self._lock:
            entries = [
                e for e in self._cache["trades"]
                if e["_ts"] >= cutoff
                and e["data"].get("pnl", 0) < 0
                and (market is None or e["data"].get("market") == market)
            ]
        return [e["data"] for e in entries]

    def get_recent_signals(
        self, market: Optional[str] = None, hours: int = 4
    ) -> List[Dict[str, Any]]:
        """Return recently generated signals."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self._lock:
            entries = [
                e for e in self._cache["signals"]
                if e["_ts"] >= cutoff
                and (market is None or e["data"].get("market") == market)
            ]
        return [e["data"] for e in entries]

    def get_recent_news(self, hours: int = 6) -> List[Dict[str, Any]]:
        """Return recent news events."""
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        with self._lock:
            entries = [e for e in self._cache["news"] if e["_ts"] >= cutoff]
        return [e["data"] for e in entries]

    def get_latest_indicators(
        self, symbol: str, timeframe: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """Return the most recent indicator snapshot for a symbol."""
        with self._lock:
            candidates = [
                e for e in self._cache["indicators"]
                if e["data"].get("symbol") == symbol
                and (timeframe is None or e["data"].get("timeframe") == timeframe)
            ]
        if not candidates:
            return None
        latest = max(candidates, key=lambda e: e["_ts"])
        return latest["data"]

    def count_entries(self, category: str = "trades") -> int:
        """Return current count of in-memory entries for a category."""
        with self._lock:
            return len(self._cache.get(category, []))

    def clear_all(self) -> None:
        """Clear all in-memory entries (for testing / reset)."""
        with self._lock:
            for key in self._cache:
                self._cache[key].clear()
        logger.info("ShortTermMemory cleared")

    # ------------------------------------------------------------------
    # Supabase persistence
    # ------------------------------------------------------------------

    def _persist(self, table: str, entry: Dict[str, Any]) -> None:
        """Best-effort async write to Supabase. Does not block."""
        if self._supabase is None:
            return
        try:
            payload = {
                "data": entry["data"],
                "timestamp": entry["_ts"].isoformat(),
                "expires_at": (entry["_ts"] + self._expiry_delta).isoformat(),
            }
            # Fire and forget — use response
            self._supabase.table(table).insert(payload).execute()
        except Exception as exc:
            logger.warning("Supabase persist failed [%s]: %s", table, exc)

    def _load_from_supabase(self, table: str, category: str) -> None:
        """Warm up local cache from Supabase on startup."""
        if self._supabase is None:
            return
        try:
            cutoff = (datetime.now(timezone.utc) - self._expiry_delta).isoformat()
            response = (
                self._supabase.table(table)
                .select("*")
                .gt("timestamp", cutoff)
                .execute()
            )
            records = response.data or []
            with self._lock:
                for rec in records:
                    ts_str = rec.get("timestamp", datetime.now(timezone.utc).isoformat())
                    ts = datetime.fromisoformat(ts_str)
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    self._cache[category].append({"data": rec.get("data", {}), "_ts": ts})
            logger.info("Loaded %d entries for %s from Supabase", len(records), category)
        except Exception as exc:
            logger.warning("Supabase warm-up failed [%s]: %s", table, exc)

    def warm_up_from_supabase(self) -> None:
        """Load recent data from all Supabase tables into local cache."""
        table_map = {
            "short_term_trades": "trades",
            "short_term_signals": "signals",
            "short_term_news": "news",
            "short_term_indicators": "indicators",
        }
        for table, category in table_map.items():
            self._load_from_supabase(table, category)

    # ------------------------------------------------------------------
    # Garbage collection
    # ------------------------------------------------------------------

    def _gc_loop(self) -> None:
        """Background thread: expire old entries every GC_INTERVAL seconds."""
        while True:
            time.sleep(_GC_INTERVAL_SECONDS)
            try:
                self._expire_old_entries()
            except Exception as exc:
                logger.warning("ShortTermMemory GC error: %s", exc)

    def _expire_old_entries(self) -> None:
        cutoff = datetime.now(timezone.utc) - self._expiry_delta
        total_removed = 0
        with self._lock:
            for category in self._cache:
                before = len(self._cache[category])
                self._cache[category] = [
                    e for e in self._cache[category] if e["_ts"] >= cutoff
                ]
                total_removed += before - len(self._cache[category])
        if total_removed:
            logger.debug("ShortTermMemory GC: removed %d expired entries", total_removed)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _make_entry(data: Dict[str, Any]) -> Dict[str, Any]:
        """Wrap data with a UTC timestamp."""
        return {
            "data": data,
            "_ts": datetime.now(timezone.utc),
        }

    def __repr__(self) -> str:
        with self._lock:
            counts = {k: len(v) for k, v in self._cache.items()}
        return f"<ShortTermMemory expiry={self._expiry_hours}h counts={counts}>"
