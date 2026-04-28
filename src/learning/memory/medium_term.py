"""
Medium-Term Memory for NEXUS ALPHA Learning System.

Retention: 7-30 days.
Stores patterns, winning setups, time-of-day performance,
and regime transition history.
Backed by Supabase with local in-memory index.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_DEFAULT_RETENTION_DAYS = 30
_SHORT_RETENTION_DAYS = 7


class MediumTermMemory:
    """
    7-30 day pattern and performance storage.

    Provides:
        - Pattern storage with outcome tracking
        - Winning setup retrieval filtered by market and confidence
        - Time-of-day / day-of-week performance breakdown
        - Regime transition history

    Parameters
    ----------
    supabase_client : optional
        Supabase client. If None, operates in local-only mode.
    retention_days : int
        How long patterns are retained (default: 30).
    """

    def __init__(
        self,
        supabase_client: Optional[Any] = None,
        retention_days: int = _DEFAULT_RETENTION_DAYS,
    ) -> None:
        self._supabase = supabase_client
        self._retention_days = retention_days

        # Local stores
        self._patterns: List[Dict[str, Any]] = []
        self._regime_transitions: List[Dict[str, Any]] = []
        self._performance_log: List[Dict[str, Any]] = []

        logger.info("MediumTermMemory initialised (retention=%dd)", retention_days)

    # ------------------------------------------------------------------
    # Pattern storage
    # ------------------------------------------------------------------

    def store_pattern(
        self,
        pattern_name: str,
        conditions: Dict[str, Any],
        outcome: str,  # 'win', 'loss', 'breakeven'
        confidence: float,
        market: str = "all",
        pnl: float = 0.0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """
        Store a detected trading pattern with its observed outcome.

        Parameters
        ----------
        pattern_name : str
            E.g. 'donchian_breakout_with_volume', 'rsi_oversold_reversal'
        conditions : dict
            The indicator values / conditions present at time of pattern
        outcome : str
            'win', 'loss', or 'breakeven'
        confidence : float
            0.0–1.0 confidence score at time of signal
        market : str
            Market where pattern was observed
        pnl : float
            P&L in R-multiples (positive = profitable)
        metadata : dict, optional
            Additional context (strategy, timeframe, etc.)
        """
        entry = {
            "pattern_name": pattern_name,
            "conditions": conditions,
            "outcome": outcome,
            "confidence": confidence,
            "market": market,
            "pnl": pnl,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._patterns.append(entry)
        self._persist("medium_term_patterns", entry)
        logger.debug("Stored pattern '%s' [%s] on %s", pattern_name, outcome, market)

    def get_winning_setups(
        self,
        market: str,
        min_confidence: float = 0.6,
        days: int = 30,
        min_occurrences: int = 2,
    ) -> List[Dict[str, Any]]:
        """
        Return winning patterns for a market with minimum confidence.

        Groups by pattern_name and returns those that win more than they lose.

        Parameters
        ----------
        market : str
            Target market.
        min_confidence : float
            Minimum confidence at signal time.
        days : int
            Look-back window in days.
        min_occurrences : int
            Minimum times a pattern must have appeared.

        Returns
        -------
        List[dict]
            Sorted by win rate descending.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        relevant = [
            p for p in self._patterns
            if p.get("market") in (market, "all")
            and p.get("confidence", 0) >= min_confidence
            and p.get("timestamp", "") >= cutoff
        ]

        # Group by pattern_name
        grouped: Dict[str, List[Dict]] = {}
        for p in relevant:
            name = p["pattern_name"]
            grouped.setdefault(name, []).append(p)

        result = []
        for name, entries in grouped.items():
            if len(entries) < min_occurrences:
                continue
            wins = sum(1 for e in entries if e["outcome"] == "win")
            total = len(entries)
            win_rate = wins / total
            avg_pnl = float(np.mean([e["pnl"] for e in entries]))

            if win_rate > 0.5:  # Only return patterns that win more than they lose
                result.append({
                    "pattern_name": name,
                    "win_rate": win_rate,
                    "total_occurrences": total,
                    "avg_pnl": avg_pnl,
                    "last_seen": max(e["timestamp"] for e in entries),
                    "representative_conditions": entries[-1]["conditions"],
                })

        result.sort(key=lambda x: x["win_rate"], reverse=True)
        return result

    # ------------------------------------------------------------------
    # Performance by time
    # ------------------------------------------------------------------

    def log_trade_time(
        self,
        market: str,
        symbol: str,
        pnl: float,
        entry_time: datetime,
    ) -> None:
        """Log a trade for time-based performance analysis."""
        entry = {
            "market": market,
            "symbol": symbol,
            "pnl": pnl,
            "hour_of_day": entry_time.hour,
            "day_of_week": entry_time.weekday(),  # 0=Mon, 6=Sun
            "won": pnl > 0,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._performance_log.append(entry)
        self._persist("medium_term_performance", entry)

    def get_performance_by_time(
        self, market: str, days: int = 30
    ) -> Dict[str, Any]:
        """
        Return performance broken down by hour-of-day and day-of-week.

        Returns
        -------
        dict with keys:
            'by_hour': {0: {'win_rate': 0.6, 'avg_pnl': 0.5, 'count': 10}, ...}
            'by_day':  {0: {'win_rate': 0.55, 'avg_pnl': 0.3, 'count': 8}, ...}
            'best_hour': int
            'worst_hour': int
            'best_day': int
            'worst_day': int
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        relevant = [
            p for p in self._performance_log
            if p.get("market") == market
            and p.get("timestamp", "") >= cutoff
        ]

        by_hour: Dict[int, List[float]] = {}
        by_day: Dict[int, List[float]] = {}

        for entry in relevant:
            h = entry.get("hour_of_day", 0)
            d = entry.get("day_of_week", 0)
            pnl = entry.get("pnl", 0.0)
            by_hour.setdefault(h, []).append(pnl)
            by_day.setdefault(d, []).append(pnl)

        def summarise(groups: Dict[int, List[float]]) -> Dict[int, Dict]:
            out = {}
            for k, pnls in groups.items():
                wins = sum(1 for p in pnls if p > 0)
                out[k] = {
                    "win_rate": wins / len(pnls),
                    "avg_pnl": float(np.mean(pnls)),
                    "count": len(pnls),
                }
            return out

        hour_summary = summarise(by_hour)
        day_summary = summarise(by_day)

        best_hour = max(hour_summary, key=lambda h: hour_summary[h]["avg_pnl"], default=9)
        worst_hour = min(hour_summary, key=lambda h: hour_summary[h]["avg_pnl"], default=0)
        best_day = max(day_summary, key=lambda d: day_summary[d]["avg_pnl"], default=0)
        worst_day = min(day_summary, key=lambda d: day_summary[d]["avg_pnl"], default=4)

        return {
            "by_hour": hour_summary,
            "by_day": day_summary,
            "best_hour": best_hour,
            "worst_hour": worst_hour,
            "best_day": best_day,
            "worst_day": worst_day,
        }

    # ------------------------------------------------------------------
    # Regime transitions
    # ------------------------------------------------------------------

    def store_regime_transition(
        self,
        market: str,
        old_regime: str,
        new_regime: str,
        trigger: str = "",
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record a detected market regime transition."""
        entry = {
            "market": market,
            "old_regime": old_regime,
            "new_regime": new_regime,
            "trigger": trigger,
            "metadata": metadata or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._regime_transitions.append(entry)
        self._persist("medium_term_regime_transitions", entry)
        logger.info(
            "Regime transition [%s]: %s → %s | trigger=%s",
            market, old_regime, new_regime, trigger,
        )

    def get_regime_transitions(
        self, market: Optional[str] = None, days: int = 30
    ) -> List[Dict[str, Any]]:
        """
        Return regime transitions within the last `days` days.

        Parameters
        ----------
        market : str, optional
            Filter by market.
        days : int
            Look-back window.

        Returns
        -------
        List[dict] sorted by timestamp descending.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        result = [
            t for t in self._regime_transitions
            if t.get("timestamp", "") >= cutoff
            and (market is None or t.get("market") == market)
        ]
        result.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return result

    def get_latest_regime(self, market: str) -> Optional[str]:
        """Return the most recent regime for a market."""
        transitions = self.get_regime_transitions(market=market, days=30)
        if transitions:
            return transitions[0].get("new_regime")
        return None

    # ------------------------------------------------------------------
    # Expiry & persistence
    # ------------------------------------------------------------------

    def expire_old_entries(self) -> None:
        """Purge entries older than retention_days."""
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention_days)).isoformat()
        before = sum(len(lst) for lst in [self._patterns, self._regime_transitions, self._performance_log])
        self._patterns = [p for p in self._patterns if p.get("timestamp", "") >= cutoff]
        self._regime_transitions = [r for r in self._regime_transitions if r.get("timestamp", "") >= cutoff]
        self._performance_log = [l for l in self._performance_log if l.get("timestamp", "") >= cutoff]
        after = sum(len(lst) for lst in [self._patterns, self._regime_transitions, self._performance_log])
        removed = before - after
        if removed:
            logger.debug("MediumTermMemory: expired %d entries", removed)

    def _persist(self, table: str, entry: Dict[str, Any]) -> None:
        if self._supabase is None:
            return
        try:
            self._supabase.table(table).insert(entry).execute()
        except Exception as exc:
            logger.warning("Supabase persist failed [%s]: %s", table, exc)

    def load_from_supabase(self) -> None:
        """Warm up local stores from Supabase on startup."""
        if self._supabase is None:
            return
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self._retention_days)).isoformat()
        tables = {
            "medium_term_patterns": "_patterns",
            "medium_term_regime_transitions": "_regime_transitions",
            "medium_term_performance": "_performance_log",
        }
        for table, attr in tables.items():
            try:
                resp = (
                    self._supabase.table(table)
                    .select("*")
                    .gt("timestamp", cutoff)
                    .execute()
                )
                records = resp.data or []
                current = getattr(self, attr)
                current.extend(records)
                logger.info("Loaded %d records from %s", len(records), table)
            except Exception as exc:
                logger.warning("Supabase load failed [%s]: %s", table, exc)

    def __repr__(self) -> str:
        return (
            f"<MediumTermMemory retention={self._retention_days}d "
            f"patterns={len(self._patterns)} "
            f"transitions={len(self._regime_transitions)}>"
        )
