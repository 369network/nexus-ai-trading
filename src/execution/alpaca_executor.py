"""
NEXUS ALPHA - Alpaca Executor
===============================
US stocks and ETFs execution via Alpaca Trading API.

Features:
  - Fractional share support (expensive stocks like AMZN, NVDA, TSLA)
  - Extended hours trading (pre-market 4 AM / after-hours 8 PM EST)
  - PDT (Pattern Day Trader) rule awareness
  - Trailing stop orders via Alpaca's trailing_percent parameter
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

from src.execution.base_executor import BaseExecutor, Order, OrderStatus, OrderType, Position

logger = logging.getLogger(__name__)

# PDT rule: 3 day trades in a rolling 5-day window if account < $25,000
PDT_DAY_TRADE_LIMIT = 3
PDT_ACCOUNT_MINIMUM = 25_000.0

# Fractional share threshold: only enable for stocks > this price
FRACTIONAL_PRICE_THRESHOLD = 100.0


class AlpacaExecutor(BaseExecutor):
    """
    Alpaca Markets executor for US equities.

    Parameters
    ----------
    api_key : str
        Alpaca API key ID.
    api_secret : str
        Alpaca API secret key.
    paper : bool
        If True, use paper trading endpoint.
    extended_hours : bool
        If True, allow pre-market (4:00 AM) and after-hours (8:00 PM) trading.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        paper: bool = True,
        extended_hours: bool = False,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._paper = paper
        self._extended_hours = extended_hours
        self._trading_client: Any = None
        self._data_client: Any = None
        # PDT tracking: list of dates when day trades occurred
        self._day_trade_dates: List[str] = []

    @property
    def exchange_name(self) -> str:
        return "alpaca_paper" if self._paper else "alpaca"

    # ------------------------------------------------------------------
    # Client initialization
    # ------------------------------------------------------------------

    def _get_trading_client(self) -> Any:
        if self._trading_client is not None:
            return self._trading_client
        try:
            from alpaca.trading.client import TradingClient  # type: ignore[import]
            self._trading_client = TradingClient(
                api_key=self._api_key,
                secret_key=self._api_secret,
                paper=self._paper,
            )
            logger.info("AlpacaExecutor: trading client initialized (paper=%s)", self._paper)
        except ImportError:
            raise RuntimeError("alpaca-py is required: pip install alpaca-py")
        return self._trading_client

    # ------------------------------------------------------------------
    # PDT rule management
    # ------------------------------------------------------------------

    def _is_pdt_risk(self) -> bool:
        """Check if placing another day trade would violate PDT rules."""
        today = time.strftime("%Y-%m-%d")
        # Count day trades in rolling 5-day window
        recent = [d for d in self._day_trade_dates
                  if (time.time() - time.mktime(time.strptime(d, "%Y-%m-%d"))) < 5 * 86400]
        if len(recent) >= PDT_DAY_TRADE_LIMIT:
            return True
        return False

    def _record_day_trade(self) -> None:
        today = time.strftime("%Y-%m-%d")
        self._day_trade_dates.append(today)
        # Prune old entries (keep 10 days)
        cutoff = time.time() - 10 * 86400
        self._day_trade_dates = [
            d for d in self._day_trade_dates
            if time.mktime(time.strptime(d, "%Y-%m-%d")) > cutoff
        ]

    def check_pdt_warning(self, account_value: float) -> Optional[str]:
        """
        Return a warning string if the account approaches PDT limits.

        Parameters
        ----------
        account_value : float
            Current account equity in USD.

        Returns
        -------
        Optional[str]
            Warning message or None.
        """
        if account_value >= PDT_ACCOUNT_MINIMUM:
            return None
        recent_day_trades = len([
            d for d in self._day_trade_dates
            if (time.time() - time.mktime(time.strptime(d, "%Y-%m-%d"))) < 5 * 86400
        ])
        if recent_day_trades >= PDT_DAY_TRADE_LIMIT - 1:
            return (
                f"PDT WARNING: {recent_day_trades}/{PDT_DAY_TRADE_LIMIT} day trades used "
                f"(account ${account_value:,.0f} < ${PDT_ACCOUNT_MINIMUM:,.0f} threshold)"
            )
        return None

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
        Place an order on Alpaca with fractional share and extended-hours support.
        """
        import asyncio
        from alpaca.trading.requests import (  # type: ignore[import]
            MarketOrderRequest,
            LimitOrderRequest,
            StopLimitOrderRequest,
        )
        from alpaca.trading.enums import (  # type: ignore[import]
            OrderSide,
            TimeInForce,
        )

        client = self._get_trading_client()
        side = OrderSide.BUY if direction.lower() in ("long", "buy") else OrderSide.SELL
        tif = TimeInForce.DAY

        # Fractional shares: use qty as notional or qty depending on stock price
        use_fractional = (size < 1.0) or (price > FRACTIONAL_PRICE_THRESHOLD and size != int(size))

        client_id = f"nexus_{uuid.uuid4().hex[:12]}"

        if order_type == OrderType.MARKET:
            if use_fractional and size < 1.0:
                # Use notional value for fractional market orders
                req = MarketOrderRequest(
                    symbol=symbol,
                    notional=round(size * price, 2) if price > 0 else size,
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_id,
                )
            else:
                req = MarketOrderRequest(
                    symbol=symbol,
                    qty=size if use_fractional else int(size),
                    side=side,
                    time_in_force=TimeInForce.DAY,
                    client_order_id=client_id,
                )
            if self._extended_hours:
                # Market orders don't support extended hours; will be queued
                logger.debug("AlpacaExecutor: extended hours market order for %s", symbol)

        elif order_type == OrderType.LIMIT:
            req = LimitOrderRequest(
                symbol=symbol,
                qty=size if use_fractional else int(size),
                side=side,
                time_in_force=tif,
                limit_price=round(price, 2),
                extended_hours=self._extended_hours,
                client_order_id=client_id,
            )
        else:
            raise ValueError(f"AlpacaExecutor: unsupported order_type {order_type}")

        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, lambda: client.submit_order(req))

        logger.info(
            "AlpacaExecutor.place_order: %s %s qty=%.4f price=%.2f → id=%s",
            symbol, direction, size, price, raw.id,
        )

        return Order(
            order_id=str(raw.id),
            symbol=symbol,
            direction=direction,
            size=size,
            price=price,
            status=OrderStatus.OPEN,
            timestamp=time.time(),
            market="stocks_us",
            exchange=self.exchange_name,
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_id,
        )

    async def place_trailing_stop_order(
        self,
        symbol: str,
        size: float,
        direction: str,
        trailing_percent: float,
    ) -> Order:
        """
        Place an Alpaca trailing stop order.

        Parameters
        ----------
        symbol : str
            US stock ticker (e.g. "AAPL").
        size : float
            Quantity in shares.
        direction : str
            "sell" to protect a long, "buy" to protect a short.
        trailing_percent : float
            Trailing distance as a percentage (e.g. 1.5 = 1.5%).

        Returns
        -------
        Order
            Submitted trailing stop order.
        """
        import asyncio
        from alpaca.trading.requests import TrailingStopOrderRequest  # type: ignore[import]
        from alpaca.trading.enums import OrderSide, TimeInForce  # type: ignore[import]

        client = self._get_trading_client()
        side = OrderSide.BUY if direction.lower() in ("long", "buy") else OrderSide.SELL
        client_id = f"nexus_trail_{uuid.uuid4().hex[:10]}"

        req = TrailingStopOrderRequest(
            symbol=symbol,
            qty=int(size),
            side=side,
            time_in_force=TimeInForce.GTC,
            trail_percent=trailing_percent,
            client_order_id=client_id,
        )

        loop = asyncio.get_event_loop()
        raw = await loop.run_in_executor(None, lambda: client.submit_order(req))
        logger.info(
            "AlpacaExecutor.place_trailing_stop: %s side=%s qty=%d trail=%.2f%% → id=%s",
            symbol, direction, int(size), trailing_percent, raw.id,
        )
        return Order(
            order_id=str(raw.id),
            symbol=symbol,
            direction=direction,
            size=float(size),
            price=0.0,
            status=OrderStatus.OPEN,
            timestamp=time.time(),
            market="stocks_us",
            exchange=self.exchange_name,
        )

    async def cancel_order(self, order_id: str) -> bool:
        import asyncio
        client = self._get_trading_client()
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: client.cancel_order_by_id(order_id))
            logger.info("AlpacaExecutor.cancel_order: %s cancelled", order_id)
            return True
        except Exception as exc:
            logger.error("AlpacaExecutor.cancel_order(%s) failed: %s", order_id, exc)
            return False

    async def close_position(
        self,
        symbol: str,
        size: float,
        reason: str = "manual",
    ) -> Order:
        import asyncio
        from alpaca.trading.requests import ClosePositionRequest  # type: ignore[import]

        client = self._get_trading_client()
        loop = asyncio.get_event_loop()
        req = ClosePositionRequest(qty=str(int(size)))
        raw = await loop.run_in_executor(
            None, lambda: client.close_position(symbol_or_asset_id=symbol, close_options=req)
        )
        logger.info(
            "AlpacaExecutor.close_position: %s size=%.2f reason=%s",
            symbol, size, reason,
        )
        return Order(
            order_id=str(raw.id) if hasattr(raw, "id") else "close",
            symbol=symbol,
            direction="sell",
            size=size,
            price=0.0,
            status=OrderStatus.FILLED,
            timestamp=time.time(),
            market="stocks_us",
            exchange=self.exchange_name,
        )

    async def cancel_all_orders(self) -> List[Order]:
        import asyncio
        client = self._get_trading_client()
        loop = asyncio.get_event_loop()
        cancelled = await loop.run_in_executor(None, client.cancel_orders)
        logger.info("AlpacaExecutor.cancel_all_orders: %d cancelled", len(cancelled))
        return []

    async def close_all_positions(self) -> List[Order]:
        import asyncio
        client = self._get_trading_client()
        loop = asyncio.get_event_loop()
        raw_list = await loop.run_in_executor(None, client.close_all_positions)
        orders: List[Order] = []
        for raw in raw_list:
            if hasattr(raw, "id"):
                orders.append(Order(
                    order_id=str(raw.id),
                    symbol=str(raw.symbol),
                    direction="sell",
                    size=float(getattr(raw, "qty", 0)),
                    price=0.0,
                    status=OrderStatus.FILLED,
                    timestamp=time.time(),
                    market="stocks_us",
                    exchange=self.exchange_name,
                ))
        return orders

    async def get_positions(self) -> List[Position]:
        import asyncio
        client = self._get_trading_client()
        loop = asyncio.get_event_loop()
        raw_positions = await loop.run_in_executor(None, client.get_all_positions)
        positions: List[Position] = []
        for rp in raw_positions:
            qty = float(rp.qty)
            if abs(qty) < 1e-6:
                continue
            direction = "long" if qty > 0 else "short"
            positions.append(Position(
                symbol=str(rp.symbol),
                direction=direction,
                size=abs(qty),
                entry_price=float(rp.avg_entry_price),
                current_price=float(rp.current_price),
                unrealized_pnl=float(rp.unrealized_pl),
                stop_loss=None,
                market="stocks_us",
                exchange=self.exchange_name,
            ))
        return positions

    async def get_open_orders(self) -> List[Order]:
        import asyncio
        from alpaca.trading.requests import GetOrdersRequest  # type: ignore[import]
        from alpaca.trading.enums import QueryOrderStatus  # type: ignore[import]

        client = self._get_trading_client()
        loop = asyncio.get_event_loop()
        req = GetOrdersRequest(status=QueryOrderStatus.OPEN)
        raw = await loop.run_in_executor(None, lambda: client.get_orders(req))
        orders: List[Order] = []
        for ro in raw:
            orders.append(Order(
                order_id=str(ro.id),
                symbol=str(ro.symbol),
                direction="buy" if str(ro.side) == "OrderSide.BUY" else "sell",
                size=float(ro.qty or 0),
                price=float(ro.limit_price or 0),
                status=OrderStatus.OPEN,
                timestamp=time.time(),
                market="stocks_us",
                exchange=self.exchange_name,
            ))
        return orders

    async def get_account_balance(self) -> float:
        import asyncio
        client = self._get_trading_client()
        loop = asyncio.get_event_loop()
        account = await loop.run_in_executor(None, client.get_account)
        return float(account.cash)
