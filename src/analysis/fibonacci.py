# src/analysis/fibonacci.py
"""Auto-Fibonacci analysis with O(1)-per-zone detection and ML features."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Standard Fibonacci retracement ratios
FIB_RATIOS: Dict[str, float] = {
    "0.0": 0.000,
    "0.236": 0.236,
    "0.382": 0.382,
    "0.5": 0.500,
    "0.618": 0.618,
    "0.786": 0.786,
    "1.0": 1.000,
    "1.272": 1.272,  # Extension
    "1.618": 1.618,  # Extension
    "2.0": 2.000,    # Extension
    "2.618": 2.618,  # Extension
}

# Confluence tolerance: levels within this % of each other cluster together
CONFLUENCE_TOLERANCE_PCT = 0.005


@dataclass
class FibonacciResult:
    """Result of a Fibonacci swing analysis."""

    high: float
    low: float
    high_idx: int
    low_idx: int
    swing_direction: str  # "UP" or "DOWN"
    levels: Dict[str, float] = field(default_factory=dict)
    # Pre-computed zone membership for O(1) lookup
    _zone_map: Dict[str, str] = field(default_factory=dict, repr=False)

    def level_at(self, ratio: str) -> Optional[float]:
        return self.levels.get(ratio)

    def nearest_level(self, price: float) -> Tuple[str, float, float]:
        """Return (ratio_name, level_price, distance_pct) for the closest level."""
        best_ratio = ""
        best_price = 0.0
        best_dist = float("inf")
        for ratio, level in self.levels.items():
            if level > 0:
                dist = abs(price - level) / level
                if dist < best_dist:
                    best_dist = dist
                    best_ratio = ratio
                    best_price = level
        return best_ratio, best_price, best_dist

    def is_in_zone(self, price: float, tolerance_pct: float = 0.005) -> Optional[str]:
        """Return the ratio name if price is within *tolerance_pct* of any level.

        This is O(1) for the common case once the zone map is built.
        Falls back to linear scan if zone map is empty.
        """
        if self._zone_map:
            # Quantise price to 3 significant figures and look up
            price_key = _quantise_price(price)
            return self._zone_map.get(price_key)
        # Linear fallback
        for ratio, level in self.levels.items():
            if level > 0 and abs(price - level) / level <= tolerance_pct:
                return ratio
        return None

    def build_zone_map(
        self,
        price_min: float,
        price_max: float,
        steps: int = 10_000,
        tolerance_pct: float = 0.005,
    ) -> None:
        """Pre-build a hash map for O(1) zone membership checks.

        Populates ``self._zone_map`` with quantised price keys → ratio name.
        """
        if price_min <= 0 or price_max <= price_min:
            return

        prices = np.linspace(price_min, price_max, steps)
        zone_map: Dict[str, str] = {}

        for price in prices:
            for ratio, level in self.levels.items():
                if level > 0 and abs(price - level) / level <= tolerance_pct:
                    zone_map[_quantise_price(price)] = ratio
                    break  # first match wins (closest ratio handled elsewhere)

        self._zone_map = zone_map


def _quantise_price(price: float) -> str:
    """Reduce price resolution to 3 significant figures for hash map keys."""
    if price <= 0:
        return "0"
    magnitude = 10 ** (int(np.log10(price)) - 2)
    quantised = round(price / magnitude) * magnitude
    return f"{quantised:.8g}"


# ---------------------------------------------------------------------------
# Core auto-Fibonacci computation
# ---------------------------------------------------------------------------

def auto_fibonacci(
    df: pd.DataFrame,
    lookback: int = 100,
    min_swing_pct: float = 0.03,
    build_zone_maps: bool = True,
) -> Optional[FibonacciResult]:
    """Automatically identify the most significant recent swing and compute
    Fibonacci levels.

    Parameters
    ----------
    df:
        OHLCV DataFrame.
    lookback:
        Number of bars to look back for the swing.
    min_swing_pct:
        Minimum swing size as a fraction of price (e.g. 0.03 = 3%).
    build_zone_maps:
        If True, pre-build O(1) zone lookup maps.

    Returns
    -------
    FibonacciResult or None if no significant swing found.
    """
    if len(df) < 5:
        return None

    window = df.iloc[-lookback:] if len(df) > lookback else df
    highs = window["high"].values
    lows = window["low"].values

    # Find global high and low in the window
    high_idx_local = int(np.argmax(highs))
    low_idx_local = int(np.argmin(lows))

    # Edge case: identical index
    if high_idx_local == low_idx_local:
        if high_idx_local > 0:
            low_idx_local = high_idx_local - 1
        else:
            low_idx_local = min(high_idx_local + 1, len(highs) - 1)

    high_price = highs[high_idx_local]
    low_price = lows[low_idx_local]

    swing_size = (high_price - low_price) / low_price if low_price > 0 else 0
    if swing_size < min_swing_pct:
        logger.debug(
            "Swing too small (%.2f%%) — minimum required: %.2f%%",
            swing_size * 100, min_swing_pct * 100,
        )
        return None

    # Determine swing direction: if high comes after low → UP swing (retrace down)
    swing_direction = "UP" if high_idx_local > low_idx_local else "DOWN"

    # Map local indices back to full DataFrame indices
    start_abs = len(df) - len(window)
    high_idx_abs = start_abs + high_idx_local
    low_idx_abs = start_abs + low_idx_local

    levels = _compute_levels(high_price, low_price, swing_direction)

    result = FibonacciResult(
        high=high_price,
        low=low_price,
        high_idx=high_idx_abs,
        low_idx=low_idx_abs,
        swing_direction=swing_direction,
        levels=levels,
    )

    if build_zone_maps:
        # Build zone map over the full swing range with 20% buffer
        margin = (high_price - low_price) * 0.2
        result.build_zone_map(
            price_min=low_price - margin,
            price_max=high_price + margin,
        )

    return result


def _compute_levels(
    high: float, low: float, swing_direction: str
) -> Dict[str, float]:
    """Compute absolute price levels for each Fibonacci ratio."""
    rng = high - low
    levels: Dict[str, float] = {}

    for name, ratio in FIB_RATIOS.items():
        if swing_direction == "UP":
            # Retracement from high down towards low
            levels[name] = high - ratio * rng
        else:
            # Retracement from low up towards high
            levels[name] = low + ratio * rng

    return levels


# ---------------------------------------------------------------------------
# ML feature extraction
# ---------------------------------------------------------------------------

def fibonacci_ml_features(
    result: FibonacciResult,
    current_price: float,
    current_volume: float,
    avg_volume: float,
) -> Dict[str, float]:
    """Extract numeric features from a FibonacciResult for ML models.

    Returns
    -------
    dict
        Feature dict suitable for ML input (all values are floats).
    """
    nearest_ratio, nearest_level, dist_pct = result.nearest_level(current_price)

    # Distance from each key level (as % of price)
    features: Dict[str, float] = {
        "fib_dist_0": _level_dist(current_price, result.levels.get("0.0", 0)),
        "fib_dist_236": _level_dist(current_price, result.levels.get("0.236", 0)),
        "fib_dist_382": _level_dist(current_price, result.levels.get("0.382", 0)),
        "fib_dist_50": _level_dist(current_price, result.levels.get("0.5", 0)),
        "fib_dist_618": _level_dist(current_price, result.levels.get("0.618", 0)),
        "fib_dist_786": _level_dist(current_price, result.levels.get("0.786", 0)),
        "fib_dist_100": _level_dist(current_price, result.levels.get("1.0", 0)),
        "fib_dist_nearest": dist_pct,
        "fib_swing_size_pct": (result.high - result.low) / result.low * 100 if result.low > 0 else 0,
        "fib_swing_bars": abs(result.high_idx - result.low_idx),
        "fib_direction_up": 1.0 if result.swing_direction == "UP" else 0.0,
        "fib_price_in_fib_zone": 1.0 if dist_pct < 0.005 else 0.0,
        "fib_volume_ratio": current_volume / avg_volume if avg_volume > 0 else 1.0,
        "fib_pct_retrace": _pct_retrace(current_price, result.high, result.low, result.swing_direction),
    }

    return features


def _level_dist(price: float, level: float) -> float:
    if level <= 0 or price <= 0:
        return 1.0  # max distance
    return abs(price - level) / price


def _pct_retrace(
    price: float, high: float, low: float, direction: str
) -> float:
    """How far price has retraced from the swing extreme (0=no retrace, 1=full)."""
    rng = high - low
    if rng <= 0:
        return 0.0
    if direction == "UP":
        return (high - price) / rng
    else:
        return (price - low) / rng


# ---------------------------------------------------------------------------
# Confluence zone detection
# ---------------------------------------------------------------------------

def find_confluence_zones(
    fib_results: List[FibonacciResult],
    tolerance_pct: float = CONFLUENCE_TOLERANCE_PCT,
) -> List[float]:
    """Find price levels where Fibonacci levels from multiple swings cluster.

    A confluence zone is defined as a price at which two or more Fibonacci
    levels from *different* swings fall within *tolerance_pct* of each other.

    Parameters
    ----------
    fib_results:
        List of FibonacciResult objects from different swing analyses.
    tolerance_pct:
        Maximum relative distance for two levels to be considered confluent.

    Returns
    -------
    list of float
        Median price of each detected confluence zone, sorted ascending.
    """
    if len(fib_results) < 2:
        return []

    # Collect all levels as (price, swing_index) tuples
    all_levels: List[Tuple[float, int]] = []
    for swing_idx, result in enumerate(fib_results):
        for level in result.levels.values():
            if level > 0:
                all_levels.append((level, swing_idx))

    if not all_levels:
        return []

    # Sort by price for efficient proximity grouping
    all_levels.sort(key=lambda x: x[0])

    confluence_zones: List[List[float]] = []

    i = 0
    while i < len(all_levels):
        price_i, swing_i = all_levels[i]
        group_prices = [price_i]
        group_swings = {swing_i}

        j = i + 1
        while j < len(all_levels):
            price_j, swing_j = all_levels[j]
            # Use the first price in the group as reference
            if abs(price_j - price_i) / price_i <= tolerance_pct:
                group_prices.append(price_j)
                group_swings.add(swing_j)
                j += 1
            else:
                break

        # Confluence requires levels from at least 2 different swings
        if len(group_swings) >= 2:
            confluence_zones.append(group_prices)

        i = j if j > i else i + 1

    # Return the median of each cluster, sorted ascending
    return sorted(
        float(np.median(group)) for group in confluence_zones
    )
