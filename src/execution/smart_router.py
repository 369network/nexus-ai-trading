"""
NEXUS ALPHA - Smart Order Router
===================================
Routes trading signals to the appropriate executor based on market,
mode (paper vs live), and executor availability.  Handles fallback
logic when the primary executor fails.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from src.execution.base_executor import BaseExecutor
from src.execution.paper_trader import PaperTrader

logger = logging.getLogger(__name__)

# Maps market segment → preferred executor name
MARKET_EXECUTOR_MAP: Dict[str, str] = {
    "crypto":      "binance",
    "forex":       "oanda",
    "commodities": "oanda",
    "stocks_in":   "kite",
    "stocks_us":   "alpaca",
    "stocks":      "alpaca",
}

# Fallback executor map: if primary fails, try these in order
FALLBACK_EXECUTORS: Dict[str, List[str]] = {
    "binance":  ["ibkr"],   # CCXT / IBKR as Binance fallback (CCXTExecutor if added)
    "oanda":    ["ibkr"],
    "kite":     ["ibkr"],
    "alpaca":   ["ibkr"],
    "ibkr":     [],         # No fallback for IBKR
}


class SmartOrderRouter:
    """
    Intelligent order routing layer for NEXUS ALPHA.

    Responsibilities:
      1. In paper mode: always route to PaperTrader
      2. In live mode: select the executor appropriate for the signal's market
      3. On primary executor failure: attempt fallback executors in order

    Parameters
    ----------
    executors : dict
        Mapping of executor_name → BaseExecutor instance.
        Expected keys: "binance", "oanda", "kite", "alpaca", "ibkr", "paper".
    paper_mode : bool
        If True, all signals route to the PaperTrader regardless of market.
    """

    def __init__(
        self,
        executors: Dict[str, BaseExecutor],
        paper_mode: bool = True,
    ) -> None:
        self._executors = executors
        self._paper_mode = paper_mode
        self._paper_trader: Optional[PaperTrader] = executors.get("paper")  # type: ignore[assignment]

    # ------------------------------------------------------------------
    # Main routing
    # ------------------------------------------------------------------

    def route(
        self,
        signal: Any,
        executors: Optional[Dict[str, BaseExecutor]] = None,
        paper_mode: Optional[bool] = None,
    ) -> BaseExecutor:
        """
        Select and return the appropriate executor for a signal.

        Parameters
        ----------
        signal : Any
            Signal object with a ``market`` attribute (str).
        executors : dict, optional
            Override the instance-level executor map for this call.
        paper_mode : bool, optional
            Override the instance-level paper_mode flag.

        Returns
        -------
        BaseExecutor
            The executor that should handle this signal.

        Raises
        ------
        RuntimeError
            If no suitable executor is available.
        """
        exec_map = executors or self._executors
        is_paper = paper_mode if paper_mode is not None else self._paper_mode

        # ---- Paper mode: always return PaperTrader ----
        if is_paper:
            paper = exec_map.get("paper") or self._paper_trader
            if paper is None:
                raise RuntimeError("SmartOrderRouter: paper_mode=True but no PaperTrader configured")
            logger.debug("SmartOrderRouter.route: paper_mode → PaperTrader")
            return paper

        # ---- Live mode: route by market ----
        market: str = getattr(signal, "market", "crypto")
        preferred_name = MARKET_EXECUTOR_MAP.get(market, "ibkr")
        preferred = exec_map.get(preferred_name)

        if preferred is not None:
            logger.debug(
                "SmartOrderRouter.route: market=%s → %s executor",
                market, preferred_name,
            )
            return preferred

        # ---- Fallback: try any available executor for the market ----
        for fallback_name in FALLBACK_EXECUTORS.get(preferred_name, []):
            fallback = exec_map.get(fallback_name)
            if fallback is not None:
                logger.warning(
                    "SmartOrderRouter.route: %s not available, using fallback %s",
                    preferred_name, fallback_name,
                )
                return fallback

        raise RuntimeError(
            f"SmartOrderRouter: no executor available for market={market} "
            f"(preferred={preferred_name})"
        )

    # ------------------------------------------------------------------
    # Failure handling
    # ------------------------------------------------------------------

    async def handle_execution_failure(
        self,
        signal: Any,
        failed_executor: BaseExecutor,
        executors: Optional[Dict[str, BaseExecutor]] = None,
    ) -> Optional[BaseExecutor]:
        """
        Attempt to find a fallback executor after a primary execution failure.

        Parameters
        ----------
        signal : Any
            The signal that failed to execute.
        failed_executor : BaseExecutor
            The executor that failed.
        executors : dict, optional
            Executor map override.

        Returns
        -------
        Optional[BaseExecutor]
            A fallback executor, or None if no fallback is available.
        """
        exec_map = executors or self._executors
        failed_name = failed_executor.exchange_name.split("_")[0]  # strip _paper/_futures suffix
        fallbacks = FALLBACK_EXECUTORS.get(failed_name, [])

        logger.warning(
            "SmartOrderRouter.handle_execution_failure: %s failed – "
            "trying fallbacks %s",
            failed_executor.exchange_name, fallbacks,
        )

        for fallback_name in fallbacks:
            fallback = exec_map.get(fallback_name)
            if fallback is None:
                continue
            # Health check
            try:
                healthy = await fallback.health_check()
                if healthy:
                    logger.info(
                        "SmartOrderRouter: fallback executor %s is healthy – using it",
                        fallback_name,
                    )
                    return fallback
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "SmartOrderRouter: fallback %s health check failed: %s",
                    fallback_name, exc,
                )

        # Last resort: paper trader (to log the missed trade)
        paper = exec_map.get("paper")
        if paper is not None:
            logger.error(
                "SmartOrderRouter: all live fallbacks failed – routing to PaperTrader "
                "for %s (order will NOT execute in live market)",
                getattr(signal, "symbol", "unknown"),
            )
            return paper

        return None

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    async def get_executor_health(self) -> Dict[str, bool]:
        """
        Run health checks on all registered executors.

        Returns
        -------
        dict
            Mapping of executor_name → healthy (bool).
        """
        health: Dict[str, bool] = {}
        for name, executor in self._executors.items():
            try:
                health[name] = await executor.health_check()
            except Exception as exc:  # noqa: BLE001
                logger.error("SmartOrderRouter: health_check for %s failed: %s", name, exc)
                health[name] = False
        return health

    @property
    def paper_mode(self) -> bool:
        return self._paper_mode

    @paper_mode.setter
    def paper_mode(self, value: bool) -> None:
        logger.info(
            "SmartOrderRouter: paper_mode changed %s → %s",
            self._paper_mode, value,
        )
        self._paper_mode = value
