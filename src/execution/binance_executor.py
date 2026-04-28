"""
NEXUS ALPHA - Binance Executor
================================
Full implementation for Binance Spot and Futures (USDT-M) trading.

Features:
  - LIMIT orders with GTC time-in-force (auto-cancelled after 4 hours)
  - OCO (One-Cancels-Other) orders for stop + take-profit
  - Trailing stop orders
  - Symbol precision enforcement (lot size, tick size)
  - Graceful error handling for INSUFFICIENT_BALANCE, MIN_NOTIONAL
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
import uuid
from typing import Any, Dict, List, Optional

from src.execution.base_executor import BaseExecutor, Direction, Order, OrderStatus, OrderType, Position

logger = logging.getLogger(__name__)

# Auto-cancel limit orders after this many seconds (4 hours)
LIMIT_ORDER_CANCEL_SECONDS = 4 * 3600

# Binance error codes
ERR_INSUFFICIENT_BALANCE = -2010
ERR_MIN_NOTIONAL         = -1013
ERR_INVALID_QUANTITY     = -1111


class BinanceExecutor(BaseExecutor):
    """
    Binance Spot + USDT-M Futures executor via the ``python-binance`` client.

    Environment variables expected:
      - BINANCE_API_KEY
      - BINANCE_API_SECRET
      - BINANCE_FUTURES (set to "1" to use futures client)
      - BINANCE_TESTNET (set to "1" for testnet)

    Parameters
    ----------
    api_key : str
        Binance API key.
    api_secret : str
        Binance API secret.
    futures : bool
        If True, use USDT-M Futures; otherwise Spot.
    testnet : bool
        If True, connect to testnet.
    """

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        futures: bool = False,
        testnet: bool = False,
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._futures = futures
        self._testnet = testnet
        self._client: Any = None
        self._symbol_info_cache: Dict[str, Dict[str, Any]] = {}
        # Track pending cancel tasks for GTC limit orders
        self._cancel_tasks: Dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _ensure_client(self) -> Any:
        """Lazily initialize the Binance async client."""
        if self._client is not None:
            return self._client
        try:
            from binance import AsyncClient  # type: ignore[import]
            self._client = await AsyncClient.create(
                api_key=self._api_key,
                api_secret=self._api_secret,
                testnet=self._testnet,
            )
            logger.info("BinanceExecutor: client initialized (futures=%s testnet=%s)",
                        self._futures, self._testnet)
        except ImportError:
            raise RuntimeError("python-binance is required: pip install python-binance")
        return self._client

    @property
    def exchange_name(self) -> str:
        return "binance_futures" if self._futures else "binance_spot"

    # ------------------------------------------------------------------
    # Symbol precision
    # ------------------------------------------------------------------

    async def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch and cache exchange precision requirements for a symbol.

        Returns a dict with keys: lot_size, min_qty, step_size, tick_size,
        min_notional, price_precision, quantity_precision.
        """
        if symbol in self._symbol_info_cache:
            return self._symbol_info_cache[symbol]

        client = await self._ensure_client()
        try:
            if self._futures:
                info = await client.futures_exchange_info()
                symbols = info["symbols"]
            else:
                info = await client.get_exchange_info()
                symbols = info["symbols"]

            for s in symbols:
                if s["symbol"] == symbol:
                    filters = {f["filterType"]: f for f in s.get("filters", [])}
                    lot = filters.get("LOT_SIZE", {})
                    price_f = filters.get("PRICE_FILTER", {})
                    notional = filters.get("MIN_NOTIONAL", filters.get("NOTIONAL", {}))

                    parsed = {
                        "min_qty":             float(lot.get("minQty", 0.001)),
                        "max_qty":             float(lot.get("maxQty", 9999999)),
                        "step_size":           float(lot.get("stepSize", 0.001)),
                        "tick_size":           float(price_f.get("tickSize", 0.01)),
                        "min_notional":        float(notional.get("minNotional", 10.0)),
                        "quantity_precision":  s.get("quantityPrecision", 3),
                        "price_precision":     s.get("pricePrecision", 2),
                    }
                    self._symbol_info_cache[symbol] = parsed
                    return parsed

            logger.warning("BinanceExecutor: symbol %s not found in exchange info", symbol)
            return {
                "min_qty": 0.001, "max_qty": 9999999, "step_size": 0.001,
                "tick_size": 0.01, "min_notional": 10.0,
                "quantity_precision": 3, "price_precision": 2,
            }
        except Exception as exc:
            logger.error("BinanceExecutor: get_symbol_info(%s) failed: %s", symbol, exc)
            raise

    def _round_quantity(self, qty: float, step_size: float, precision: int) -> float:
        """Round quantity to exchange lot-size step."""
        if step_size <= 0:
            return round(qty, precision)
        steps = math.floor(qty / step_size)
        return round(steps * step_size, precision)

    def _round_price(self, price: float, tick_size: float, precision: int) -> float:
        """Round price to exchange tick size."""
        if tick_size <= 0:
            return round(price, precision)
        ticks = math.floor(price / tick_size)
        return round(ticks * tick_size, precision)

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
        Place a LIMIT (GTC) or MARKET order on Binance.

        LIMIT orders are automatically scheduled for cancellation after 4 hours
        if not filled, avoiding stale orders accumulating.
        """
        client = await self._ensure_client()
        info = await self.get_symbol_info(symbol)

        # Precision rounding
        qty = self._round_quantity(size, info["step_size"], info["quantity_precision"])
        if qty < info["min_qty"]:
            raise ValueError(
                f"BinanceExecutor: quantity {qty} below minQty {info['min_qty']} for {symbol}"
            )

        # Determine side
        side = "BUY" if direction.lower() in ("long", "buy") else "SELL"

        # Build order params
        client_id = f"nexus_{uuid.uuid4().hex[:12]}"
        params: Dict[str, Any] = {
            "symbol": symbol,
            "side": side,
            "quantity": qty,
            "newClientOrderId": client_id,
        }

        if order_type == OrderType.MARKET:
            params["type"] = "MARKET"
        else:
            # LIMIT GTC
            rounded_price = self._round_price(price, info["tick_size"], info["price_precision"])
            params["type"] = "LIMIT"
            params["timeInForce"] = "GTC"
            params["price"] = rounded_price

        try:
            if self._futures:
                raw = await client.futures_create_order(**params)
            else:
                raw = await client.create_order(**params)

            order = self._raw_to_order(raw, symbol, direction, size, order_type,
                                        price, stop_loss, take_profit)
            logger.info(
                "BinanceExecutor.place_order: %s %s %s qty=%.6f price=%.2f → id=%s",
                symbol, side, order_type.value, qty, price, order.order_id,
            )

            # Schedule auto-cancel for LIMIT orders
            if order_type == OrderType.LIMIT and order.is_active:
                task = asyncio.create_task(
                    self._schedule_cancel(order.order_id, symbol, LIMIT_ORDER_CANCEL_SECONDS)
                )
                self._cancel_tasks[order.order_id] = task

            return order

        except Exception as exc:
            err_msg = str(exc)
            if "insufficient balance" in err_msg.lower() or f"{ERR_INSUFFICIENT_BALANCE}" in err_msg:
                logger.error("BinanceExecutor: INSUFFICIENT_BALANCE for %s qty=%.6f", symbol, qty)
                raise RuntimeError(f"Insufficient balance: {exc}") from exc
            if f"{ERR_MIN_NOTIONAL}" in err_msg or "min notional" in err_msg.lower():
                logger.error("BinanceExecutor: MIN_NOTIONAL breach for %s qty=%.6f price=%.2f",
                             symbol, qty, price)
                raise RuntimeError(f"Order below minimum notional: {exc}") from exc
            raise

    async def _schedule_cancel(self, order_id: str, symbol: str, delay_s: int) -> None:
        """Coroutine: wait delay_s seconds then cancel the order if still open."""
        await asyncio.sleep(delay_s)
        try:
            await self.cancel_order(order_id)
            logger.info("BinanceExecutor: GTC auto-cancel fired for %s/%s", symbol, order_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BinanceExecutor: auto-cancel failed for %s: %s", order_id, exc)
        finally:
            self._cancel_tasks.pop(order_id, None)

    async def place_oco_order(
        self,
        symbol: str,
        size: float,
        direction: str,
        stop_loss: float,
        take_profit: float,
    ) -> Dict[str, Any]:
        """
        Place an OCO (One-Cancels-Other) order combining stop and take-profit.

        Parameters
        ----------
        symbol : str
            Trading symbol.
        size : float
            Position size in base units.
        direction : str
            "buy" or "sell" (direction of the CLOSING OCO order).
        stop_loss : float
            Stop trigger price.
        take_profit : float
            Take-profit limit price.

        Returns
        -------
        dict
            Raw OCO response from Binance.
        """
        client = await self._ensure_client()
        info = await self.get_symbol_info(symbol)
        qty = self._round_quantity(size, info["step_size"], info["quantity_precision"])
        side = "BUY" if direction.lower() in ("long", "buy") else "SELL"

        # For an OCO selling (closing a long): stop < price < take_profit
        stop_rounded  = self._round_price(stop_loss,   info["tick_size"], info["price_precision"])
        tp_rounded    = self._round_price(take_profit,  info["tick_size"], info["price_precision"])

        try:
            raw = await client.create_oco_order(
                symbol=symbol,
                side=side,
                quantity=qty,
                price=tp_rounded,
                stopPrice=stop_rounded,
                stopLimitPrice=stop_rounded,
                stopLimitTimeInForce="GTC",
            )
            logger.info(
                "BinanceExecutor.place_oco_order: %s side=%s qty=%.6f sl=%.4f tp=%.4f",
                symbol, side, qty, stop_rounded, tp_rounded,
            )
            return raw
        except Exception as exc:
            logger.error("BinanceExecutor.place_oco_order failed: %s", exc)
            raise

    async def place_trailing_stop(
        self,
        symbol: str,
        size: float,
        callback_rate: float,
    ) -> Dict[str, Any]:
        """
        Place a trailing stop order (Futures only).

        Parameters
        ----------
        symbol : str
            Trading symbol.
        size : float
            Quantity to protect.
        callback_rate : float
            Trailing callback rate as a percentage (e.g. 1.0 = 1%).

        Returns
        -------
        dict
            Raw order response.
        """
        if not self._futures:
            raise RuntimeError("Trailing stop orders require the futures client")

        client = await self._ensure_client()
        info = await self.get_symbol_info(symbol)
        qty = self._round_quantity(size, info["step_size"], info["quantity_precision"])

        raw = await client.futures_create_order(
            symbol=symbol,
            side="SELL",
            type="TRAILING_STOP_MARKET",
            callbackRate=callback_rate,
            quantity=qty,
        )
        logger.info(
            "BinanceExecutor.place_trailing_stop: %s qty=%.6f callback=%.2f%%",
            symbol, qty, callback_rate,
        )
        return raw

    async def cancel_order(self, order_id: str) -> bool:
        client = await self._ensure_client()
        # We need the symbol to cancel – look it up from open orders
        try:
            open_orders = await self.get_open_orders()
            symbol = next(
                (o.symbol for o in open_orders if o.order_id == order_id), None
            )
            if symbol is None:
                logger.warning("BinanceExecutor.cancel_order: order %s not in open orders", order_id)
                return True  # Assume already cancelled/filled

            if self._futures:
                await client.futures_cancel_order(symbol=symbol, orderId=order_id)
            else:
                await client.cancel_order(symbol=symbol, orderId=order_id)

            logger.info("BinanceExecutor.cancel_order: %s cancelled", order_id)
            # Stop auto-cancel task if pending
            task = self._cancel_tasks.pop(order_id, None)
            if task and not task.done():
                task.cancel()
            return True
        except Exception as exc:
            logger.error("BinanceExecutor.cancel_order(%s) failed: %s", order_id, exc)
            return False

    async def close_position(
        self,
        symbol: str,
        size: float,
        reason: str = "manual",
    ) -> Order:
        """Close a position by placing a market order in the opposite direction."""
        # Determine direction from open positions
        positions = await self.get_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)
        if pos is None:
            raise ValueError(f"BinanceExecutor: no open position for {symbol}")

        close_direction = "sell" if pos.direction.lower() in ("long", "buy") else "buy"
        order = await self.place_order(
            symbol=symbol,
            direction=close_direction,
            size=size,
            order_type=OrderType.MARKET,
        )
        logger.info(
            "BinanceExecutor.close_position: %s size=%.6f reason=%s order_id=%s",
            symbol, size, reason, order.order_id,
        )
        return order

    async def cancel_all_orders(self) -> List[Order]:
        client = await self._ensure_client()
        cancelled: List[Order] = []
        open_orders = await self.get_open_orders()

        tasks = []
        for o in open_orders:
            tasks.append(self.cancel_order(o.order_id))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        cancelled = [o for o, r in zip(open_orders, results) if r is True]
        logger.info(
            "BinanceExecutor.cancel_all_orders: cancelled %d of %d orders",
            len(cancelled), len(open_orders),
        )
        return cancelled

    async def close_all_positions(self) -> List[Order]:
        positions = await self.get_positions()
        close_orders: List[Order] = []

        tasks = [
            self.close_position(p.symbol, p.size, reason="emergency_close")
            for p in positions
            if p.size > 0
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Order):
                close_orders.append(result)
            else:
                logger.error("BinanceExecutor.close_all_positions: error: %s", result)

        return close_orders

    async def get_positions(self) -> List[Position]:
        client = await self._ensure_client()
        positions: List[Position] = []
        try:
            if self._futures:
                raw_positions = await client.futures_position_information()
            else:
                # Spot: derive from account balances (no true "position" concept)
                raw_positions = []

            for rp in raw_positions:
                amt = float(rp.get("positionAmt", 0))
                if abs(amt) < 1e-8:
                    continue

                entry = float(rp.get("entryPrice", 0))
                mark  = float(rp.get("markPrice", entry))
                upnl  = float(rp.get("unRealizedProfit", 0))
                direction = "long" if amt > 0 else "short"

                positions.append(Position(
                    symbol=rp["symbol"],
                    direction=direction,
                    size=abs(amt),
                    entry_price=entry,
                    current_price=mark,
                    unrealized_pnl=upnl,
                    stop_loss=None,
                    market="crypto",
                    exchange=self.exchange_name,
                ))

        except Exception as exc:
            logger.error("BinanceExecutor.get_positions failed: %s", exc)
            raise

        return positions

    async def get_open_orders(self) -> List[Order]:
        client = await self._ensure_client()
        orders: List[Order] = []
        try:
            if self._futures:
                raw = await client.futures_get_open_orders()
            else:
                raw = await client.get_open_orders()

            for r in raw:
                orders.append(self._raw_to_order(
                    r, r["symbol"],
                    "buy" if r["side"] == "BUY" else "sell",
                    float(r.get("origQty", 0)),
                    OrderType.LIMIT,
                    float(r.get("price", 0)),
                ))
        except Exception as exc:
            logger.error("BinanceExecutor.get_open_orders failed: %s", exc)
            raise
        return orders

    async def get_account_balance(self) -> float:
        client = await self._ensure_client()
        try:
            if self._futures:
                info = await client.futures_account()
                return float(info.get("availableBalance", 0.0))
            else:
                info = await client.get_asset_balance(asset="USDT")
                return float(info.get("free", 0.0))
        except Exception as exc:
            logger.error("BinanceExecutor.get_account_balance failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _raw_to_order(
        self,
        raw: Dict[str, Any],
        symbol: str,
        direction: str,
        size: float,
        order_type: OrderType,
        price: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Order:
        """Convert a raw Binance API response dict to an Order dataclass."""
        status_map = {
            "NEW": OrderStatus.OPEN,
            "PARTIALLY_FILLED": OrderStatus.PARTIALLY_FILLED,
            "FILLED": OrderStatus.FILLED,
            "CANCELED": OrderStatus.CANCELLED,
            "REJECTED": OrderStatus.REJECTED,
            "EXPIRED": OrderStatus.EXPIRED,
        }
        raw_status = raw.get("status", "NEW")
        status = status_map.get(raw_status, OrderStatus.OPEN)

        return Order(
            order_id=str(raw.get("orderId", "")),
            symbol=symbol,
            direction=direction,
            size=float(raw.get("origQty", size)),
            price=float(raw.get("price", price)),
            status=status,
            timestamp=raw.get("transactTime", time.time() * 1000) / 1000.0,
            market="crypto",
            exchange=self.exchange_name,
            filled_qty=float(raw.get("executedQty", 0.0)),
            avg_fill_price=float(raw.get("avgPrice", 0.0)),
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=str(raw.get("clientOrderId", "")),
        )

    async def close(self) -> None:
        """Clean up the async Binance client."""
        if self._client:
            await self._client.close_connection()
            self._client = None
        # Cancel pending auto-cancel tasks
        for task in self._cancel_tasks.values():
            task.cancel()
        self._cancel_tasks.clear()
