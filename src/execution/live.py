"""
NEXUS ALPHA - Live Executor
==============================
Live execution stub.  Raises RuntimeError if used in paper mode.
In a production deployment this would delegate to exchange-specific
executors (Bybit, Binance, etc.) via SmartRouter.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.execution.base_executor import BaseExecutor, Order, OrderType, Position

logger = logging.getLogger(__name__)


class LiveExecutor(BaseExecutor):
    """
    Live order execution engine.

    Currently raises RuntimeError on all execution methods because the
    system is operating in paper mode.  This class exists so that the
    execution engine interface is complete and can be swapped out when
    live trading is enabled.

    Parameters
    ----------
    settings : Settings
        Application settings.
    db : SupabaseClient
        Database client.
    """

    def __init__(self, settings: Any, db: Any) -> None:
        self._settings = settings
        self._db = db

    @property
    def exchange_name(self) -> str:
        return "live"

    async def init(self) -> None:
        """
        Initialise live executor.

        Raises RuntimeError if paper_mode is True (the expected case).
        """
        paper_mode = getattr(self._settings, "paper_mode", True)
        if paper_mode:
            logger.warning(
                "LiveExecutor.init() called in paper mode — "
                "live execution is disabled"
            )
            return

        logger.info("LiveExecutor: initialising live connections...")
        # Future: initialise SmartRouter, exchange connections, etc.
        raise NotImplementedError(
            "LiveExecutor: live trading not yet implemented in this build"
        )

    # ------------------------------------------------------------------
    # BaseExecutor implementation — all raise in paper mode
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        direction: str,
        size: float,
        order_type: OrderType,
        price: float = 0.0,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Order:
        raise RuntimeError(
            "LiveExecutor.place_order: live execution is not allowed in paper mode. "
            "Set PAPER_MODE=false and configure exchange credentials to enable live trading."
        )

    async def cancel_order(self, order_id: str) -> bool:
        raise RuntimeError("LiveExecutor: live execution not allowed in paper mode")

    async def close_position(
        self,
        symbol: str,
        size: float,
        reason: str = "manual",
    ) -> Order:
        raise RuntimeError("LiveExecutor: live execution not allowed in paper mode")

    async def cancel_all_orders(self) -> List[Order]:
        raise RuntimeError("LiveExecutor: live execution not allowed in paper mode")

    async def close_all_positions(self) -> List[Order]:
        raise RuntimeError("LiveExecutor: live execution not allowed in paper mode")

    async def get_positions(self) -> List[Position]:
        raise RuntimeError("LiveExecutor: live execution not allowed in paper mode")

    async def get_open_orders(self) -> List[Order]:
        raise RuntimeError("LiveExecutor: live execution not allowed in paper mode")

    async def get_account_balance(self) -> float:
        raise RuntimeError("LiveExecutor: live execution not allowed in paper mode")
