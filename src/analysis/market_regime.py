# src/analysis/market_regime.py
"""Market regime detection for NEXUS ALPHA."""

from __future__ import annotations

import logging
from enum import Enum
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class MarketRegime(Enum):
    """Enumeration of detectable market regimes."""

    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    RANGING = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT = "BREAKOUT"
    BREAKDOWN = "BREAKDOWN"


# ---------------------------------------------------------------------------
# Strategy parameter sets per regime
# ---------------------------------------------------------------------------

_REGIME_PARAMS: Dict[MarketRegime, Dict[str, Any]] = {
    MarketRegime.TRENDING_UP: {
        "entry_style": "pullback",
        "rsi_buy_threshold": 45,
        "rsi_sell_threshold": 75,
        "stop_atr_multiplier": 2.0,
        "tp_atr_multiplier": 4.0,
        "position_size_factor": 1.2,
        "use_trailing_stop": True,
    },
    MarketRegime.TRENDING_DOWN: {
        "entry_style": "bounce",
        "rsi_buy_threshold": 25,
        "rsi_sell_threshold": 55,
        "stop_atr_multiplier": 2.0,
        "tp_atr_multiplier": 4.0,
        "position_size_factor": 1.2,
        "use_trailing_stop": True,
    },
    MarketRegime.RANGING: {
        "entry_style": "mean_reversion",
        "rsi_buy_threshold": 30,
        "rsi_sell_threshold": 70,
        "stop_atr_multiplier": 1.5,
        "tp_atr_multiplier": 2.0,
        "position_size_factor": 0.8,
        "use_trailing_stop": False,
    },
    MarketRegime.HIGH_VOLATILITY: {
        "entry_style": "wait",
        "rsi_buy_threshold": 25,
        "rsi_sell_threshold": 75,
        "stop_atr_multiplier": 3.0,
        "tp_atr_multiplier": 5.0,
        "position_size_factor": 0.5,
        "use_trailing_stop": True,
    },
    MarketRegime.LOW_VOLATILITY: {
        "entry_style": "breakout_anticipation",
        "rsi_buy_threshold": 40,
        "rsi_sell_threshold": 60,
        "stop_atr_multiplier": 1.5,
        "tp_atr_multiplier": 3.0,
        "position_size_factor": 1.0,
        "use_trailing_stop": False,
    },
    MarketRegime.BREAKOUT: {
        "entry_style": "breakout_follow",
        "rsi_buy_threshold": 50,
        "rsi_sell_threshold": 80,
        "stop_atr_multiplier": 1.5,
        "tp_atr_multiplier": 4.0,
        "position_size_factor": 1.3,
        "use_trailing_stop": True,
    },
    MarketRegime.BREAKDOWN: {
        "entry_style": "breakdown_follow",
        "rsi_buy_threshold": 20,
        "rsi_sell_threshold": 50,
        "stop_atr_multiplier": 1.5,
        "tp_atr_multiplier": 4.0,
        "position_size_factor": 1.3,
        "use_trailing_stop": True,
    },
}


