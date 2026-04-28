"""
Data provider factory — returns the right REST provider for each market/symbol.

All returned providers expose a unified async interface:
    fetch_ohlcv(timeframe: str, limit: int) -> list[dict]
    close() -> None
"""
from __future__ import annotations
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unified provider adapter
# ---------------------------------------------------------------------------

class _ProviderAdapter:
    """
    Wraps a raw provider and adds a normalised fetch_ohlcv(timeframe, limit)
    that always returns list[dict] with keys:
        timestamp, open, high, low, close, volume
    """

    def __init__(self, raw_provider: Any, symbol: str) -> None:
        self._provider = raw_provider
        self._symbol = symbol

    async def fetch_ohlcv(self, timeframe: str = "1h", limit: int = 200) -> List[Dict]:
        """Delegate to the underlying provider's fetch method."""
        p = self._provider
        try:
            # CCXTProvider: fetch_ohlcv(symbol, timeframe, since=None, limit=500)
            # Pass limit as a keyword argument to avoid landing in the `since` slot.
            if hasattr(p, "fetch_ohlcv"):
                raw = await p.fetch_ohlcv(self._symbol, timeframe, limit=limit)
                return _normalise_ohlcv(raw)
            # YFinanceProvider: get_historical(symbol, period, interval)
            if hasattr(p, "get_historical"):
                interval = _tf_to_yf_interval(timeframe)
                raw = await p.get_historical(self._symbol, period="60d", interval=interval)
                return _normalise_df(raw, limit)
        except Exception as exc:
            logger.warning("fetch_ohlcv error [%s/%s]: %s", self._symbol, timeframe, exc)
        return []

    async def close(self) -> None:
        if hasattr(self._provider, "close"):
            try:
                await self._provider.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def get_provider_for_market(market: str, symbol: str, settings: Any) -> _ProviderAdapter:
    """Return a normalised provider adapter for the given market."""
    market = market.lower()
    raw = _create_raw_provider(market, symbol, settings)
    return _ProviderAdapter(raw_provider=raw, symbol=symbol)


def _create_raw_provider(market: str, symbol: str, settings: Any) -> Any:
    try:
        if market == "crypto":
            from src.data.providers.ccxt_provider import CCXTProvider, CCXTConfig
            creds: Dict[str, Dict] = {}
            bybit_key = getattr(settings, "bybit_api_key", "")
            bybit_secret = getattr(settings, "bybit_secret", "")
            if bybit_key and bybit_secret:
                creds["bybit"] = {"apiKey": bybit_key, "secret": bybit_secret}

            # Binance is always primary — no API key required for public OHLCV.
            # Bybit is appended as secondary only when credentials are available.
            exchanges = ["binance"] + (["bybit"] if creds else [])
            cfg = CCXTConfig(
                exchanges=exchanges,
                primary="binance",
                # sandbox=False: Binance mainnet serves public candles without auth;
                # paper-trading never routes real orders through CCXT anyway.
                sandbox=False,
                credentials=creds,
            )
            return CCXTProvider(config=cfg)

        elif market in ("us_stocks", "us", "forex", "indian_stocks", "indian", "commodities"):
            from src.data.providers.yfinance_provider import YFinanceProvider
            return YFinanceProvider()

        else:
            logger.warning("Unknown market '%s' — using YFinance", market)
            from src.data.providers.yfinance_provider import YFinanceProvider
            return YFinanceProvider()

    except Exception as exc:
        logger.warning("Provider init failed for %s: %s — using NullProvider", market, exc)
        return _NullRaw()


class _NullRaw:
    """Stub provider that always returns empty candles."""
    async def fetch_ohlcv(self, *a, **kw) -> list:
        return []
    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# OHLCV normalisation helpers
# ---------------------------------------------------------------------------

def _normalise_ohlcv(raw: Any) -> List[Dict]:
    """Convert CCXT raw candles to list[dict].

    Handles three input formats:
    * ``list[dict]``           — already normalised (pass-through)
    * ``list[list|tuple]``     — raw CCXT ``[[ts_ms, o, h, l, c, v], ...]``
    * ``list[OHLCV]``          — CCXTProvider dataclass objects (``timestamp_ms`` attr)

    The ``timestamp`` field in every returned dict is always **UTC milliseconds**
    so that callers can safely divide by 1000 to get seconds.
    """
    if not raw:
        return []
    result = []
    for c in raw:
        if isinstance(c, dict):
            # Ensure timestamp is in ms (normalise seconds → ms if needed)
            ts = c.get("timestamp", 0)
            if ts and float(ts) < 1e12:   # looks like seconds
                c = dict(c, timestamp=int(float(ts) * 1000))
            result.append(c)
        elif isinstance(c, (list, tuple)) and len(c) >= 6:
            # CCXT raw format: [ts_ms, open, high, low, close, volume]
            ts_ms = int(c[0]) if c[0] >= 1e12 else int(float(c[0]) * 1000)
            result.append({
                "timestamp": ts_ms,
                "open":   float(c[1]),
                "high":   float(c[2]),
                "low":    float(c[3]),
                "close":  float(c[4]),
                "volume": float(c[5]),
            })
        elif hasattr(c, "timestamp_ms"):
            # OHLCV dataclass from CCXTProvider — timestamp_ms is already in ms
            result.append({
                "timestamp": int(c.timestamp_ms),
                "open":   float(c.open),
                "high":   float(c.high),
                "low":    float(c.low),
                "close":  float(c.close),
                "volume": float(c.volume),
            })
    return result


def _normalise_df(raw: Any, limit: int) -> List[Dict]:
    """Convert a pandas DataFrame (from yfinance) to list[dict]."""
    try:
        import pandas as pd
        if raw is None or (hasattr(raw, "empty") and raw.empty):
            return []
        df = raw.tail(limit)
        result = []
        for ts, row in df.iterrows():
            result.append({
                "timestamp": ts.timestamp() if hasattr(ts, "timestamp") else float(ts),
                "open":   float(row.get("Open", row.get("open", 0))),
                "high":   float(row.get("High", row.get("high", 0))),
                "low":    float(row.get("Low", row.get("low", 0))),
                "close":  float(row.get("Close", row.get("close", 0))),
                "volume": float(row.get("Volume", row.get("volume", 0))),
            })
        return result
    except Exception as exc:
        logger.debug("_normalise_df error: %s", exc)
        return []


def _tf_to_yf_interval(timeframe: str) -> str:
    """Convert NEXUS timeframe strings to yfinance interval strings."""
    mapping = {
        "1m": "1m",  "3m": "5m",  "5m": "5m",  "15m": "15m",
        "30m": "30m","1h": "1h",  "2h": "1h",  "4h": "1h",
        "1d": "1d",  "1w": "1wk", "1M": "1mo",
    }
    return mapping.get(timeframe, "1h")
