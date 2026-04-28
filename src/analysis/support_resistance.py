# src/analysis/support_resistance.py
"""Support and resistance level detection using multiple methods."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class SRLevel:
    """A detected support or resistance level."""

    price: float
    type: str          # "support" | "resistance"
    strength: int      # 1 (weak) to 5 (very strong)
    touches: int       # number of times price has tested this level
    last_touch: Optional[pd.Timestamp] = None
    method: str = "fractal"  # how it was detected


class SupportResistanceFinder:
    """Detect horizontal support and resistance levels using configurable methods."""

    VALID_METHODS = ("fractal", "volume_profile", "pivot")

    def __init__(self, default_method: str = "fractal") -> None:
        self._method = default_method

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def find_levels(
        self,
        df: pd.DataFrame,
        method: Optional[str] = None,
        lookback: int = 100,
        sensitivity: int = 3,
        tolerance_pct: float = 0.005,
    ) -> List[SRLevel]:
        """Find support and resistance levels.

        Parameters
        ----------
        df:
            OHLCV DataFrame.
        method:
            One of ``"fractal"``, ``"volume_profile"``, ``"pivot"``.
        lookback:
            Number of bars to consider.
        sensitivity:
            Fractal sensitivity — number of bars on each side to confirm a pivot.
        tolerance_pct:
            Price levels within this % of each other are merged.
        """
        m = method or self._method
        if m not in self.VALID_METHODS:
            raise ValueError(f"Invalid method {m!r}. Choose from {self.VALID_METHODS}")

        window = df.iloc[-lookback:] if len(df) > lookback else df.copy()

        if m == "fractal":
            levels = self._fractal_levels(window, sensitivity, tolerance_pct)
        elif m == "volume_profile":
            levels = self._volume_profile_levels(window, tolerance_pct)
        elif m == "pivot":
            levels = self._pivot_levels(window, tolerance_pct)
        else:
            levels = []

        return sorted(levels, key=lambda x: x.price)

    def is_near_level(
        self,
        price: float,
        levels: List[SRLevel],
        tolerance_pct: float = 0.005,
    ) -> Optional[SRLevel]:
        """Return the closest SRLevel within *tolerance_pct* of *price*, or None."""
        best: Optional[SRLevel] = None
        best_dist = float("inf")

        for lvl in levels:
            dist = abs(price - lvl.price) / lvl.price if lvl.price > 0 else float("inf")
            if dist <= tolerance_pct and dist < best_dist:
                best_dist = dist
                best = lvl

        return best

    def find_nearest_support(
        self, price: float, levels: List[SRLevel]
    ) -> Optional[float]:
        """Return the nearest support price below *price*."""
        supports = [l.price for l in levels if l.type == "support" and l.price < price]
        return max(supports) if supports else None

    def find_nearest_resistance(
        self, price: float, levels: List[SRLevel]
    ) -> Optional[float]:
        """Return the nearest resistance price above *price*."""
        resistances = [l.price for l in levels if l.type == "resistance" and l.price > price]
        return min(resistances) if resistances else None

    # ------------------------------------------------------------------
    # Fractal method
    # ------------------------------------------------------------------

    def _fractal_levels(
        self,
        df: pd.DataFrame,
        sensitivity: int,
        tolerance_pct: float,
    ) -> List[SRLevel]:
        """Detect pivot highs (resistance) and pivot lows (support) via fractals."""
        highs = df["high"].values
        lows = df["low"].values
        n = len(df)
        s = sensitivity

        pivot_highs: List[float] = []
        pivot_lows: List[float] = []

        for i in range(s, n - s):
            # Pivot high: local max over [i-s, i+s]
            if highs[i] == max(highs[i - s : i + s + 1]):
                pivot_highs.append(highs[i])
            # Pivot low: local min
            if lows[i] == min(lows[i - s : i + s + 1]):
                pivot_lows.append(lows[i])

        current_price = df["close"].iloc[-1]
        levels: List[SRLevel] = []

        for cluster in _cluster_prices(pivot_highs, tolerance_pct):
            median_price = float(np.median(cluster))
            lvl_type = "resistance" if median_price > current_price else "support"
            strength = min(5, max(1, len(cluster)))
            levels.append(
                SRLevel(
                    price=median_price,
                    type=lvl_type,
                    strength=strength,
                    touches=len(cluster),
                    method="fractal",
                )
            )

        for cluster in _cluster_prices(pivot_lows, tolerance_pct):
            median_price = float(np.median(cluster))
            lvl_type = "support" if median_price < current_price else "resistance"
            strength = min(5, max(1, len(cluster)))
            levels.append(
                SRLevel(
                    price=median_price,
                    type=lvl_type,
                    strength=strength,
                    touches=len(cluster),
                    method="fractal",
                )
            )

        return levels

    # ------------------------------------------------------------------
    # Volume profile method
    # ------------------------------------------------------------------

    def _volume_profile_levels(
        self,
        df: pd.DataFrame,
        tolerance_pct: float,
        bins: int = 50,
    ) -> List[SRLevel]:
        """Identify high-volume price nodes as support/resistance."""
        from .volume_profile import VolumeProfile

        vp = VolumeProfile()
        hvn_prices = vp.get_high_volume_nodes(df, bins=bins, threshold=0.6)

        current_price = df["close"].iloc[-1]
        levels: List[SRLevel] = []

        for price in hvn_prices:
            lvl_type = "resistance" if price > current_price else "support"
            levels.append(
                SRLevel(
                    price=price,
                    type=lvl_type,
                    strength=3,
                    touches=1,
                    method="volume_profile",
                )
            )

        return levels

    # ------------------------------------------------------------------
    # Pivot points method
    # ------------------------------------------------------------------

    def _pivot_levels(
        self,
        df: pd.DataFrame,
        tolerance_pct: float,
    ) -> List[SRLevel]:
        """Classic floor-trader pivot points from the most recent completed session."""
        if len(df) < 2:
            return []

        # Use the second-to-last bar as the completed session
        prev = df.iloc[-2]
        h = float(prev["high"])
        lo = float(prev["low"])
        c = float(prev["close"])

        pivot = (h + lo + c) / 3
        r1 = 2 * pivot - lo
        r2 = pivot + (h - lo)
        r3 = h + 2 * (pivot - lo)
        s1 = 2 * pivot - h
        s2 = pivot - (h - lo)
        s3 = lo - 2 * (h - pivot)

        current_price = df["close"].iloc[-1]
        levels: List[SRLevel] = []

        pivot_data = [
            (r3, "resistance", 4),
            (r2, "resistance", 3),
            (r1, "resistance", 2),
            (pivot, "support" if pivot < current_price else "resistance", 5),
            (s1, "support", 2),
            (s2, "support", 3),
            (s3, "support", 4),
        ]

        for price, lvl_type, strength in pivot_data:
            if price > 0:
                levels.append(
                    SRLevel(
                        price=price,
                        type=lvl_type,
                        strength=strength,
                        touches=1,
                        method="pivot",
                    )
                )

        return levels


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cluster_prices(
    prices: List[float], tolerance_pct: float
) -> List[List[float]]:
    """Group nearby prices into clusters.

    Uses a simple greedy scan (O(n log n)) after sorting.
    """
    if not prices:
        return []

    sorted_prices = sorted(prices)
    clusters: List[List[float]] = [[sorted_prices[0]]]

    for price in sorted_prices[1:]:
        ref = clusters[-1][0]
        if ref > 0 and abs(price - ref) / ref <= tolerance_pct:
            clusters[-1].append(price)
        else:
            clusters.append([price])

    return clusters
