"""
NEXUS ALPHA - Alpaca REST Provider
=====================================
REST-only endpoints: assets, market clock, calendar, and news.

Environment variables:
    ALPACA_API_KEY
    ALPACA_API_SECRET
    ALPACA_PAPER    "true" for paper trading
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

_REST_PAPER_BASE = "https://paper-api.alpaca.markets"
_REST_LIVE_BASE  = "https://api.alpaca.markets"
_REST_DATA_BASE  = "https://data.alpaca.markets"


class AlpacaRESTProvider:
    """
    Alpaca REST client for market meta-data and news.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        paper: bool = True,
    ) -> None:
        self._api_key    = api_key    or os.getenv("ALPACA_API_KEY",    "")
        self._api_secret = api_secret or os.getenv("ALPACA_API_SECRET", "")
        env_paper = os.getenv("ALPACA_PAPER", "true").lower() == "true"
        self._paper   = paper or env_paper
        self._session: Optional[aiohttp.ClientSession] = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "APCA-API-KEY-ID":     self._api_key,
                    "APCA-API-SECRET-KEY": self._api_secret,
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    @property
    def _broker_base(self) -> str:
        return _REST_PAPER_BASE if self._paper else _REST_LIVE_BASE

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "AlpacaRESTProvider":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ------------------------------------------------------------------

    async def _get(
        self,
        base: str,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        session = self._get_session()
        async with session.get(f"{base}{path}", params=params or {}) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ------------------------------------------------------------------

    async def get_assets(
        self,
        status: str = "active",
        asset_class: str = "us_equity",
    ) -> List[Dict[str, Any]]:
        """
        Fetch a list of tradeable US equity assets.

        Parameters
        ----------
        status:
            ``"active"`` or ``"inactive"``.
        asset_class:
            ``"us_equity"`` or ``"crypto"``.

        Returns
        -------
        List[dict]
            Each dict contains ``id``, ``symbol``, ``name``,
            ``tradable``, ``fractionable``, ``marginable``, etc.
        """
        return await self._get(
            self._broker_base,
            "/v2/assets",
            {"status": status, "asset_class": asset_class},
        )

    # ------------------------------------------------------------------

    async def get_clock(self) -> Dict[str, Any]:
        """
        Fetch the current US market clock.

        Returns
        -------
        dict
            ``{timestamp, is_open, next_open, next_close}``.
        """
        return await self._get(self._broker_base, "/v2/clock")

    # ------------------------------------------------------------------

    async def get_calendar(
        self,
        start: Optional[str] = None,
        end: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch the NYSE market calendar.

        Parameters
        ----------
        start:
            Start date ``"YYYY-MM-DD"``.
        end:
            End date ``"YYYY-MM-DD"``.

        Returns
        -------
        List[dict]
            Each dict: ``{date, open, close}``.
        """
        params: Dict[str, str] = {}
        if start:
            params["start"] = start
        if end:
            params["end"] = end
        return await self._get(self._broker_base, "/v2/calendar", params)

    # ------------------------------------------------------------------

    async def get_news(
        self,
        symbols: Optional[List[str]] = None,
        limit: int = 50,
        start: Optional[str] = None,
        end: Optional[str] = None,
        include_content: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Fetch market news articles, optionally filtered by symbols.

        Parameters
        ----------
        symbols:
            List of ticker symbols to filter news by.
        limit:
            Maximum articles to return (max 50 per call).
        start:
            ISO-8601 start datetime.
        end:
            ISO-8601 end datetime.
        include_content:
            Whether to include full article HTML.

        Returns
        -------
        List[dict]
            Each dict: ``{id, headline, summary, author, created_at,
            updated_at, url, symbols, images}``.
        """
        params: Dict[str, Any] = {
            "limit":           min(limit, 50),
            "include_content": include_content,
        }
        if symbols:
            params["symbols"] = ",".join(symbols)
        if start:
            params["start"] = start
        if end:
            params["end"] = end

        result = await self._get(_REST_DATA_BASE, "/v1beta1/news", params)
        return result.get("news", result) if isinstance(result, dict) else result
