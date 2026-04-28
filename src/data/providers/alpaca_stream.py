"""
NEXUS ALPHA - Alpaca WebSocket Stream Provider
================================================
Real-time bar, trade, and quote streaming for US equities and crypto
via the Alpaca Markets WebSocket API v2.

Supports:
* IEX (free, 15-min delayed) and SIP (paid, real-time) data feeds
* Auto-reconnect with credentials re-auth on reconnect
* Async callback dispatch for bars, trades, quotes
* Historical bars via REST fallback

Environment variables:
    ALPACA_API_KEY
    ALPACA_API_SECRET
    ALPACA_DATA_FEED    "iex" (default) or "sip"
    ALPACA_PAPER        "true" for paper trading account
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import aiohttp
import websockets
from websockets.exceptions import ConnectionClosed, WebSocketException

from ..base import OHLCV
from ..normalizer import UnifiedDataNormalizer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

_WS_STOCK_IEX   = "wss://stream.data.alpaca.markets/v2/iex"
_WS_STOCK_SIP   = "wss://stream.data.alpaca.markets/v2/sip"
_WS_CRYPTO      = "wss://stream.data.alpaca.markets/v1beta3/crypto/us"
_REST_DATA_BASE  = "https://data.alpaca.markets"
_REST_PAPER_BASE = "https://paper-api.alpaca.markets"
_REST_LIVE_BASE  = "https://api.alpaca.markets"

_RECONNECT_INITIAL = 1.0
_RECONNECT_CAP     = 60.0
_RECONNECT_MULT    = 2.0


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class AlpacaConfig:
    """Configuration for the Alpaca streaming provider."""

    api_key:    str = field(default_factory=lambda: os.getenv("ALPACA_API_KEY",    ""))
    api_secret: str = field(default_factory=lambda: os.getenv("ALPACA_API_SECRET", ""))
    data_feed:  str = field(default_factory=lambda: os.getenv("ALPACA_DATA_FEED",  "iex"))
    paper:      bool = field(
        default_factory=lambda: os.getenv("ALPACA_PAPER", "true").lower() == "true"
    )


# ---------------------------------------------------------------------------
# Streaming provider
# ---------------------------------------------------------------------------

class AlpacaProvider:
    """
    Alpaca real-time market data provider.

    Streams bars, trades, and quotes over WebSocket and exposes REST
    endpoints for historical bars and account information.
    """

    def __init__(self, config: Optional[AlpacaConfig] = None) -> None:
        self.config = config or AlpacaConfig()
        self._normalizer = UnifiedDataNormalizer()

        # Callback registries keyed by symbol
        self._bar_callbacks:   Dict[str, List[Callable]] = {}
        self._trade_callbacks: Dict[str, List[Callable]] = {}
        self._quote_callbacks: Dict[str, List[Callable]] = {}

        # Running WS tasks
        self._ws_task: Optional[asyncio.Task] = None
        self._running = False
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    def _get_http_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "APCA-API-KEY-ID":     self.config.api_key,
                    "APCA-API-SECRET-KEY": self.config.api_secret,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        """Stop streaming and close connections."""
        self._running = False
        if self._ws_task and not self._ws_task.done():
            self._ws_task.cancel()
            try:
                await self._ws_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Streaming setup
    # ------------------------------------------------------------------

    def _ws_url(self, asset_class: str = "stock") -> str:
        if asset_class == "crypto":
            return _WS_CRYPTO
        return _WS_STOCK_SIP if self.config.data_feed == "sip" else _WS_STOCK_IEX

    async def _start_stream(
        self,
        symbols: List[str],
        subscribe_msg: Dict[str, Any],
        asset_class: str = "stock",
    ) -> None:
        """Start a WebSocket stream and subscribe with *subscribe_msg*."""
        self._running = True
        url = self._ws_url(asset_class)
        self._ws_task = asyncio.create_task(
            self._ws_loop(url, subscribe_msg),
            name=f"alpaca-ws-{asset_class}",
        )

    async def _ws_loop(
        self,
        url: str,
        subscribe_msg: Dict[str, Any],
    ) -> None:
        """Outer reconnect loop for the Alpaca WebSocket."""
        delay = _RECONNECT_INITIAL

        while self._running:
            try:
                async with websockets.connect(url) as ws:
                    delay = _RECONNECT_INITIAL
                    logger.info("AlpacaWS: connected to %s", url)

                    # Auth
                    await ws.send(json.dumps({
                        "action": "auth",
                        "key":    self.config.api_key,
                        "secret": self.config.api_secret,
                    }))
                    auth_resp = json.loads(await ws.recv())
                    if not any(
                        m.get("T") == "success" for m in (
                            auth_resp if isinstance(auth_resp, list) else [auth_resp]
                        )
                    ):
                        logger.warning("AlpacaWS auth response: %s", auth_resp)

                    # Subscribe
                    await ws.send(json.dumps(subscribe_msg))

                    async for raw in ws:
                        if not self._running:
                            return
                        try:
                            msgs = json.loads(raw)
                            if not isinstance(msgs, list):
                                msgs = [msgs]
                            for msg in msgs:
                                self._dispatch_message(msg)
                        except json.JSONDecodeError:
                            continue

            except asyncio.CancelledError:
                return
            except (ConnectionClosed, WebSocketException, OSError) as exc:
                logger.warning(
                    "AlpacaWS: disconnected: %s – retrying in %.1fs", exc, delay
                )
            except Exception as exc:
                logger.exception("AlpacaWS: unexpected error: %s", exc)

            if not self._running:
                return
            await asyncio.sleep(delay)
            delay = min(delay * _RECONNECT_MULT, _RECONNECT_CAP)

    # ------------------------------------------------------------------

    def _dispatch_message(self, msg: Dict[str, Any]) -> None:
        """Fan-out an incoming WS message to the correct callbacks."""
        msg_type = msg.get("T")

        if msg_type == "b":   # bar
            symbol = msg.get("S", "")
            candle = self._parse_bar(msg)
            for cb in self._bar_callbacks.get(symbol, []):
                self._invoke_callback(cb, candle)

        elif msg_type == "t":  # trade
            symbol = msg.get("S", "")
            for cb in self._trade_callbacks.get(symbol, []):
                self._invoke_callback(cb, msg)

        elif msg_type == "q":  # quote
            symbol = msg.get("S", "")
            for cb in self._quote_callbacks.get(symbol, []):
                self._invoke_callback(cb, msg)

    def _invoke_callback(self, cb: Callable, data: Any) -> None:
        try:
            if asyncio.iscoroutinefunction(cb):
                asyncio.create_task(cb(data))
            else:
                cb(data)
        except Exception as exc:
            logger.exception("AlpacaWS callback error: %s", exc)

    # ------------------------------------------------------------------
    # Bar parsing
    # ------------------------------------------------------------------

    def _parse_bar(self, msg: Dict[str, Any]) -> OHLCV:
        """Parse an Alpaca bar message into an OHLCV."""
        ts = self._normalizer.normalize_timestamp(msg.get("t", ""), "alpaca")
        return OHLCV(
            timestamp_ms     = ts,
            open             = float(msg.get("o", 0)),
            high             = float(msg.get("h", 0)),
            low              = float(msg.get("l", 0)),
            close            = float(msg.get("c", 0)),
            volume           = float(msg.get("v", 0)),
            quote_volume     = float(msg.get("av", 0)),  # accumulated volume
            trades           = int(msg.get("n", 0)),
            vwap             = float(msg.get("vw", 0)),
            taker_buy_volume = 0.0,
            source           = "alpaca",
            market           = "equity",
            symbol           = msg.get("S", ""),
            interval         = "1m",
            complete         = True,
        )

    # ------------------------------------------------------------------
    # Public streaming API
    # ------------------------------------------------------------------

    async def stream_bars(
        self,
        symbols: List[str],
        callback: Callable[[OHLCV], None],
    ) -> None:
        """
        Subscribe to real-time 1-minute bar updates for *symbols*.

        Parameters
        ----------
        symbols:
            List of equity tickers, e.g. ``["AAPL", "TSLA"]``.
        callback:
            Called with each :class:`OHLCV` as it closes.
        """
        for sym in symbols:
            self._bar_callbacks.setdefault(sym, []).append(callback)
        await self._start_stream(
            symbols,
            {"action": "subscribe", "bars": symbols},
        )

    # ------------------------------------------------------------------

    async def stream_trades(
        self,
        symbols: List[str],
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """
        Subscribe to real-time trade tick messages for *symbols*.

        Parameters
        ----------
        symbols:
            List of equity tickers.
        callback:
            Called with each raw trade dict.
        """
        for sym in symbols:
            self._trade_callbacks.setdefault(sym, []).append(callback)
        await self._start_stream(
            symbols,
            {"action": "subscribe", "trades": symbols},
        )

    # ------------------------------------------------------------------

    async def stream_quotes(
        self,
        symbols: List[str],
        callback: Callable[[Dict[str, Any]], None],
    ) -> None:
        """
        Subscribe to real-time NBBO quote updates for *symbols*.

        Parameters
        ----------
        symbols:
            List of equity tickers.
        callback:
            Called with each raw quote dict containing bid/ask.
        """
        for sym in symbols:
            self._quote_callbacks.setdefault(sym, []).append(callback)
        await self._start_stream(
            symbols,
            {"action": "subscribe", "quotes": symbols},
        )

    # ------------------------------------------------------------------
    # Historical bars via REST
    # ------------------------------------------------------------------

    async def get_bars(
        self,
        symbol: str,
        timeframe: str = "1Min",
        start: Optional[str] = None,
        end: Optional[str] = None,
        limit: int = 1000,
    ) -> List[OHLCV]:
        """
        Fetch historical bars for *symbol*.

        Parameters
        ----------
        symbol:
            Equity ticker.
        timeframe:
            ``"1Min"``, ``"5Min"``, ``"15Min"``, ``"30Min"``,
            ``"1Hour"``, ``"1Day"``.
        start:
            ISO-8601 start datetime string.
        end:
            ISO-8601 end datetime string.
        limit:
            Maximum bars per page (max 10 000).

        Returns
        -------
        List[OHLCV]
        """
        session = self._get_http_session()
        params: Dict[str, Any] = {
            "timeframe": timeframe,
            "limit":     min(limit, 10_000),
            "feed":      self.config.data_feed,
        }
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        url = f"{_REST_DATA_BASE}/v2/stocks/{symbol}/bars"
        candles: List[OHLCV] = []

        while True:
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            for bar in data.get("bars", []):
                ts = self._normalizer.normalize_timestamp(bar["t"], "alpaca")
                candles.append(OHLCV(
                    timestamp_ms     = ts,
                    open             = float(bar["o"]),
                    high             = float(bar["h"]),
                    low              = float(bar["l"]),
                    close            = float(bar["c"]),
                    volume           = float(bar["v"]),
                    quote_volume     = float(bar.get("vw", 0)) * float(bar["v"]),
                    trades           = int(bar.get("n", 0)),
                    vwap             = float(bar.get("vw", 0)),
                    taker_buy_volume = 0.0,
                    source           = "alpaca",
                    market           = "equity",
                    symbol           = symbol,
                    interval         = timeframe,
                    complete         = True,
                ))

            next_token = data.get("next_page_token")
            if not next_token or len(candles) >= limit:
                break
            params["page_token"] = next_token

        return candles[:limit]

    # ------------------------------------------------------------------

    async def get_latest_bar(self, symbol: str) -> Optional[OHLCV]:
        """
        Fetch the most recent completed bar for *symbol*.

        Returns
        -------
        OHLCV or None
        """
        session = self._get_http_session()
        url = f"{_REST_DATA_BASE}/v2/stocks/{symbol}/bars/latest"
        async with session.get(url, params={"feed": self.config.data_feed}) as resp:
            resp.raise_for_status()
            data = await resp.json()

        bar = data.get("bar")
        if not bar:
            return None

        ts = self._normalizer.normalize_timestamp(bar["t"], "alpaca")
        return OHLCV(
            timestamp_ms=ts,
            open=float(bar["o"]),
            high=float(bar["h"]),
            low=float(bar["l"]),
            close=float(bar["c"]),
            volume=float(bar["v"]),
            quote_volume=float(bar.get("vw", 0)) * float(bar["v"]),
            trades=int(bar.get("n", 0)),
            vwap=float(bar.get("vw", 0)),
            taker_buy_volume=0.0,
            source="alpaca",
            market="equity",
            symbol=symbol,
            interval="1Min",
            complete=True,
        )

    # ------------------------------------------------------------------

    async def get_account(self) -> Dict[str, Any]:
        """
        Fetch Alpaca account details (balance, buying power, equity).

        Returns
        -------
        dict
        """
        base = _REST_PAPER_BASE if self.config.paper else _REST_LIVE_BASE
        session = self._get_http_session()
        async with session.get(f"{base}/v2/account") as resp:
            resp.raise_for_status()
            return await resp.json()
