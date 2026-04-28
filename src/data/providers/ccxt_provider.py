"""
NEXUS ALPHA - CCXT Multi-Exchange Provider
============================================
Wraps the CCXT library to provide a uniform interface across Binance,
Bybit, OKX, Coinbase Advanced, and Kraken.

Acts as a smart fallback: if the primary exchange raises an exception,
the same method is retried on the next configured exchange.

Usage
-----
::

    cfg = CCXTConfig(exchanges=["binance", "bybit"], primary="binance")
    async with CCXTProvider(cfg) as provider:
        ohlcv = await provider.fetch_ohlcv("BTC/USDT", "1h")
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Tuple

import ccxt.async_support as ccxt

from ..base import OHLCV, OrderBook, Tick
from ..normalizer import UnifiedDataNormalizer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Supported exchanges and credential env-var prefixes
# ---------------------------------------------------------------------------

_SUPPORTED = ("binance", "bybit", "okx", "coinbasepro", "kraken")

_ENV_KEYS: Dict[str, Tuple[str, str]] = {
    "binance":     ("BINANCE_API_KEY",     "BINANCE_API_SECRET"),
    "bybit":       ("BYBIT_API_KEY",       "BYBIT_API_SECRET"),
    "okx":         ("OKX_API_KEY",         "OKX_API_SECRET"),
    "coinbasepro": ("COINBASE_API_KEY",    "COINBASE_API_SECRET"),
    "kraken":      ("KRAKEN_API_KEY",      "KRAKEN_API_SECRET"),
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class CCXTConfig:
    """Configuration for the multi-exchange CCXT provider."""

    # Ordered list of exchange IDs to use (first = primary)
    exchanges: List[str] = field(
        default_factory=lambda: ["binance", "bybit", "okx"]
    )
    primary: str = "binance"

    # Whether to use sandbox/testnet endpoints
    sandbox: bool = False

    # Per-exchange API keys (override env vars)
    credentials: Dict[str, Dict[str, str]] = field(default_factory=dict)

    # Timeout for individual exchange requests (seconds)
    request_timeout_ms: int = 30_000

    # OKX-specific passphrase
    okx_passphrase: str = ""


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class CCXTProvider:
    """
    Multi-exchange provider using CCXT async support.

    Instantiates one CCXT exchange object per configured exchange and
    tries them in order when a method is called.  The first successful
    response is returned; if all fail the last exception is raised.
    """

    def __init__(self, config: Optional[CCXTConfig] = None) -> None:
        self.config = config or CCXTConfig()
        self._normalizer = UnifiedDataNormalizer()
        self._exchanges: Dict[str, ccxt.Exchange] = {}
        self._initialised = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def _init_exchanges(self) -> None:
        if self._initialised:
            return
        for ex_id in self.config.exchanges:
            if ex_id not in _SUPPORTED:
                logger.warning("Unsupported exchange %r – skipping.", ex_id)
                continue
            try:
                self._exchanges[ex_id] = await self._build_exchange(ex_id)
            except Exception as exc:
                logger.error("Failed to init exchange %r: %s", ex_id, exc)
        self._initialised = True

    async def _build_exchange(self, ex_id: str) -> ccxt.Exchange:
        """Instantiate and load markets for a CCXT exchange."""
        cls = getattr(ccxt, ex_id, None)
        if cls is None:
            raise ValueError(f"CCXT has no exchange class for {ex_id!r}")

        # Credentials from config or env
        creds = self.config.credentials.get(ex_id, {})
        key_var, sec_var = _ENV_KEYS.get(ex_id, ("", ""))
        api_key    = creds.get("apiKey",    os.getenv(key_var, ""))
        api_secret = creds.get("secret",    os.getenv(sec_var, ""))

        init_kwargs: Dict[str, Any] = {
            "apiKey": api_key,
            "secret": api_secret,
            "timeout": self.config.request_timeout_ms,
            "enableRateLimit": True,
        }

        if ex_id == "okx" and self.config.okx_passphrase:
            init_kwargs["password"] = self.config.okx_passphrase

        exchange: ccxt.Exchange = cls(init_kwargs)

        if self.config.sandbox and exchange.urls.get("test"):
            exchange.set_sandbox_mode(True)

        await exchange.load_markets()
        return exchange

    async def close(self) -> None:
        """Close all underlying exchange connections."""
        for exchange in self._exchanges.values():
            try:
                await exchange.close()
            except Exception:
                pass
        self._exchanges.clear()
        # Reset initialised flag so a re-used instance can re-init on next call.
        self._initialised = False

    async def __aenter__(self) -> "CCXTProvider":
        await self._init_exchanges()
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Internal retry helper
    # ------------------------------------------------------------------

    async def _try_exchanges(self, method: str, *args, **kwargs) -> Any:
        """
        Call *method* on exchanges in priority order.

        Tries the primary exchange first, then falls back through the
        remaining configured exchanges.  Raises the last exception if
        all fail.
        """
        await self._init_exchanges()

        ordered = [self.config.primary] + [
            e for e in self.config.exchanges if e != self.config.primary
        ]
        last_exc: Optional[Exception] = None

        for ex_id in ordered:
            exchange = self._exchanges.get(ex_id)
            if exchange is None:
                continue
            try:
                fn = getattr(exchange, method)
                result = await fn(*args, **kwargs)
                return result
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as exc:
                logger.warning(
                    "Exchange %r failed for %s: %s – trying next.",
                    ex_id, method, exc,
                )
                last_exc = exc
            except ccxt.ExchangeError as exc:
                logger.warning(
                    "Exchange %r error for %s: %s – trying next.",
                    ex_id, method, exc,
                )
                last_exc = exc

        raise last_exc or RuntimeError("All exchanges failed")

    # ------------------------------------------------------------------
    # Market data
    # ------------------------------------------------------------------

    async def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        since: Optional[int] = None,
        limit: int = 500,
    ) -> List[OHLCV]:
        """
        Fetch OHLCV candles for *symbol*.

        Parameters
        ----------
        symbol:
            CCXT unified symbol, e.g. ``"BTC/USDT"``.
        timeframe:
            Timeframe string, e.g. ``"1m"``, ``"1h"``.
        since:
            Start time as UTC milliseconds.
        limit:
            Maximum candles.

        Returns
        -------
        List[OHLCV]
        """
        params: Dict[str, Any] = {}
        raw = await self._try_exchanges(
            "fetch_ohlcv", symbol, timeframe, since, limit, params
        )
        # CCXT returns [[timestamp, o, h, l, c, vol], ...]
        result: List[OHLCV] = []
        for row in raw:
            ts, o, h, l, c, v = row[:6]
            candle = OHLCV(
                timestamp_ms=int(ts),
                open=float(o),
                high=float(h),
                low=float(l),
                close=float(c),
                volume=float(v),
                quote_volume=float(v) * float(c),  # approximation
                trades=0,
                vwap=(float(o) + float(h) + float(l) + float(c)) / 4,
                taker_buy_volume=0.0,
                source="ccxt",
                market="spot",
                symbol=symbol,
                interval=timeframe,
                complete=True,
            )
            result.append(candle)
        return result

    # ------------------------------------------------------------------

    async def fetch_order_book(
        self,
        symbol: str,
        limit: int = 20,
    ) -> OrderBook:
        """
        Fetch a level-2 order book snapshot.

        Returns
        -------
        OrderBook
        """
        raw = await self._try_exchanges("fetch_order_book", symbol, limit)
        bids = [(Decimal(str(p)), Decimal(str(q))) for p, q in raw["bids"]]
        asks = [(Decimal(str(p)), Decimal(str(q))) for p, q in raw["asks"]]
        return OrderBook(
            symbol=symbol,
            bids=bids,
            asks=asks,
            timestamp=raw.get("timestamp", time.time() * 1_000) / 1_000.0,
            source="ccxt",
        )

    # ------------------------------------------------------------------

    async def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch a 24-hour ticker snapshot for *symbol*.

        Returns
        -------
        dict
            CCXT unified ticker.
        """
        return await self._try_exchanges("fetch_ticker", symbol)

    # ------------------------------------------------------------------

    async def fetch_balance(self) -> Dict[str, Any]:
        """
        Fetch account balances across all asset types.

        Returns
        -------
        dict
            CCXT unified balance dict.
        """
        return await self._try_exchanges("fetch_balance")

    # ------------------------------------------------------------------

    async def fetch_positions(
        self,
        symbols: Optional[List[str]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch open derivative positions.

        Returns
        -------
        List[dict]
            CCXT unified position list.
        """
        return await self._try_exchanges("fetch_positions", symbols)

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: Optional[float] = None,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Place an order on the primary exchange.

        Parameters
        ----------
        symbol:
            CCXT unified symbol.
        order_type:
            ``"market"`` or ``"limit"``.
        side:
            ``"buy"`` or ``"sell"``.
        amount:
            Base asset quantity.
        price:
            Limit price (required for limit orders).
        params:
            Exchange-specific extra parameters.

        Returns
        -------
        dict
            CCXT unified order dict.
        """
        return await self._try_exchanges(
            "create_order", symbol, order_type, side, amount, price, params or {}
        )

    # ------------------------------------------------------------------

    async def cancel_order(
        self,
        order_id: str,
        symbol: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Cancel an open order by ID."""
        return await self._try_exchanges(
            "cancel_order", order_id, symbol, params or {}
        )

    # ------------------------------------------------------------------

    async def fetch_order(
        self,
        order_id: str,
        symbol: str,
    ) -> Dict[str, Any]:
        """Fetch a single order by ID."""
        return await self._try_exchanges("fetch_order", order_id, symbol)
