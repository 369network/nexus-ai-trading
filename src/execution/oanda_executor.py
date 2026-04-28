"""
NEXUS ALPHA - OANDA Executor
==============================
Forex and commodities execution via OANDA v20 REST API.

Features:
  - LIMIT orders with GTC time-in-force
  - Stop-loss and take-profit attached directly to orders
  - Margin call detection and emergency position reduction
  - Correct unit calculation (USD notional → currency units)
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Dict, List, Optional

import aiohttp

from src.execution.base_executor import BaseExecutor, Order, OrderStatus, OrderType, Position

logger = logging.getLogger(__name__)

OANDA_LIVE_URL   = "https://api-fxtrade.oanda.com"
OANDA_PAPER_URL  = "https://api-fxpractice.oanda.com"
MARGIN_CALL_PCT  = 0.20   # Reduce positions if margin < 20% of used margin


class OANDAExecutor(BaseExecutor):
    """
    OANDA v20 REST API executor for forex pairs and commodities (XAU, XAG, OIL).

    Parameters
    ----------
    api_token : str
        OANDA personal access token.
    account_id : str
        OANDA account identifier (e.g. "001-001-XXXXXXXX-001").
    live : bool
        If True, connect to live trading endpoint; otherwise practice.
    """

    def __init__(
        self,
        api_token: str,
        account_id: str,
        live: bool = False,
    ) -> None:
        self._token = api_token
        self._account_id = account_id
        self._base_url = OANDA_LIVE_URL if live else OANDA_PAPER_URL
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def exchange_name(self) -> str:
        return "oanda"

    # ------------------------------------------------------------------
    # HTTP session
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {
                "Authorization": f"Bearer {self._token}",
                "Content-Type": "application/json",
            }
            self._session = aiohttp.ClientSession(
                base_url=self._base_url,
                headers=headers,
            )
        return self._session

    async def _request(
        self,
        method: str,
        path: str,
        payload: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Make an authenticated OANDA API request."""
        session = await self._get_session()
        url = f"/v3{path}"
        try:
            async with session.request(method, url, json=payload) as resp:
                data = await resp.json()
                if resp.status >= 400:
                    raise RuntimeError(
                        f"OANDA API error {resp.status}: {data.get('errorMessage', data)}"
                    )
                return data
        except aiohttp.ClientError as exc:
            logger.error("OANDAExecutor HTTP error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Unit calculation
    # ------------------------------------------------------------------

    def _notional_to_units(
        self,
        symbol: str,
        notional_usd: float,
        price: float,
        direction: str,
    ) -> int:
        """
        Convert USD notional to OANDA units.

        OANDA uses signed integer units:
          - Positive = buy (long)
          - Negative = sell (short)

        For a EUR/USD pair, 1 unit = 1 EUR.  To buy $10,000 USD worth at
        1.10 price, units = 10000 / 1.10 ≈ 9090 units.

        For XAU_USD (gold), 1 unit = 1 troy ounce.
        """
        if price <= 0:
            raise ValueError(f"price must be positive: {price}")

        base_units = int(notional_usd / price)
        sign = 1 if direction.lower() in ("long", "buy") else -1
        return sign * base_units

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
        Place a LIMIT or MARKET order via OANDA v20.

        ``size`` is interpreted as the number of units (already converted
        from USD notional by the caller via ``_notional_to_units``).
        If ``size`` is given as a positive float, direction sign is applied
        internally.
        """
        sign = 1 if direction.lower() in ("long", "buy") else -1
        units = int(abs(size)) * sign

        order_body: Dict[str, Any] = {
            "units": str(units),
            "instrument": symbol,
            "timeInForce": "GTC",
            "positionFill": "DEFAULT",
        }

        if stop_loss is not None:
            order_body["stopLossOnFill"] = {"price": f"{stop_loss:.5f}"}

        if take_profit is not None:
            order_body["takeProfitOnFill"] = {"price": f"{take_profit:.5f}"}

        if order_type == OrderType.MARKET:
            order_body["type"] = "MARKET"
            order_body.pop("timeInForce", None)
        else:
            order_body["type"] = "LIMIT"
            order_body["price"] = f"{price:.5f}"

        payload = {"order": order_body}
        client_id = f"nexus_{uuid.uuid4().hex[:12]}"

        try:
            data = await self._request(
                "POST",
                f"/accounts/{self._account_id}/orders",
                payload,
            )
            order_raw = data.get("orderCreateTransaction", {})
            order_id = order_raw.get("id", client_id)

            logger.info(
                "OANDAExecutor.place_order: %s units=%d type=%s price=%.5f → id=%s",
                symbol, units, order_type.value, price, order_id,
            )

            return Order(
                order_id=str(order_id),
                symbol=symbol,
                direction=direction,
                size=abs(size),
                price=price,
                status=OrderStatus.OPEN,
                timestamp=time.time(),
                market="forex" if "_" in symbol else "commodities",
                exchange=self.exchange_name,
                stop_loss=stop_loss,
                take_profit=take_profit,
                client_order_id=client_id,
            )

        except Exception as exc:
            logger.error("OANDAExecutor.place_order failed: %s", exc)
            raise

    async def cancel_order(self, order_id: str) -> bool:
        try:
            await self._request(
                "PUT",
                f"/accounts/{self._account_id}/orders/{order_id}/cancel",
            )
            logger.info("OANDAExecutor.cancel_order: %s cancelled", order_id)
            return True
        except Exception as exc:
            logger.error("OANDAExecutor.cancel_order(%s) failed: %s", order_id, exc)
            return False

    async def close_position(
        self,
        symbol: str,
        size: float,
        reason: str = "manual",
    ) -> Order:
        """Close position using OANDA's close position endpoint."""
        # Get current position direction
        positions = await self.get_positions()
        pos = next((p for p in positions if p.symbol == symbol), None)

        if pos is None:
            raise ValueError(f"OANDAExecutor: no open position for {symbol}")

        if pos.direction.lower() in ("long", "buy"):
            body = {"longUnits": str(int(size))}
        else:
            body = {"shortUnits": str(int(size))}

        try:
            data = await self._request(
                "PUT",
                f"/accounts/{self._account_id}/positions/{symbol}/close",
                body,
            )
            tx = data.get("relatedTransactionIDs", [""])[0]
            logger.info(
                "OANDAExecutor.close_position: %s size=%.0f reason=%s tx=%s",
                symbol, size, reason, tx,
            )
            return Order(
                order_id=str(tx),
                symbol=symbol,
                direction="sell" if pos.direction.lower() in ("long", "buy") else "buy",
                size=size,
                price=0.0,
                status=OrderStatus.FILLED,
                timestamp=time.time(),
                market=pos.market,
                exchange=self.exchange_name,
            )
        except Exception as exc:
            logger.error("OANDAExecutor.close_position(%s) failed: %s", symbol, exc)
            raise

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
            except Exception as exc:
                logger.error("OANDAExecutor.close_all_positions: %s failed: %s", pos.symbol, exc)
        return orders

    async def get_positions(self) -> List[Position]:
        try:
            data = await self._request("GET", f"/accounts/{self._account_id}/openPositions")
            raw_positions = data.get("positions", [])
            positions: List[Position] = []

            for rp in raw_positions:
                instrument = rp.get("instrument", "")
                long_units  = float(rp.get("long",  {}).get("units", 0))
                short_units = float(rp.get("short", {}).get("units", 0))

                if abs(long_units) > 0:
                    avg_price = float(rp.get("long", {}).get("averagePrice", 0))
                    upnl = float(rp.get("long", {}).get("unrealizedPL", 0))
                    positions.append(Position(
                        symbol=instrument,
                        direction="long",
                        size=long_units,
                        entry_price=avg_price,
                        current_price=avg_price,  # updated by caller with live price
                        unrealized_pnl=upnl,
                        stop_loss=None,
                        market="forex" if "_" in instrument else "commodities",
                        exchange=self.exchange_name,
                    ))

                if abs(short_units) > 0:
                    avg_price = float(rp.get("short", {}).get("averagePrice", 0))
                    upnl = float(rp.get("short", {}).get("unrealizedPL", 0))
                    positions.append(Position(
                        symbol=instrument,
                        direction="short",
                        size=abs(short_units),
                        entry_price=avg_price,
                        current_price=avg_price,
                        unrealized_pnl=upnl,
                        stop_loss=None,
                        market="forex" if "_" in instrument else "commodities",
                        exchange=self.exchange_name,
                    ))

            return positions
        except Exception as exc:
            logger.error("OANDAExecutor.get_positions failed: %s", exc)
            raise

    async def get_open_orders(self) -> List[Order]:
        try:
            data = await self._request("GET", f"/accounts/{self._account_id}/pendingOrders")
            raw_orders = data.get("orders", [])
            orders: List[Order] = []
            for ro in raw_orders:
                units = float(ro.get("units", 0))
                direction = "buy" if units > 0 else "sell"
                orders.append(Order(
                    order_id=str(ro.get("id", "")),
                    symbol=ro.get("instrument", ""),
                    direction=direction,
                    size=abs(units),
                    price=float(ro.get("price", 0)),
                    status=OrderStatus.OPEN,
                    timestamp=time.time(),
                    market="forex",
                    exchange=self.exchange_name,
                ))
            return orders
        except Exception as exc:
            logger.error("OANDAExecutor.get_open_orders failed: %s", exc)
            raise

    async def get_account_balance(self) -> float:
        try:
            data = await self._request("GET", f"/accounts/{self._account_id}/summary")
            account = data.get("account", {})
            return float(account.get("balance", 0.0))
        except Exception as exc:
            logger.error("OANDAExecutor.get_account_balance failed: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Margin call handler
    # ------------------------------------------------------------------

    async def handle_margin_call(self) -> None:
        """
        Check margin utilisation; reduce all positions by 50% if margin < 20%.

        This should be called periodically by the risk monitor, and is
        triggered automatically on margin-call API events.
        """
        try:
            data = await self._request("GET", f"/accounts/{self._account_id}/summary")
            account = data.get("account", {})
            margin_used      = float(account.get("marginUsed",      0.0))
            margin_available = float(account.get("marginAvailable", 0.0))

            if margin_used <= 0:
                return

            margin_pct = margin_available / (margin_used + margin_available)
            if margin_pct < MARGIN_CALL_PCT:
                logger.critical(
                    "OANDAExecutor.handle_margin_call: margin=%.1f%% < %.1f%% – "
                    "reducing all positions 50%%",
                    margin_pct * 100, MARGIN_CALL_PCT * 100,
                )
                positions = await self.get_positions()
                for pos in positions:
                    reduce_size = pos.size * 0.50
                    try:
                        await self.close_position(pos.symbol, reduce_size, reason="margin_call")
                    except Exception as exc:  # noqa: BLE001
                        logger.error("OANDAExecutor.margin_call: failed to reduce %s: %s",
                                     pos.symbol, exc)

        except Exception as exc:
            logger.error("OANDAExecutor.handle_margin_call failed: %s", exc)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
