"""
NEXUS ALPHA - Google Trends Provider
=======================================
Fetches search interest data from Google Trends via the pytrends library.

Features:
* Search interest time-series for any keyword set
* Spike detection with a configurable multiplier
* Related query discovery
* Market-specific pre-defined keyword sets
* 4-hour in-process cache (pytrends has strict rate limits)

Environment variables: None required (public Google data).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_TTL_S  = 4 * 3_600   # 4 hours
_PYTRENDS_TIMEOUT = 30       # seconds

# ---------------------------------------------------------------------------
# Predefined keyword sets per asset class
# ---------------------------------------------------------------------------

KEYWORD_SETS: Dict[str, List[str]] = {
    "crypto_btc":    ["Bitcoin", "BTC price", "buy bitcoin"],
    "crypto_eth":    ["Ethereum", "ETH price", "buy ethereum"],
    "crypto_general":["cryptocurrency", "crypto crash", "crypto bull"],
    "forex_usd":     ["US dollar", "dollar crash", "DXY"],
    "forex_eur":     ["Euro EUR", "ECB rate", "eurusd"],
    "equity_india":  ["Nifty 50", "SEBI", "Indian stock market"],
    "equity_us":     ["S&P 500", "stock market crash", "NASDAQ"],
    "gold":          ["gold price", "buy gold", "gold ETF"],
    "oil":           ["crude oil price", "WTI oil", "OPEC"],
    "macro":         ["recession", "inflation", "interest rate hike"],
}


# ---------------------------------------------------------------------------
# In-memory cache entry
# ---------------------------------------------------------------------------

class _CacheEntry:
    def __init__(self, data: Any) -> None:
        self.data       = data
        self.created_at = time.monotonic()

    def is_valid(self) -> bool:
        return time.monotonic() - self.created_at < _CACHE_TTL_S


class GoogleTrendsProvider:
    """
    Async wrapper around pytrends for Google Trends data.

    Results are cached for :data:`_CACHE_TTL_S` seconds to stay within
    Google's undocumented rate limits.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, _CacheEntry] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_pytrends(self, hl: str = "en-US", tz: int = 0):
        """Build a TrendReq instance (one per call – not thread-safe to reuse)."""
        from pytrends.request import TrendReq
        return TrendReq(hl=hl, tz=tz, timeout=_PYTRENDS_TIMEOUT, retries=2)

    async def _run_sync(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, lambda: fn(*args, **kwargs))

    # ------------------------------------------------------------------

    async def get_search_interest(
        self,
        keywords: List[str],
        timeframe: str = "today 3-m",
        geo: str = "",
    ) -> Dict[str, Any]:
        """
        Fetch normalised (0-100) search interest over *timeframe*.

        Parameters
        ----------
        keywords:
            List of up to 5 search terms (Google Trends limit).
        timeframe:
            Google Trends timeframe string, e.g.:
            * ``"now 1-H"``   – last hour
            * ``"now 7-d"``   – last 7 days
            * ``"today 1-m"`` – last month
            * ``"today 3-m"`` – last 3 months
            * ``"today 12-m"``– last year
        geo:
            Country code, e.g. ``"US"``, ``"IN"``.  Empty = worldwide.

        Returns
        -------
        dict
            ``{keyword: pandas.Series, ...}`` dict (each Series indexed by date).
        """
        if len(keywords) > 5:
            raise ValueError("Google Trends supports at most 5 keywords per request.")

        cache_key = f"interest|{'|'.join(keywords)}|{timeframe}|{geo}"
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached.is_valid():
                return cached.data

        def _fetch():
            pt = self._get_pytrends()
            pt.build_payload(keywords, cat=0, timeframe=timeframe, geo=geo)
            df = pt.interest_over_time()
            if df.empty:
                return {}
            # Drop "isPartial" column if present
            df = df.drop(columns=["isPartial"], errors="ignore")
            return {kw: df[kw].to_dict() for kw in keywords if kw in df.columns}

        result = await self._run_sync(_fetch)

        async with self._lock:
            self._cache[cache_key] = _CacheEntry(result)

        return result

    # ------------------------------------------------------------------

    async def detect_spike(
        self,
        keyword: str,
        timeframe: str = "today 1-m",
        threshold_multiplier: float = 2.0,
        geo: str = "",
    ) -> Dict[str, Any]:
        """
        Detect a recent spike in search interest for *keyword*.

        A spike is detected when the most recent value exceeds the
        rolling mean by *threshold_multiplier* × rolling std.

        Parameters
        ----------
        keyword:
            Search term.
        timeframe:
            Lookback window (passed to :meth:`get_search_interest`).
        threshold_multiplier:
            Standard-deviation multiplier for spike classification.
        geo:
            Country filter.

        Returns
        -------
        dict
            ::

                {
                  "keyword":    "Bitcoin",
                  "spike":      True,
                  "current":    95,
                  "mean":       40.2,
                  "std":        15.8,
                  "multiplier": 2.0,
                  "signal":     "SPIKE_DETECTED"
                }
        """
        interest = await self.get_search_interest([keyword], timeframe, geo)
        series = interest.get(keyword, {})

        if not series:
            return {
                "keyword": keyword, "spike": False,
                "current": 0, "mean": 0, "std": 0,
                "multiplier": threshold_multiplier, "signal": "NO_DATA",
            }

        values = list(series.values())
        if len(values) < 3:
            return {
                "keyword": keyword, "spike": False,
                "current": values[-1] if values else 0,
                "mean": 0, "std": 0,
                "multiplier": threshold_multiplier, "signal": "INSUFFICIENT_DATA",
            }

        current    = float(values[-1])
        historical = [float(v) for v in values[:-1]]

        mean = sum(historical) / len(historical)
        variance = sum((v - mean) ** 2 for v in historical) / len(historical)
        std = variance ** 0.5

        spike = (std > 0) and (current > mean + threshold_multiplier * std)

        return {
            "keyword":    keyword,
            "spike":      spike,
            "current":    current,
            "mean":       round(mean, 2),
            "std":        round(std, 2),
            "multiplier": threshold_multiplier,
            "signal":     "SPIKE_DETECTED" if spike else "NORMAL",
        }

    # ------------------------------------------------------------------

    async def get_related_queries(
        self,
        keyword: str,
        timeframe: str = "today 3-m",
        geo: str = "",
    ) -> List[str]:
        """
        Fetch the top related queries for *keyword*.

        Parameters
        ----------
        keyword:
            Search term.
        timeframe:
            Lookback window.
        geo:
            Country filter.

        Returns
        -------
        List[str]
            Related search queries sorted by interest.
        """
        cache_key = f"related|{keyword}|{timeframe}|{geo}"
        async with self._lock:
            cached = self._cache.get(cache_key)
            if cached and cached.is_valid():
                return cached.data

        def _fetch():
            pt = self._get_pytrends()
            pt.build_payload([keyword], timeframe=timeframe, geo=geo)
            related = pt.related_queries()
            top_df = related.get(keyword, {}).get("top", None)
            if top_df is None or top_df.empty:
                return []
            return top_df["query"].tolist()

        result = await self._run_sync(_fetch)

        async with self._lock:
            self._cache[cache_key] = _CacheEntry(result)

        return result

    # ------------------------------------------------------------------

    def get_keyword_set(self, market: str) -> List[str]:
        """
        Return the pre-defined keyword set for a market category.

        Parameters
        ----------
        market:
            Key from :data:`KEYWORD_SETS`, e.g. ``"crypto_btc"``.

        Returns
        -------
        List[str]
        """
        return KEYWORD_SETS.get(market, [])
