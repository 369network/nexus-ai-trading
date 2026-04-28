# src/analysis/multi_timeframe.py
"""Multi-timeframe analysis for NEXUS ALPHA."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TF_ORDER = ["weekly", "daily", "4h", "1h", "15m", "5m", "1m"]
TF_MAP = {
    "W": "weekly", "1W": "weekly",
    "D": "daily", "1D": "daily",
    "4H": "4h", "4h": "4h",
    "1H": "1h", "1h": "1h",
    "15M": "15m", "15m": "15m",
    "5M": "5m", "5m": "5m",
    "1M": "1m", "1m": "1m",
}


@dataclass
class MTFAnalysis:
    """Result of multi-timeframe trend analysis."""

    trend_weekly: str = "N/A"
    trend_daily: str = "N/A"
    trend_4h: str = "N/A"
    trend_1h: str = "N/A"
    trend_15m: str = "N/A"
    overall_bias: str = "N/A"
    key_levels: List[float] = field(default_factory=list)
    strength_score: float = 0.5   # 0-1, 1 = all timeframes aligned
    alignment_score: float = 0.0  # 0-1
    details: Dict[str, Dict] = field(default_factory=dict)


class MultiTimeframeAnalyzer:
    """Analyse multiple timeframe DataFrames to determine overall market bias."""

    def analyze(
        self,
        symbol: str,
        market: str,
        candles_by_tf: Dict[str, pd.DataFrame],
    ) -> MTFAnalysis:
        """Run MTF analysis and return a populated :class:`MTFAnalysis`.

        Parameters
        ----------
        symbol:
            Trading symbol (e.g. "BTC/USDT").
        market:
            Market type (e.g. "crypto", "forex").
        candles_by_tf:
            Dict of timeframe label → OHLCV DataFrame.
            Keys should use consistent labels such as ``"1H"``, ``"4H"``, ``"1D"``.
        """
        result = MTFAnalysis()
        trend_map: Dict[str, str] = {}
        details: Dict[str, Dict] = {}

        for tf_label, df in candles_by_tf.items():
            normalised = TF_MAP.get(tf_label, tf_label.lower())
            trend = self.get_higher_tf_trend(tf_label, candles_by_tf)
            trend_map[normalised] = trend
            details[normalised] = self._compute_tf_details(df, trend)

        result.trend_weekly = trend_map.get("weekly", "N/A")
        result.trend_daily = trend_map.get("daily", "N/A")
        result.trend_4h = trend_map.get("4h", "N/A")
        result.trend_1h = trend_map.get("1h", "N/A")
        result.trend_15m = trend_map.get("15m", "N/A")
        result.details = details

        result.alignment_score = self.check_alignment(candles_by_tf)
        result.overall_bias = self._determine_bias(trend_map, result.alignment_score)
        result.strength_score = result.alignment_score

        # Collect key levels from all timeframes
        all_levels: List[float] = []
        for tf_detail in details.values():
            all_levels.extend(tf_detail.get("key_levels", []))
        result.key_levels = sorted(set(all_levels))

        return result

    def get_higher_tf_trend(
        self, tf: str, candles_by_tf: Dict[str, pd.DataFrame]
    ) -> str:
        """Determine trend direction for a given timeframe.

        Returns "UP", "DOWN", or "RANGING".
        """
        df = candles_by_tf.get(tf)
        if df is None or len(df) < 20:
            return "N/A"
        return self._classify_trend(df)

    def check_alignment(
        self, candles_by_tf: Dict[str, pd.DataFrame]
    ) -> float:
        """Compute alignment score across all provided timeframes.

        Returns
        -------
        float
            1.0 if all timeframes agree, 0.0 if maximally conflicting.
        """
        trends: List[str] = []
        for tf, df in candles_by_tf.items():
            if df is not None and len(df) >= 20:
                trends.append(self._classify_trend(df))

        if not trends:
            return 0.0

        # Filter out RANGING — only count directional timeframes
        directional = [t for t in trends if t in ("UP", "DOWN")]
        if not directional:
            return 0.5  # all ranging — moderate uncertainty

        up_count = directional.count("UP")
        down_count = directional.count("DOWN")
        dominant = max(up_count, down_count)
        return dominant / len(directional)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _classify_trend(self, df: pd.DataFrame) -> str:
        """Classify a DataFrame's trend as UP, DOWN, or RANGING."""
        if len(df) < 20:
            return "N/A"

        c = df["close"].values
        sma20 = float(pd.Series(c).rolling(20).mean().iloc[-1])
        sma50 = float(pd.Series(c).rolling(min(50, len(c))).mean().iloc[-1])
        current = c[-1]

        # ADX check for ranging vs trending
        adx_col = "adx" if "adx" in df.columns else None
        adx_val = float(df[adx_col].iloc[-1]) if adx_col else 0
        is_trending = adx_val > 25 if adx_col else True

        if not is_trending:
            return "RANGING"

        # Trend by MA stack
        if current > sma20 > sma50:
            return "UP"
        elif current < sma20 < sma50:
            return "DOWN"
        else:
            # Check slope of SMA20 over last 5 bars
            recent_sma = pd.Series(c).rolling(20).mean().iloc[-5:].values
            if len(recent_sma) >= 5:
                slope = recent_sma[-1] - recent_sma[0]
                if slope > 0:
                    return "UP"
                elif slope < 0:
                    return "DOWN"
            return "RANGING"

    def _determine_bias(
        self, trend_map: Dict[str, str], alignment_score: float
    ) -> str:
        """Determine overall bias from the trend map."""
        # Weight higher timeframes more
        weights = {
            "weekly": 4,
            "daily": 3,
            "4h": 2,
            "1h": 1,
            "15m": 0.5,
        }

        up_score = 0.0
        down_score = 0.0
        total_weight = 0.0

        for tf, trend in trend_map.items():
            w = weights.get(tf, 1)
            total_weight += w
            if trend == "UP":
                up_score += w
            elif trend == "DOWN":
                down_score += w

        if total_weight == 0:
            return "N/A"

        net = (up_score - down_score) / total_weight

        if net > 0.5:
            return "BULLISH"
        elif net > 0.2:
            return "SLIGHTLY_BULLISH"
        elif net < -0.5:
            return "BEARISH"
        elif net < -0.2:
            return "SLIGHTLY_BEARISH"
        else:
            return "NEUTRAL"

    def _compute_tf_details(
        self, df: pd.DataFrame, trend: str
    ) -> Dict:
        """Extract key statistics and levels from a single timeframe DataFrame."""
        if df is None or len(df) < 5:
            return {"trend": trend, "key_levels": []}

        h = df["high"].values
        lo = df["low"].values
        c = df["close"].values

        # Simple pivot-based key levels
        n = min(20, len(df))
        recent_h = h[-n:]
        recent_lo = lo[-n:]

        swing_high = float(recent_h.max())
        swing_low = float(recent_lo.min())
        mid = (swing_high + swing_low) / 2

        key_levels = [swing_low, mid, swing_high]

        # Add SMA levels if pre-computed
        if "sma20" in df.columns:
            key_levels.append(float(df["sma20"].iloc[-1]))
        if "sma50" in df.columns:
            key_levels.append(float(df["sma50"].iloc[-1]))

        return {
            "trend": trend,
            "swing_high": swing_high,
            "swing_low": swing_low,
            "current_close": float(c[-1]),
            "key_levels": [l for l in key_levels if l > 0],
        }
