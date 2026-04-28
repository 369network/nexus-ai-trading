"""
NEXUS ALPHA - Kite Executor
==============================
Zerodha Kite Connect executor for Indian equities (NSE/BSE) and F&O (NFO).

Features:
  - CNC (delivery) for equity, MIS (intraday) for F&O
  - NSE equity on NSE exchange, F&O on NFO exchange
  - Automatic MIS square-off before 3:15 PM IST
  - Market hours validation
  - Bracket orders for intraday with stop-loss and target
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from src.execution.base_executor import BaseExecutor, Order, OrderStatus, OrderType, Position

logger = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

# NSE market hours (IST)
MARKET_OPEN_H  = 9
MARKET_OPEN_M  = 15
MARKET_CLOSE_H = 15
MARKET_CLOSE_M = 30

# Auto square-off MIS positions before this time (IST)
MIS_SQUAREOFF_H = 15
MIS_SQUAREOFF_M = 10   # 3:10 PM IST (5 min buffer before 3:15)


class KiteExecutor(BaseExecutor):
    """
    Zerodha Kite Connect executor.

    Parameters
    ----------
    api_key : str
        Kite Connect API key.
    access_token : str
        Session access token (obtained after OAuth login).
    """

    def __init__(self, api_key: str, access_token: str) -> None:
        self._api_key = api_key
        self._access_token = access_token
        self._kite: Any = None

    @property
    def exchange_name(self) -> str:
        return "kite"

    # ------------------------------------------------------------------
    # Kite client initialization
    # ------------------------------------------------------------------

    def _get_kite(self) -> Any:
        """Lazily initialise the KiteConnect client."""
        if self._kite is not None:
            return self._kite
        try:
            from kiteconnect import KiteConnect  # type: ignore[import]
            kite = KiteConnect(api_key=self._api_key)
            kite.set_access_token(self._access_token)
            self._kite = kite
            logger.info("KiteExecutor: client initialized")
        except ImportError:
            raise RuntimeError("kiteconnect is required: pip install kiteconnect")
        return self._kite

    # ------------------------------------------------------------------
    # Market hours helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _now_ist() -> datetime:
        return datetime.now(tz=IST)

    def _is_market_open(self) -> bool:
        """Return True if NSE is currently in regular trading hours."""
        now = self._now_ist()
        # Weekday check: 0=Monday … 4=Friday
        if now.weekday() > 4:
            return False
        open_time  = now.replace(hour=MARKET_OPEN_H,  minute=MARKET_OPEN_M,  second=0, microsecond=0)
        close_time = now.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M, second=0, microsecond=0)
        return open_time <= now <= close_time

    def _is_mis_squareoff_time(self) -> bool:
        """Return True if it is time to auto-square-off MIS positions."""
        now = self._now_ist()
        squareoff = now.replace(hour=MIS_SQUAREOFF_H, minute=MIS_SQUAREOFF_M,
                                second=0, microsecond=0)
        close = now.replace(hour=MARKET_CLOSE_H, minute=MARKET_CLOSE_M,
                            second=0, microsecond=0)
        return squareoff <= now <= close

    @staticmethod
    def _get_exchange(symbol: str, product: str) -> str:
        """Determine the correct exchange for an order."""
        if product in ("MIS", "NRML") and ("FUT" in symbol or "CE" in symbol or "PE" in symbol):
            return "NFO"
        return "NSE"

    @staticmethod
    def _get_product(symbol: str, intraday: bool) -> str:
        """
        CNC for delivery equity, MIS for intraday, NRML for F&O overnight.
        """
        is_fo = "FUT" in symbol or "CE" in symbol or "PE" in symbol
        if is_fo:
            return "MIS" if intraday else "NRML"
        return "MIS" if intraday else "CNC"

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
        Place an order via Kite Connect.

        Market hours are validated; orders outside hours are rejected.
        Product type (CNC/MIS/NRML) is inferred from symbol type.
        """
        if not self._is_market_open():
            raise RuntimeError(
                f"KiteExecutor: market is closed (IST {self._now_ist():%H:%M})"
            )

        kite = self._get_kite()
        transaction = "BUY" if direction.lower() in ("long", "buy") else "SELL"
        quantity = int(round(size))   # Kite uses integer quantities

        # Infer product from context
        product = self._get_product(symbol, intraday=True)  # default MIS for now
        exchange = self._get_exchange(symbol, product)

        order_params: Dict[str, Any] = {
            "tradingsymbol": symbol,
            "exchange": exchange,
            "transaction_type": transaction,
            "quantity": quantity,
            "product": product,
        }

        if order_type == OrderType.MARKET:
            order_params["order_type"] = kite.ORDER_TYPE_MARKET
        else:
            order_params["order_type"] = kite.ORDER_TYPE_LIMIT
            order_params["price"] = price

        # Trigger stoploss order if stop_loss provided
        if stop_loss is not None:
            order_params["trigger_price"] = stop_loss

        try:
            # Kite Connect is synchronous – run in executor
            loop = asyncio.get_event_loop()
            order_id = await loop.run_in_executor(
                None, lambda: kite.place_order(variety=kite.VARIETY_REGULAR, **order_params)
            )

            logger.info(
                "KiteExecutor.place_order: %s %s %s qty=%d price=%.2f → id=%s",
                symbol, transaction, product, quantity, price, order_id,
            )

            return Order(
                order_id=str(order_id),
                symbol=symbol,
                direction=direction,
                size=float(quantity),
                price=price,
                status=OrderStatus.OPEN,
                timestamp=time.time(),
                market="stocks_in",
                exchange=self.exchange_name,
                stop_loss=stop_loss,
                take_profit=take_profit,
            )

        except Exception as exc:
            logger.error("KiteExecutor.place_order failed: %s", exc)
            raise

    async def place_bracket_order(
        self,
        symbol: str,
        direction: str,
        size: float,
        price: float,
        stop_loss_points: float,
        target_points: float,
    ) -> Order:
        """
        Place a bracket order with SL and target (intraday MIS only).

        Parameters
        ----------
        symbol : str
            NSE trading symbol.
        direction : str
            "buy" or "sell".
        size : float
            Quantity in shares.
        price : float
            Limit order entry price.
        stop_loss_points : float
            Points away from price for the stop-loss leg.
        target_points : float
            Points away from price for the take-profit leg.
        """
        if not self._is_market_open():
            raise RuntimeError("KiteExecutor: market is closed – bracket orders rejected")

        kite = self._get_kite()
        transaction = "BUY" if direction.lower() in ("long", "buy") else "SELL"
        quantity = int(round(size))

        loop = asyncio.get_event_loop()
        order_id = await loop.run_in_executor(
            None,
            lambda: kite.place_order(
                variety=kite.VARIETY_BO,
                tradingsymbol=symbol,
                exchange="NSE",
                transaction_type=transaction,
                quantity=quantity,
                order_type=kite.ORDER_TYPE_LIMIT,
                product=kite.PRODUCT_MIS,
                price=price,
                stoploss=stop_loss_points,
                squareoff=target_points,
            )
        )
        logger.info(
            "KiteExecutor.place_bracket_order: %s %s qty=%d price=%.2f "
            "sl=%.2f tgt=%.2f → id=%s",
            symbol, transaction, quantity, price, stop_loss_points, target_points, order_id,
        )
        return Order(
            order_id=str(order_id),
            symbol=symbol,
            direction=direction,
            size=float(quantity),
            price=price,
            status=OrderStatus.OPEN,
            timestamp=time.time(),
            market="stocks_in",
            exchange=self.exchange_name,
        )

    async def cancel_order(self, order_id: str) -> bool:
        kite = self._get_kite()
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: kite.cancel_order(variety=kite.VARIETY_REGULAR, order_id=order_id)
            )
            logger.info("KiteExecutor.cancel_order: %s cancelled", order_id)
            return True
        except Exception as exc:
            logger.error("KiteExecutor.cancel_order(%s) failed: %s", order_id, exc)
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
            raise ValueError(f"KiteExecutor: no open position for {symbol}")

        close_dir = "sell" if pos.direction.lower() in ("long", "buy") else "buy"
        return await self.place_order(
            symbol=symbol,
            direction=close_dir,
            size=size,
            order_type=OrderType.MARKET,
        )

    async def square_off_all_mis(self) -> List[Order]:
        """
        Square off all MIS positions.  Called automatically at 3:10 PM IST
        and should also be triggered by the scheduler.
        """
        logger.info("KiteExecutor: squaring off all MIS positions")
        positions = await self.get_positions()
        orders: List[Order] = []
        for pos in positions:
            if not pos.symbol:
                continue
            try:
                o = await self.close_position(pos.symbol, pos.size, reason="mis_squareoff")
                orders.append(o)
            except Exception as exc:  # noqa: BLE001
                logger.error("KiteExecutor.square_off_all_mis: %s failed: %s", pos.symbol, exc)
        return orders

    async def cancel_all_orders(self) -> List[Order]:
        open_orders = await self.get_open_orders()
        cancelled: List[Order] = []
        for o in open_orders:
            if await self.cancel_order(o.order_id):
                cancelled.append(o)
        return cancelled

    async def close_all_positions(self) -> List[Order]:
        positions = await self.get_positions()
        orders: List[Order] = []
        for pos in positions:
            try:
                o = await self.close_position(pos.symbol, pos.size, reason="emergency_close")
                orders.append(o)
            except Exception as exc:  # noqa: BLE001
                logger.error("KiteExecutor.close_all_positions: %s failed: %s", pos.symbol, exc)
        return orders

    async def get_positions(self) -> List[Position]:
        kite = self._get_kite()
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, kite.positions)
            positions: List[Position] = []
            all_pos = raw.get("net", []) + raw.get("day", [])
            seen: set = set()

            for rp in all_pos:
                symbol = rp.get("tradingsymbol", "")
                qty = rp.get("quantity", 0)
                if qty == 0 or symbol in seen:
                    continue
                seen.add(symbol)

                direction = "long" if qty > 0 else "short"
                avg_price = float(rp.get("average_price", 0))
                last_price = float(rp.get("last_price", avg_price))
                pnl = float(rp.get("unrealised", 0))

                positions.append(Position(
                    symbol=symbol,
                    direction=direction,
                    size=abs(qty),
                    entry_price=avg_price,
                    current_price=last_price,
                    unrealized_pnl=pnl,
                    stop_loss=None,
                    market="stocks_in",
                    exchange=self.exchange_name,
                ))
            return positions
        except Exception as exc:
            logger.error("KiteExecutor.get_positions failed: %s", exc)
            raise

    async def get_open_orders(self) -> List[Order]:
        kite = self._get_kite()
        loop = asyncio.get_event_loop()
        try:
            raw = await loop.run_in_executor(None, kite.orders)
            orders: List[Order] = []
            for ro in raw:
                if ro.get("status", "") not in ("OPEN", "TRIGGER PENDING"):
                    continue
                qty = float(ro.get("quantity", 0))
                transaction = ro.get("transaction_type", "BUY")
                orders.append(Order(
                    order_id=str(ro.get("order_id", "")),
                    symbol=ro.get("tradingsymbol", ""),
                    direction="buy" if transaction == "BUY" else "sell",
                    size=qty,
                    price=float(ro.get("price", 0)),
                    status=OrderStatus.OPEN,
                    timestamp=time.time(),
                    market="stocks_in",
                    exchange=self.exchange_name,
                ))
            return orders
        except Exception as exc:
            logger.error("KiteExecutor.get_open_orders failed: %s", exc)
            raise

    async def get_account_balance(self) -> float:
        kite = self._get_kite()
        loop = asyncio.get_event_loop()
        try:
            margins = await loop.run_in_executor(None, kite.margins)
            equity = margins.get("equity", {})
            return float(equity.get("available", {}).get("cash", 0.0))
        except Exception as exc:
            logger.error("KiteExecutor.get_account_balance failed: %s", exc)
            raise
