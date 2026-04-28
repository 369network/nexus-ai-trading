"""
NEXUS ALPHA - Binance REST Provider
======================================
Full REST client for Binance Spot and Futures (USDM Perpetuals).

Handles HMAC-SHA256 request signing, per-instance rate limiting (not
global), pagination for large kline requests, and all endpoints needed
by the NEXUS ALPHA trading system.

Environment variables consumed (via dotenv or os.environ):
    BINANCE_API_KEY
    BINANCE_API_SECRET
    BINANCE_TESTNET          "true" to use testnet endpoints
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
import time
import urllib.parse
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional

import aiohttp

from ..base import OHLCV, OrderBook
from ..normalizer import UnifiedDataNormalizer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint roots
# ---------------------------------------------------------------------------

_SPOT_BASE     = "https://api.binance.com"
_FUTURES_BASE  = "https://fapi.binance.com"
_SPOT_TEST     = "https://testnet.binance.vision"
_FUTURES_TEST  = "https://testnet.binancefuture.com"

# ---------------------------------------------------------------------------
# Rate limiter (token-bucket per instance)
# ---------------------------------------------------------------------------

@dataclass
class RateLimiter:
    """
    Token-bucket rate limiter suitable for async code.

    Parameters
    ----------
    rate:
        Number of tokens added per second.
    capacity:
        Maximum burst capacity.
    """

    rate: float
    capacity: float
    _tokens: float = field(init=False)
    _last_refill: float = field(init=False)
    _lock: asyncio.Lock = field(init=False, compare=False, repr=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: float = 1.0) -> None:
        """Block until *tokens* are available."""
        async with self._lock:
            while True:
                now = time.monotonic()
                elapsed = now - self._last_refill
                self._tokens = min(
                    self.capacity,
                    self._tokens + elapsed * self.rate,
                )
                self._last_refill = now

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return

                wait = (tokens - self._tokens) / self.rate
                await asyncio.sleep(wait)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class BinanceRESTProvider:
    """
    Async REST client for Binance Spot and USDM Futures markets.

    Parameters
    ----------
    api_key:
        Binance API key.  Defaults to the ``BINANCE_API_KEY`` env var.
    api_secret:
        Binance API secret.  Defaults to the ``BINANCE_API_SECRET`` env var.
    market:
        ``"spot"`` or ``"futures"``.
    testnet:
        Use Binance testnet endpoints when True.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        market: str = "spot",
        testnet: bool = False,
    ) -> None:
        self._api_key    = api_key    or os.getenv("BINANCE_API_KEY", "")
        self._api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        self._market     = market
        self._testnet    = testnet or os.getenv("BINANCE_TESTNET", "").lower() == "true"

        if market == "futures":
            self._base = _FUTURES_TEST if self._testnet else _FUTURES_BASE
        else:
            self._base = _SPOT_TEST    if self._testnet else _SPOT_BASE

        # Per-instance rate limiter: Binance allows 1200 req/min = 20 req/s
        self._limiter = RateLimiter(rate=18.0, capacity=50.0)
        self._session: Optional[aiohttp.ClientSession] = None
        self._normalizer = UnifiedDataNormalizer()

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(
                headers={"X-MBX-APIKEY": self._api_key},
                timeout=timeout,
            )
        return self._session

    async def close(self) -> None:
        """Close the underlying HTTP session."""
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Request helpers
    # ------------------------------------------------------------------

    def _sign(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """Add HMAC-SHA256 signature to a parameter dict (in-place)."""
        params["timestamp"] = int(time.time() * 1_000)
        qs = urllib.parse.urlencode(params)
        sig = hmac.new(
            self._api_secret.encode(), qs.encode(), hashlib.sha256
        ).hexdigest()
        params["signature"] = sig
        return params

    async def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
        signed: bool = False,
        weight: float = 1.0,
    ) -> Any:
        """
        Perform a rate-limited GET request.

        Parameters
        ----------
        path:
            API path, e.g. ``"/api/v3/klines"``.
        params:
            Query parameters.
        signed:
            Whether to add HMAC signature.
        weight:
            Request weight for rate-limiting accounting.
        """
        await self._limiter.acquire(weight)
        session = await self._get_session()
        p = dict(params or {})
        if signed:
            p = self._sign(p)

        url = f"{self._base}{path}"
        async with session.get(url, params=p) as resp:
            if resp.status == 429:
                retry_after = float(resp.headers.get("Retry-After", "60"))
                logger.warning("Binance rate limit hit – sleeping %.0fs", retry_after)
                await asyncio.sleep(retry_after)
                return await self._get(path, params, signed, weight)
            resp.raise_for_status()
            return await resp.json()

    # ------------------------------------------------------------------
    # Public endpoints
    # ------------------------------------------------------------------

    async def get_klines(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500,
    ) -> List[OHLCV]:
        """
        Fetch klines (OHLCV candles) with automatic pagination.

        Binance returns at most 1000 candles per call; this method pages
        until the requested window is covered.

        Parameters
        ----------
        symbol:
            Trading pair, e.g. ``"BTCUSDT"``.
        interval:
            Kline interval string, e.g. ``"1m"``, ``"1h"``.
        start_time:
            UTC milliseconds.
        end_time:
            UTC milliseconds.
        limit:
            Maximum candles to return (capped at 1000 per call internally).

        Returns
        -------
        List[OHLCV]
        """
        path = (
            "/fapi/v1/klines"
            if self._market == "futures"
            else "/api/v3/klines"
        )
        all_candles: List[OHLCV] = []
        per_call = min(limit, 1000)
        current_start = start_time

        while True:
            params: Dict[str, Any] = {
                "symbol":   symbol,
                "interval": interval,
                "limit":    per_call,
            }
            if current_start is not None:
                params["startTime"] = current_start
            if end_time is not None:
                params["endTime"] = end_time

            raw = await self._get(path, params, weight=2.0)

            if not raw:
                break

            for row in raw:
                # Binance kline row: [open_time, o, h, l, c, vol, close_time,
                #                      quote_vol, trades, taker_buy_base, taker_buy_quote, ignored]
                candle = OHLCV(
                    timestamp_ms     = int(row[0]),
                    open             = float(row[1]),
                    high             = float(row[2]),
                    low              = float(row[3]),
                    close            = float(row[4]),
                    volume           = float(row[5]),
                    quote_volume     = float(row[7]),
                    trades           = int(row[8]),
                    vwap             = float(row[7]) / float(row[5]) if float(row[5]) > 0 else float(row[4]),
                    taker_buy_volume = float(row[9]),
                    source           = "binance",
                    market           = self._market,
                    symbol           = symbol,
                    interval         = interval,
                    complete         = True,
                )
                all_candles.append(candle)

            if len(raw) < per_call:
                break

            # Advance start_time for next page
            current_start = int(raw[-1][0]) + 1

            if len(all_candles) >= limit:
                break

            if end_time is not None and current_start >= end_time:
                break

        return all_candles[:limit]

    # ------------------------------------------------------------------

    async def get_funding_rate(
        self,
        symbol: str,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Fetch funding rate history for a futures symbol.

        Parameters
        ----------
        symbol:
            Futures symbol, e.g. ``"BTCUSDT"``.
        limit:
            Number of records (max 1000).

        Returns
        -------
        List[dict]
            Each dict: ``{symbol, fundingTime, fundingRate}``.
        """
        if self._market != "futures":
            raise RuntimeError("get_funding_rate requires market='futures'")
        return await self._get(
            "/fapi/v1/fundingRate",
            {"symbol": symbol, "limit": min(limit, 1000)},
            weight=1.0,
        )

    # ------------------------------------------------------------------

    async def get_open_interest(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch current open interest for a futures symbol.

        Returns
        -------
        dict
            ``{symbol, openInterest, time}``.
        """
        if self._market != "futures":
            raise RuntimeError("get_open_interest requires market='futures'")
        return await self._get(
            "/fapi/v1/openInterest",
            {"symbol": symbol},
            weight=1.0,
        )

    # ------------------------------------------------------------------

    async def get_24h_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch 24-hour rolling statistics for *symbol*.

        Returns
        -------
        dict
            Binance ticker dict with priceChange, priceChangePercent,
            weightedAvgPrice, volume, quoteVolume, etc.
        """
        path = (
            "/fapi/v1/ticker/24hr"
            if self._market == "futures"
            else "/api/v3/ticker/24hr"
        )
        return await self._get(path, {"symbol": symbol}, weight=1.0)

    # ------------------------------------------------------------------

    async def get_exchange_info(self) -> Dict[str, Any]:
        """
        Fetch exchange information including trading rules per symbol.

        Returns
        -------
        dict
            Contains ``symbols`` list with filters (PRICE_FILTER,
            LOT_SIZE, MIN_NOTIONAL, etc.).
        """
        path = (
            "/fapi/v1/exchangeInfo"
            if self._market == "futures"
            else "/api/v3/exchangeInfo"
        )
        return await self._get(path, weight=10.0)

    # ------------------------------------------------------------------

    async def get_order_book(
        self,
        symbol: str,
        limit: int = 20,
    ) -> OrderBook:
        """
        Fetch a level-2 order book snapshot.

        Parameters
        ----------
        symbol:
            Trading pair.
        limit:
            Number of price levels per side.  Valid values: 5, 10, 20,
            50, 100, 500, 1000.

        Returns
        -------
        OrderBook
        """
        path = (
            "/fapi/v1/depth"
            if self._market == "futures"
            else "/api/v3/depth"
        )
        raw = await self._get(path, {"symbol": symbol, "limit": limit}, weight=2.0)

        bids = [(Decimal(p), Decimal(q)) for p, q in raw.get("bids", [])]
        asks = [(Decimal(p), Decimal(q)) for p, q in raw.get("asks", [])]

        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=time.time(),
            source="binance",
        )

    # ------------------------------------------------------------------
    # Authenticated endpoints
    # ------------------------------------------------------------------

    async def get_account_balance(self) -> Dict[str, Any]:
        """
        Fetch account balances.

        For spot returns a list of ``{asset, free, locked}`` dicts.
        For futures returns account information including ``totalWalletBalance``.

        Returns
        -------
        dict
        """
        if self._market == "futures":
            return await self._get("/fapi/v2/account", signed=True, weight=5.0)
        return await self._get("/api/v3/account", signed=True, weight=10.0)

    # ------------------------------------------------------------------

    async def get_open_orders(
        self,
        symbol: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch all open orders, optionally filtered by *symbol*.

        Parameters
        ----------
        symbol:
            If provided, returns open orders for that symbol only.

        Returns
        -------
        List[dict]
        """
        path = (
            "/fapi/v1/openOrders"
            if self._market == "futures"
            else "/api/v3/openOrders"
        )
        params: Dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        return await self._get(path, params, signed=True, weight=3.0)

    # ------------------------------------------------------------------

    async def __aenter__(self) -> "BinanceRESTProvider":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()