class MarketRegimeDetector:
    """Classify the current market environment using ADX, ATR, and Bollinger Bands."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def detect(
        self,
        df: pd.DataFrame,
        adx_trend_threshold: float = 25.0,
        vol_percentile_high: float = 80.0,
        vol_percentile_low: float = 20.0,
        bb_breakout_pct: float = 1.0,
    ) -> MarketRegime:
        """Detect the current market regime.

        Decision tree:
        1. Check for breakout / breakdown (price outside BB by large margin)
        2. ADX > threshold → trending (use slope for direction)
        3. ATR percentile → high/low volatility regime
        4. Default → ranging
        """
        if len(df) < 30:
            return MarketRegime.RANGING

        c = df["close"].values
        current = float(c[-1])

        # --- Pre-fetch indicator values (use pre-computed cols if available) ---
        adx = self._get_indicator(df, "adx", self._compute_adx, df)
        atr = self._get_indicator(df, "atr14", self._compute_atr, df)
        bb_upper = self._get_indicator(df, "bb_upper", self._compute_bb_upper, df)
        bb_lower = self._get_indicator(df, "bb_lower", self._compute_bb_lower, df)

        # --- ATR percentile for volatility classification ---
        atr_series = self._rolling_atr(df, period=14)
        vol_pct = _percentile_rank(atr_series, atr) if atr > 0 else 50.0

        # --- 1. Breakout / breakdown check ---
        if bb_upper > 0 and current > bb_upper * (1 + bb_breakout_pct / 100):
            return MarketRegime.BREAKOUT
        if bb_lower > 0 and current < bb_lower * (1 - bb_breakout_pct / 100):
            return MarketRegime.BREAKDOWN

        # --- 2. Trending check ---
        if adx > adx_trend_threshold:
            sma20 = float(pd.Series(c).rolling(20).mean().iloc[-1])
            sma50 = float(pd.Series(c).rolling(min(50, len(c))).mean().iloc[-1])
            if current > sma20 and sma20 > sma50:
                return MarketRegime.TRENDING_UP
            elif current < sma20 and sma20 < sma50:
                return MarketRegime.TRENDING_DOWN

        # --- 3. Volatility regime ---
        if vol_pct >= vol_percentile_high:
            return MarketRegime.HIGH_VOLATILITY
        if vol_pct <= vol_percentile_low:
            return MarketRegime.LOW_VOLATILITY

        # --- 4. Default ---
        return MarketRegime.RANGING

    def detect_regime_change(
        self, df: pd.DataFrame, lookback: int = 20
    ) -> bool:
        """Return True if the regime has changed in the last *lookback* bars."""
        if len(df) < lookback + 10:
            return False

        current_regime = self.detect(df)
        prior_regime = self.detect(df.iloc[:-lookback])

        return current_regime != prior_regime

    def get_regime_params(self, regime: MarketRegime) -> Dict[str, Any]:
        """Return strategy tuning parameters suitable for *regime*."""
        return dict(_REGIME_PARAMS.get(regime, _REGIME_PARAMS[MarketRegime.RANGING]))

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _get_indicator(
        df: pd.DataFrame, col: str, fallback_fn, *args
    ) -> float:
        """Read a pre-computed indicator column or compute on-the-fly."""
        if col in df.columns:
            val = df[col].iloc[-1]
            if pd.notna(val):
                return float(val)
        return float(fallback_fn(*args))

    @staticmethod
    def _compute_adx(df: pd.DataFrame, period: int = 14) -> float:
        h = df["high"].values
        lo = df["low"].values
        c = df["close"].values
        n = len(c)
        if n < period + 1:
            return 0.0

        prev_c = np.roll(c, 1)
        prev_c[0] = c[0]
        tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
        atr = pd.Series(tr).rolling(period).mean().values

        dm_plus = np.where((h[1:] - h[:-1]) > (lo[:-1] - lo[1:]),
                           np.maximum(h[1:] - h[:-1], 0), 0)
        dm_minus = np.where((lo[:-1] - lo[1:]) > (h[1:] - h[:-1]),
                            np.maximum(lo[:-1] - lo[1:], 0), 0)

        atr_trim = atr[1:]
        di_plus = np.where(atr_trim > 0, 100 * pd.Series(dm_plus).rolling(period).mean().values / atr_trim, 0)
        di_minus = np.where(atr_trim > 0, 100 * pd.Series(dm_minus).rolling(period).mean().values / atr_trim, 0)
        dx = np.where((di_plus + di_minus) > 0,
                      100 * np.abs(di_plus - di_minus) / (di_plus + di_minus), 0)
        adx = pd.Series(dx).rolling(period).mean().values
        return float(adx[-1]) if adx[-1] != np.nan else 0.0

    @staticmethod
    def _compute_atr(df: pd.DataFrame, period: int = 14) -> float:
        h = df["high"].values
        lo = df["low"].values
        c = df["close"].values
        prev_c = np.roll(c, 1)
        prev_c[0] = c[0]
        tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
        atr = pd.Series(tr).rolling(period).mean().values
        return float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

    @staticmethod
    def _compute_bb_upper(df: pd.DataFrame, period: int = 20, std: float = 2) -> float:
        mid = df["close"].rolling(period).mean()
        std_val = df["close"].rolling(period).std()
        return float((mid + std * std_val).iloc[-1])

    @staticmethod
    def _compute_bb_lower(df: pd.DataFrame, period: int = 20, std: float = 2) -> float:
        mid = df["close"].rolling(period).mean()
        std_val = df["close"].rolling(period).std()
        return float((mid - std * std_val).iloc[-1])

    @staticmethod
    def _rolling_atr(df: pd.DataFrame, period: int = 14) -> np.ndarray:
        h = df["high"].values
        lo = df["low"].values
        c = df["close"].values
        prev_c = np.roll(c, 1)
        prev_c[0] = c[0]
        tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
        return pd.Series(tr).rolling(period).mean().values


def _percentile_rank(series: np.ndarray, value: float) -> float:
    """Return what percentile *value* falls at within *series*."""
    valid = series[~np.isnan(series)]
    if len(valid) == 0:
        return 50.0
    return float(np.sum(valid <= value) / len(valid) * 100)
