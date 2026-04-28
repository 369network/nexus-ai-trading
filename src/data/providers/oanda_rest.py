"""
NEXUS ALPHA - OANDA REST Provider
====================================
Pure REST access for OANDA instrument listings, pricing snapshots,
transaction history, and full account state.

Environment variables:
    OANDA_API_KEY
    OANDA_ACCOUNT_ID
    OANDA_ENV        "practice" (default) or "live"
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

_API_PRACTICE = "https://api-fxpractice.oanda.com"
_API_LIVE     = "https://api-fxtrade.oanda.com"


class OANDARESTProvider:
    """
    Stateless REST client for OANDA v20 API.

    Parameters
    ----------
    api_key:
        OANDA personal access token.
    account_id:
        OANDA account ID.
    environment:
        ``"practice"`` or ``"live"``.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        account_id: Optional[str] = None,
        environment: Optional[str] = None,
    ) -> None:
        self._api_key    = api_key    or os.getenv("OANDA_API_KEY", "")
        self._account_id = account_id or os.getenv("OANDA_ACCOUNT_ID", "")
        env              = environment or os.getenv("OANDA_ENV", "practice")
        self._base       = _API_LIVE if env == "live" else _API_PRACTICE
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type":  "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def __aenter__(self) -> "OANDARESTProvider":
        return self

    async def __aexit__(self, *_) -> None:
        await self.close()

    # ------------------------------------------------------------------

    async def _get(
        self,
        path: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> Any:
        url = f"{self._base}{path}"
        session = self._get_session()
        async with session.get(url, params=params or {}) as resp:
            resp.raise_for_status()
            return await resp.json()

    # ------------------------------------------------------------------

    async def get_instruments(
        self,
        account_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Fetch the list of tradeable instruments for the account.

        Returns a list of instrument spec dicts, each containing
        ``name``, ``type``, ``displayName``, ``pipLocation``,
        ``tradeUnitsPrecision``, ``minimumTradeSize``, etc.

        Parameters
        ----------
        account_id:
            Override the default account ID.

        Returns
        -------
        List[dict]
        """
        acct = account_id or self._account_id
        raw = await self._get(f"/v3/accounts/{acct}/instruments")
        return raw.get("instruments", [])

    # ------------------------------------------------------------------

    async def get_pricing(
        self,
        instruments: List[str],
    ) -> List[Dict[str, Any]]:
        """
        Fetch current bid/ask pricing for multiple instruments in one call.

        Parameters
        ----------
        instruments:
            List of OANDA instrument names, e.g. ``["EUR_USD", "GBP_USD"]``.

        Returns
        -------
        List[dict]
            Each dict contains ``instrument``, ``bids``, ``asks``,
            ``tradeable``, ``time``.
        """
        raw = await self._get(
            f"/v3/accounts/{self._account_id}/pricing",
            {"instruments": ",".join(instruments)},
        )
        return raw.get("prices", [])

    # ------------------------------------------------------------------

    async def get_transaction_history(
        self,
        from_time: Optional[str] = None,
        to_time: Optional[str] = None,
        page_size: int = 100,
    ) -> List[Dict[str, Any]]:
        """
        Fetch paginated transaction history.

        Parameters
        ----------
        from_time:
            RFC 3339 start time string.
        to_time:
            RFC 3339 end time string.
        page_size:
            Number of records per page (max 1000).

        Returns
        -------
        List[dict]
            All transactions in the time window, paginated transparently.
        """
        params: Dict[str, Any] = {"pageSize": min(page_size, 1000)}
        if from_time:
            params["from"] = from_time
        if to_time:
            params["to"] = to_time

        all_transactions: List[Dict[str, Any]] = []

        raw = await self._get(
            f"/v3/accounts/{self._account_id}/transactions",
            params,
        )
        pages = raw.get("pages", [])

        # Each page URL is a complete endpoint – fetch them
        for page_url in pages:
            # Strip the base URL prefix from the page link
            path = page_url.replace(self._base, "")
            page_raw = await self._get(path)
            all_transactions.extend(
                page_raw.get("transactions", [])
            )

        return all_transactions

    # ------------------------------------------------------------------

    async def get_account_details(self) -> Dict[str, Any]:
        """
        Fetch the full account state including open trades, positions,
        orders, balance, margin, and P&L.

        Returns
        -------
        dict
            Full OANDA account object.
        """
        raw = await self._get(
            f"/v3/accounts/{self._account_id}"
        )
        return raw.get("account", raw)
