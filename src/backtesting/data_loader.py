"""
NEXUS ALPHA — Historical Data Loader for Backtesting
======================================================
Loads OHLCV data from Supabase (primary), falls back to yfinance/ccxt.
Normalises timezone, handles stock splits/dividends, caches to parquet.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pandas as pd
import structlog

log = structlog.get_logger(__name__)

# Cache directory
_CACHE_DIR = Path("data/cache")
_CACHE_DIR.mkdir(parents=True, exist_ok=True)

# Supported timeframes and their pandas aliases
_TF_MAP: dict[str, str] = {
    "1m": "1min",
    "5m": "5min",
    "15m": "15min",
    "30m": "30min",
    "1h": "1h",
    "4h": "4h",
    "1d": "1D",
    "1w": "1W",
}

# yfinance timeframe aliases
_YF_TF_MAP: dict[str, str] = {
    "1m": "1m",
    "5m": "5m",
    "15m": "15m",
    "30m": "30m",
    "1h": "1h",
    "4h": "1h",   # yfinance doesn't support 4h natively; resample after
    "1d": "1d",
    "1w": "1wk",
}


# ---------------------------------------------------------------------------
# Main public function
# ---------------------------------------------------------------------------


async def load_ohlcv(
    symbol: str,
    market: str,
    start: str | datetime,
    end: str | datetime,
    timeframe: str = "1h",
    use_cache: bool = True,
    adjust_splits: bool = True,
) -> pd.DataFrame:
    """
    Load historical OHLCV data for backtesting.

    Load priority:
    1. Local parquet cache (if exists and covers the range).
    2. Supabase ``candles`` table.
    3. yfinance (for stock markets: us_stocks, indian_stocks).
    4. ccxt (for crypto).

    Args:
        symbol: Ticker symbol (e.g. "BTCUSDT", "AAPL", "RELIANCE.NS").
        market: Market type: "crypto" | "us_stocks" | "indian_stocks" | "forex".
        start: Start datetime or ISO string (UTC).
        end: End datetime or ISO string (UTC).
        timeframe: Bar timeframe: "1m" | "5m" | "15m" | "30m" | "1h" | "4h" | "1d".
        use_cache: Whether to read/write the local parquet cache.
        adjust_splits: Apply split/dividend adjustment (stocks only).

    Returns:
        DataFrame with DatetimeIndex (UTC) and columns:
        open, high, low, close, volume.
        Empty DataFrame if no data found.
    """
    start_dt = _parse_dt(start)
    end_dt = _parse_dt(end)

    if timeframe not in _TF_MAP:
        raise ValueError(f"Unsupported timeframe {timeframe!r}. Choose from {list(_TF_MAP)}")

    log.info(
        "data_loader_request",
        symbol=symbol,
        market=market,
        start=start_dt.date().isoformat(),
        end=end_dt.date().isoformat(),
        timeframe=timeframe,
    )

    # 1. Try cache
    if use_cache:
        cached = _load_from_cache(symbol, market, timeframe, start_dt, end_dt)
        if cached is not None:
            log.info("data_loader_cache_hit", symbol=symbol, bars=len(cached))
            return cached

    # 2. Try Supabase
    df = await _load_from_supabase(symbol, market, start_dt, end_dt, timeframe)
    if df is not None and not df.empty:
        log.info("data_loader_supabase_hit", symbol=symbol, bars=len(df))
        df = _normalize(df, timeframe)
        if use_cache:
            _save_to_cache(df, symbol, market, timeframe)
        return df

    # 3. Try yfinance / ccxt fallback
    log.info("data_loader_fallback", symbol=symbol, market=market)
    if market in ("us_stocks", "indian_stocks"):
        df = await asyncio.to_thread(
            _load_from_yfinance, symbol, start_dt, end_dt, timeframe, adjust_splits
        )
    elif market == "crypto":
        df = await _load_from_ccxt(symbol, start_dt, end_dt, timeframe)
    elif market == "forex":
        df = await asyncio.to_thread(
            _load_from_yfinance, symbol, start_dt, end_dt, timeframe, False
        )
    else:
        log.warning("data_loader_unknown_market", market=market)
        return pd.DataFrame()

    if df is None or df.empty:
        log.warning("data_loader_no_data", symbol=symbol, market=market)
        return pd.DataFrame()

    df = _normalize(df, timeframe)
    if use_cache:
        _save_to_cache(df, symbol, market, timeframe)

    log.info("data_loader_loaded", symbol=symbol, bars=len(df))
    return df


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------


def _cache_path(symbol: str, market: str, timeframe: str) -> Path:
    safe_symbol = symbol.replace("/", "_").replace(":", "_")
    return _CACHE_DIR / f"{market}_{safe_symbol}_{timeframe}.parquet"


def _load_from_cache(
    symbol: str,
    market: str,
    timeframe: str,
    start: datetime,
    end: datetime,
) -> Optional[pd.DataFrame]:
    """Return cached data if it covers the requested range, else None."""
    path = _cache_path(symbol, market, timeframe)
    if not path.exists():
        return None
    try:
        df = pd.read_parquet(path)
        if df.empty:
            return None
        df.index = pd.to_datetime(df.index, utc=True)
        # Check coverage
        cache_start = df.index.min()
        cache_end = df.index.max()
        if cache_start > pd.Timestamp(start, tz="UTC") or cache_end < pd.Timestamp(end, tz="UTC"):
            # Cache doesn't fully cover; return partial and re-fetch remaining
            mask = (df.index >= pd.Timestamp(start, tz="UTC")) & (df.index <= pd.Timestamp(end, tz="UTC"))
            subset = df[mask]
            if len(subset) > 0:
                return subset
            return None
        mask = (df.index >= pd.Timestamp(start, tz="UTC")) & (df.index <= pd.Timestamp(end, tz="UTC"))
        return df[mask]
    except Exception as exc:
        log.warning("data_loader_cache_read_error", path=str(path), error=str(exc))
        return None


def _save_to_cache(df: pd.DataFrame, symbol: str, market: str, timeframe: str) -> None:
    """Merge new data with existing cache and persist to parquet."""
    path = _cache_path(symbol, market, timeframe)
    try:
        if path.exists():
            existing = pd.read_parquet(path)
            existing.index = pd.to_datetime(existing.index, utc=True)
            df = pd.concat([existing, df]).drop_duplicates().sort_index()
        df.to_parquet(path, engine="pyarrow", compression="snappy")
        log.debug("data_loader_cache_saved", path=str(path), rows=len(df))
    except Exception as exc:
        log.warning("data_loader_cache_write_error", path=str(path), error=str(exc))


# ---------------------------------------------------------------------------
# Supabase loader
# ---------------------------------------------------------------------------


async def _load_from_supabase(
    symbol: str,
    market: str,
    start: datetime,
    end: datetime,
    timeframe: str,
) -> Optional[pd.DataFrame]:
    """Attempt to load OHLCV rows from the Supabase candles table."""
    try:
        from src.db.supabase_client import SupabaseClient
        from src.config import get_settings

        settings = get_settings()
        client = await SupabaseClient.get_instance(
            url=settings.supabase_url,
            key=settings.supabase_service_key,
        )

        rows = await client.fetch_candles(
            symbol=symbol,
            market=market,
            start=start.isoformat(),
            end=end.isoformat(),
            timeframe=timeframe,
        )
        if not rows:
            return None

        df = pd.DataFrame(rows)
        ts_col = next((c for c in ("timestamp", "time", "date", "open_time") if c in df.columns), None)
        if ts_col:
            df.index = pd.to_datetime(df[ts_col], utc=True)
            df = df.drop(columns=[ts_col])
        return df

    except ImportError:
        log.debug("data_loader_supabase_import_skipped")
        return None
    except Exception as exc:
        log.warning("data_loader_supabase_error", error=str(exc))
        return None


# ---------------------------------------------------------------------------
# yfinance loader
# ---------------------------------------------------------------------------


def _load_from_yfinance(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str,
    adjust_splits: bool,
) -> Optional[pd.DataFrame]:
    """Load OHLCV from yfinance with optional split/dividend adjustment."""
    try:
        import yfinance as yf

        yf_tf = _YF_TF_MAP.get(timeframe, "1h")
        ticker = yf.Ticker(symbol)

        df = ticker.history(
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval=yf_tf,
            auto_adjust=adjust_splits,   # handles splits + dividends
            prepost=False,
            repair=True,
        )

        if df.empty:
            return None

        df.columns = [c.lower() for c in df.columns]
        df.index = pd.to_datetime(df.index, utc=True)

        # Drop yfinance extras
        keep = ["open", "high", "low", "close", "volume"]
        df = df[[c for c in keep if c in df.columns]]

        # Resample 4h from 1h if needed
        if timeframe == "4h" and yf_tf == "1h":
            df = df.resample("4h").agg(
                {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
            ).dropna()

        return df

    except Exception as exc:
        log.warning("data_loader_yfinance_error", symbol=symbol, error=str(exc))
        return None


# ---------------------------------------------------------------------------
# ccxt loader (async)
# ---------------------------------------------------------------------------


async def _load_from_ccxt(
    symbol: str,
    start: datetime,
    end: datetime,
    timeframe: str,
) -> Optional[pd.DataFrame]:
    """Load OHLCV from ccxt (Binance by default) for crypto symbols."""
    try:
        import ccxt.async_support as ccxt_async

        exchange = ccxt_async.binance(
            {
                "enableRateLimit": True,
                "options": {"defaultType": "future"},
            }
        )

        since_ms = int(start.timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000)
        tf = timeframe if timeframe != "4h" else "4h"

        all_candles: list[list] = []
        batch_limit = 1000

        try:
            while since_ms < end_ms:
                candles = await exchange.fetch_ohlcv(
                    symbol, timeframe=tf, since=since_ms, limit=batch_limit
                )
                if not candles:
                    break
                all_candles.extend(candles)
                last_ts = candles[-1][0]
                if last_ts <= since_ms:
                    break
                since_ms = last_ts + 1
                # Brief pause to respect rate limits
                await asyncio.sleep(0.1)
        finally:
            await exchange.close()

        if not all_candles:
            return None

        df = pd.DataFrame(
            all_candles, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df = df.drop(columns=["timestamp"])
        df = df[df.index <= pd.Timestamp(end, tz="UTC")]
        return df

    except ImportError:
        log.warning("data_loader_ccxt_not_installed")
        return None
    except Exception as exc:
        log.warning("data_loader_ccxt_error", symbol=symbol, error=str(exc))
        return None


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------


def _normalize(df: pd.DataFrame, timeframe: str) -> pd.DataFrame:
    """
    Standardise a raw DataFrame to clean OHLCV format.

    - Lowercase column names.
    - UTC DatetimeIndex.
    - Drop NaN rows in OHLCV.
    - Resample to requested timeframe to fill any gaps.
    - Cast to float64.
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    # Ensure UTC index
    if not isinstance(df.index, pd.DatetimeIndex):
        df.index = pd.to_datetime(df.index, utc=True)
    elif df.index.tz is None:
        df.index = df.index.tz_localize("UTC")
    else:
        df.index = df.index.tz_convert("UTC")

    df = df.sort_index()

    # Keep standard columns only
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            df[col] = float("nan")

    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    df = df.dropna(subset=["open", "high", "low", "close"])

    # Resample to requested timeframe to fill gaps
    tf_alias = _TF_MAP.get(timeframe, "1h")
    try:
        df = df.resample(tf_alias).agg(
            {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
        ).dropna(subset=["open", "close"])
    except Exception as exc:
        log.warning("data_loader_resample_error", timeframe=timeframe, error=str(exc))

    return df


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------


def _parse_dt(value: str | datetime) -> datetime:
    """Parse a datetime or ISO string, ensuring UTC awareness."""
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    dt = pd.Timestamp(value)
    if dt.tzinfo is None:
        dt = dt.tz_localize("UTC")
    return dt.to_pydatetime()
