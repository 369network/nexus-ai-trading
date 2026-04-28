"""
NEXUS ALPHA - Execution Engine
================================
Routes orders to the correct executor (paper or live) and provides
a unified interface for order placement and health checking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class ExecutionHealth:
    """Health snapshot for the execution engine."""
    ok: bool
    detail: str
    executor_type: str
    timestamp: datetime = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


@dataclass
class TradeResult:
    """Result of a completed trade execution."""
    trade_id: str
    symbol: str
    direction: str
    quantity: float
    entry_price: float
    status: str
    stop_loss: float = 0.0
    take_profit: float = 0.0
    fee_usd: float = 0.0
    slippage_pct: float = 0.0
    timestamp: datetime = None
    executor: str = "paper"
    raw_order: Optional[Any] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now(timezone.utc)


class ExecutionEngine:
    """
    Routes orders to the correct executor (paper or live).

    Provides a uniform interface regardless of whether the system is in
    paper mode or live mode.  Handles pre-execution validation and
    post-execution reconciliation.

    Parameters
    ----------
    executor : BaseExecutor
        The concrete executor (PaperTrader or LiveExecutor).
    settings : Settings
        Application settings.
    db : SupabaseClient
        Database client for persisting trade results.
    """

    def __init__(self, executor: Any, settings: Any, db: Any) -> None:
        self._executor = executor
        self._settings = settings
        self._db = db
        self._trade_count = 0
        self._executor_type = type(executor).__name__

    # ------------------------------------------------------------------
    # Order execution
    # ------------------------------------------------------------------

    async def execute(self, signal: Any, risk_result: Any) -> TradeResult:
        """
        Execute a trade based on *signal* and *risk_result*.

        Parameters
        ----------
        signal :
            FusedSignal or TradeSignal with attributes: symbol, market,
            direction, size_pct, stop_loss, take_profit_1/take_profit.
        risk_result :
            RiskResult (or dict) with position_size, stop_loss, take_profit.

        Returns
        -------
        TradeResult
        """
        symbol = getattr(signal, "symbol", "")
        market = getattr(signal, "market", "crypto")
        direction_raw = getattr(signal, "direction", "NEUTRAL")
        direction = str(direction_raw.value if hasattr(direction_raw, "value") else direction_raw)

        # Get risk-adjusted position size
        position_size_fraction = (
            risk_result.position_size
            if hasattr(risk_result, "position_size")
            else risk_result.get("position_size", 0.03)
        )

        stop_loss = (
            risk_result.stop_loss
            if hasattr(risk_result, "stop_loss")
            else risk_result.get("stop_loss", 0.0)
        ) or float(getattr(signal, "stop_loss", 0.0) or 0.0)

        take_profit = (
            risk_result.take_profit
            if hasattr(risk_result, "take_profit")
            else risk_result.get("take_profit", 0.0)
        ) or float(
            getattr(signal, "take_profit_1", None)
            or getattr(signal, "take_profit", 0.0)
            or 0.0
        )

        # Determine entry price from signal
        entry_price = float(
            getattr(signal, "entry", None)
            or getattr(signal, "entry_price", None)
            or 0.0
        )
        if entry_price <= 0:
            # Try to get last price from executor portfolio
            try:
                portfolio = self._executor.get_portfolio_state()
                positions = getattr(portfolio, "positions", {})
                pos = positions.get(symbol)
                entry_price = pos.current_price if pos else 0.0
            except Exception:
                pass

        if entry_price <= 0:
            logger.error(
                "ExecutionEngine: cannot execute %s — no entry price available", symbol
            )
            raise ValueError(f"No entry price for {symbol}")

        # Convert fraction to absolute quantity
        try:
            capital = await self._get_capital()
        except Exception:
            capital = 100_000.0  # fallback for paper mode

        notional = capital * position_size_fraction
        quantity = notional / entry_price if entry_price > 0 else 0.0

        if quantity <= 0:
            raise ValueError(
                f"ExecutionEngine: computed zero quantity for {symbol} "
                f"(capital={capital:.2f}, fraction={position_size_fraction:.4f}, "
                f"price={entry_price:.4f})"
            )

        logger.info(
            "ExecutionEngine: placing %s %s qty=%.4f @ %.4f sl=%.4f tp=%.4f",
            direction, symbol, quantity, entry_price, stop_loss, take_profit,
        )

        # Delegate to executor
        try:
            from src.execution.base_executor import OrderType
            order = await self._executor.place_order(
                symbol=symbol,
                direction=direction.lower(),
                size=quantity,
                order_type=OrderType.MARKET,
                price=entry_price,
                stop_loss=stop_loss if stop_loss > 0 else None,
                take_profit=take_profit if take_profit > 0 else None,
            )
        except Exception as exc:
            logger.error(
                "ExecutionEngine: executor failed for %s: %s", symbol, exc, exc_info=True
            )
            raise

        self._trade_count += 1

        fill_price = float(getattr(order, "avg_fill_price", entry_price) or entry_price)
        filled_qty = float(getattr(order, "filled_qty", quantity) or quantity)
        fee_usd = float(getattr(order, "fee_usd", 0.0) or 0.0)
        status = str(getattr(order, "status", "FILLED"))
        if hasattr(status, "value"):
            status = status.value
        order_id = str(getattr(order, "order_id", f"order_{self._trade_count}"))

        result = TradeResult(
            trade_id=order_id,
            symbol=symbol,
            direction=direction,
            quantity=filled_qty,
            entry_price=fill_price,
            status=status,
            stop_loss=stop_loss,
            take_profit=take_profit,
            fee_usd=fee_usd,
            executor=self._executor_type,
            raw_order=order,
        )

        logger.info(
            "ExecutionEngine: trade complete #%d — %s %s qty=%.4f fill=%.4f status=%s",
            self._trade_count, symbol, direction, filled_qty, fill_price, status,
        )

        return result

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> ExecutionHealth:
        """Check whether the executor is operational."""
        try:
            # For paper trader: check if capital is accessible
            if hasattr(self._executor, "get_account_balance"):
                balance = await self._executor.get_account_balance()
                return ExecutionHealth(
                    ok=True,
                    detail=f"Balance: ${balance:,.2f}",
                    executor_type=self._executor_type,
                )
            return ExecutionHealth(
                ok=True,
                detail="executor responsive",
                executor_type=self._executor_type,
            )
        except Exception as exc:
            return ExecutionHealth(
                ok=False,
                detail=str(exc),
                executor_type=self._executor_type,
            )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Gracefully shut down the execution engine."""
        logger.info(
            "ExecutionEngine: shutting down (%s) — %d trades executed",
            self._executor_type, self._trade_count,
        )
        try:
            if hasattr(self._executor, "cancel_all_orders"):
                await self._executor.cancel_all_orders()
        except Exception as exc:
            logger.warning("ExecutionEngine: cancel_all_orders failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _get_capital(self) -> float:
        """Retrieve current capital from the executor."""
        if hasattr(self._executor, "get_account_balance"):
            return await self._executor.get_account_balance()
        if hasattr(self._executor, "get_capital"):
            return float(self._executor.get_capital())
        return 100_000.0
