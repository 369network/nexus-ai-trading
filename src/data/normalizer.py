"""
NEXUS ALPHA - Unified Data Normalizer
======================================
Converts raw provider-specific dicts into canonical OHLCV objects,
fills gaps in candle series, detects outliers, validates individual
candles, and resamples between timeframes.
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Callable, Dict, List, Optional, Tuple

import numpy as np

from .base import OHLCV

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Standalone helper used by MarketOrchestrator
# ---------------------------------------------------------------------------

def normalize_candle(raw: dict, market: str, symbol: str, timeframe: str) -> dict:
    """
    Normalise a raw candle dict from any provider into a canonical form.

    The canonical dict always has:
        timestamp  – UTC milliseconds (int)
        open, high, low, close, volume – float
        symbol, timeframe, market      – str

    This is a lightweight wrapper so callers do not need to instantiate
    :class:`UnifiedDataNormalizer` for simple dict-to-dict normalization.
    """
    ts_raw = raw.get("timestamp", raw.get("time", raw.get("ts", raw.get("t", 0))))
    ts_num = float(ts_raw) if ts_raw else 0.0

    # Convert to milliseconds if needed
    if ts_num > 1e15:                 # nanoseconds
        ts_ms = int(ts_num / 1_000_000)
    elif ts_num >= 1e12:              # already milliseconds
        ts_ms = int(ts_num)
    elif ts_num > 0:                  # seconds
        ts_ms = int(ts_num * 1_000)
    else:
        ts_ms = 0

    def _f(keys, default=0.0):
        for k in keys:
            v = raw.get(k)
            if v is not None:
                return float(v)
        return float(default)

    return {
        "timestamp": ts_ms,
        "open":   _f(["open", "o", "Open"]),
        "high":   _f(["high", "h", "High"]),
        "low":    _f(["low",  "l", "Low"]),
        "close":  _f(["close", "c", "Close"]),
        "volume": _f(["volume", "v", "Volume", "vol"]),
        "symbol":    raw.get("symbol", symbol),
        "timeframe": raw.get("timeframe", raw.get("interval", timeframe)),
        "market":    raw.get("market", market),
    }

# ---------------------------------------------------------------------------
# Interval helpers
# ---------------------------------------------------------------------------

_INTERVAL_SECONDS: Dict[str, int] = {
    "1s": 1,
    "1m": 60,
    "3m": 180,
    "5m": 300,
    "15m": 900,
    "30m": 1_800,
    "1h": 3_600,
    "2h": 7_200,
    "4h": 14_400,
    "6h": 21_600,
    "8h": 28_800,
    "12h": 43_200,
    "1d": 86_400,
    "3d": 259_200,
    "1w": 604_800,
    "1M": 2_592_000,  # approximate
}


def _interval_ms(interval: str) -> int:
    """Return the number of milliseconds for a given interval string."""
    if interval not in _INTERVAL_SECONDS:
        raise ValueError(f"Unknown interval: {interval!r}. "
                         f"Supported: {sorted(_INTERVAL_SECONDS)}")
    return _INTERVAL_SECONDS[interval] * 1_000


def _gcd_interval(a: str, b: str) -> bool:
    """Return True if interval *a* divides evenly into interval *b*."""
    ms_a = _interval_ms(a)
    ms_b = _interval_ms(b)
    return ms_b % ms_a == 0 and ms_b >= ms_a


# ---------------------------------------------------------------------------
# Source-specific timestamp normalisation rules
# ---------------------------------------------------------------------------

# Timestamps whose raw value is measured in seconds (not milliseconds)
_SECOND_SOURCES = {"oanda", "kite", "alpaca", "yfinance", "nsepython"}


class UnifiedDataNormalizer:
    """
    Central normalisation layer for all market data.

    All methods are pure (no side effects, no I/O).  They accept raw
    provider data and return canonical NEXUS ALPHA types.
    """

    # Number of standard deviations to consider a candle an outlier
    OUTLIER_ZSCORE_THRESHOLD = 3.5

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize_ohlcv(
        self,
        raw: dict,
        source: str,
        market: str,
    ) -> OHLCV:
        """
        Convert a raw provider dict into an :class:`OHLCV`.

        The method tries a sequence of common key aliases and falls back
        to sensible defaults so it works across Binance, OANDA, Kite,
        Alpaca, and CCXT-compatible payloads.

        Parameters
        ----------
        raw:
            Provider-native dict (or list that has been pre-converted).
        source:
            Provider identifier string, e.g. ``"binance"``.
        market:
            Market type string, e.g. ``"spot"``, ``"futures"``, ``"forex"``.

        Returns
        -------
        OHLCV
        """
        def _pick(keys: List[str], default=0.0):
            for k in keys:
                if k in raw and raw[k] is not None:
                    return raw[k]
            return default

        ts_raw = _pick(["t", "T", "time", "timestamp", "openTime",
                         "open_time", "ts", "date"])
        timestamp_ms = self.normalize_timestamp(ts_raw, source)

        open_  = float(_pick(["o", "open",  "Open",  "mid.o"]))
        high   = float(_pick(["h", "high",  "High",  "mid.h"]))
        low    = float(_pick(["l", "low",   "Low",   "mid.l"]))
        close  = float(_pick(["c", "close", "Close", "mid.c"]))
        volume = float(_pick(["v", "volume", "Volume", "baseAssetVolume",
                               "base_volume", "vol", "volume"]))

        quote_vol = float(_pick(["q", "quoteAssetVolume", "quote_volume",
                                  "quoteVolume", "amount"], default=0.0))
        trades    = int(_pick(["n", "trades", "tradeCount",
                                "number_of_trades"], default=0))
        taker_buy = float(_pick(["V", "takerBuyBaseAssetVolume",
                                  "taker_buy_volume"], default=0.0))

        # VWAP: use provided or compute from quote_vol/volume
        vwap_raw = _pick(["vwap", "VWAP", "Vwap"], default=None)
        if vwap_raw is not None and float(vwap_raw) > 0:
            vwap = float(vwap_raw)
        elif volume > 0 and quote_vol > 0:
            vwap = quote_vol / volume
        else:
            vwap = (open_ + high + low + close) / 4.0

        symbol   = str(_pick(["s", "symbol", "instrument",
                               "ticker", "pair"], default="UNKNOWN"))
        interval = str(_pick(["interval", "granularity",
                               "timeframe", "tf"], default="1m"))
        complete = bool(_pick(["x", "complete", "closed",
                                "is_closed"], default=True))

        return OHLCV(
            timestamp_ms=timestamp_ms,
            open=open_,
            high=high,
            low=low,
            close=close,
            volume=volume,
            quote_volume=quote_vol,
            trades=trades,
            vwap=vwap,
            taker_buy_volume=taker_buy,
            source=source,
            market=market,
            symbol=symbol,
            interval=interval,
            complete=complete,
        )

    # ------------------------------------------------------------------

    def normalize_timestamp(self, ts, source: str) -> int:
        """
        Normalise any timestamp representation to UTC milliseconds (int).

        Handles:
        * int/float in milliseconds (>= 1e12)
        * int/float in seconds (< 1e12)
        * ISO-8601 strings with or without timezone
        * :class:`datetime` objects

        Parameters
        ----------
        ts:
            Raw timestamp value from the provider.
        source:
            Provider name used to apply source-specific heuristics.

        Returns
        -------
        int
            UTC milliseconds since Unix epoch.
        """
        if ts is None:
            raise ValueError("Cannot normalise None timestamp")

        # datetime object
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return int(ts.timestamp() * 1_000)

        # string: parse as ISO-8601
        if isinstance(ts, str):
            ts = ts.rstrip("Z")
            for fmt in (
                "%Y-%m-%dT%H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d",
            ):
                try:
                    dt = datetime.strptime(ts, fmt)
                    return int(dt.replace(tzinfo=timezone.utc).timestamp() * 1_000)
                except ValueError:
                    continue
            raise ValueError(f"Cannot parse timestamp string: {ts!r}")

        ts_num = float(ts)

        # Nanosecond timestamps (> 1e18)
        if ts_num > 1e18:
            return int(ts_num / 1_000_000)

        # Microsecond timestamps (> 1e15)
        if ts_num > 1e15:
            return int(ts_num / 1_000)

        # Already milliseconds
        if ts_num >= 1e12:
            return int(ts_num)

        # Source known to use seconds
        if source in _SECOND_SOURCES:
            return int(ts_num * 1_000)

        # Heuristic: if < 2e10 treat as seconds, otherwise milliseconds
        if ts_num < 2e10:
            return int(ts_num * 1_000)

        return int(ts_num)

    # ------------------------------------------------------------------

    def fill_gaps(
        self,
        candles: List[OHLCV],
        interval: str,
    ) -> List[OHLCV]:
        """
        Insert synthetic candles for any gaps in a candle series.

        A gap exists when two consecutive candles are more than one
        interval apart.  The synthetic candle copies the close of the
        previous bar as open/high/low/close with zero volume.

        Parameters
        ----------
        candles:
            Chronologically sorted OHLCV list.
        interval:
            Expected interval between consecutive candles.

        Returns
        -------
        List[OHLCV]
            Sorted candle list with gaps filled.
        """
        if len(candles) < 2:
            return list(candles)

        step_ms = _interval_ms(interval)
        result: List[OHLCV] = []
        candles = sorted(candles, key=lambda c: c.timestamp_ms)

        for i, candle in enumerate(candles):
            result.append(candle)
            if i == len(candles) - 1:
                break

            next_candle = candles[i + 1]
            expected_ts = candle.timestamp_ms + step_ms

            while expected_ts < next_candle.timestamp_ms:
                fill_price = candle.close
                synthetic = OHLCV(
                    timestamp_ms=expected_ts,
                    open=fill_price,
                    high=fill_price,
                    low=fill_price,
                    close=fill_price,
                    volume=0.0,
                    quote_volume=0.0,
                    trades=0,
                    vwap=fill_price,
                    taker_buy_volume=0.0,
                    source=candle.source,
                    market=candle.market,
                    symbol=candle.symbol,
                    interval=interval,
                    complete=True,
                )
                result.append(synthetic)
                expected_ts += step_ms

        return result

    # ------------------------------------------------------------------

    def detect_outliers(
        self,
        candles: List[OHLCV],
    ) -> List[int]:
        """
        Return indices of candles whose close price is statistically
        anomalous (|z-score| > OUTLIER_ZSCORE_THRESHOLD).

        Uses a robust estimator (median ± MAD) so a cluster of bad ticks
        does not distort the threshold.

        Parameters
        ----------
        candles:
            List of OHLCV candles.

        Returns
        -------
        List[int]
            Indices (0-based) of anomalous candles.
        """
        if len(candles) < 10:
            return []

        closes = np.array([c.close for c in candles], dtype=np.float64)
        median = np.median(closes)
        mad = np.median(np.abs(closes - median))

        if mad == 0:
            return []

        # Modified Z-score
        z_scores = 0.6745 * (closes - median) / mad
        return [i for i, z in enumerate(z_scores)
                if abs(z) > self.OUTLIER_ZSCORE_THRESHOLD]

    # ------------------------------------------------------------------

    def validate_candle(self, candle: OHLCV) -> bool:
        """
        Return True if a candle passes all quality checks.

        Checks performed:
        * No NaN or Inf in OHLCV fields.
        * ``high >= max(open, close)``
        * ``low  <= min(open, close)``
        * ``high >= low``
        * Positive volume.
        * Non-negative quote_volume and trades.

        Parameters
        ----------
        candle:
            OHLCV instance to validate.

        Returns
        -------
        bool
        """
        for attr in ("open", "high", "low", "close", "volume", "vwap"):
            val = getattr(candle, attr)
            if math.isnan(val) or math.isinf(val):
                logger.debug("validate_candle FAIL: %s has nan/inf %s", attr, val)
                return False

        if candle.high < max(candle.open, candle.close):
            logger.debug("validate_candle FAIL: high < max(open, close)")
            return False

        if candle.low > min(candle.open, candle.close):
            logger.debug("validate_candle FAIL: low > min(open, close)")
            return False

        if candle.high < candle.low:
            logger.debug("validate_candle FAIL: high < low")
            return False

        if candle.volume < 0:
            logger.debug("validate_candle FAIL: negative volume")
            return False

        if candle.quote_volume < 0:
            logger.debug("validate_candle FAIL: negative quote_volume")
            return False

        if candle.trades < 0:
            logger.debug("validate_candle FAIL: negative trades")
            return False

        return True

    # ------------------------------------------------------------------

    def resample(
        self,
        candles: List[OHLCV],
        from_interval: str,
        to_interval: str,
    ) -> List[OHLCV]:
        """
        Aggregate candles from a shorter to a longer timeframe.

        For example: 1-minute candles → 5-minute candles.

        Parameters
        ----------
        candles:
            Chronologically sorted source candles at *from_interval*.
        from_interval:
            Source interval string (e.g. ``"1m"``).
        to_interval:
            Target interval string (e.g. ``"5m"``).  Must be an integer
            multiple of *from_interval*.

        Returns
        -------
        List[OHLCV]
            Resampled candles at *to_interval*.

        Raises
        ------
        ValueError
            If *to_interval* is not an integer multiple of *from_interval*.
        """
        if not _gcd_interval(from_interval, to_interval):
            raise ValueError(
                f"to_interval {to_interval!r} is not an integer multiple "
                f"of from_interval {from_interval!r}"
            )

        if not candles:
            return []

        step_ms = _interval_ms(to_interval)
        candles = sorted(candles, key=lambda c: c.timestamp_ms)

        # Group candles into buckets aligned to the target interval
        buckets: Dict[int, List[OHLCV]] = {}
        for candle in candles:
            bucket_ts = (candle.timestamp_ms // step_ms) * step_ms
            buckets.setdefault(bucket_ts, []).append(candle)

        result: List[OHLCV] = []
        for bucket_ts in sorted(buckets):
            group = buckets[bucket_ts]

            open_   = group[0].open
            high    = max(c.high for c in group)
            low     = min(c.low  for c in group)
            close   = group[-1].close
            volume  = sum(c.volume for c in group)
            q_vol   = sum(c.quote_volume for c in group)
            trades  = sum(c.trades for c in group)
            taker   = sum(c.taker_buy_volume for c in group)
            vwap    = q_vol / volume if volume > 0 else (open_ + high + low + close) / 4

            # A resampled candle is complete only if all source candles are
            all_complete = all(c.complete for c in group)

            result.append(OHLCV(
                timestamp_ms=bucket_ts,
                open=open_,
                high=high,
                low=low,
                close=close,
                volume=volume,
                quote_volume=q_vol,
                trades=trades,
                vwap=vwap,
                taker_buy_volume=taker,
                source=group[0].source,
                market=group[0].market,
                symbol=group[0].symbol,
                interval=to_interval,
                complete=all_complete,
            ))

        return result
