# src/analysis/volume_profile.py
"""Volume Profile analysis for NEXUS ALPHA."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class VolumeProfile:
    """Compute volume-at-price distributions and identify key price nodes."""

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def compute(
        self,
        df: pd.DataFrame,
        bins: int = 50,
    ) -> Dict:
        """Compute the volume profile over all rows in *df*.

        Parameters
        ----------
        df:
            OHLCV DataFrame.
        bins:
            Number of price buckets.

        Returns
        -------
        dict with keys:
            poc     – Point of Control (highest volume price)
            vah     – Value Area High (top of 70% of volume)
            val     – Value Area Low (bottom of 70% of volume)
            distribution – list of (price_mid, volume) tuples
        """
        h = df["high"].values
        lo = df["low"].values
        v = df["volume"].values

        price_min = lo.min()
        price_max = h.max()

        if price_max <= price_min:
            return {"poc": price_min, "vah": price_max, "val": price_min, "distribution": []}

        bin_edges = np.linspace(price_min, price_max, bins + 1)
        bin_mids = (bin_edges[:-1] + bin_edges[1:]) / 2
        bin_volumes = np.zeros(bins)

        for i in range(len(df)):
            # Distribute each bar's volume proportionally across touched bins
            bar_low = lo[i]
            bar_high = h[i]
            bar_vol = v[i]

            # Find which bins this bar touches
            low_bin = int(np.searchsorted(bin_edges, bar_low, side="right") - 1)
            high_bin = int(np.searchsorted(bin_edges, bar_high, side="left"))
            low_bin = max(0, min(low_bin, bins - 1))
            high_bin = max(0, min(high_bin, bins - 1))

            touched = high_bin - low_bin + 1
            if touched > 0:
                per_bin = bar_vol / touched
                bin_volumes[low_bin : high_bin + 1] += per_bin

        poc_idx = int(np.argmax(bin_volumes))
        poc = float(bin_mids[poc_idx])

        vah, val = self._value_area(bin_mids, bin_volumes, value_area_pct=0.70)

        distribution = [
            (float(bin_mids[i]), float(bin_volumes[i])) for i in range(bins)
        ]

        return {
            "poc": poc,
            "vah": vah,
            "val": val,
            "distribution": distribution,
        }

    def get_high_volume_nodes(
        self,
        df: pd.DataFrame,
        bins: int = 50,
        threshold: float = 0.7,
    ) -> List[float]:
        """Return price levels where volume exceeds *threshold* × max volume.

        Parameters
        ----------
        threshold:
            Fraction of the maximum bin volume (0.7 = 70%).
        """
        profile = self.compute(df, bins=bins)
        distribution = profile["distribution"]

        if not distribution:
            return []

        max_vol = max(vol for _, vol in distribution)
        if max_vol == 0:
            return []

        cutoff = max_vol * threshold
        return [price for price, vol in distribution if vol >= cutoff]

    def vpvr(
        self,
        df: pd.DataFrame,
        bins: int = 50,
    ) -> Dict:
        """Visible Range Volume Profile — same as compute() but uses all visible data.

        This is an alias that makes intent explicit when calling from chart code.
        """
        return self.compute(df, bins=bins)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _value_area(
        bin_mids: np.ndarray,
        bin_volumes: np.ndarray,
        value_area_pct: float = 0.70,
    ) -> tuple[float, float]:
        """Find Value Area High and Low by expanding from the POC.

        The Value Area contains *value_area_pct* of total volume.
        """
        total_vol = bin_volumes.sum()
        if total_vol == 0:
            return float(bin_mids[-1]), float(bin_mids[0])

        target = total_vol * value_area_pct
        poc_idx = int(np.argmax(bin_volumes))

        # Expand symmetrically from POC until target volume captured
        lo_idx = poc_idx
        hi_idx = poc_idx
        accumulated = bin_volumes[poc_idx]

        while accumulated < target:
            can_expand_lo = lo_idx > 0
            can_expand_hi = hi_idx < len(bin_volumes) - 1

            if not can_expand_lo and not can_expand_hi:
                break

            vol_below = bin_volumes[lo_idx - 1] if can_expand_lo else 0
            vol_above = bin_volumes[hi_idx + 1] if can_expand_hi else 0

            if vol_above >= vol_below:
                hi_idx += 1
                accumulated += bin_volumes[hi_idx]
            else:
                lo_idx -= 1
                accumulated += bin_volumes[lo_idx]

        return float(bin_mids[hi_idx]), float(bin_mids[lo_idx])
