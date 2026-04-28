"""
NEXUS ALPHA - Dark Pool Monitor
==================================
Fetches FINRA OTC/ATS (dark pool) volume data for US equities.

FINRA publishes OTC transparency data daily (T+1 delay) at:
  https://www.finra.org/investors/learn-to-invest/advanced-investing/dark-pools

Data is free and publicly accessible – no API key required.

Environment variables: None required.
"""

from __future__ import annotations

import asyncio
import csv
import io
import logging
import time
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

# FINRA OTC daily report URL (CSV download)
_FINRA_OTC_BASE = (
    "https://api.finra.org/data/group/otcMarket/name/weeklySummary"
    "?limit=52&dateRangeFilters=weekStartDate:%3E%3D{start}"
)

# Alternative: FINRA ATS (Alternative Trading System) data
_FINRA_ATS_BASE = (
    "https://api.finra.org/data/group/otcMarket/name/atsWeeklySummary"
    "?limit=52&dateRangeFilters=weekStartDate:%3E%3D{start}"
)

# FINRA ticker-level OTC data
_FINRA_TICKER_URL = (
    "https://api.finra.org/data/group/otcMarket/name/otcDailyList"
    "?limit=500&offset=0&dateRangeFilters=recordDate%3A%3E%3D{date}"
    "&domainFilters=issueSymbolIdentifier:{symbol}"
)

_CACHE_TTL_S = 3_600 * 6   # 6 hours (data is T+1 daily)


class _CacheEntry:
    def __init__(self, data: Any) -> None:
        self.data = data
        self.ts   = time.monotonic()

    def is_valid(self) -> bool:
        return time.monotonic() - self.ts < _CACHE_TTL_S


class DarkPoolMonitor:
    """
    Monitor for dark pool (OTC/ATS) trading activity via FINRA data.

    Note
    ----
    All FINRA data is delayed by at least 1 business day.  This data
    is suitable for multi-day pattern analysis, not intraday signals.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, _CacheEntry] = {}
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Accept":     "application/json",
                    "User-Agent": "NEXUS-ALPHA/1.0",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------

    async def get_dark_pool_activity(
        self,
        symbol: str,
        query_date: Optional[date] = None,
    ) -> Dict[str, Any]:
        """
        Fetch dark pool volume statistics for *symbol* on *query_date*.

        Returns the OTC volume as a percentage of total consolidated
        volume (dark pool %).  Values above 40% are considered high.

        Parameters
        ----------
        symbol:
            US equity ticker, e.g. ``"AAPL"``.
        query_date:
            Date to query.  Defaults to yesterday (most recent T+1).

        Returns
        -------
        dict
            ::

                {
                  "symbol":           "AAPL",
                  "date":             "2025-04-25",
                  "otc_volume":       5_432_100,
                  "total_volume":     15_000_000,
                  "dark_pool_pct":    36.2,
                  "unusual":          False,
                  "data_source":      "FINRA_OTC"
                }
        """
        if query_date is None:
            # T+1 – use yesterday
            query_date = date.today() - timedelta(days=1)

        date_str   = query_date.isoformat()
        cache_key  = f"dp|{symbol}|{date_str}"

        cached = self._cache.get(cache_key)
        if cached and cached.is_valid():
            return cached.data

        session = self._get_session()
        url = _FINRA_TICKER_URL.format(
            date=date_str,
            symbol=symbol.upper(),
        )

        try:
            async with session.get(url) as resp:
                if resp.status in (404, 204):
                    # No data for this symbol/date
                    return self._empty_result(symbol, date_str)
                resp.raise_for_status()
                records = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("DarkPoolMonitor.get_dark_pool_activity error: %s", exc)
            return self._empty_result(symbol, date_str)

        if not records:
            return self._empty_result(symbol, date_str)

        # FINRA field names (may vary by endpoint version)
        rec = records[0] if isinstance(records, list) else records
        otc_volume   = float(rec.get("totalWeeklyShareQuantity",
                               rec.get("shareQuantity", 0)) or 0)
        total_volume = float(rec.get("consolidatedVolume",
                               rec.get("totalVolume", 0)) or 0)

        dark_pct = (otc_volume / total_volume * 100.0) if total_volume > 0 else 0.0
        unusual  = dark_pct > 50.0

        result = {
            "symbol":        symbol.upper(),
            "date":          date_str,
            "otc_volume":    otc_volume,
            "total_volume":  total_volume,
            "dark_pool_pct": round(dark_pct, 2),
            "unusual":       unusual,
            "data_source":   "FINRA_OTC",
        }

        self._cache[cache_key] = _CacheEntry(result)
        return result

    # ------------------------------------------------------------------

    async def detect_unusual_activity(
        self,
        symbol: str,
        lookback_days: int = 10,
    ) -> bool:
        """
        Return True if today's dark pool percentage is statistically
        unusual compared to the past *lookback_days* sessions.

        Uses a simple z-score approach: unusual if current day is more
        than 1.5 standard deviations above the historical mean.

        Parameters
        ----------
        symbol:
            US equity ticker.
        lookback_days:
            Number of prior trading sessions to compare against.

        Returns
        -------
        bool
        """
        tasks = []
        today = date.today()
        for i in range(1, lookback_days + 2):
            d = today - timedelta(days=i)
            if d.weekday() < 5:   # skip weekends
                tasks.append(self.get_dark_pool_activity(symbol, d))

        results = await asyncio.gather(*tasks, return_exceptions=True)
        pcts: List[float] = []
        for r in results:
            if isinstance(r, Exception) or not r:
                continue
            pcts.append(float(r.get("dark_pool_pct", 0)))

        if len(pcts) < 3:
            return False

        current     = pcts[0]
        historical  = pcts[1:]
        mean        = sum(historical) / len(historical)
        variance    = sum((v - mean) ** 2 for v in historical) / len(historical)
        std         = variance ** 0.5

        if std == 0:
            return current > mean * 1.5

        z_score = (current - mean) / std
        return z_score > 1.5

    # ------------------------------------------------------------------

    @staticmethod
    def _empty_result(symbol: str, date_str: str) -> Dict[str, Any]:
        return {
            "symbol":        symbol.upper(),
            "date":          date_str,
            "otc_volume":    0,
            "total_volume":  0,
            "dark_pool_pct": 0.0,
            "unusual":       False,
            "data_source":   "FINRA_OTC",
            "error":         "no_data",
        }
