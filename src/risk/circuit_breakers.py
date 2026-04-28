"""
NEXUS ALPHA - Circuit Breaker Manager
=======================================
Six automated circuit breakers that protect the system from adverse
market conditions, technical failures, and systemic risk events.

CB1 - Flash Crash:         5% price drop in 5 min → halt 1 hour
CB2 - Consecutive Losses:  3+ losses in a row → size -50%, halt 2 hours
CB3 - Spread Explosion:    Spread > 3x normal → skip new entries
CB4 - API Error Storm:     >5 errors in 60 s → halt all markets
CB5 - Correlation Spike:   Cross-market corr > 0.85 → exposure -50%
CB6 - Drawdown Gate:       Integrates with FiveLayerRisk Layer 5
"""

from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

CB1_DROP_THRESHOLD   = 0.05    # 5% price drop in 5 min
CB1_HALT_SECONDS     = 3600    # 1 hour halt

CB2_LOSS_THRESHOLD   = 3       # consecutive losses before trigger
CB2_SIZE_REDUCTION   = 0.50    # reduce to 50% of normal size
CB2_HALT_SECONDS     = 7200    # 2 hour halt

CB3_SPREAD_MULT      = 3.0     # spread > 3× normal = explosion

CB4_ERROR_WINDOW_S   = 60      # rolling 60-second window
CB4_ERROR_THRESHOLD  = 5       # errors in window before halt

CB5_CORR_THRESHOLD   = 0.85    # correlation spike hard limit
CB5_EXPOSURE_MULT    = 0.50    # reduce exposure to 50%

CB6_PAUSE_THRESHOLD  = 0.30    # 30% drawdown → PAUSE
CB6_STOP_THRESHOLD   = 0.40    # 40% drawdown → STOP


# ---------------------------------------------------------------------------
# Event dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CB1Event:
    """Flash crash detection event."""
    market: str
    symbol: str
    drop_pct: float
    prices_5m: List[float]
    timestamp: float = field(default_factory=time.time)
    halt_until: float = field(init=False)

    def __post_init__(self) -> None:
        self.halt_until = self.timestamp + CB1_HALT_SECONDS


@dataclass
class CB2Event:
    """Consecutive loss event."""
    market: str
    consecutive_losses: int
    timestamp: float = field(default_factory=time.time)
    halt_until: float = field(init=False)

    def __post_init__(self) -> None:
        self.halt_until = self.timestamp + CB2_HALT_SECONDS


@dataclass
class CB5Event:
    """Correlation spike event."""
    correlated_pairs: List[Tuple[str, str, float]]  # (sym1, sym2, corr)
    timestamp: float = field(default_factory=time.time)


@dataclass
class HaltRecord:
    """Tracks an active market halt."""
    market: str
    reason: str
    halt_until: float
    cb_number: int


# ---------------------------------------------------------------------------
# CircuitBreakerManager
# ---------------------------------------------------------------------------

