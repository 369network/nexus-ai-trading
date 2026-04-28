"""
NEXUS ALPHA - On-Chain Analytics: Whale Alerts & Funding Rates
================================================================
Aggregates whale transfer data from the Whale Alert API and funding
rates from Binance, Bybit, and OKX.

Environment variables:
    WHALE_ALERT_API_KEY
    BINANCE_API_KEY / BINANCE_API_SECRET   (for funding rates)
    BYBIT_API_KEY  / BYBIT_API_SECRET
    OKX_API_KEY    / OKX_API_SECRET
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

_WHALE_ALERT_BASE = "https://api.whale-alert.io/v1"
_BINANCE_FAPI     = "https://fapi.binance.com"
_BYBIT_API        = "https://api.bybit.com"
_OKX_API          = "https://www.okx.com"


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class WhaleAlert:
    """Represents a single large on-chain token transfer."""

    tx_hash:     str
    blockchain:  str
    symbol:      str
    from_address: str
    to_address:  str
    amount:      float         # token amount
    amount_usd:  float         # USD value
    timestamp:   float         # Unix seconds
    from_owner:  str = ""      # "binance", "unknown", etc.
    to_owner:    str = ""


@dataclass
class FundingRate:
    """Funding rate snapshot from a single exchange."""

    exchange:       str
    symbol:         str
    funding_rate:   float      # e.g. 0.0001 = 0.01%
    next_funding_time: Optional[int]  # UTC ms
    timestamp:      float      # fetch time (Unix seconds)


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class OnChainAnalytics:
    """
    Aggregates whale transfer alerts and cross-exchange funding rates.
    """

    def __init__(
        self,
        whale_api_key: Optional[str] = None,
    ) -> None:
        self._whale_key = whale_api_key or os.getenv("WHALE_ALERT_API_KEY", "")
        self._session: Optional[aiohttp.ClientSession] = None

    # ------------------------------------------------------------------

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=15)
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # ------------------------------------------------------------------
    # Whale transfers
    # ------------------------------------------------------------------

    async def get_recent_whale_transfers(
        self,
        min_usd: float = 1_000_000,
        blockchain: Optional[str] = None,
        limit: int = 100,
    ) -> List[WhaleAlert]:
        """
        Fetch recent large on-chain transfers from the Whale Alert API.

        Parameters
        ----------
        min_usd:
            Minimum transfer value in USD.
        blockchain:
            Filter to a specific blockchain (``"ethereum"``,
            ``"bitcoin"``, ``"tron"``, etc.).  None = all chains.
        limit:
            Maximum number of transfers to return.

        Returns
        -------
        List[WhaleAlert]
        """
        params: Dict[str, Any] = {
            "api_key":   self._whale_key,
            "min_value": int(min_usd),
            "limit":     min(limit, 100),
            "start":     int(time.time()) - 3_600,  # last 1h
        }
        if blockchain:
            params["blockchain"] = blockchain.lower()

        session = self._get_session()
        try:
            async with session.get(
                f"{_WHALE_ALERT_BASE}/transactions",
                params=params,
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("Whale Alert API error: %s", exc)
            return []

        alerts: List[WhaleAlert] = []
        for tx in data.get("transactions", []):
            alerts.append(WhaleAlert(
                tx_hash     = tx.get("hash", ""),
                blockchain  = tx.get("blockchain", ""),
                symbol      = tx.get("symbol", "").upper(),
                from_address= tx.get("from", {}).get("address", ""),
                to_address  = tx.get("to",   {}).get("address", ""),
                amount      = float(tx.get("amount", 0)),
                amount_usd  = float(tx.get("amount_usd", 0)),
                timestamp   = float(tx.get("timestamp", 0)),
                from_owner  = tx.get("from", {}).get("owner_type", ""),
                to_owner    = tx.get("to",   {}).get("owner_type", ""),
            ))

        return alerts

    # ------------------------------------------------------------------
    # Funding rates
    # ------------------------------------------------------------------

    async def get_funding_rates_all_exchanges(
        self,
        symbol: str,
    ) -> List[FundingRate]:
        """
        Fetch the current funding rate for *symbol* from Binance,
        Bybit, and OKX simultaneously.

        Parameters
        ----------
        symbol:
            Base symbol, e.g. ``"BTC"`` or ``"ETH"``.

        Returns
        -------
        List[FundingRate]
            One entry per exchange.
        """
        tasks = [
            self._get_binance_funding(symbol),
            self._get_bybit_funding(symbol),
            self._get_okx_funding(symbol),
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        rates: List[FundingRate] = []
        for r in results:
            if isinstance(r, Exception):
                logger.debug("Funding rate fetch error: %s", r)
            elif r is not None:
                rates.append(r)
        return rates

    # ------------------------------------------------------------------

    async def _get_binance_funding(self, symbol: str) -> Optional[FundingRate]:
        """Fetch current funding rate from Binance USDM futures."""
        pair = f"{symbol.upper()}USDT"
        session = self._get_session()
        try:
            async with session.get(
                f"{_BINANCE_FAPI}/fapi/v1/premiumIndex",
                params={"symbol": pair},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
            return FundingRate(
                exchange         = "binance",
                symbol           = pair,
                funding_rate     = float(data.get("lastFundingRate", 0)),
                next_funding_time= int(data.get("nextFundingTime", 0)) or None,
                timestamp        = time.time(),
            )
        except Exception as exc:
            logger.debug("Binance funding rate error for %s: %s", pair, exc)
            return None

    # ------------------------------------------------------------------

    async def _get_bybit_funding(self, symbol: str) -> Optional[FundingRate]:
        """Fetch current funding rate from Bybit linear perpetuals."""
        pair = f"{symbol.upper()}USDT"
        session = self._get_session()
        try:
            async with session.get(
                f"{_BYBIT_API}/v5/market/tickers",
                params={"category": "linear", "symbol": pair},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

            items = data.get("result", {}).get("list", [])
            if not items:
                return None
            item = items[0]
            return FundingRate(
                exchange         = "bybit",
                symbol           = pair,
                funding_rate     = float(item.get("fundingRate", 0)),
                next_funding_time= int(item.get("nextFundingTime", 0)) or None,
                timestamp        = time.time(),
            )
        except Exception as exc:
            logger.debug("Bybit funding rate error for %s: %s", pair, exc)
            return None

    # ------------------------------------------------------------------

    async def _get_okx_funding(self, symbol: str) -> Optional[FundingRate]:
        """Fetch current funding rate from OKX swap contracts."""
        inst_id = f"{symbol.upper()}-USDT-SWAP"
        session = self._get_session()
        try:
            async with session.get(
                f"{_OKX_API}/api/v5/public/funding-rate",
                params={"instId": inst_id},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

            items = data.get("data", [])
            if not items:
                return None
            item = items[0]
            return FundingRate(
                exchange         = "okx",
                symbol           = inst_id,
                funding_rate     = float(item.get("fundingRate", 0)),
                next_funding_time= int(item.get("nextFundingTime", 0)) or None,
                timestamp        = time.time(),
            )
        except Exception as exc:
            logger.debug("OKX funding rate error for %s: %s", inst_id, exc)
            return None

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def _funding_signal(self, rate: float) -> str:
        """
        Derive a trading signal from a funding rate value.

        Parameters
        ----------
        rate:
            Funding rate as a decimal (e.g. 0.001 = 0.1%).

        Returns
        -------
        str
            ``"EXTREME_LONG"`` (longs paying heavily),
            ``"LONG_BIAS"``, ``"NEUTRAL"``,
            ``"SHORT_BIAS"``, or ``"EXTREME_SHORT"``.
        """
        if rate > 0.003:
            return "EXTREME_LONG"    # market extremely bullish; consider short
        if rate > 0.001:
            return "LONG_BIAS"
        if rate > -0.001:
            return "NEUTRAL"
        if rate > -0.003:
            return "SHORT_BIAS"
        return "EXTREME_SHORT"       # shorts paying; consider long

    def aggregate_funding_rates(
        self,
        rates: List[FundingRate],
    ) -> Dict[str, Any]:
        """
        Compute a weighted average funding rate across exchanges.

        Binance weight = 0.5 (largest OI), Bybit = 0.3, OKX = 0.2.

        Parameters
        ----------
        rates:
            List of :class:`FundingRate` objects.

        Returns
        -------
        dict
            ``{weighted_avg, signal, rates_by_exchange}``.
        """
        weights = {"binance": 0.5, "bybit": 0.3, "okx": 0.2}
        total_weight = 0.0
        weighted_sum = 0.0
        by_exchange: Dict[str, float] = {}

        for r in rates:
            w = weights.get(r.exchange, 0.1)
            weighted_sum  += r.funding_rate * w
            total_weight  += w
            by_exchange[r.exchange] = r.funding_rate

        avg = weighted_sum / total_weight if total_weight > 0 else 0.0
        return {
            "weighted_avg":      avg,
            "signal":            self._funding_signal(avg),
            "rates_by_exchange": by_exchange,
        }
