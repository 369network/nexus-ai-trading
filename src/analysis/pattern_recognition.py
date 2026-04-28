# src/analysis/pattern_recognition.py
"""Chart pattern and candlestick pattern recognition for NEXUS ALPHA."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import talib
    _TALIB_AVAILABLE = True
except ImportError:
    _TALIB_AVAILABLE = False
    logger.warning("TA-Lib not available — candlestick pattern detection disabled")


@dataclass
class ChartPattern:
    """A detected chart pattern."""

    name: str
    confidence: float        # 0.0–1.0
    direction: str           # "BULLISH" | "BEARISH" | "NEUTRAL"
    target_price: Optional[float]
    invalidation_price: Optional[float]
    bars_ago: int            # bars since pattern completion


@dataclass
class CandlePattern:
    """A single-bar or multi-bar candlestick pattern."""

    name: str
    direction: str           # "BULLISH" | "BEARISH" | "NEUTRAL"
    strength: int            # 1–3 (1=weak, 3=strong)
    bars_ago: int


class PatternRecognizer:
    """Detect both chart patterns and candlestick patterns."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def detect_patterns(
        self, df: pd.DataFrame, min_confidence: float = 0.5
    ) -> List[ChartPattern]:
        """Detect chart patterns in *df*.

        Patterns detected:
            head_and_shoulders, inverse_hs, double_top, double_bottom,
            ascending_triangle, descending_triangle, symmetrical_triangle,
            bull_flag, bear_flag, cup_and_handle, rising_wedge, falling_wedge
        """
        if len(df) < 30:
            return []

        patterns: List[ChartPattern] = []

        detectors = [
            self._detect_head_and_shoulders,
            self._detect_inverse_hs,
            self._detect_double_top,
            self._detect_double_bottom,
            self._detect_ascending_triangle,
            self._detect_descending_triangle,
            self._detect_symmetrical_triangle,
            self._detect_bull_flag,
            self._detect_bear_flag,
            self._detect_rising_wedge,
            self._detect_falling_wedge,
            self._detect_cup_and_handle,
        ]

        for detector in detectors:
            try:
                result = detector(df)
                if result and result.confidence >= min_confidence:
                    patterns.append(result)
            except Exception as exc:
                logger.debug("Pattern detector %s failed: %s", detector.__name__, exc)

        return sorted(patterns, key=lambda p: p.confidence, reverse=True)

    def detect_candlestick_patterns(
        self, df: pd.DataFrame
    ) -> List[CandlePattern]:
        """Detect single and multi-bar candlestick patterns using TA-Lib."""
        if not _TALIB_AVAILABLE:
            return self._detect_candlestick_manual(df)

        o = df["open"].values
        h = df["high"].values
        lo = df["low"].values
        c = df["close"].values

        # Map of TA-Lib CDL function → (name, direction, strength)
        cdl_functions = {
            "CDL_DOJI": ("Doji", "NEUTRAL", 1),
            "CDL_HAMMER": ("Hammer", "BULLISH", 2),
            "CDL_INVERTEDHAMMER": ("Inverted Hammer", "BULLISH", 2),
            "CDL_SHOOTINGSTAR": ("Shooting Star", "BEARISH", 2),
            "CDL_HANGINGMAN": ("Hanging Man", "BEARISH", 2),
            "CDL_ENGULFING": ("Engulfing", None, 3),
            "CDL_MORNINGSTAR": ("Morning Star", "BULLISH", 3),
            "CDL_EVENINGSTAR": ("Evening Star", "BEARISH", 3),
            "CDL_3WHITESOLDIERS": ("Three White Soldiers", "BULLISH", 3),
            "CDL_3BLACKCROWS": ("Three Black Crows", "BEARISH", 3),
            "CDL_PIERCING": ("Piercing Line", "BULLISH", 2),
            "CDL_DARKCLOUDCOVER": ("Dark Cloud Cover", "BEARISH", 2),
            "CDL_HARAMI": ("Harami", None, 1),
            "CDL_MARUBOZU": ("Marubozu", None, 2),
            "CDL_SPINNINGTOP": ("Spinning Top", "NEUTRAL", 1),
        }

        candle_patterns: List[CandlePattern] = []
        n = len(c)

        for fn_name, (display_name, direction, strength) in cdl_functions.items():
            cdl_fn = getattr(talib, fn_name, None)
            if cdl_fn is None:
                continue
            try:
                result = cdl_fn(o, h, lo, c)
                # Find the most recent non-zero signal in the last 5 bars
                for offset in range(min(5, n)):
                    val = result[n - 1 - offset]
                    if val != 0:
                        actual_direction = direction
                        if actual_direction is None:
                            actual_direction = "BULLISH" if val > 0 else "BEARISH"
                        candle_patterns.append(
                            CandlePattern(
                                name=display_name,
                                direction=actual_direction,
                                strength=strength,
                                bars_ago=offset,
                            )
                        )
                        break  # only report most recent
            except Exception:
                pass

        return sorted(candle_patterns, key=lambda p: (p.bars_ago, -p.strength))

    # ------------------------------------------------------------------
    # Chart pattern detectors
    # ------------------------------------------------------------------

    def _detect_head_and_shoulders(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        """Detect Head and Shoulders top reversal pattern."""
        pivots = self._get_pivot_highs(df["high"].values, sensitivity=5)
        if len(pivots) < 3:
            return None

        # Last 3 swing highs
        h1_idx, h1 = pivots[-3]
        h2_idx, h2 = pivots[-2]  # should be the head (highest)
        h3_idx, h3 = pivots[-1]  # right shoulder

        if h2 <= max(h1, h3):
            return None
        if abs(h1 - h3) / h2 > 0.08:  # shoulders should be roughly equal
            return None

        # Neckline is the low between the shoulders
        neck = df["low"].iloc[h1_idx:h3_idx].min()
        current = df["close"].iloc[-1]
        confidence = 0.65 + 0.1 * (1 - abs(h1 - h3) / h2)

        target = neck - (h2 - neck)

        return ChartPattern(
            name="Head and Shoulders",
            confidence=min(0.95, confidence),
            direction="BEARISH",
            target_price=float(target),
            invalidation_price=float(h2 * 1.01),
            bars_ago=len(df) - h3_idx - 1,
        )

    def _detect_inverse_hs(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        """Detect Inverse Head and Shoulders bottom reversal."""
        pivots = self._get_pivot_lows(df["low"].values, sensitivity=5)
        if len(pivots) < 3:
            return None

        l1_idx, l1 = pivots[-3]
        l2_idx, l2 = pivots[-2]  # head (lowest)
        l3_idx, l3 = pivots[-1]  # right shoulder

        if l2 >= min(l1, l3):
            return None
        if abs(l1 - l3) / max(abs(l2), 0.001) > 0.08:
            return None

        neck = df["high"].iloc[l1_idx:l3_idx].max()
        target = neck + (neck - l2)

        return ChartPattern(
            name="Inverse Head and Shoulders",
            confidence=0.70,
            direction="BULLISH",
            target_price=float(target),
            invalidation_price=float(l2 * 0.99),
            bars_ago=len(df) - l3_idx - 1,
        )

    def _detect_double_top(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        highs = self._get_pivot_highs(df["high"].values, sensitivity=5)
        if len(highs) < 2:
            return None

        h1_idx, h1 = highs[-2]
        h2_idx, h2 = highs[-1]

        if abs(h1 - h2) / h1 > 0.03:  # within 3%
            return None

        neck = df["low"].iloc[h1_idx:h2_idx].min()
        target = neck - (h1 - neck)

        return ChartPattern(
            name="Double Top",
            confidence=0.68,
            direction="BEARISH",
            target_price=float(target),
            invalidation_price=float(max(h1, h2) * 1.01),
            bars_ago=len(df) - h2_idx - 1,
        )

    def _detect_double_bottom(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        lows = self._get_pivot_lows(df["low"].values, sensitivity=5)
        if len(lows) < 2:
            return None

        l1_idx, l1 = lows[-2]
        l2_idx, l2 = lows[-1]

        if abs(l1 - l2) / l1 > 0.03:
            return None

        neck = df["high"].iloc[l1_idx:l2_idx].max()
        target = neck + (neck - l1)

        return ChartPattern(
            name="Double Bottom",
            confidence=0.68,
            direction="BULLISH",
            target_price=float(target),
            invalidation_price=float(min(l1, l2) * 0.99),
            bars_ago=len(df) - l2_idx - 1,
        )

    def _detect_ascending_triangle(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        """Flat top resistance + rising lows = ascending triangle (bullish)."""
        n = min(50, len(df))
        recent = df.iloc[-n:]

        highs = recent["high"].values
        lows = recent["low"].values

        top_std = highs[-20:].std() / highs[-20:].mean()
        lows_slope = np.polyfit(range(20), lows[-20:], 1)[0]

        if top_std < 0.015 and lows_slope > 0:
            flat_top = highs[-20:].mean()
            target = flat_top + (flat_top - lows[-20:].min())
            return ChartPattern(
                name="Ascending Triangle",
                confidence=0.65,
                direction="BULLISH",
                target_price=float(target),
                invalidation_price=float(lows[-20:].min() * 0.99),
                bars_ago=0,
            )
        return None

    def _detect_descending_triangle(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        """Flat bottom support + declining highs = descending triangle (bearish)."""
        n = min(50, len(df))
        recent = df.iloc[-n:]

        highs = recent["high"].values
        lows = recent["low"].values

        bottom_std = lows[-20:].std() / lows[-20:].mean()
        highs_slope = np.polyfit(range(20), highs[-20:], 1)[0]

        if bottom_std < 0.015 and highs_slope < 0:
            flat_bottom = lows[-20:].mean()
            target = flat_bottom - (highs[-20:].max() - flat_bottom)
            return ChartPattern(
                name="Descending Triangle",
                confidence=0.65,
                direction="BEARISH",
                target_price=float(target),
                invalidation_price=float(highs[-20:].max() * 1.01),
                bars_ago=0,
            )
        return None

    def _detect_symmetrical_triangle(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        """Converging highs and lows = symmetrical triangle (continuation)."""
        n = min(50, len(df))
        recent = df.iloc[-n:]

        highs = recent["high"].values[-20:]
        lows = recent["low"].values[-20:]

        high_slope = np.polyfit(range(20), highs, 1)[0]
        low_slope = np.polyfit(range(20), lows, 1)[0]

        if high_slope < 0 and low_slope > 0:
            current = df["close"].iloc[-1]
            trend_prior = df["close"].iloc[-n] > df["close"].iloc[-n // 2]
            direction = "BULLISH" if trend_prior else "BEARISH"
            width = (highs.max() - lows.min())
            target = current + width * (1 if direction == "BULLISH" else -1)
            return ChartPattern(
                name="Symmetrical Triangle",
                confidence=0.58,
                direction=direction,
                target_price=float(target),
                invalidation_price=float(lows[-1] if direction == "BULLISH" else highs[-1]),
                bars_ago=0,
            )
        return None

    def _detect_bull_flag(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        """Sharp rally (flag pole) followed by tight consolidation = bull flag."""
        if len(df) < 20:
            return None

        c = df["close"].values
        pole_start = c[-20]
        pole_top = c[-15:].max()
        pole_change = (pole_top - pole_start) / pole_start if pole_start > 0 else 0

        if pole_change < 0.05:  # pole must be at least 5%
            return None

        # Consolidation: last 5 bars should be range-bound
        consol = df.iloc[-5:]
        consol_range = (consol["high"].max() - consol["low"].min()) / consol["close"].mean()

        if consol_range < 0.02:
            target = pole_top + (pole_top - pole_start) * 0.5
            return ChartPattern(
                name="Bull Flag",
                confidence=0.62,
                direction="BULLISH",
                target_price=float(target),
                invalidation_price=float(consol["low"].min() * 0.99),
                bars_ago=0,
            )
        return None

    def _detect_bear_flag(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        if len(df) < 20:
            return None

        c = df["close"].values
        pole_start = c[-20]
        pole_bottom = c[-15:].min()
        pole_change = (pole_start - pole_bottom) / pole_start if pole_start > 0 else 0

        if pole_change < 0.05:
            return None

        consol = df.iloc[-5:]
        consol_range = (consol["high"].max() - consol["low"].min()) / consol["close"].mean()

        if consol_range < 0.02:
            target = pole_bottom - (pole_start - pole_bottom) * 0.5
            return ChartPattern(
                name="Bear Flag",
                confidence=0.62,
                direction="BEARISH",
                target_price=float(target),
                invalidation_price=float(consol["high"].max() * 1.01),
                bars_ago=0,
            )
        return None

    def _detect_rising_wedge(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        n = min(30, len(df))
        recent = df.iloc[-n:]
        highs = recent["high"].values
        lows = recent["low"].values

        high_slope = np.polyfit(range(n), highs, 1)[0]
        low_slope = np.polyfit(range(n), lows, 1)[0]

        # Both slopes positive but highs rising slower (converging upward)
        if high_slope > 0 and low_slope > 0 and low_slope > high_slope:
            target = lows[0]  # back to start of wedge
            return ChartPattern(
                name="Rising Wedge",
                confidence=0.60,
                direction="BEARISH",
                target_price=float(target),
                invalidation_price=float(highs[-1] * 1.01),
                bars_ago=0,
            )
        return None

    def _detect_falling_wedge(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        n = min(30, len(df))
        recent = df.iloc[-n:]
        highs = recent["high"].values
        lows = recent["low"].values

        high_slope = np.polyfit(range(n), highs, 1)[0]
        low_slope = np.polyfit(range(n), lows, 1)[0]

        # Both slopes negative but lows falling faster (converging downward)
        if high_slope < 0 and low_slope < 0 and abs(low_slope) > abs(high_slope):
            target = highs[0]  # back to start of wedge
            return ChartPattern(
                name="Falling Wedge",
                confidence=0.60,
                direction="BULLISH",
                target_price=float(target),
                invalidation_price=float(lows[-1] * 0.99),
                bars_ago=0,
            )
        return None

    def _detect_cup_and_handle(self, df: pd.DataFrame) -> Optional[ChartPattern]:
        """U-shaped base followed by small consolidation = cup and handle."""
        if len(df) < 60:
            return None

        c = df["close"].values
        # Cup: middle 40 bars should form a U shape
        cup = c[-60:-10]
        if len(cup) < 40:
            return None

        mid_idx = len(cup) // 2
        left_avg = cup[:mid_idx // 2].mean()
        mid_avg = cup[mid_idx - 5: mid_idx + 5].mean()
        right_avg = cup[-mid_idx // 2:].mean()

        u_shape = left_avg > mid_avg and right_avg > mid_avg
        if not u_shape:
            return None

        # Handle: last 10 bars consolidating
        handle = c[-10:]
        handle_range = (handle.max() - handle.min()) / handle.mean()
        if handle_range > 0.06:
            return None

        target = left_avg + (left_avg - mid_avg)
        return ChartPattern(
            name="Cup and Handle",
            confidence=0.63,
            direction="BULLISH",
            target_price=float(target),
            invalidation_price=float(handle.min() * 0.98),
            bars_ago=0,
        )

    # ------------------------------------------------------------------
    # Pivot point helpers
    # ------------------------------------------------------------------

    def _get_pivot_highs(
        self, highs: np.ndarray, sensitivity: int = 5
    ) -> List[Tuple[int, float]]:
        pivots: List[Tuple[int, float]] = []
        n = len(highs)
        for i in range(sensitivity, n - sensitivity):
            if highs[i] == max(highs[i - sensitivity : i + sensitivity + 1]):
                pivots.append((i, float(highs[i])))
        return pivots

    def _get_pivot_lows(
        self, lows: np.ndarray, sensitivity: int = 5
    ) -> List[Tuple[int, float]]:
        pivots: List[Tuple[int, float]] = []
        n = len(lows)
        for i in range(sensitivity, n - sensitivity):
            if lows[i] == min(lows[i - sensitivity : i + sensitivity + 1]):
                pivots.append((i, float(lows[i])))
        return pivots

    # ------------------------------------------------------------------
    # Manual candlestick fallback (no TA-Lib)
    # ------------------------------------------------------------------

    def _detect_candlestick_manual(
        self, df: pd.DataFrame
    ) -> List[CandlePattern]:
        """Simplified candlestick detection without TA-Lib."""
        patterns: List[CandlePattern] = []
        if len(df) < 2:
            return patterns

        for offset in range(min(3, len(df))):
            idx = len(df) - 1 - offset
            row = df.iloc[idx]
            o, h, lo, c = row["open"], row["high"], row["low"], row["close"]
            body = abs(c - o)
            upper_wick = h - max(o, c)
            lower_wick = min(o, c) - lo
            full_range = h - lo

            if full_range == 0:
                continue

            # Doji
            if body / full_range < 0.1:
                patterns.append(CandlePattern("Doji", "NEUTRAL", 1, offset))

            # Hammer
            elif (
                lower_wick > body * 2
                and upper_wick < body * 0.5
                and c > o
            ):
                patterns.append(CandlePattern("Hammer", "BULLISH", 2, offset))

            # Shooting Star
            elif (
                upper_wick > body * 2
                and lower_wick < body * 0.5
                and c < o
            ):
                patterns.append(CandlePattern("Shooting Star", "BEARISH", 2, offset))

        return patterns
