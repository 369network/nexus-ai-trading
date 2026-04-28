"""
NEXUS ALPHA - Kite REST Provider
====================================
KiteConnect REST API wrapper for holdings, positions, orders, margins,
order placement/cancellation, and OHLCV snapshots.

Environment variables (same as kite_ws.py):
    KITE_API_KEY
    KITE_API_SECRET
    KITE_TOKEN_FILE
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TOKEN_FILE = Path(".kite_token")


class KiteRESTProvider:
    """
    Async wrapper around the KiteConnect REST client.

    All KiteConnect calls are synchronous; they are run in a thread
    executor to avoid blocking the event loop.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        token_file: Optional[Path] = None,
    ) -> None:
        self._api_key    = api_key    or os.getenv("KITE_API_KEY", "")
        self._api_secret = api_secret or os.getenv("KITE_API_SECRET", "")
        self._token_file = token_file or Path(
            os.getenv("KITE_TOKEN_FILE", str(_DEFAULT_TOKEN_FILE))
        )
        self._kite = None
        self._access_token: Optional[str] = None

    # ------------------------------------------------------------------

    async def _ensure_kite(self):
        """Initialise the KiteConnect object with a valid access token."""
        if self._kite is not None:
            return

        from kiteconnect import KiteConnect

        token = self._load_token()
        if not token:
            raise RuntimeError(
                "No valid Kite access token found. "
                "Use KiteWebSocketProvider.complete_login() first."
            )

        self._access_token = token
        self._kite = KiteConnect(api_key=self._api_key)
        self._kite.set_access_token(token)

    def _load_token(self) -> Optional[str]:
        if not self._token_file.exists():
            return None
        try:
            payload = json.loads(self._token_file.read_text())
            if payload.get("date") == date.today().isoformat():
                return payload.get("access_token")
        except Exception:
            pass
        return None

    async def _run(self, fn, *args, **kwargs):
        """Run a blocking kiteconnect call in the thread executor."""
        await self._ensure_kite()
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    # ------------------------------------------------------------------
    # Portfolio / account
    # ------------------------------------------------------------------

    async def get_holdings(self) -> List[Dict[str, Any]]:
        """
        Fetch the current equity holdings (long-term delivery positions).

        Returns
        -------
        List[dict]
            Each dict contains ``tradingsymbol``, ``exchange``,
            ``quantity``, ``average_price``, ``last_price``, ``pnl``.
        """
        return await self._run(self._kite.holdings)

    # ------------------------------------------------------------------

    async def get_positions(self) -> Dict[str, List[Dict[str, Any]]]:
        """
        Fetch current intraday and overnight F&O positions.

        Returns
        -------
        dict
            ``{"net": [...], "day": [...]}``.
        """
        return await self._run(self._kite.positions)

    # ------------------------------------------------------------------

    async def get_orders(self) -> List[Dict[str, Any]]:
        """
        Fetch all orders placed today (all statuses).

        Returns
        -------
        List[dict]
        """
        return await self._run(self._kite.orders)

    # ------------------------------------------------------------------

    async def get_margins(
        self,
        segment: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch available and used margins.

        Parameters
        ----------
        segment:
            ``"equity"`` or ``"commodity"``.  None returns both.

        Returns
        -------
        dict
        """
        return await self._run(self._kite.margins, segment)

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def place_order(
        self,
        variety: str,
        exchange: str,
        tradingsymbol: str,
        transaction_type: str,
        quantity: int,
        price: float = 0,
        order_type: str = "MARKET",
        product: str = "MIS",
        validity: str = "DAY",
        trigger_price: Optional[float] = None,
        disclosed_quantity: Optional[int] = None,
        squareoff: Optional[float] = None,
        stoploss: Optional[float] = None,
        trailing_stoploss: Optional[float] = None,
        tag: Optional[str] = None,
    ) -> str:
        """
        Place an order on Zerodha Kite.

        Parameters
        ----------
        variety:
            Order variety: ``"regular"``, ``"co"`` (cover), ``"amo"``.
        exchange:
            Exchange: ``"NSE"``, ``"BSE"``, ``"NFO"``, ``"MCX"``.
        tradingsymbol:
            NSE/BSE symbol or F&O contract name.
        transaction_type:
            ``"BUY"`` or ``"SELL"``.
        quantity:
            Number of shares / lots.
        price:
            Limit price (0 for market orders).
        order_type:
            ``"MARKET"``, ``"LIMIT"``, ``"SL"``, ``"SL-M"``.
        product:
            ``"CNC"`` (delivery), ``"MIS"`` (intraday), ``"NRML"`` (F&O).
        validity:
            ``"DAY"`` or ``"IOC"``.
        trigger_price:
            Trigger price for SL/SL-M orders.
        tag:
            Optional user tag (max 8 alphanumeric chars).

        Returns
        -------
        str
            Order ID.
        """
        kwargs: Dict[str, Any] = {
            "variety":          variety,
            "exchange":         exchange,
            "tradingsymbol":    tradingsymbol,
            "transaction_type": transaction_type,
            "quantity":         quantity,
            "price":            price,
            "order_type":       order_type,
            "product":          product,
            "validity":         validity,
        }
        if trigger_price is not None:
            kwargs["trigger_price"] = trigger_price
        if disclosed_quantity is not None:
            kwargs["disclosed_quantity"] = disclosed_quantity
        if squareoff is not None:
            kwargs["squareoff"] = squareoff
        if stoploss is not None:
            kwargs["stoploss"] = stoploss
        if trailing_stoploss is not None:
            kwargs["trailing_stoploss"] = trailing_stoploss
        if tag is not None:
            kwargs["tag"] = tag

        result = await self._run(self._kite.place_order, **kwargs)
        return result["order_id"]

    # ------------------------------------------------------------------

    async def cancel_order(
        self,
        variety: str,
        order_id: str,
    ) -> str:
        """
        Cancel an open order.

        Parameters
        ----------
        variety:
            Order variety used when placing (``"regular"``, ``"co"``, etc.).
        order_id:
            Order ID to cancel.

        Returns
        -------
        str
            The cancelled order ID.
        """
        result = await self._run(
            self._kite.cancel_order,
            variety=variety,
            order_id=order_id,
        )
        return result["order_id"]

    # ------------------------------------------------------------------

    async def get_ohlc(
        self,
        instruments: List[str],
    ) -> Dict[str, Any]:
        """
        Fetch the current OHLCV snapshot for one or more instruments.

        Parameters
        ----------
        instruments:
            List of exchange:symbol strings,
            e.g. ``["NSE:INFY", "NSE:RELIANCE"]``.

        Returns
        -------
        dict
            ``{instrument: {ohlc: {open, high, low, close}, last_price, volume}}``.
        """
        return await self._run(self._kite.ohlc, instruments)
