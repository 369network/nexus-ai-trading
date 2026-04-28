"""
NEXUS ALPHA - Indicator Engine
================================
Per-symbol wrapper around src/analysis/indicators.py.
Maintains a rolling OHLCV DataFrame per (symbol, timeframe) and computes
all TechnicalIndicators on each new candle.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

import pandas as pd

from src.analysis.indicators import compute_indicators

logger = logging.getLogger(__name__)

# Maximum number of candles retained per (symbol, timeframe) rolling window
MAX_ROLLING_CANDLES = 500

# Minimum candles required before indicators can be computed meaningfully
MIN_CANDLES_FOR_INDICATORS = 30

# Required OHLCV columns
_OHLCV_COLS = ["open", "high", "low", "close", "volume"]


class IndicatorEngine:
    """
    Wraps src/analysis/indicators.py for per-symbol, per-timeframe computation.

    Maintains an internal rolling DataFrame for each timeframe so that indicators
    requiring lookback periods (e.g. SMA200, ATR14) can be accurately computed.

    Parameters
    ----------
    symbol : str
        Trading symbol (e.g. "BTC/USDT").
    market : str
        Market segment (e.g. "crypto", "forex").
    settings : Settings
        Application settings (reserved for future config overrides).
    max_candles : int
        How many candles to retain per timeframe rolling window.
    """

    def __init__(
        self,
        symbol: str,
        market: str,
        settings: Any,
        max_candles: int = MAX_ROLLING_CANDLES,
    ) -> None:
        self._symbol = symbol
        self._market = market
        self._settings = settings
        self._max_candles = max_candles

        # Rolling candle lists keyed by timeframe
        self._candles: Dict[str, List[Dict[str, Any]]] = defaultdict(list)

        logger.debug(
            "IndicatorEngine created: symbol=%s market=%s max_candles=%d",
            symbol, market, max_candles,
        )

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def compute(self, candle: Dict[str, Any], timeframe: str) -> Dict[str, Any]:
        """
        Append *candle* to the rolling window for *timeframe*, then compute
        all technical indicators and return them as a flat dict.

        Parameters
        ----------
        candle : dict
            Must contain keys: open, high, low, close, volume.
            May optionally include: timestamp, trades (ignored if missing).
        timeframe : str
            Candle interval string (e.g. "15m", "1h", "4h").

        Returns
        -------
        dict
            All indicator values for the latest (most recent) candle.
            Returns an empty dict if fewer than MIN_CANDLES_FOR_INDICATORS
            candles have been collected.
        """
        self._append_candle(candle, timeframe)

        candle_list = self._candles[timeframe]
        if len(candle_list) < MIN_CANDLES_FOR_INDICATORS:
            logger.debug(
                "IndicatorEngine: %s/%s only %d/%d candles — skipping indicator computation",
                self._symbol, timeframe, len(candle_list), MIN_CANDLES_FOR_INDICATORS,
            )
            return {}

        try:
            df = self._to_dataframe(candle_list)
            df_with_indicators = compute_indicators(df)
            result = self._extract_latest_row(df_with_indicators)
            return result
        except Exception as exc:
            logger.error(
                "IndicatorEngine: failed to compute indicators for %s/%s: %s",
                self._symbol, timeframe, exc, exc_info=True,
            )
            return {}

    def get_dataframe(self, timeframe: str) -> Optional[pd.DataFrame]:
        """
        Return the current rolling DataFrame for *timeframe* with indicators,
        or None if fewer than MIN_CANDLES_FOR_INDICATORS candles are available.
        """
        candle_list = self._candles.get(timeframe, [])
        if len(candle_list) < MIN_CANDLES_FOR_INDICATORS:
            return None
        try:
            df = self._to_dataframe(candle_list)
            return compute_indicators(df)
        except Exception as exc:
            logger.error(
                "IndicatorEngine.get_dataframe: %s/%s failed: %s",
                self._symbol, timeframe, exc,
            )
            return None

    def candle_count(self, timeframe: str) -> int:
        """Return the number of candles currently stored for *timeframe*."""
        return len(self._candles.get(timeframe, []))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _append_candle(self, candle: Dict[str, Any], timeframe: str) -> None:
        """Append a candle to the rolling window, pruning if necessary."""
        # Normalise keys to lowercase
        normalised = {k.lower(): v for k, v in candle.items()}
        self._candles[timeframe].append(normalised)

        # Prune to max_candles
        if len(self._candles[timeframe]) > self._max_candles:
            self._candles[timeframe] = self._candles[timeframe][-self._max_candles:]

    def _to_dataframe(self, candle_list: List[Dict[str, Any]]) -> pd.DataFrame:
        """Convert the rolling candle list to a pandas DataFrame."""
        df = pd.DataFrame(candle_list)

        # Ensure required columns exist with float dtype
        for col in _OHLCV_COLS:
            if col not in df.columns:
                df[col] = 0.0
            df[col] = df[col].astype(float)

        # Try to set a DatetimeIndex from timestamp column
        if "timestamp" in df.columns:
            try:
                df.index = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
            except Exception:
                try:
                    df.index = pd.to_datetime(df["timestamp"], utc=True)
                except Exception:
                    pass  # Fall back to integer index

        return df

    def _extract_latest_row(self, df: pd.DataFrame) -> Dict[str, Any]:
        """Extract the last row of *df* as a plain dict, excluding NaN values.

        Only numeric columns are included — string metadata fields such as
        'symbol', 'timeframe', and 'market' that arrive in normalised candle
        dicts are silently skipped so they never reach float conversion.
        """
        if df.empty:
            return {}
        last_row = df.iloc[-1]
        result = {}
        for col, val in last_row.items():
            if val is None or str(val) == "nan":
                continue
            try:
                result[col] = float(val)
            except (TypeError, ValueError):
                pass  # skip non-numeric metadata (symbol, timeframe, market, …)
        return result
