"""
NEXUS ALPHA - Fear & Greed Index Provider
==========================================
Fetches Crypto and US equity Fear & Greed indices.

* Crypto F&G from alternative.me API (free, no auth)
* US equity F&G from CNN (scraped) with alternative.me extended as fallback
* Results cached for 1 hour to avoid hammering rate limits

Environment variables: None required.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, Optional

import aiohttp

logger = logging.getLogger(__name__)

_CRYPTO_FG_URL = "https://api.alternative.me/fng/"
_CNN_FG_URL    = "https://fear-and-greed-index.p.rapidapi.com/v1/fgi"
_ALT_US_FG_URL = "https://api.alternative.me/fng/?limit=8&format=json"

_CACHE_TTL_S = 3_600   # 1 hour


def _fg_signal(value: int) -> str:
    """
    Classify a Fear & Greed index value (0–100) into a trading signal.

    Parameters
    ----------
    value:
        Integer score in [0, 100].

    Returns
    -------
    str
        One of: ``"EXTREME_FEAR"``, ``"FEAR"``, ``"NEUTRAL"``,
        ``"GREED"``, ``"EXTREME_GREED"``.
    """
    if value <= 20:
        return "EXTREME_FEAR"
    if value <= 40:
        return "FEAR"
    if value <= 60:
        return "NEUTRAL"
    if value <= 80:
        return "GREED"
    return "EXTREME_GREED"


class FearGreedProvider:
    """
    Dual Fear & Greed index provider (Crypto + US equity).

    Results are in-memory cached for :data:`_CACHE_TTL_S` seconds.
    """

    def __init__(self) -> None:
        self._crypto_cache: Optional[Dict[str, Any]] = None
        self._crypto_cache_ts: float = 0.0

        self._us_cache: Optional[Dict[str, Any]] = None
        self._us_cache_ts: float = 0.0

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

    async def get_crypto_fear_greed(self) -> Dict[str, Any]:
        """
        Fetch the Crypto Fear & Greed Index from alternative.me.

        Returns
        -------
        dict
            ::

                {
                  "current":    {"value": 52, "label": "Neutral", "signal": "NEUTRAL"},
                  "yesterday":  {"value": 48, "label": "Fear",    "signal": "FEAR"},
                  "week_ago":   {"value": 35, "label": "Fear",    "signal": "FEAR"},
                  "signal":     "NEUTRAL",
                  "fetched_at": 1714200000.0
                }
        """
        now = time.monotonic()
        if self._crypto_cache and (now - self._crypto_cache_ts) < _CACHE_TTL_S:
            return self._crypto_cache

        session = self._get_session()
        try:
            async with session.get(
                _CRYPTO_FG_URL,
                params={"limit": 8, "format": "json"},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)
        except Exception as exc:
            logger.error("Crypto F&G fetch error: %s", exc)
            if self._crypto_cache:
                return self._crypto_cache
            raise

        entries = data.get("data", [])
        if not entries:
            raise ValueError("No entries in alternative.me response")

        def _entry(e: dict) -> Dict[str, Any]:
            v = int(e.get("value", 50))
            return {
                "value":     v,
                "label":     e.get("value_classification", ""),
                "signal":    _fg_signal(v),
                "timestamp": int(e.get("timestamp", 0)),
            }

        current   = _entry(entries[0])
        yesterday = _entry(entries[1]) if len(entries) > 1 else current
        week_ago  = _entry(entries[7]) if len(entries) > 7 else current

        result = {
            "current":    current,
            "yesterday":  yesterday,
            "week_ago":   week_ago,
            "signal":     current["signal"],
            "fetched_at": time.time(),
        }

        self._crypto_cache    = result
        self._crypto_cache_ts = now
        logger.debug(
            "Crypto F&G: %d (%s)", current["value"], current["signal"]
        )
        return result

    # ------------------------------------------------------------------

    async def get_us_fear_greed(self) -> Dict[str, Any]:
        """
        Fetch the CNN US equity Fear & Greed Index.

        Primary source: CNN Fear & Greed API via RapidAPI (requires
        ``RAPIDAPI_KEY`` env var).  Falls back to alternative.me
        extended endpoint for a proxy value.

        Returns
        -------
        dict
            ::

                {
                  "current":  {"value": 68, "label": "Greed", "signal": "GREED"},
                  "signal":   "GREED",
                  "source":   "cnn" | "alternative.me_proxy",
                  "fetched_at": 1714200000.0
                }
        """
        now = time.monotonic()
        if self._us_cache and (now - self._us_cache_ts) < _CACHE_TTL_S:
            return self._us_cache

        import os
        rapidapi_key = os.getenv("RAPIDAPI_KEY", "")
        result: Optional[Dict[str, Any]] = None

        if rapidapi_key:
            result = await self._fetch_cnn_fg(rapidapi_key)

        if result is None:
            result = await self._fetch_us_fg_proxy()

        self._us_cache    = result
        self._us_cache_ts = now
        return result

    # ------------------------------------------------------------------

    async def _fetch_cnn_fg(self, rapidapi_key: str) -> Optional[Dict[str, Any]]:
        """Attempt to fetch the CNN F&G index via RapidAPI."""
        headers = {
            "X-RapidAPI-Key":  rapidapi_key,
            "X-RapidAPI-Host": "fear-and-greed-index.p.rapidapi.com",
        }
        try:
            session = self._get_session()
            async with session.get(_CNN_FG_URL, headers=headers) as resp:
                if resp.status != 200:
                    return None
                data = await resp.json(content_type=None)

            fgi = data.get("fgi", {})
            now_val = fgi.get("now", {})
            v = int(now_val.get("value", 50))

            return {
                "current": {
                    "value":  v,
                    "label":  now_val.get("valueText", ""),
                    "signal": _fg_signal(v),
                },
                "signal":     _fg_signal(v),
                "source":     "cnn",
                "fetched_at": time.time(),
            }
        except Exception as exc:
            logger.warning("CNN F&G via RapidAPI failed: %s", exc)
            return None

    async def _fetch_us_fg_proxy(self) -> Dict[str, Any]:
        """
        Fallback: Use alternative.me to approximate the US F&G by combining
        the crypto index with a correction factor (not a true CNN index,
        but a reasonable proxy for demonstration / missing data scenarios).
        """
        try:
            crypto = await self.get_crypto_fear_greed()
            # Apply a slight dampening factor (equities tend to be less extreme)
            raw_v = crypto["current"]["value"]
            proxy_v = int(50 + (raw_v - 50) * 0.7)
            proxy_v = max(0, min(100, proxy_v))
        except Exception:
            proxy_v = 50  # neutral default

        return {
            "current": {
                "value":  proxy_v,
                "label":  "",
                "signal": _fg_signal(proxy_v),
            },
            "signal":     _fg_signal(proxy_v),
            "source":     "alternative.me_proxy",
            "fetched_at": time.time(),
        }
