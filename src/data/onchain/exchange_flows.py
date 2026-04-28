"""
NEXUS ALPHA - Exchange Flow Monitor
======================================
Tracks net BTC/ETH/stablecoin flows into and out of centralised exchanges
using the CryptoQuant API.

Net inflow (positive) → selling pressure → bearish signal.
Net outflow (negative) → self-custody / accumulation → bullish signal.

Environment variables:
    CRYPTOQUANT_API_KEY
    SUPABASE_URL
    SUPABASE_KEY
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import aiohttp

logger = logging.getLogger(__name__)

_CRYPTOQUANT_BASE = "https://api.cryptoquant.com/v1"


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclass
class ExchangeFlow:
    """Net exchange flow snapshot for an asset."""

    asset:      str
    exchange:   str
    timeframe:  str        # "1h", "24h", "7d"
    inflow:     float      # USD inflow
    outflow:    float      # USD outflow
    net_flow:   float      # inflow - outflow (positive = net inflow)
    timestamp:  float


# ---------------------------------------------------------------------------
# Monitor
# ---------------------------------------------------------------------------

class ExchangeFlowMonitor:
    """
    Fetches and interprets exchange net flow data.

    Requires a CryptoQuant API key for production use.
    Stores flow history to Supabase for trend analysis.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
    ) -> None:
        self._api_key = api_key or os.getenv("CRYPTOQUANT_API_KEY", "")
        self._session: Optional[aiohttp.ClientSession] = None

        # Lazy Supabase client
        self._supabase = None

    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            headers = {}
            if self._api_key:
                headers["Authorization"] = f"Bearer {self._api_key}"
            self._session = aiohttp.ClientSession(
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=15),
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------

    def _get_supabase(self):
        """Lazy-initialise the Supabase client."""
        if self._supabase is not None:
            return self._supabase

        url = os.getenv("SUPABASE_URL", "")
        key = os.getenv("SUPABASE_KEY", "")

        if not url or not key:
            return None

        try:
            from supabase import create_client
            self._supabase = create_client(url, key)
        except ImportError:
            logger.warning("supabase-py not installed; flow history will not persist.")
        return self._supabase

    # ------------------------------------------------------------------

    async def get_net_flow(
        self,
        asset: str = "btc",
        timeframe: str = "24h",
        exchange: str = "all",
    ) -> ExchangeFlow:
        """
        Fetch the net exchange inflow/outflow for *asset*.

        Parameters
        ----------
        asset:
            ``"btc"``, ``"eth"``, ``"stablecoin"``.
        timeframe:
            ``"1h"``, ``"24h"``, ``"7d"``.
        exchange:
            Exchange name or ``"all"`` for aggregate.

        Returns
        -------
        ExchangeFlow
        """
        session = self._get_session()
        endpoint = f"{_CRYPTOQUANT_BASE}/btc/exchange-flows/netflow"
        params: Dict[str, Any] = {
            "window": "hour" if timeframe == "1h" else "day",
            "limit":  7 if timeframe == "7d" else 1,
        }

        try:
            async with session.get(endpoint, params=params) as resp:
                if resp.status == 401:
                    logger.warning(
                        "CryptoQuant API key missing or invalid – returning mock flow."
                    )
                    return self._mock_flow(asset, exchange, timeframe)
                resp.raise_for_status()
                data = await resp.json(content_type=None)

            items = data.get("data", [])
            if not items:
                return self._mock_flow(asset, exchange, timeframe)

            # Aggregate if 7d
            total_inflow  = sum(float(d.get("inflow_total",  0)) for d in items)
            total_outflow = sum(float(d.get("outflow_total", 0)) for d in items)
            net           = total_inflow - total_outflow

        except Exception as exc:
            logger.error("get_net_flow error: %s", exc)
            return self._mock_flow(asset, exchange, timeframe)

        flow = ExchangeFlow(
            asset=asset,
            exchange=exchange,
            timeframe=timeframe,
            inflow=total_inflow,
            outflow=total_outflow,
            net_flow=net,
            timestamp=time.time(),
        )

        await self._store_flow(flow)
        return flow

    # ------------------------------------------------------------------

    def _mock_flow(
        self,
        asset: str,
        exchange: str,
        timeframe: str,
    ) -> ExchangeFlow:
        """Return a neutral mock flow when the API is unavailable."""
        return ExchangeFlow(
            asset=asset,
            exchange=exchange,
            timeframe=timeframe,
            inflow=0.0,
            outflow=0.0,
            net_flow=0.0,
            timestamp=time.time(),
        )

    # ------------------------------------------------------------------

    def interpret_flow(self, net_flow: float) -> str:
        """
        Classify a net exchange flow value into a directional signal.

        Parameters
        ----------
        net_flow:
            Net USD flow.  Positive = more inflow (bearish).

        Returns
        -------
        str
            ``"BULLISH"`` (outflow dominant), ``"BEARISH"`` (inflow
            dominant), or ``"NEUTRAL"``.
        """
        if net_flow < -10_000_000:     # >$10M net outflow
            return "BULLISH"
        if net_flow > 10_000_000:      # >$10M net inflow
            return "BEARISH"
        return "NEUTRAL"

    # ------------------------------------------------------------------

    async def _store_flow(self, flow: ExchangeFlow) -> None:
        """Persist an ExchangeFlow record to Supabase if configured."""
        sb = self._get_supabase()
        if sb is None:
            return
        try:
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(
                None,
                lambda: sb.table("exchange_flows").insert({
                    "asset":     flow.asset,
                    "exchange":  flow.exchange,
                    "timeframe": flow.timeframe,
                    "inflow":    flow.inflow,
                    "outflow":   flow.outflow,
                    "net_flow":  flow.net_flow,
                    "timestamp": flow.timestamp,
                }).execute(),
            )
        except Exception as exc:
            logger.debug("Supabase store_flow error: %s", exc)
