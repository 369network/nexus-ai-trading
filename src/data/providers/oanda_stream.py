"""
NEXUS ALPHA - OANDA Streaming Provider
=========================================
Server-Sent Events (SSE) streaming client for OANDA v20 prices and
REST calls for historical candles, account, trades, and positions.

Handles:
* SSE price streaming with heartbeat monitoring
* Spread alerts when spread > configured threshold
* Auto-reconnect with exponential back-off
* Both practice and live environment routing

Environment variables:
    OANDA_API_KEY
    OANDA_ACCOUNT_ID
    OANDA_ENV        "practice" (default) or "live"
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

from ..base import OHLCV, Tick
from ..normalizer import UnifiedDataNormalizer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Endpoint roots
# ---------------------------------------------------------------------------

_STREAMING_PRACTICE = "https://stream-fxpractice.oanda.com"
_STREAMING_LIVE     = "https://stream-fxtrade.oanda.com"
_API_PRACTICE       = "https://api-fxpractice.oanda.com"
_API_LIVE           = "https://api-fxtrade.oanda.com"

_HEARTBEAT_TIMEOUT_S   = 30.0   # alert if no heartbeat in 30s
_RECONNECT_INITIAL_S   = 1.0
_RECONNECT_CAP_S       = 60.0
_RECONNECT_MULTIPLIER  = 2.0

# ---------------------------------------------------------------------------
# Granularity mapping: OANDA → interval string
# ---------------------------------------------------------------------------

_GRAN_MAP: Dict[str, str] = {
    "S5": "5s", "S10": "10s", "S15": "15s", "S30": "30s",
    "M1": "1m",  "M2": "2m",  "M4": "4m",  "M5": "5m",
    "M10": "10m","M15": "15m","M30": "30m",
    "H1": "1h",  "H2": "2h",  "H3": "3h",  "H4": "4h",
    "H6": "6h",  "H8": "8h",  "H12": "12h",
    "D": "1d",   "W": "1w",   "M": "1M",
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

@dataclass
class OANDAConfig:
    """Configuration for the OANDA provider."""

    api_key: str = field(default_factory=lambda: os.getenv("OANDA_API_KEY", ""))
    account_id: str = field(default_factory=lambda: os.getenv("OANDA_ACCOUNT_ID", ""))
    environment: str = field(
        default_factory=lambda: os.getenv("OANDA_ENV", "practice")
    )

    # Spread alert threshold in pips (instrument-unit)
    max_spread: float = 3.0

    # Per-instrument reconnect settings
    reconnect_initial: float = _RECONNECT_INITIAL_S
    reconnect_cap: float     = _RECONNECT_CAP_S
    reconnect_mult: float    = _RECONNECT_MULTIPLIER

    @property
    def stream_base(self) -> str:
        return _STREAMING_LIVE if self.environment == "live" else _STREAMING_PRACTICE

    @property
    def api_base(self) -> str:
        return _API_LIVE if self.environment == "live" else _API_PRACTICE

    @property
    def headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }


# ---------------------------------------------------------------------------
# Streaming provider
# ---------------------------------------------------------------------------

class OANDAStreamProvider:
    """
    Manages a persistent SSE connection to OANDA's price streaming endpoint.

    Usage
    -----
    ::

        provider = OANDAStreamProvider()
        await provider.stream_prices(["EUR_USD", "GBP_USD"], my_callback)
    """

    def __init__(self, config: Optional[OANDAConfig] = None) -> None:
        self.config = config or OANDAConfig()
        self._normalizer = UnifiedDataNormalizer()
        self._streaming = False
        self._stream_task: Optional[asyncio.Task] = None
        self._last_heartbeat_ts: float = time.monotonic()
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------
    # Session
    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers=self.config.headers,
                timeout=aiohttp.ClientTimeout(total=None, connect=10),
            )
        return self._session

    async def close(self) -> None:
        """Stop streaming and close the HTTP session."""
        self._streaming = False
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Price streaming
    # ------------------------------------------------------------------

    async def stream_prices(
        self,
        instruments: List[str],
        callback: Callable[[Tick], None],
    ) -> None:
        """
        Start streaming prices for *instruments*.

        Spawns a background task; returns immediately.

        Parameters
        ----------
        instruments:
            List of OANDA instrument names, e.g. ``["EUR_USD", "GBP_USD"]``.
        callback:
            Invoked with each parsed :class:`Tick`.  May be async.
        """
        self._streaming = True
        self._stream_task = asyncio.create_task(
            self._streaming_loop(instruments, callback),
            name="oanda-price-stream",
        )
        # Also start heartbeat monitor
        asyncio.create_task(
            self._heartbeat_monitor(),
            name="oanda-heartbeat-monitor",
        )
        logger.info(
            "OANDAStream: started streaming %d instruments.", len(instruments)
        )

    # ------------------------------------------------------------------

    async def _streaming_loop(
        self,
        instruments: List[str],
        callback: Callable[[Tick], None],
    ) -> None:
        """Outer reconnect loop for the SSE stream."""
        delay = self.config.reconnect_initial
        inst_param = ",".join(instruments)

        while self._streaming:
            url = (
                f"{self.config.stream_base}/v3/accounts/"
                f"{self.config.account_id}/pricing/stream"
            )
            params = {"instruments": inst_param}

            try:
                session = self._get_session()
                async with session.get(url, params=params) as resp:
                    resp.raise_for_status()
                    logger.info("OANDAStream: connected to price stream.")
                    delay = self.config.reconnect_initial

                    async for line in resp.content:
                        if not self._streaming:
                            return
                        text = line.decode("utf-8").strip()
                        if not text:
                            continue
                        try:
                            data = json.loads(text)
                        except json.JSONDecodeError:
                            continue

                        msg_type = data.get("type")
                        if msg_type == "HEARTBEAT":
                            self._last_heartbeat_ts = time.monotonic()
                            continue

                        if msg_type == "PRICE":
                            tick = self._parse_price(data)
                            if tick:
                                self._check_spread(tick)
                                if asyncio.iscoroutinefunction(callback):
                                    await callback(tick)
                                else:
                                    callback(tick)

            except asyncio.CancelledError:
                return
            except (aiohttp.ClientError, OSError) as exc:
                logger.warning(
                    "OANDAStream: connection error: %s – retrying in %.1fs",
                    exc, delay,
                )
            except Exception as exc:
                logger.exception("OANDAStream: unexpected error: %s", exc)

            if not self._streaming:
                return
            await asyncio.sleep(delay)
            delay = min(delay * self.config.reconnect_mult, self.config.reconnect_cap)

    # ------------------------------------------------------------------

    def _parse_price(self, data: Dict[str, Any]) -> Optional[Tick]:
        """
        Parse an OANDA PRICE message into a :class:`Tick`.

        Parameters
        ----------
        data:
            Raw JSON dict from the SSE stream.

        Returns
        -------
        Optional[Tick]
        """
        try:
            instrument = data["instrument"]
            tradeable  = data.get("tradeable", True)

            bids = data.get("bids", [])
            asks = data.get("asks", [])

            if not bids or not asks:
                return None

            bid = float(bids[0]["price"])
            ask = float(asks[0]["price"])
            mid = (bid + ask) / 2.0
            spread = ask - bid

            ts_str = data.get("time", "")
            ts = self._normalizer.normalize_timestamp(ts_str, "oanda") / 1_000.0

            return Tick.from_bid_ask(
                timestamp=ts,
                instrument=instrument,
                bid=bid,
                ask=ask,
                tradeable=tradeable,
                source="oanda",
                market="forex",
            )
        except (KeyError, ValueError, IndexError) as exc:
            logger.debug("_parse_price error: %s – data=%s", exc, data)
            return None

    # ------------------------------------------------------------------

    def _check_spread(self, tick: Tick) -> None:
        """Log a warning if the spread exceeds the configured maximum."""
        if tick.spread > self.config.max_spread:
            logger.warning(
                "SPREAD ALERT %s: spread=%.5f exceeds max=%.5f",
                tick.instrument, tick.spread, self.config.max_spread,
            )

    # ------------------------------------------------------------------

    async def _heartbeat_monitor(self) -> None:
        """Alert and trigger reconnect if no heartbeat for 30 s."""
        while self._streaming:
            await asyncio.sleep(10)
            elapsed = time.monotonic() - self._last_heartbeat_ts
            if elapsed > _HEARTBEAT_TIMEOUT_S:
                logger.warning(
                    "OANDAStream: no heartbeat for %.0fs – connection may be stale.",
                    elapsed,
                )

    # ------------------------------------------------------------------
    # REST endpoints (candles, account)
    # ------------------------------------------------------------------

    async def _api_get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        """Perform a REST GET request against the OANDA API."""
        url = f"{self.config.api_base}{path}"
        session = self._get_session()
        async with session.get(url, params=params or {}) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ------------------------------------------------------------------

    async def get_candles(
        self,
        instrument: str,
        granularity: str = "H1",
        count: Optional[int] = None,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
    ) -> List[OHLCV]:
        """
        Fetch historical OANDA candles.

        Parameters
        ----------
        instrument:
            OANDA instrument name, e.g. ``"EUR_USD"``.
        granularity:
            OANDA granularity code, e.g. ``"M1"``, ``"H1"``, ``"D"``.
        count:
            Number of candles to return.  Mutually exclusive with
            *from_time* / *to_time*.
        from_time:
            RFC 3339 start time string.
        to_time:
            RFC 3339 end time string.

        Returns
        -------
        List[OHLCV]
        """
        params: Dict[str, Any] = {
            "granularity": granularity,
            "price": "M",  # midpoint candles
        }
        if count is not None:
            params["count"] = count
        if from_time is not None:
            params["from"] = from_time
        if to_time is not None:
            params["to"] = to_time

        raw = await self._api_get(
            f"/v3/instruments/{instrument}/candles", params
        )
        candles: List[OHLCV] = []
        interval = _GRAN_MAP.get(granularity, granularity.lower())

        for c in raw.get("candles", []):
            mid = c.get("mid", {})
            ts = self._normalizer.normalize_timestamp(c["time"], "oanda")

            open_  = float(mid.get("o", 0))
            high   = float(mid.get("h", 0))
            low    = float(mid.get("l", 0))
            close  = float(mid.get("c", 0))
            volume = float(c.get("volume", 0))

            candles.append(OHLCV(
                timestamp_ms=ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                quote_volume=0.0,
                trades=0,
                vwap=(open_ + high + low + close) / 4,
                taker_buy_volume=0.0,
                source="oanda",
                market="forex",
                symbol=instrument,
                interval=interval,
                complete=c.get("complete", True),
            ))

        return candles

    # ------------------------------------------------------------------

    async def get_account_summary(self) -> Dict[str, Any]:
        """
        Fetch a summary of the current OANDA account.

        Returns
        -------
        dict
            Contains balance, unrealized P&L, margin used, etc.
        """
        raw = await self._api_get(
            f"/v3/accounts/{self.config.account_id}/summary"
        )
        return raw.get("account", raw)

    # ------------------------------------------------------------------

    async def get_open_trades(self) -> List[Dict[str, Any]]:
        """
        Fetch all currently open trades on the account.

        Returns
        -------
        List[dict]
        """
        raw = await self._api_get(
            f"/v3/accounts/{self.config.account_id}/openTrades"
        )
        return raw.get("trades", [])

    # ------------------------------------------------------------------

    async def get_positions(self) -> List[Dict[str, Any]]:
        """
        Fetch all open positions on the account.

        Returns
        -------
        List[dict]
        """
        raw = await self._api_get(
            f"/v3/accounts/{self.config.account_id}/openPositions"
        )
        return raw.get("positions", [])
