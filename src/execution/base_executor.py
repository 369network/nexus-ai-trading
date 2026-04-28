"""
NEXUS ALPHA - Base Executor
==============================
Abstract base class that all exchange-specific executors must implement.
Defines the Order and Position dataclasses used across the system.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------

class OrderStatus(str, Enum):
    """Lifecycle status of an order."""
    PENDING      = "PENDING"
    OPEN         = "OPEN"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED       = "FILLED"
    CANCELLED    = "CANCELLED"
    REJECTED     = "REJECTED"
    EXPIRED      = "EXPIRED"


class OrderType(str, Enum):
    """Order execution type."""
    MARKET       = "MARKET"
    LIMIT        = "LIMIT"
    STOP_MARKET  = "STOP_MARKET"
    STOP_LIMIT   = "STOP_LIMIT"
    TRAILING_STOP = "TRAILING_STOP"
    OCO          = "OCO"


class Direction(str, Enum):
    """Trade direction."""
    LONG  = "long"
    SHORT = "short"
    BUY   = "buy"    # alias for LONG in some exchanges
    SELL  = "sell"   # alias for SHORT / closing long


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Order:
    """
    Unified order representation across all exchanges.

    Fields
    ------
    order_id : str
        Exchange-native order ID.
    symbol : str
        Normalised trading symbol (e.g. "BTCUSDT", "EUR_USD").
    direction : str
        "long" / "short" / "buy" / "sell".
    size : float
        Order quantity in base units.
    price : float
        Limit price (0.0 for market orders).
    status : OrderStatus
        Current lifecycle state.
    timestamp : float
        Unix timestamp (seconds) when the order was created locally.
    market : str
        Market segment: "crypto" | "forex" | "stocks_in" | "stocks_us" | "commodities".
    exchange : str
        Exchange / broker identifier: "binance" | "oanda" | "kite" | "alpaca" | "ibkr".
    filled_qty : float
        Quantity that has been filled so far.
    avg_fill_price : float
        Volume-weighted average fill price.
    stop_loss : Optional[float]
        Attached stop-loss price (if supported by the exchange).
    take_profit : Optional[float]
        Attached take-profit price (if supported by the exchange).
    client_order_id : str
        Client-assigned order identifier for deduplication.
    """
    order_id: str
    symbol: str
    direction: str
    size: float
    price: float
    status: OrderStatus
    timestamp: float
    market: str
    exchange: str
    filled_qty: float = 0.0
    avg_fill_price: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    client_order_id: str = ""

    @property
    def is_filled(self) -> bool:
        return self.status == OrderStatus.FILLED

    @property
    def is_active(self) -> bool:
        return self.status in (OrderStatus.OPEN, OrderStatus.PARTIALLY_FILLED)

    @property
    def remaining_qty(self) -> float:
        return max(0.0, self.size - self.filled_qty)


@dataclass
class Position:
    """
    Unified open position representation.

    Fields
    ------
    symbol : str
        Trading symbol.
    direction : str
        "long" or "short".
    size : float
        Open position size in base units.
    entry_price : float
        Volume-weighted average entry price.
    current_price : float
        Latest mark-to-market price.
    unrealized_pnl : float
        Unrealised profit/loss in quote currency (USD).
    stop_loss : Optional[float]
        Active stop-loss price.
    take_profit_levels : List[float]
        List of take-profit price levels (TP1, TP2, TP3).
    market : str
        Market segment.
    exchange : str
        Exchange/broker identifier.
    open_time : float
        Unix timestamp when the position was opened.
    """
    symbol: str
    direction: str
    size: float
    entry_price: float
    current_price: float
    unrealized_pnl: float
    stop_loss: Optional[float]
    take_profit_levels: List[float] = field(default_factory=list)
    market: str = ""
    exchange: str = ""
    open_time: float = 0.0

    @property
    def notional_usd(self) -> float:
        return self.size * self.current_price

    @property
    def pnl_pct(self) -> float:
        if self.entry_price <= 0:
            return 0.0
        if self.direction.lower() in ("long", "buy"):
            return (self.current_price - self.entry_price) / self.entry_price
        else:
            return (self.entry_price - self.current_price) / self.entry_price


# ---------------------------------------------------------------------------
# Abstract BaseExecutor
# ---------------------------------------------------------------------------

class BaseExecutor(ABC):
    """
    Abstract base class for all NEXUS ALPHA exchange executors.

    Concrete implementations: BinanceExecutor, OANDAExecutor, KiteExecutor,
    AlpacaExecutor, IBKRExecutor, PaperTrader.

    All methods are async to support non-blocking I/O in the trading loop.
    """

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """Return a short identifier for this executor (e.g. "binance")."""

    @abstractmethod
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
        """
        Place a new order.

        Parameters
        ----------
        symbol : str
            Trading symbol.
        direction : str
            "buy" / "sell" or "long" / "short".
        size : float
            Quantity in base units.
        order_type : OrderType
            MARKET, LIMIT, STOP_MARKET, etc.
        price : float
            Limit price (ignored for MARKET orders).
        stop_loss : Optional[float]
            Attached stop-loss price.
        take_profit : Optional[float]
            Attached take-profit price.

        Returns
        -------
        Order
            Created order with exchange-assigned order_id.
        """

    @abstractmethod
    async def cancel_order(self, order_id: str) -> bool:
        """
        Cancel an open order.

        Parameters
        ----------
        order_id : str
            Exchange order ID.

        Returns
        -------
        bool
            True if successfully cancelled or already cancelled.
        """

    @abstractmethod
    async def close_position(
        self,
        symbol: str,
        size: float,
        reason: str = "manual",
    ) -> Order:
        """
        Close (partially or fully) an open position at market price.

        Parameters
        ----------
        symbol : str
            Symbol of the position to close.
        size : float
            Quantity to close.  Pass the full position size to close entirely.
        reason : str
            Descriptive reason for logging (e.g. "stop_loss", "take_profit").

        Returns
        -------
        Order
            The closing market order.
        """

    @abstractmethod
    async def cancel_all_orders(self) -> List[Order]:
        """
        Cancel all open orders across all symbols.

        Returns
        -------
        List[Order]
            List of cancelled orders.
        """

    @abstractmethod
    async def close_all_positions(self) -> List[Order]:
        """
        Close all open positions at market price.

        Returns
        -------
        List[Order]
            List of market orders executed to close positions.
        """

    @abstractmethod
    async def get_positions(self) -> List[Position]:
        """
        Return all currently open positions.

        Returns
        -------
        List[Position]
            Open positions.  Empty list if none.
        """

    @abstractmethod
    async def get_open_orders(self) -> List[Order]:
        """
        Return all currently open (unfilled) orders.

        Returns
        -------
        List[Order]
            Open orders.  Empty list if none.
        """

    @abstractmethod
    async def get_account_balance(self) -> float:
        """
        Return the available (free) account balance in USD.

        Returns
        -------
        float
            Available balance in USD.
        """

    # ------------------------------------------------------------------
    # Default implementations (may be overridden)
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """
        Quick connectivity / auth test.

        Returns True if the executor can communicate with the exchange.
        Default implementation tries to fetch the account balance.
        """
        try:
            balance = await self.get_account_balance()
            return balance >= 0
        except Exception as exc:  # noqa: BLE001
            logger.error("%s health_check failed: %s", self.exchange_name, exc)
            return False

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(exchange={self.exchange_name})"
