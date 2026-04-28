"""
WebSocket connector factory — returns the right WS connector for each market.

For paper trading we use a polling-based connector that periodically
fetches OHLCV candles via REST and emits them as candle events.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Any, Callable, Coroutine, Optional

logger = logging.getLogger(__name__)


def get_ws_connector_for_market(
    market: str,
    symbol: str,
    settings: Any,
    on_candle: Callable,
    timeframes: Optional[list] = None,
) -> "BaseWSConnector":
    """
    Factory: return the best available WebSocket connector.
    Falls back to REST-polling connector for paper mode.

    Parameters
    ----------
    timeframes:
        Timeframes to poll (default: ``["1h"]``).  Pass the full list from
        :class:`MarketConfig` so all configured intervals receive data.
    """
    market = market.lower()
    paper_mode = getattr(settings, "paper_mode", True)
    _tfs = timeframes or ["1h"]

    if paper_mode:
        # In paper mode, use polling REST connector — no real WS needed
        return PollingConnector(
            market=market,
            symbol=symbol,
            settings=settings,
            on_candle=on_candle,
            poll_interval_seconds=60,  # poll every 60 s
            timeframes=_tfs,
        )

    try:
        if market == "crypto":
            from src.data.providers.binance_ws import BinanceWSConnector
            return BinanceWSConnector(
                symbol=symbol,
                settings=settings,
                on_candle=on_candle,
            )
        else:
            return PollingConnector(
                market=market, symbol=symbol,
                settings=settings, on_candle=on_candle,
                timeframes=_tfs,
            )
    except Exception as exc:
        logger.warning("WS connector failed (%s) — using polling fallback", exc)
        return PollingConnector(
            market=market, symbol=symbol,
            settings=settings, on_candle=on_candle,
            timeframes=_tfs,
        )


class BaseWSConnector:
    async def run(self) -> None: ...
    async def close(self) -> None: ...


class PollingConnector(BaseWSConnector):
    """
    Simulates live candle streaming by polling the REST provider.
    Polls every `poll_interval_seconds` for all configured timeframes
    and emits the latest closed candle for each one.
    """

    def __init__(
        self,
        market: str,
        symbol: str,
        settings: Any,
        on_candle: Callable,
        poll_interval_seconds: int = 60,
        timeframes: Optional[list] = None,
    ) -> None:
        self.market = market
        self.symbol = symbol
        self.settings = settings
        self._on_candle = on_candle
        self._poll_interval = poll_interval_seconds
        self._timeframes: list = timeframes or ["1h"]
        self._running = False
        # last seen timestamp per timeframe
        self._last_ts: dict = {}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_candle_dict(raw: Any) -> dict:
        if isinstance(raw, dict):
            return raw
        return {
            "timestamp": raw[0],
            "open":  raw[1],
            "high":  raw[2],
            "low":   raw[3],
            "close": raw[4],
            "volume": raw[5],
        }

    async def run(self) -> None:
        self._running = True
        logger.info(
            "[%s/%s] PollingConnector started (interval=%ds, timeframes=%s)",
            self.market, self.symbol, self._poll_interval, self._timeframes,
        )

        from src.data.providers import get_provider_for_market
        provider = get_provider_for_market(
            market=self.market,
            symbol=self.symbol,
            settings=self.settings,
        )

        while self._running:
            for tf in self._timeframes:
                try:
                    candles = await provider.fetch_ohlcv(timeframe=tf, limit=3)
                    if candles:
                        raw = candles[-1]
                        latest: dict = self._to_candle_dict(raw)
                        ts = latest.get("timestamp", 0)
                        # Always emit so wall-clock freshness is refreshed
                        # every poll cycle even when no new candle closed.
                        is_new = ts != self._last_ts.get(tf)
                        self._last_ts[tf] = ts
                        await self._on_candle(
                            symbol=self.symbol,
                            timeframe=tf,
                            raw_candle=latest,
                            is_closed=is_new,  # True only when a new candle closed
                        )
                        logger.debug(
                            "[%s/%s/%s] Polled candle ts=%s close=%s new=%s",
                            self.market, self.symbol, tf,
                            ts, latest.get("close"), is_new,
                        )
                except asyncio.CancelledError:
                    return
                except Exception as exc:
                    logger.debug(
                        "[%s/%s/%s] Polling error: %s",
                        self.market, self.symbol, tf, exc,
                    )

            try:
                await asyncio.sleep(self._poll_interval)
            except asyncio.CancelledError:
                break

        logger.info("[%s/%s] PollingConnector stopped.", self.market, self.symbol)

    async def close(self) -> None:
        self._running = False
