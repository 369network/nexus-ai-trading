"""
NEXUS ALPHA - IBKR Executor
==============================
Interactive Brokers execution via ib_insync library.

Features:
  - Connects to TWS or IB Gateway (paper port 7497, live port 7496)
  - Adaptive Algo orders for best execution
  - Handles Stock, Forex (FX), and Commodity contracts
  - Fully async via ib_insync's IB object

Note: ib_insync must be added to pyproject.toml:
  ib_insync = "^0.9"
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

from src.execution.base_executor import BaseExecutor, Order, OrderStatus, OrderType, Position

logger = logging.getLogger(__name__)

# IB Gateway / TWS ports
IBKR_LIVE_PORT  = 7496
IBKR_PAPER_PORT = 7497
IBKR_HOST       = "127.0.0.1"


class IBKRExecutor(BaseExecutor):
    """
    Interactive Brokers executor via ib_insync.

    Parameters
    ----------
    host : str
        TWS / IB Gateway hostname (default: localhost).
    port : int
        TWS / Gateway port.  7497 = paper, 7496 = live.
    client_id : int
        Unique client ID (different from other connections to TWS).
    paper : bool
        Informational flag (actual paper/live is determined by port).
    """

    def __init__(
        self,
        host: str = IBKR_HOST,
        port: int = IBKR_PAPER_PORT,
        client_id: int = 1,
        paper: bool = True,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._paper = paper
        self._ib: Any = None

    @property
    def exchange_name(self) -> str:
        return "ibkr_paper" if self._paper else "ibkr"

    # ------------------------------------------------------------------
    # Connection lifecycle
    # ------------------------------------------------------------------

    async def _ensure_connected(self) -> Any:
        """Ensure ib_insync IB object is connected to TWS/Gateway."""
        if self._ib is not None and self._ib.isConnected():
            return self._ib

        try:
            from ib_insync import IB  # type: ignore[import]
        except ImportError:
            raise RuntimeError(
                "ib_insync is required: add 'ib_insync = \"^0.9\"' to pyproject.toml"
            )

        ib = IB()
        await ib.connectAsync(
            host=self._host,
            port=self._port,
            clientId=self._client_id,
        )
        self._ib = ib
        logger.info(
            "IBKRExecutor: connected to %s:%d (clientId=%d)",
            self._host, self._port, self._client_id,
        )
        return self._ib

    # ------------------------------------------------------------------
    # Contract resolution
    # ------------------------------------------------------------------

    async def _resolve_contract(self, symbol: str, market: str) -> Any:
        """
        Resolve a symbol to an IB Contract object.

        Supports:
          - Stocks: "AAPL", "MSFT"
          - Forex: "EUR.USD", "GBP.USD" (dot-separated)
          - Commodities: "XAUUSD" (Gold), "XAGUSD" (Silver), "CL" (Crude Oil)
        """
        from ib_insync import Stock, Forex, Commodity, Contract  # type: ignore[import]

        ib = await self._ensure_connected()

        if market in ("forex",) or "." in symbol:
            pair = symbol.replace(".", "").replace("_", "").replace("/", "")
            base = pair[:3]
            quote = pair[3:]
            contract = Forex(f"{base}{quote}")
        elif market in ("commodities",):
            # Gold: XAUUSD → continuous futures or spot CFD
            if symbol.upper() in ("XAUUSD", "GOLD"):
                contract = Commodity("XAUUSD", "CMDTY", "IBCFD")
            elif symbol.upper() in ("XAGUSD", "SILVER"):
                contract = Commodity("XAGUSD", "CMDTY", "IBCFD")
            else:
                contract = Stock(symbol, "SMART", "USD")
        else:
            contract = Stock(symbol, "SMART", "USD")

        contracts = await ib.qualifyContractsAsync(contract)
        if not contracts:
            raise ValueError(f"IBKRExecutor: could not qualify contract for {symbol}")
        return contracts[0]

    # ------------------------------------------------------------------
    # BaseExecutor implementation
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
        """
        Place an Adaptive Algo order for best execution.

        Adaptive orders on IB behave like smart-routed limit orders that
        adapt to market conditions (patient/normal/urgent priority).
        """
        from ib_insync import MarketOrder, LimitOrder, Order as IBOrder  # type: ignore[import]

        ib = await self._ensure_connected()
        action = "BUY" if direction.lower() in ("long", "buy") else "SELL"

        # Determine market from symbol pattern
        market = "stocks_us"
        if "." in symbol or "_" in symbol:
            market = "forex"
        elif symbol.upper() in ("XAUUSD", "XAGUSD", "CL", "NG"):
            market = "commodities"

        contract = await self._resolve_contract(symbol, market)

        # Build adaptive order
        if order_type == OrderType.MARKET:
            ib_order = MarketOrder(action, size)
            ib_order.algoStrategy = "Adaptive"
            ib_order.algoParams = [{"tag": "adaptivePriority", "val": "Normal"}]
        else:
            ib_order = LimitOrder(action, size, price)
            ib_order.tif = "GTC"
            ib_order.algoStrategy = "Adaptive"
            ib_order.algoParams = [{"tag": "adaptivePriority", "val": "Normal"}]

        # Attach stop loss as a separate order bracket if needed
        trade = ib.placeOrder(contract, ib_order)
        await asyncio.sleep(0.5)  # Allow IB to assign orderId

        order_id = str(trade.order.orderId)
        logger.info(
            "IBKRExecutor.place_order: %s %s %s qty=%.2f price=%.4f → id=%s",
            symbol, action, order_type.value, size, price, order_id,
        )

        return Order(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            size=size,
            price=price,
            status=OrderStatus.OPEN,
            timestamp=time.time(),
            market=market,
            exchange=self.exchange_name,
            stop_loss=stop_loss,
            take_profit=take_profit,
        )

    async def cancel_order(self, order_id: str) -> bool:
        from ib_insync import Order as IBOrder  # type: ignore[import]

        ib = await self._ensure_connected()
        trades = ib.trades()
        trade = next((t for t in trades if str(t.order.orderId) == order_id), None)
        if trade is None:
            logger.warning("IBKRExecutor.cancel_order: order %s not found", order_id)
            return True

        try:
            ib.cancelOrder(trade.order)
            await asyncio.sleep(0.2)
            logger.info("IBKRExecutor.cancel_order: %s cancelled", order_id)
            return True
        except Exception as exc:
            logger.error("IBKRExecutor.cancel_order(%s) failed: %s", order_id, exc)
            return False

    async def close_position(
        self,
        symbol: str,
        size: float,
        reason: str = "manual",
    ) -> Order:
        positions = await self.get_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)
        if pos is None:
            raise ValueError(f"IBKRExecutor: no open position for {symbol}")

        close_dir = "sell" if pos.direction.lower() in ("long", "buy") else "buy"
        return await self.place_order(
            symbol=symbol,
            direction=close_dir,
            size=size,
            order_type=OrderType.MARKET,
        )

    async def cancel_all_orders(self) -> List[Order]:
        ib = await self._ensure_connected()
        cancelled: List[Order] = []
        for trade in ib.openTrades():
            try:
                ib.cancelOrder(trade.order)
                cancelled.append(Order(
                    order_id=str(trade.order.orderId),
                    symbol=trade.contract.symbol,
                    direction="buy",
                    size=float(trade.order.totalQuantity),
                    price=float(trade.order.lmtPrice or 0),
                    status=OrderStatus.CANCELLED,
                    timestamp=time.time(),
                    market="stocks_us",
                    exchange=self.exchange_name,
                ))
            except Exception as exc:  # noqa: BLE001
                logger.error("IBKRExecutor.cancel_all_orders: trade cancel failed: %s", exc)

        await asyncio.sleep(0.5)
        return cancelled

    async def close_all_positions(self) -> List[Order]:
        positions = await self.get_positions()
        orders: List[Order] = []
        for pos in positions:
            try:
                o = await self.close_position(pos.symbol, pos.size, reason="emergency_close")
                orders.append(o)
            except Exception as exc:  # noqa: BLE001
                logger.error("IBKRExecutor.close_all_positions: %s failed: %s", pos.symbol, exc)
        return orders

    async def get_positions(self) -> List[Position]:
        ib = await self._ensure_connected()
        positions: List[Position] = []

        for pos in ib.positions():
            qty = pos.position
            if abs(qty) < 1e-6:
                continue

            direction = "long" if qty > 0 else "short"
            avg_cost = pos.avgCost

            # avg_cost in IB is per-unit, already in quote currency
            positions.append(Position(
                symbol=pos.contract.symbol,
                direction=direction,
                size=abs(qty),
                entry_price=avg_cost,
                current_price=avg_cost,  # updated by caller with live price
                unrealized_pnl=0.0,      # requires portfolio subscription
                stop_loss=None,
                market="stocks_us",
                exchange=self.exchange_name,
            ))

        return positions

    async def get_open_orders(self) -> List[Order]:
        ib = await self._ensure_connected()
        orders: List[Order] = []
        for trade in ib.openTrades():
            o = trade.order
            c = trade.contract
            direction = "buy" if o.action == "BUY" else "sell"
            orders.append(Order(
                order_id=str(o.orderId),
                symbol=c.symbol,
                direction=direction,
                size=float(o.totalQuantity),
                price=float(o.lmtPrice or 0),
                status=OrderStatus.OPEN,
                timestamp=time.time(),
                market="stocks_us",
                exchange=self.exchange_name,
            ))
        return orders

    async def get_account_balance(self) -> float:
        ib = await self._ensure_connected()
        account_values = ib.accountValues()
        for av in account_values:
            if av.tag == "CashBalance" and av.currency == "USD":
                return float(av.value)
        # Fallback: use AvailableFunds
        for av in account_values:
            if av.tag == "AvailableFunds" and av.currency == "USD":
                return float(av.value)
        return 0.0

    async def close(self) -> None:
        if self._ib and self._ib.isConnected():
            self._ib.disconnect()
            logger.info("IBKRExecutor: disconnected")
