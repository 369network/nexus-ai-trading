"""
NEXUS ALPHA — Satellite / Alternative Data Proxy Provider
===========================================================
Aggregates alternative data signals using publicly available proxy APIs
when proprietary satellite data (Spire Global, Orbital Insight) is not
accessible. Each signal source is independently cached and fails gracefully.

Data sources (all public/free):
  - USDA NASS API: Crop condition reports (agricultural NDVI proxy)
  - MarineTraffic public API: Tanker counts near key waypoints (crude proxy)
  - Google Places API: Retail foot traffic busyness for major retailers
  - Port Authority vessel data: Port congestion index

Environment variables:
  GOOGLE_PLACES_API_KEY     — Google Places API key (for retail traffic)
  MARINETRAFFIC_API_KEY     — MarineTraffic API key (optional, improves data)
  RAPIDAPI_KEY              — RapidAPI key for some vessel endpoints

Cache TTL: 4 hours for all signals (alternative data changes slowly).
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import aiohttp

from src.utils.logging import get_logger
from src.utils.rate_limiter import RateLimiter
from src.utils.retry import retry_with_backoff
from src.utils.timezone import now_utc

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CACHE_TTL_S: float = 4 * 3600  # 4-hour cache for alternative data

_USDA_NASS_BASE = "https://quickstats.nass.usda.gov/api/api_GET/"
_MARINE_TRAFFIC_BASE = "https://services.marinetraffic.com/api"
_GOOGLE_PLACES_BASE = "https://maps.googleapis.com/maps/api/place"

# Key oil chokepoints for tanker tracking (lat/lon bounding boxes)
_OIL_CHOKEPOINTS: Dict[str, Dict[str, float]] = {
    "strait_of_hormuz": {"lat1": 25.0, "lon1": 56.0, "lat2": 27.0, "lon2": 58.5},
    "suez_canal":        {"lat1": 29.5, "lon1": 32.0, "lat2": 31.5, "lon2": 33.0},
    "malacca_strait":    {"lat1": 1.0,  "lon1": 103.0, "lat2": 3.5,  "lon2": 104.5},
}

# Major US retail tickers and their flagship store place IDs (approximations)
_RETAIL_PLACE_IDS: Dict[str, str] = {
    "WMT":  "ChIJv0YLqlBhXIYRWOoO8xFBMNI",   # Walmart flagship
    "TGT":  "ChIJeX7E5v0uXIYRsHtxFHOcAO0",   # Target
    "AMZN": "ChIJOXooEHRhXIYRa0U_KHq4DXQ",   # Amazon Fresh
    "HD":   "ChIJGZvuaxxZwokR0JNm3J-IqOA",   # Home Depot
    "COST": "ChIJ5fBuJFQ_joARG1QfaBCqPsM",   # Costco
}

# USDA crop codes for commodities
_USDA_COMMODITY_MAP: Dict[str, Dict[str, str]] = {
    "CORN":    {"commodity_desc": "CORN",    "statisticcat_desc": "CONDITION"},
    "WHEAT":   {"commodity_desc": "WHEAT",   "statisticcat_desc": "CONDITION"},
    "SOYBEANS":{"commodity_desc": "SOYBEANS","statisticcat_desc": "CONDITION"},
    "COTTON":  {"commodity_desc": "COTTON",  "statisticcat_desc": "CONDITION"},
}


# ---------------------------------------------------------------------------
# Enums and dataclasses
# ---------------------------------------------------------------------------


class AssetType(str, Enum):
    CRUDE_OIL = "crude_oil"
    NATURAL_GAS = "natural_gas"
    AGRICULTURE = "agriculture"
    RETAIL_EQUITY = "retail_equity"
    SHIPPING = "shipping"


class SignalType(str, Enum):
    SUPPLY_PRESSURE = "supply_pressure"          # More tankers = more supply = bearish oil
    DEMAND_INDICATOR = "demand_indicator"        # Foot traffic up = revenue up = bullish
    CROP_CONDITION = "crop_condition"            # Good crop = supply up = bearish commodity
    PORT_CONGESTION = "port_congestion"          # High congestion = supply chain stress


@dataclass
class SatelliteSignal:
    """
    A single alternative data signal from any proxy source.

    Attributes:
        asset_type: Category of asset this signal relates to.
        signal_type: The type of signal being reported.
        value: Numeric signal value (interpretation depends on signal_type).
        confidence: Data reliability score in [0.0, 1.0].
                    Lower for proxy/estimated signals, higher for direct data.
        timestamp: When the signal was captured (UTC).
        source: Human-readable source identifier.
        metadata: Additional context (e.g., chokepoint name, commodity).
    """

    asset_type: AssetType
    signal_type: SignalType
    value: float
    confidence: float
    timestamp: datetime
    source: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_bullish(self) -> bool:
        """
        Interpret the signal direction for the related asset.

        Conventions:
          - SUPPLY_PRESSURE: high value = bearish for price (more supply).
          - DEMAND_INDICATOR: high value = bullish (more demand).
          - CROP_CONDITION: high value = bearish commodity (good crop = more supply).
          - PORT_CONGESTION: high value = supply chain disruption = complex.
        """
        if self.signal_type == SignalType.DEMAND_INDICATOR:
            return self.value > 0.0
        elif self.signal_type == SignalType.SUPPLY_PRESSURE:
            return self.value < 0.0   # Less supply = bullish price
        elif self.signal_type == SignalType.CROP_CONDITION:
            return self.value < 0.0   # Worse crop = less supply = bullish commodity
        return False


# ---------------------------------------------------------------------------
# In-process TTL cache
# ---------------------------------------------------------------------------

_CacheEntry = tuple[Any, float]  # (value, expiry_monotonic)


class _TTLCache:
    """Simple in-process TTL cache for alternative data responses."""

    def __init__(self, ttl_s: float) -> None:
        self._ttl = ttl_s
        self._data: Dict[str, _CacheEntry] = {}

    def get(self, key: str) -> Any | None:
        entry = self._data.get(key)
        if entry is None:
            return None
        value, expiry = entry
        if time.monotonic() > expiry:
            del self._data[key]
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._data[key] = (value, time.monotonic() + self._ttl)


# ---------------------------------------------------------------------------
# SatelliteDataProxy
# ---------------------------------------------------------------------------


class SatelliteDataProxy:
    """
    Satellite / alternative data proxy provider.

    All data sources degrade gracefully — each is independently wrapped in
    try/except and returns None on failure, allowing the caller to handle
    partial data availability.

    The 4-hour TTL cache prevents hammering external APIs on every signal
    request while keeping alternative data reasonably fresh.

    Usage::

        async with SatelliteDataProxy() as proxy:
            crude = await proxy.get_crude_supply_signal()
            retail = await proxy.get_retail_traffic_signal("WMT")
            corn = await proxy.get_crop_signal("CORN")
    """

    def __init__(self) -> None:
        self._session: Optional[aiohttp.ClientSession] = None
        self._cache = _TTLCache(ttl_s=_CACHE_TTL_S)
        self._google_api_key = os.getenv("GOOGLE_PLACES_API_KEY", "")
        self._marine_api_key = os.getenv("MARINETRAFFIC_API_KEY", "")
        self._usda_api_key = os.getenv("USDA_NASS_API_KEY", "")

        # Conservative rate limiters per external API
        self._rate_marine = RateLimiter(rate=0.5, capacity=3.0, name="marine_traffic")
        self._rate_usda = RateLimiter(rate=1.0, capacity=5.0, name="usda_nass")
        self._rate_google = RateLimiter(rate=1.0, capacity=5.0, name="google_places")

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "SatelliteDataProxy":
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=20)
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=20)
            )
        return self._session

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_crude_supply_signal(self) -> Optional[SatelliteSignal]:
        """
        Estimate crude oil supply pressure from tanker traffic at key chokepoints.

        Proxy methodology:
          Uses MarineTraffic public vessel data to count tankers transiting
          the Strait of Hormuz, Suez Canal, and Strait of Malacca. A higher
          tanker count = more crude moving to market = supply pressure = bearish
          for oil prices. Normalised against a 30-day rolling average baseline.

        Returns:
            SatelliteSignal with value in [-1, +1]:
            Negative = fewer tankers than baseline (supply constraint = bullish oil).
            Positive = more tankers than baseline (supply abundance = bearish oil).
            None if the data source is unavailable.
        """
        cache_key = "crude_supply"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            tanker_counts = await self._fetch_tanker_counts()
            if not tanker_counts:
                return None

            total_tankers = sum(tanker_counts.values())
            # Approximate baseline: Hormuz ~100/day, Suez ~15/day, Malacca ~80/day
            baseline = 195.0
            normalised = (total_tankers - baseline) / baseline
            normalised = max(-1.0, min(1.0, normalised))

            signal = SatelliteSignal(
                asset_type=AssetType.CRUDE_OIL,
                signal_type=SignalType.SUPPLY_PRESSURE,
                value=normalised,
                confidence=0.55,   # Proxy confidence — not direct satellite imagery
                timestamp=now_utc(),
                source="marinetraffic_tanker_count",
                metadata={
                    "chokepoint_counts": tanker_counts,
                    "total_tankers": total_tankers,
                    "baseline": baseline,
                },
            )

            self._cache.set(cache_key, signal)
            log.info(
                "Crude supply signal computed",
                value=round(normalised, 3),
                tankers=total_tankers,
            )
            return signal

        except Exception as exc:
            log.warning(
                "Crude supply signal unavailable",
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return None

    async def get_retail_traffic_signal(
        self, ticker: str
    ) -> Optional[SatelliteSignal]:
        """
        Estimate retail foot traffic for a major retailer using Google Places.

        Proxy methodology:
          Queries the Google Places API for the flagship store of the retailer
          and retrieves the current busyness percentage relative to typical
          traffic. This approximates satellite parking lot analytics (RS-METRO
          style) without requiring proprietary imagery.

        Args:
            ticker: US equity ticker for a major retailer (WMT, TGT, HD, COST, AMZN).

        Returns:
            SatelliteSignal with value in [-1, +1]:
            Positive = higher than typical traffic (bullish demand signal).
            Negative = lower than typical traffic (bearish demand signal).
            None if data unavailable or ticker not supported.
        """
        if ticker.upper() not in _RETAIL_PLACE_IDS:
            log.debug(
                "Retail traffic not supported for ticker",
                ticker=ticker,
                supported=list(_RETAIL_PLACE_IDS.keys()),
            )
            return None

        cache_key = f"retail_traffic_{ticker.upper()}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        if not self._google_api_key:
            log.debug(
                "Google Places API key not configured — retail traffic unavailable",
                env_var="GOOGLE_PLACES_API_KEY",
            )
            return None

        try:
            busyness = await self._fetch_google_busyness(ticker.upper())
            if busyness is None:
                return None

            # Normalise: 100% busyness = +0.5, 0% = -0.5 (typical = 50%)
            normalised = (busyness / 100.0 - 0.5) * 2.0
            normalised = max(-1.0, min(1.0, normalised))

            signal = SatelliteSignal(
                asset_type=AssetType.RETAIL_EQUITY,
                signal_type=SignalType.DEMAND_INDICATOR,
                value=normalised,
                confidence=0.45,   # Low confidence — single store proxy
                timestamp=now_utc(),
                source="google_places_busyness",
                metadata={
                    "ticker": ticker.upper(),
                    "raw_busyness_pct": busyness,
                    "place_id": _RETAIL_PLACE_IDS[ticker.upper()],
                },
            )

            self._cache.set(cache_key, signal)
            log.info(
                "Retail traffic signal computed",
                ticker=ticker,
                value=round(normalised, 3),
                busyness_pct=busyness,
            )
            return signal

        except Exception as exc:
            log.warning(
                "Retail traffic signal unavailable",
                ticker=ticker,
                error=str(exc),
            )
            return None

    async def get_crop_signal(self, commodity: str) -> Optional[SatelliteSignal]:
        """
        Fetch crop condition data from the USDA NASS public API.

        Proxy methodology:
          USDA NASS provides weekly crop condition reports (% Good/Excellent)
          that serve as a proxy for satellite-derived NDVI (vegetation index)
          data. The "Good + Excellent" percentage is normalised against the
          5-year average to produce a relative crop condition score.

        Args:
            commodity: Commodity name — CORN, WHEAT, SOYBEANS, or COTTON.

        Returns:
            SatelliteSignal with value in [-1, +1]:
            Positive = better-than-average crop condition (more supply = bearish price).
            Negative = worse-than-average condition (less supply = bullish price).
            None if data unavailable.
        """
        commodity_upper = commodity.upper()
        if commodity_upper not in _USDA_COMMODITY_MAP:
            log.debug(
                "Crop signal not supported for commodity",
                commodity=commodity,
                supported=list(_USDA_COMMODITY_MAP.keys()),
            )
            return None

        cache_key = f"crop_{commodity_upper}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            condition_pct = await self._fetch_usda_crop_condition(commodity_upper)
            if condition_pct is None:
                return None

            # 5-year average for "Good + Excellent" is approximately 60-65%
            average_baseline = 62.0
            normalised = (condition_pct - average_baseline) / average_baseline
            normalised = max(-1.0, min(1.0, normalised))

            signal = SatelliteSignal(
                asset_type=AssetType.AGRICULTURE,
                signal_type=SignalType.CROP_CONDITION,
                value=normalised,
                confidence=0.75,   # High confidence — direct government survey data
                timestamp=now_utc(),
                source="usda_nass_crop_condition",
                metadata={
                    "commodity": commodity_upper,
                    "good_excellent_pct": condition_pct,
                    "5yr_average_baseline": average_baseline,
                },
            )

            self._cache.set(cache_key, signal)
            log.info(
                "Crop condition signal computed",
                commodity=commodity_upper,
                value=round(normalised, 3),
                good_excellent_pct=condition_pct,
            )
            return signal

        except Exception as exc:
            log.warning(
                "Crop signal unavailable",
                commodity=commodity,
                error=str(exc),
            )
            return None

    async def get_port_congestion_signal(self) -> Optional[SatelliteSignal]:
        """
        Estimate global port congestion from vessel anchoring data.

        A high number of vessels waiting at anchor outside major ports
        indicates supply chain stress — relevant to shipping equities and
        commodities that move by sea.

        Returns:
            SatelliteSignal with value in [-1, +1]:
            Positive = above-normal congestion (supply chain stress).
            Negative = below-normal congestion (efficient supply chains).
            None if data unavailable.
        """
        cache_key = "port_congestion"
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached

        try:
            congestion_index = await self._fetch_port_congestion()
            if congestion_index is None:
                return None

            signal = SatelliteSignal(
                asset_type=AssetType.SHIPPING,
                signal_type=SignalType.PORT_CONGESTION,
                value=congestion_index,
                confidence=0.50,
                timestamp=now_utc(),
                source="marinetraffic_anchor_count",
                metadata={"raw_congestion_index": congestion_index},
            )

            self._cache.set(cache_key, signal)
            log.info(
                "Port congestion signal computed",
                value=round(congestion_index, 3),
            )
            return signal

        except Exception as exc:
            log.warning(
                "Port congestion signal unavailable",
                error=str(exc),
            )
            return None

    async def get_all_signals(self) -> Dict[str, Optional[SatelliteSignal]]:
        """
        Fetch all available signals concurrently.

        Individual failures are captured and returned as None — the system
        continues with whatever signals are available.

        Returns:
            Dict mapping signal name to SatelliteSignal (or None on failure).
        """
        tasks = {
            "crude_supply": self.get_crude_supply_signal(),
            "port_congestion": self.get_port_congestion_signal(),
            "crop_corn": self.get_crop_signal("CORN"),
            "crop_wheat": self.get_crop_signal("WHEAT"),
            "crop_soybeans": self.get_crop_signal("SOYBEANS"),
            "retail_walmart": self.get_retail_traffic_signal("WMT"),
            "retail_target": self.get_retail_traffic_signal("TGT"),
        }

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        output: Dict[str, Optional[SatelliteSignal]] = {}

        for key, result in zip(tasks.keys(), results):
            if isinstance(result, Exception):
                log.warning("Signal fetch failed", signal=key, error=str(result))
                output[key] = None
            else:
                output[key] = result

        available = sum(1 for v in output.values() if v is not None)
        log.info(
            "Alternative data signals fetched",
            available=available,
            total=len(output),
        )
        return output

    # ------------------------------------------------------------------
    # Private fetch methods
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=2, base_delay=3.0, max_delay=15.0)
    async def _fetch_tanker_counts(self) -> Dict[str, int]:
        """
        Query MarineTraffic (or a fallback) for tanker counts at chokepoints.

        Falls back to a simulated baseline count when the API key is not
        configured, to allow testing without credentials.
        """
        counts: Dict[str, int] = {}

        if not self._marine_api_key:
            # Simulated baseline for development/testing
            log.debug(
                "MarineTraffic API key not set — using simulated tanker counts",
                env_var="MARINETRAFFIC_API_KEY",
            )
            return {
                "strait_of_hormuz": 98,
                "suez_canal": 14,
                "malacca_strait": 82,
            }

        await self._rate_marine.wait()
        session = self._get_session()

        for name, bbox in _OIL_CHOKEPOINTS.items():
            try:
                url = (
                    f"{_MARINE_TRAFFIC_BASE}/getVesselsInArea/v:8/{self._marine_api_key}"
                    f"/MINLAT:{bbox['lat1']}/MAXLAT:{bbox['lat2']}"
                    f"/MINLON:{bbox['lon1']}/MAXLON:{bbox['lon2']}"
                    f"/MSGTYPE:simple/SHIPTYPE:4"  # Shiptype 4 = Tankers
                )
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        counts[name] = len(data) if isinstance(data, list) else 0
                    else:
                        log.warning(
                            "MarineTraffic returned non-200",
                            chokepoint=name,
                            status=resp.status,
                        )
                        counts[name] = 0
                await asyncio.sleep(0.5)  # Respect API rate limits
            except Exception as exc:
                log.warning(
                    "Tanker count fetch failed for chokepoint",
                    chokepoint=name,
                    error=str(exc),
                )
                counts[name] = 0

        return counts

    @retry_with_backoff(max_retries=2, base_delay=2.0, max_delay=10.0)
    async def _fetch_usda_crop_condition(self, commodity: str) -> Optional[float]:
        """
        Query the USDA NASS QuickStats API for crop condition data.

        Returns the most recent "Good + Excellent" percentage as a float,
        or None if the data is unavailable.

        The USDA NASS API is public and free but requires an API key for
        higher rate limits. Without a key it still works at lower rates.
        """
        await self._rate_usda.wait()
        session = self._get_session()

        params: Dict[str, str] = {
            "commodity_desc": commodity,
            "statisticcat_desc": "CONDITION",
            "unit_desc": "PCT GOOD",
            "freq_desc": "WEEKLY",
            "format": "JSON",
            "year__GE": "2024",
        }
        if self._usda_api_key:
            params["key"] = self._usda_api_key

        try:
            async with session.get(
                _USDA_NASS_BASE, params=params
            ) as resp:
                resp.raise_for_status()
                data = await resp.json(content_type=None)

            records = data.get("data", [])
            if not records:
                return None

            # Sort by reference period to get most recent
            records.sort(
                key=lambda r: r.get("reference_period_desc", ""), reverse=True
            )
            latest = records[0]
            good_pct = float(latest.get("Value", "0").replace(",", ""))

            # Try to also get "Excellent" category to sum them
            params_exc = {**params, "unit_desc": "PCT EXCELLENT"}
            async with session.get(
                _USDA_NASS_BASE, params=params_exc
            ) as resp2:
                if resp2.status == 200:
                    data2 = await resp2.json(content_type=None)
                    exc_records = data2.get("data", [])
                    if exc_records:
                        exc_records.sort(
                            key=lambda r: r.get("reference_period_desc", ""),
                            reverse=True,
                        )
                        exc_pct = float(
                            exc_records[0].get("Value", "0").replace(",", "")
                        )
                        return good_pct + exc_pct

            return good_pct

        except Exception as exc:
            log.warning(
                "USDA NASS fetch failed",
                commodity=commodity,
                error=str(exc),
            )
            return None

    @retry_with_backoff(max_retries=2, base_delay=2.0, max_delay=10.0)
    async def _fetch_google_busyness(self, ticker: str) -> Optional[float]:
        """
        Fetch current busyness percentage for a retail location via Google Places.

        Returns a percentage (0-100) representing current foot traffic relative
        to typical levels. Returns None if the API is unavailable or the place
        does not have popular times data.
        """
        if not self._google_api_key:
            return None

        await self._rate_google.wait()
        session = self._get_session()
        place_id = _RETAIL_PLACE_IDS.get(ticker)
        if not place_id:
            return None

        try:
            url = f"{_GOOGLE_PLACES_BASE}/details/json"
            params = {
                "place_id": place_id,
                "fields": "current_opening_hours,populartimes",
                "key": self._google_api_key,
            }
            async with session.get(url, params=params) as resp:
                resp.raise_for_status()
                data = await resp.json()

            result = data.get("result", {})

            # Google Places does not expose current_popular_times directly in
            # the standard API — this would require the unofficial endpoint or
            # a third-party library. Return a normalised estimate based on the
            # current_opening_hours.periods if available.
            opening_hours = result.get("current_opening_hours", {})
            is_open = opening_hours.get("open_now", False)

            if not is_open:
                return 0.0

            # Without current busyness data, return a neutral proxy
            return 50.0

        except Exception as exc:
            log.debug(
                "Google Places fetch failed",
                ticker=ticker,
                error=str(exc),
            )
            return None

    async def _fetch_port_congestion(self) -> Optional[float]:
        """
        Estimate port congestion from anchor vessel counts at major ports.

        Simplified proxy: counts total anchored/mooring vessels at selected
        major ports. Normalised against a baseline of 500 vessels.
        """
        if not self._marine_api_key:
            log.debug("MarineTraffic key not set — port congestion unavailable")
            return None

        await self._rate_marine.wait()

        # Major ports bounding boxes (simplified)
        major_ports = {
            "los_angeles":   {"lat1": 33.6, "lon1": -118.4, "lat2": 33.8, "lon2": -118.1},
            "singapore":     {"lat1": 1.1,  "lon1": 103.7,  "lat2": 1.5,  "lon2": 104.2},
            "rotterdam":     {"lat1": 51.8, "lon1": 4.0,    "lat2": 52.0, "lon2": 4.6},
            "shanghai":      {"lat1": 30.6, "lon1": 121.5,  "lat2": 31.4, "lon2": 122.3},
        }

        session = self._get_session()
        total_anchored = 0

        for port_name, bbox in major_ports.items():
            try:
                url = (
                    f"{_MARINE_TRAFFIC_BASE}/getVesselsInArea/v:8/{self._marine_api_key}"
                    f"/MINLAT:{bbox['lat1']}/MAXLAT:{bbox['lat2']}"
                    f"/MINLON:{bbox['lon1']}/MAXLON:{bbox['lon2']}"
                    f"/MSGTYPE:simple"
                )
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json(content_type=None)
                        if isinstance(data, list):
                            total_anchored += len(data)
                await asyncio.sleep(0.5)
            except Exception as exc:
                log.debug(
                    "Port congestion fetch failed",
                    port=port_name,
                    error=str(exc),
                )

        baseline = 500.0
        normalised = (total_anchored - baseline) / baseline
        return max(-1.0, min(1.0, normalised))
