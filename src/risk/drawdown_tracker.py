"""
NEXUS ALPHA - Drawdown Tracker
================================
Tracks portfolio equity peak, current drawdown, and the number of days
spent in drawdown.  Persists snapshots to Supabase portfolio_snapshots.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds (must match five_layer_risk.py)
# ---------------------------------------------------------------------------
DRAWDOWN_WARNING_THRESHOLD = 0.15  # 15%
DRAWDOWN_PAUSE_THRESHOLD   = 0.30  # 30%
DRAWDOWN_STOP_THRESHOLD    = 0.40  # 40%


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class DrawdownState:
    """Current drawdown snapshot."""
    peak_equity: float
    current_equity: float
    current_drawdown_pct: float       # 0–1 (positive means in drawdown)
    max_drawdown_pct: float           # worst ever seen
    days_in_drawdown: int             # calendar days since drawdown started
    drawdown_start_timestamp: Optional[float] = None


# ---------------------------------------------------------------------------
# DrawdownTracker
# ---------------------------------------------------------------------------

class DrawdownTracker:
    """
    Rolling drawdown tracker for the NEXUS ALPHA portfolio.

    Call ``update()`` after every equity mark-to-market event.  The
    tracker maintains the running peak and computes all drawdown metrics.

    Supabase persistence is attempted on every ``record_to_db()`` call;
    failures are logged but do NOT raise to avoid blocking trade execution.
    """

    def __init__(self, initial_equity: float = 0.0) -> None:
        self._peak_equity: float = initial_equity
        self._max_drawdown_pct: float = 0.0
        self._drawdown_start_ts: Optional[float] = None
        self._last_state: Optional[DrawdownState] = None

    # ------------------------------------------------------------------
    # Core update
    # ------------------------------------------------------------------

    def update(self, current_equity: float) -> DrawdownState:
        """
        Update the tracker with the latest portfolio equity value.

        Parameters
        ----------
        current_equity : float
            Current mark-to-market portfolio equity in USD.

        Returns
        -------
        DrawdownState
            Full drawdown state after this update.
        """
        now = time.time()

        # Update peak
        if current_equity > self._peak_equity:
            self._peak_equity = current_equity
            # If we recover above peak, reset drawdown start
            if self._drawdown_start_ts is not None:
                logger.info(
                    "DrawdownTracker: equity $%.2f exceeded previous peak – drawdown cleared",
                    current_equity,
                )
                self._drawdown_start_ts = None

        # Compute current drawdown
        if self._peak_equity > 0:
            dd_pct = (self._peak_equity - current_equity) / self._peak_equity
        else:
            dd_pct = 0.0

        dd_pct = max(0.0, dd_pct)

        # Track when drawdown began
        if dd_pct > 0 and self._drawdown_start_ts is None:
            self._drawdown_start_ts = now

        # Reset if fully recovered
        if dd_pct <= 0 and self._drawdown_start_ts is not None:
            self._drawdown_start_ts = None

        # Update worst-ever drawdown
        self._max_drawdown_pct = max(self._max_drawdown_pct, dd_pct)

        # Compute days in drawdown
        days_in_dd = 0
        if self._drawdown_start_ts is not None:
            elapsed_s = now - self._drawdown_start_ts
            days_in_dd = int(elapsed_s / 86400)

        state = DrawdownState(
            peak_equity=self._peak_equity,
            current_equity=current_equity,
            current_drawdown_pct=dd_pct,
            max_drawdown_pct=self._max_drawdown_pct,
            days_in_drawdown=days_in_dd,
            drawdown_start_timestamp=self._drawdown_start_ts,
        )
        self._last_state = state

        if dd_pct > 0:
            logger.debug(
                "DrawdownTracker: equity=$%.2f peak=$%.2f dd=%.2f%% max_dd=%.2f%% days=%d",
                current_equity, self._peak_equity,
                dd_pct * 100, self._max_drawdown_pct * 100, days_in_dd,
            )

        return state

    # ------------------------------------------------------------------
    # Status queries
    # ------------------------------------------------------------------

    def is_in_drawdown(self) -> bool:
        """Return True if the portfolio is currently below its peak."""
        if self._last_state is None:
            return False
        return self._last_state.current_drawdown_pct > 0

    def get_drawdown_level(self) -> str:
        """
        Return the categorical drawdown level.

        Returns
        -------
        str
            One of "NORMAL", "WARNING", "PAUSE", "STOP".
        """
        if self._last_state is None:
            return "NORMAL"

        dd = self._last_state.current_drawdown_pct

        if dd >= DRAWDOWN_STOP_THRESHOLD:
            return "STOP"
        if dd >= DRAWDOWN_PAUSE_THRESHOLD:
            return "PAUSE"
        if dd >= DRAWDOWN_WARNING_THRESHOLD:
            return "WARNING"
        return "NORMAL"

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def record_to_db(self) -> None:
        """
        Persist the current drawdown state to Supabase ``portfolio_snapshots``.

        Failures are logged at ERROR level but never raised so that
        trade execution is not disrupted by a DB outage.
        """
        if self._last_state is None:
            return

        state = self._last_state
        now_iso = datetime.fromtimestamp(time.time(), tz=timezone.utc).isoformat()

        record = {
            "snapshot_time": now_iso,
            "peak_equity": state.peak_equity,
            "current_equity": state.current_equity,
            "current_drawdown_pct": state.current_drawdown_pct,
            "max_drawdown_pct": state.max_drawdown_pct,
            "days_in_drawdown": state.days_in_drawdown,
            "drawdown_level": self.get_drawdown_level(),
        }

        try:
            from src.db.supabase_client import get_supabase_client  # type: ignore[import]
            client = get_supabase_client()
            client.table("portfolio_snapshots").insert(record).execute()
            logger.debug("DrawdownTracker: snapshot persisted to Supabase")
        except ImportError:
            logger.warning("DrawdownTracker: Supabase client not available – skipping DB write")
        except Exception as exc:  # noqa: BLE001
            logger.error("DrawdownTracker: DB write failed: %s | record=%s", exc, record)