class CircuitBreakerManager:
    """
    Central manager for all six circuit breakers.

    State is held in memory; the caller is responsible for persisting
    halt records to a database if durability across restarts is required.

    Thread-safety: this class is NOT thread-safe.  Use an asyncio lock
    or a single-threaded event loop if concurrent access is needed.
    """

    def __init__(self) -> None:
        # Active halts keyed by market string or "__ALL__" for global
        self._halts: Dict[str, HaltRecord] = {}

        # CB4: sliding window of API error timestamps
        self._api_errors: Deque[float] = deque()

        # CB2: consecutive loss tracker per market
        self._consecutive_losses: Dict[str, int] = {}

        # CB5: last event (for reference)
        self._last_cb5_event: Optional[CB5Event] = None

    # ------------------------------------------------------------------
    # CB1 - Flash Crash
    # ------------------------------------------------------------------

    def cb1_check(
        self,
        market: str,
        symbol: str,
        prices_5m: List[float],
    ) -> Optional[CB1Event]:
        """
        Detect a flash crash: ≥5% price drop over the 5-minute window.

        Parameters
        ----------
        market : str
            Market segment (e.g. "crypto").
        symbol : str
            Trading symbol (e.g. "BTCUSDT").
        prices_5m : List[float]
            List of prices sampled in the last 5 minutes (oldest first).

        Returns
        -------
        Optional[CB1Event]
            CB1Event if triggered, else None.  Registers a halt internally.
        """
        if len(prices_5m) < 2:
            return None

        high_price = max(prices_5m)
        last_price = prices_5m[-1]

        if high_price <= 0:
            return None

        drop_pct = (high_price - last_price) / high_price

        if drop_pct >= CB1_DROP_THRESHOLD:
            event = CB1Event(
                market=market,
                symbol=symbol,
                drop_pct=drop_pct,
                prices_5m=prices_5m,
            )
            self._register_halt(
                market=market,
                reason=f"CB1 Flash Crash: {symbol} dropped {drop_pct:.1%} in 5 min",
                halt_until=event.halt_until,
                cb_number=1,
            )
            logger.critical(
                "CB1 FLASH CRASH: %s dropped %.2f%% in 5 min – halting %s for 1h",
                symbol, drop_pct * 100, market,
            )
            return event

        return None

    # ------------------------------------------------------------------
    # CB2 - Consecutive Losses
    # ------------------------------------------------------------------

    def cb2_check(
        self,
        market: str,
        recent_trades: List[Dict[str, Any]],
    ) -> Optional[CB2Event]:
        """
        Detect 3 or more consecutive losing trades in the given market.

        Parameters
        ----------
        market : str
            Market segment to check.
        recent_trades : List[dict]
            List of recent trade dicts, each with a ``'pnl'`` key (float).
            Most recent trade should be LAST.

        Returns
        -------
        Optional[CB2Event]
            CB2Event if triggered, else None.
        """
        if not recent_trades:
            return None

        consecutive = 0
        for trade in reversed(recent_trades):
            pnl = trade.get("pnl", 0.0)
            if pnl < 0:
                consecutive += 1
            else:
                break

        self._consecutive_losses[market] = consecutive

        if consecutive >= CB2_LOSS_THRESHOLD:
            event = CB2Event(market=market, consecutive_losses=consecutive)
            self._register_halt(
                market=market,
                reason=f"CB2 Consecutive Losses: {consecutive} losses in a row",
                halt_until=event.halt_until,
                cb_number=2,
            )
            logger.error(
                "CB2 CONSECUTIVE LOSSES: %s has %d consecutive losses – halting 2h",
                market, consecutive,
            )
            return event

        return None

    # ------------------------------------------------------------------
    # CB3 - Spread Explosion
    # ------------------------------------------------------------------

    def cb3_check(
        self,
        symbol: str,
        current_spread: float,
        normal_spread: float,
    ) -> bool:
        """
        Return True if the spread has exploded beyond 3× normal.

        A True return means new trades in this symbol should be SKIPPED
        (not halted – existing positions are unaffected).

        Parameters
        ----------
        symbol : str
            Trading symbol.
        current_spread : float
            Current bid-ask spread in price units.
        normal_spread : float
            Typical/baseline spread for this symbol.

        Returns
        -------
        bool
            True if spread is dangerous (skip new entries).
        """
        if normal_spread <= 0:
            return False

        ratio = current_spread / normal_spread
        if ratio >= CB3_SPREAD_MULT:
            logger.warning(
                "CB3 SPREAD EXPLOSION: %s spread=%.5f (%.1fx normal) – skipping entries",
                symbol, current_spread, ratio,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # CB4 - API Error Storm
    # ------------------------------------------------------------------

    def record_error(self, source: str) -> None:
        """
        Record an API error event.  Called by executors on any API failure.

        Parameters
        ----------
        source : str
            Identifier for the failing component (e.g. "binance_executor").
        """
        now = time.time()
        self._api_errors.append(now)

        # Prune old entries outside the 60-second window
        cutoff = now - CB4_ERROR_WINDOW_S
        while self._api_errors and self._api_errors[0] < cutoff:
            self._api_errors.popleft()

        recent_count = len(self._api_errors)
        logger.debug("CB4 error recorded from %s – %d in last 60s", source, recent_count)

    def cb4_check(self, error_count_60s: Optional[int] = None) -> bool:
        """
        Return True if the API error storm threshold has been breached.

        Can be called with an external ``error_count_60s`` override, or
        rely on the internally tracked ``record_error`` deque.

        Parameters
        ----------
        error_count_60s : Optional[int]
            If provided, use this count directly.  Otherwise uses the
            internal deque.

        Returns
        -------
        bool
            True if threshold exceeded → all markets should halt.
        """
        now = time.time()

        if error_count_60s is None:
            # Prune and count
            cutoff = now - CB4_ERROR_WINDOW_S
            while self._api_errors and self._api_errors[0] < cutoff:
                self._api_errors.popleft()
            count = len(self._api_errors)
        else:
            count = error_count_60s

        if count > CB4_ERROR_THRESHOLD:
            self._register_halt(
                market="__ALL__",
                reason=f"CB4 API Error Storm: {count} errors in 60s",
                halt_until=now + 1800,  # 30 min global halt
                cb_number=4,
            )
            logger.critical(
                "CB4 API ERROR STORM: %d errors in 60s – halting ALL markets",
                count,
            )
            return True

        return False

    # ------------------------------------------------------------------
    # CB5 - Correlation Spike
    # ------------------------------------------------------------------

    def cb5_check(
        self,
        correlation_matrix: Any,  # pd.DataFrame
    ) -> Optional[CB5Event]:
        """
        Detect a dangerous cross-market correlation spike (> 0.85).

        When triggered, the caller should reduce all exposures by 50%.
        This does NOT register a market halt – it returns an event for
        the caller to act on.

        Parameters
        ----------
        correlation_matrix : pd.DataFrame
            Square correlation matrix.  Columns and index are symbol strings.

        Returns
        -------
        Optional[CB5Event]
            CB5Event listing the correlated pairs if threshold is exceeded.
        """
        try:
            import pandas as pd
            import numpy as np

            if not isinstance(correlation_matrix, pd.DataFrame):
                return None

            symbols = list(correlation_matrix.columns)
            correlated_pairs: List[Tuple[str, str, float]] = []

            for i, s1 in enumerate(symbols):
                for j, s2 in enumerate(symbols):
                    if j <= i:
                        continue
                    corr = float(correlation_matrix.loc[s1, s2])
                    if abs(corr) >= CB5_CORR_THRESHOLD:
                        correlated_pairs.append((s1, s2, corr))

            if correlated_pairs:
                event = CB5Event(correlated_pairs=correlated_pairs)
                self._last_cb5_event = event
                logger.error(
                    "CB5 CORRELATION SPIKE: %d pairs exceed %.2f correlation – "
                    "reduce exposure 50%%",
                    len(correlated_pairs), CB5_CORR_THRESHOLD,
                )
                return event

        except Exception as exc:  # noqa: BLE001
            logger.error("CB5 check error: %s", exc)

        return None

    # ------------------------------------------------------------------
    # CB6 - Drawdown Gate
    # ------------------------------------------------------------------

    def cb6_check(self, drawdown_pct: float) -> Optional[str]:
        """
        Drawdown gate integrated with FiveLayerRisk Layer 5.

        Parameters
        ----------
        drawdown_pct : float
            Current drawdown as a positive fraction (0.30 = 30%).

        Returns
        -------
        Optional[str]
            ``"PAUSE"`` if drawdown ≥ 30%, ``"STOP"`` if ≥ 40%, else None.
        """
        if drawdown_pct >= CB6_STOP_THRESHOLD:
            logger.critical("CB6 DRAWDOWN STOP: %.1f%% drawdown", drawdown_pct * 100)
            self._register_halt(
                market="__ALL__",
                reason=f"CB6 Drawdown STOP: {drawdown_pct:.1%} drawdown",
                halt_until=time.time() + 86400,  # 24h mandatory review
                cb_number=6,
            )
            return "STOP"

        if drawdown_pct >= CB6_PAUSE_THRESHOLD:
            logger.error("CB6 DRAWDOWN PAUSE: %.1f%% drawdown", drawdown_pct * 100)
            return "PAUSE"

        return None

    # ------------------------------------------------------------------
    # Halt management
    # ------------------------------------------------------------------

    def _register_halt(
        self,
        market: str,
        reason: str,
        halt_until: float,
        cb_number: int,
    ) -> None:
        """Register an internal halt record."""
        self._halts[market] = HaltRecord(
            market=market,
            reason=reason,
            halt_until=halt_until,
            cb_number=cb_number,
        )

    def is_halted(self, market: str) -> bool:
        """
        Return True if the given market (or all markets) is currently halted.

        Automatically clears expired halts.

        Parameters
        ----------
        market : str
            Market segment key, or any string for a specific market.

        Returns
        -------
        bool
            True if the market should not accept new orders.
        """
        now = time.time()

        # Check global halt first
        global_halt = self._halts.get("__ALL__")
        if global_halt:
            if now < global_halt.halt_until:
                return True
            else:
                del self._halts["__ALL__"]

        # Check market-specific halt
        halt = self._halts.get(market)
        if halt:
            if now < halt.halt_until:
                return True
            else:
                del self._halts[market]

        return False

    def get_halt_reason(self, market: str) -> Optional[str]:
        """
        Return the reason for the current halt (if any).

        Parameters
        ----------
        market : str
            Market segment key.

        Returns
        -------
        Optional[str]
            Human-readable halt reason, or None if not halted.
        """
        if not self.is_halted(market):
            return None

        # Check global first, then market-specific
        global_halt = self._halts.get("__ALL__")
        if global_halt:
            return global_halt.reason

        halt = self._halts.get(market)
        return halt.reason if halt else None

    def clear_halt(self, market: str) -> None:
        """
        Manually clear a halt (e.g. after cooldown or manual review).

        Parameters
        ----------
        market : str
            Market segment key, or ``"__ALL__"`` for the global halt.
        """
        if market in self._halts:
            logger.info("CB: Clearing halt for %s", market)
            del self._halts[market]

    # ------------------------------------------------------------------
    # Status report
    # ------------------------------------------------------------------

    def get_status(self) -> Dict[str, Any]:
        """
        Return a comprehensive status snapshot of all circuit breakers.

        Returns
        -------
        dict
            Keys: active_halts, api_errors_60s, consecutive_losses,
            last_cb5_event, cb_states.
        """
        now = time.time()

        # Build active halt info
        active_halts = {}
        for market, record in list(self._halts.items()):
            if now < record.halt_until:
                active_halts[market] = {
                    "reason": record.reason,
                    "cb_number": record.cb_number,
                    "seconds_remaining": max(0, record.halt_until - now),
                }
            else:
                del self._halts[market]

        # Count recent API errors
        cutoff = now - CB4_ERROR_WINDOW_S
        api_errors_60s = sum(1 for t in self._api_errors if t >= cutoff)

        return {
            "active_halts": active_halts,
            "api_errors_60s": api_errors_60s,
            "api_error_threshold": CB4_ERROR_THRESHOLD,
            "consecutive_losses": dict(self._consecutive_losses),
            "last_cb5_event": {
                "correlated_pairs": self._last_cb5_event.correlated_pairs,
                "timestamp": self._last_cb5_event.timestamp,
            } if self._last_cb5_event else None,
            "thresholds": {
                "cb1_drop_pct": CB1_DROP_THRESHOLD,
                "cb2_loss_count": CB2_LOSS_THRESHOLD,
                "cb3_spread_mult": CB3_SPREAD_MULT,
                "cb4_errors_60s": CB4_ERROR_THRESHOLD,
                "cb5_corr": CB5_CORR_THRESHOLD,
                "cb6_pause": CB6_PAUSE_THRESHOLD,
                "cb6_stop": CB6_STOP_THRESHOLD,
            },
        }
