"""
NEXUS ALPHA - Learning Memory Package
========================================
Exports all memory tier classes and the MemoryUpdater coordinator.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tier imports (graceful: warn if a tier module is broken)
# ---------------------------------------------------------------------------

try:
    from .short_term import ShortTermMemory
except Exception as _e:
    logger.warning("learning.memory: could not import ShortTermMemory: %s", _e)
    ShortTermMemory = None  # type: ignore[assignment,misc]

try:
    from .medium_term import MediumTermMemory
except Exception as _e:
    logger.warning("learning.memory: could not import MediumTermMemory: %s", _e)
    MediumTermMemory = None  # type: ignore[assignment,misc]

try:
    from .long_term import LongTermMemory
except Exception as _e:
    logger.warning("learning.memory: could not import LongTermMemory: %s", _e)
    LongTermMemory = None  # type: ignore[assignment,misc]


# ---------------------------------------------------------------------------
# MemoryUpdater
# ---------------------------------------------------------------------------

class MemoryUpdater:
    """
    Coordinates the three memory tiers (short, medium, long).

    Listens for completed trade events and updates all memory tiers
    accordingly so that agents can learn from past trades.

    Parameters
    ----------
    settings : Settings
        Application settings.
    db : SupabaseClient
        Database client used by individual memory tiers.
    """

    def __init__(self, settings: Any, db: Any) -> None:
        self._settings = settings
        self._db = db

        self._short: Optional[Any] = None
        self._medium: Optional[Any] = None
        self._long: Optional[Any] = None

        self._running = False
        self._update_queue: asyncio.Queue = asyncio.Queue(maxsize=1000)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Initialise all three memory tiers."""
        logger.info("MemoryUpdater: initialising memory tiers...")

        if ShortTermMemory is not None:
            try:
                self._short = ShortTermMemory(supabase_client=self._db)
                logger.debug("MemoryUpdater: ShortTermMemory ready")
            except Exception as exc:
                logger.warning("MemoryUpdater: ShortTermMemory init failed: %s", exc)

        if MediumTermMemory is not None:
            try:
                self._medium = MediumTermMemory(supabase_client=self._db)
                logger.debug("MemoryUpdater: MediumTermMemory ready")
            except Exception as exc:
                logger.warning("MemoryUpdater: MediumTermMemory init failed: %s", exc)

        if LongTermMemory is not None:
            try:
                self._long = LongTermMemory(supabase_client=self._db)
                logger.debug("MemoryUpdater: LongTermMemory ready")
            except Exception as exc:
                logger.warning("MemoryUpdater: LongTermMemory init failed: %s", exc)

        logger.info(
            "MemoryUpdater: initialised — short=%s medium=%s long=%s",
            self._short is not None,
            self._medium is not None,
            self._long is not None,
        )

    async def run(self) -> None:
        """Background loop that drains the trade update queue."""
        self._running = True
        logger.info("MemoryUpdater: background loop started")

        while self._running:
            try:
                trade = await asyncio.wait_for(
                    self._update_queue.get(), timeout=5.0
                )
                await self._process_trade(trade)
                self._update_queue.task_done()
            except asyncio.TimeoutError:
                continue  # No trade in queue — check _running and loop
            except asyncio.CancelledError:
                logger.info("MemoryUpdater: background loop cancelled")
                return
            except Exception as exc:
                logger.error("MemoryUpdater: error processing trade: %s", exc, exc_info=True)

    async def stop(self) -> None:
        """Signal the background loop to stop and drain remaining updates."""
        logger.info("MemoryUpdater: stopping...")
        self._running = False

        # Drain remaining items
        remaining = self._update_queue.qsize()
        if remaining > 0:
            logger.info("MemoryUpdater: draining %d pending updates", remaining)
            for _ in range(remaining):
                try:
                    trade = self._update_queue.get_nowait()
                    await self._process_trade(trade)
                    self._update_queue.task_done()
                except (asyncio.QueueEmpty, Exception):
                    break

    # ------------------------------------------------------------------
    # Public update interface
    # ------------------------------------------------------------------

    async def update_from_trade(self, trade: Any) -> None:
        """
        Queue a trade for memory updating.

        Parameters
        ----------
        trade : dict or TradeResult
            Completed trade record.  Can be a dict or a typed object.
        """
        trade_dict = trade if isinstance(trade, dict) else _obj_to_dict(trade)
        try:
            self._update_queue.put_nowait(trade_dict)
        except asyncio.QueueFull:
            logger.warning("MemoryUpdater: update queue full — dropping trade update")

    # ------------------------------------------------------------------
    # Internal processing
    # ------------------------------------------------------------------

    async def _process_trade(self, trade: Any) -> None:
        """Update all memory tiers with a completed trade."""
        if self._short is not None:
            try:
                if hasattr(self._short, "add_trade"):
                    self._short.add_trade(trade)
                elif hasattr(self._short, "store"):
                    await self._short.store("trades", trade)
            except Exception as exc:
                logger.debug("MemoryUpdater: short-term update failed: %s", exc)

        if self._medium is not None:
            try:
                if hasattr(self._medium, "add_trade"):
                    await self._medium.add_trade(trade)
                elif hasattr(self._medium, "store"):
                    await self._medium.store("trades", trade)
            except Exception as exc:
                logger.debug("MemoryUpdater: medium-term update failed: %s", exc)

        if self._long is not None:
            try:
                if hasattr(self._long, "update"):
                    await self._long.update(trade)
            except Exception as exc:
                logger.debug("MemoryUpdater: long-term update failed: %s", exc)

        logger.debug(
            "MemoryUpdater: processed trade %s",
            trade.get("symbol", "?") if isinstance(trade, dict) else getattr(trade, "symbol", "?"),
        )


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _obj_to_dict(obj: Any) -> dict:
    """Convert a typed object to a plain dict for memory storage."""
    if hasattr(obj, "__dict__"):
        return {k: v for k, v in obj.__dict__.items() if not k.startswith("_")}
    return {}


__all__ = [
    "ShortTermMemory",
    "MediumTermMemory",
    "LongTermMemory",
    "MemoryUpdater",
]
