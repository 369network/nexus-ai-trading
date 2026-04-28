"""
NEXUS ALPHA - yFinance Provider
==================================
Async wrapper around the yfinance library for historical OHLCV data,
company fundamentals, and options chain data.

All yfinance calls are synchronous; they are wrapped in
``asyncio.get_event_loop().run_in_executor`` to avoid blocking.

Environment variables: None required (public data).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..base import OHLCV
from ..normalizer import UnifiedDataNormalizer

logger = logging.getLogger(__name__)

# Mapping of yfinance interval strings to nexus interval strings
_YF_INTERVAL_MAP: Dict[str, str] = {
    "1m":  "1m",  "2m":  "2m",  "5m":  "5m",
    "15m": "15m", "30m": "30m", "60m": "1h",
    "90m": "90m", "1h":  "1h",  "1d":  "1d",
    "5d":  "5d",  "1wk": "1w",  "1mo": "1M",
    "3mo": "3M",
}


class YFinanceProvider:
    """
    Yahoo Finance data provider.

    Wraps the synchronous yfinance library in an asyncio-compatible
    interface using ``run_in_executor``.
    """

    def __init__(self) -> None:
        self._normalizer = UnifiedDataNormalizer()

    async def _run_sync(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    # ------------------------------------------------------------------

    async def get_historical(
        self,
        symbol: str,
        period: str = "1mo",
        interval: str = "1d",
        start: Optional[str] = None,
        end: Optional[str] = None,
        auto_adjust: bool = True,
    ) -> List[OHLCV]:
        """
        Fetch historical OHLCV data for *symbol*.

        Parameters
        ----------
        symbol:
            Yahoo Finance ticker, e.g. ``"AAPL"``, ``"^NSEI"`` (Nifty 50),
            ``"BTC-USD"``.
        period:
            Data period: ``"1d"``, ``"5d"``, ``"1mo"``, ``"3mo"``,
            ``"6mo"``, ``"1y"``, ``"2y"``, ``"5y"``, ``"10y"``,
            ``"ytd"``, ``"max"``.  Ignored if *start* is given.
        interval:
            Candle interval.  Fine granularity (< ``"1d"``) is only
            available for recent history (60 days for 1m).
        start:
            Start date string ``"YYYY-MM-DD"``.
        end:
            End date string ``"YYYY-MM-DD"``.
        auto_adjust:
            Adjust OHLCV for splits and dividends.

        Returns
        -------
        List[OHLCV]
        """
        import yfinance as yf

        def _fetch():
            ticker = yf.Ticker(symbol)
            kwargs: Dict[str, Any] = {
                "interval":    interval,
                "auto_adjust": auto_adjust,
                "progress":    False,
            }
            if start:
                kwargs["start"] = start
                if end:
                    kwargs["end"] = end
            else:
                kwargs["period"] = period
            return ticker.history(**kwargs)

        df = await self._run_sync(_fetch)

        if df is None or df.empty:
            return []

        candles: List[OHLCV] = []
        nexus_interval = _YF_INTERVAL_MAP.get(interval, interval)

        for idx, row in df.iterrows():
            # idx is a Timestamp; convert to UTC ms
            if hasattr(idx, "timestamp"):
                ts_ms = int(idx.timestamp() * 1_000)
            else:
                ts_ms = self._normalizer.normalize_timestamp(str(idx), "yfinance")

            volume = float(row.get("Volume", 0) or 0)
            close  = float(row["Close"])
            open_  = float(row["Open"])
            high   = float(row["High"])
            low    = float(row["Low"])

            candles.append(OHLCV(
                timestamp_ms     = ts_ms,
                open             = open_,
                high             = high,
                low              = low,
                close            = close,
                volume           = volume,
                quote_volume     = volume * close,
                trades           = 0,
                vwap             = (open_ + high + low + close) / 4,
                taker_buy_volume = 0.0,
                source           = "yfinance",
                market           = "equity",
                symbol           = symbol,
                interval         = nexus_interval,
                complete         = True,
            ))

        return candles

    # ------------------------------------------------------------------

    async def get_info(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch company fundamentals and metadata.

        Returns a dict with keys such as:
        ``sector``, ``industry``, ``marketCap``, ``trailingPE``,
        ``forwardPE``, ``priceToBook``, ``dividendYield``,
        ``fiftyTwoWeekHigh``, ``fiftyTwoWeekLow``, ``beta``,
        ``fullTimeEmployees``, ``longBusinessSummary``, etc.

        Parameters
        ----------
        symbol:
            Yahoo Finance ticker.

        Returns
        -------
        dict
        """
        import yfinance as yf

        def _fetch():
            ticker = yf.Ticker(symbol)
            return ticker.info

        return await self._run_sync(_fetch)

    # ------------------------------------------------------------------

    async def get_options_chain(
        self,
        symbol: str,
        expiry: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fetch the options chain for *symbol*.

        Parameters
        ----------
        symbol:
            Yahoo Finance ticker.
        expiry:
            Expiry date string ``"YYYY-MM-DD"``.  If None, uses the
            nearest available expiry.

        Returns
        -------
        dict
            ``{expiry, calls: DataFrame.to_dict(), puts: DataFrame.to_dict(),
               available_expiries: [...]}``.
        """
        import yfinance as yf

        def _fetch():
            ticker = yf.Ticker(symbol)
            available = ticker.options
            if not available:
                return {
                    "expiry": None,
                    "calls": {},
                    "puts": {},
                    "available_expiries": [],
                }

            selected = expiry if (expiry and expiry in available) else available[0]
            chain = ticker.option_chain(selected)
            return {
                "expiry":              selected,
                "calls":               chain.calls.to_dict(orient="records"),
                "puts":                chain.puts.to_dict(orient="records"),
                "available_expiries":  list(available),
            }

        return await self._run_sync(_fetch)
