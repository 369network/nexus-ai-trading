"""
NEXUS ALPHA - Funding Rate Monitor
=====================================
Aggregates and analyses perpetual swap funding rates across Binance,
Bybit, and OKX for strategy signals including funding rate arbitrage.

Environment variables:
    BINANCE_API_KEY / BINANCE_API_SECRET
    BYBIT_API_KEY  / BYBIT_API_SECRET
    OKX_API_KEY    / OKX_API_SECRET
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import aiohttp

from .whale_alerts import FundingRate, OnChainAnalytics

logger = logging.getLogger(__name__)

_BINANCE_FAPI = "https://fapi.binance.com"
_BYBIT_API    = "https://api.bybit.com"
_OKX_API      = "https://www.okx.com"

_EXTREME_RATE_THRESHOLD = 0.001   # 0.1% per 8h is considered extreme


class FundingRateMonitor:
    """
    Cross-exchange funding rate aggregator and signal generator.
    """

    def __init__(self) -> None:
        self._analytics = OnChainAnalytics()
        self._history: Dict[str, List[FundingRate]] = {}  # symbol -> list

    async def close(self) -> None:
        await self._analytics.close()

    # ------------------------------------------------------------------

    async def get_current_rates(
        self,
        symbols: List[str],
    ) -> Dict[str, List[FundingRate]]:
        """
        Fetch the current funding rate for each symbol across all exchanges.

        Parameters
        ----------
        symbols:
            List of base symbols, e.g. ``["BTC", "ETH", "SOL"]``.

        Returns
        -------
        dict
            ``{symbol: [FundingRate, ...]}``.
        """
        tasks = [
            self._fetch_symbol_rates(sym)
            for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return {
            sym: (rates if not isinstance(rates, Exception) else [])
            for sym, rates in zip(symbols, results)
        }

    async def _fetch_symbol_rates(self, symbol: str) -> List[FundingRate]:
        rates = await self._analytics.get_funding_rates_all_exchanges(symbol)
        # Store in history
        key = symbol.upper()
        self._history.setdefault(key, []).extend(rates)
        # Trim history to last 500 entries per symbol
        self._history[key] = self._history[key][-500:]
        return rates

    # ------------------------------------------------------------------

    def get_rate_history(
        self,
        symbol: str,
        limit: int = 100,
    ) -> List[FundingRate]:
        """
        Return historical funding rate snapshots from in-memory cache.

        Parameters
        ----------
        symbol:
            Base symbol, e.g. ``"BTC"``.
        limit:
            Maximum records to return.

        Returns
        -------
        List[FundingRate]
            Most recent *limit* records, newest-last.
        """
        history = self._history.get(symbol.upper(), [])
        return history[-limit:]

    # ------------------------------------------------------------------

    async def get_historical_rates_api(
        self,
        symbol: str,
        limit: int = 100,
    ) -> List[FundingRate]:
        """
        Fetch historical funding rates from Binance REST API.

        Parameters
        ----------
        symbol:
            Base symbol.
        limit:
            Max records (up to 1000).

        Returns
        -------
        List[FundingRate]
        """
        pair = f"{symbol.upper()}USDT"
        session = self._analytics._get_session()
        try:
            async with session.get(
                f"{_BINANCE_FAPI}/fapi/v1/fundingRate",
                params={"symbol": pair, "limit": min(limit, 1000)},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

            return [
                FundingRate(
                    exchange          = "binance",
                    symbol            = pair,
                    funding_rate      = float(d["fundingRate"]),
                    next_funding_time = int(d.get("fundingTime", 0)) or None,
                    timestamp         = int(d.get("fundingTime", 0)) / 1_000.0,
                )
                for d in data
            ]
        except Exception as exc:
            logger.error("get_historical_rates_api error: %s", exc)
            return []

    # ------------------------------------------------------------------

    def detect_extreme_rates(
        self,
        symbol_rates: Dict[str, List[FundingRate]],
        threshold: float = _EXTREME_RATE_THRESHOLD,
    ) -> List[str]:
        """
        Return symbols whose average funding rate exceeds *threshold*.

        Parameters
        ----------
        symbol_rates:
            Output of :meth:`get_current_rates`.
        threshold:
            Absolute rate threshold (e.g. 0.001 = 0.1%).

        Returns
        -------
        List[str]
            Symbols with extreme (positive or negative) funding rates.
        """
        extreme: List[str] = []
        for sym, rates in symbol_rates.items():
            if not rates:
                continue
            avg = sum(r.funding_rate for r in rates) / len(rates)
            if abs(avg) >= threshold:
                extreme.append(sym)
        return extreme

    # ------------------------------------------------------------------

    def funding_arbitrage_signal(
        self,
        symbol: str,
        rates: List[FundingRate],
    ) -> Dict[str, Any]:
        """
        Generate a funding rate arbitrage signal.

        Funding arb: when funding is persistently positive, short the
        perp and long spot (collect funding while delta-neutral).
        When negative, do the reverse.

        Parameters
        ----------
        symbol:
            Symbol name.
        rates:
            Current funding rates across exchanges.

        Returns
        -------
        dict
            ``{symbol, avg_rate, signal, arb_side, estimated_daily_yield}``.
        """
        if not rates:
            return {
                "symbol":                symbol,
                "avg_rate":              0.0,
                "signal":                "NEUTRAL",
                "arb_side":              None,
                "estimated_daily_yield": 0.0,
            }

        avg = sum(r.funding_rate for r in rates) / len(rates)

        # Funding settles every 8h → 3 payments per day
        daily_yield = avg * 3

        if avg > 0.001:           # longs paying shorts (>0.1% per 8h)
            signal   = "ARBIT_SHORT_PERP"
            arb_side = "short_perpetual_long_spot"
        elif avg < -0.001:        # shorts paying longs (<-0.1% per 8h)
            signal   = "ARBIT_LONG_PERP"
            arb_side = "long_perpetual_short_spot"
        else:
            signal   = "NEUTRAL"
            arb_side = None

        return {
            "symbol":                symbol,
            "avg_rate":              avg,
            "signal":                signal,
            "arb_side":              arb_side,
            "estimated_daily_yield": daily_yield,
        }
