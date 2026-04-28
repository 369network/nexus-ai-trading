"""
NEXUS ALPHA - Data Validator
==============================
Post-fetch quality assurance layer.  Validates price sequences, detects
missing candles, flags volume and price spike anomalies.
"""

from __future__ import annotations

import logging
import statistics
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

from .base import OHLCV, ValidationResult
from .normalizer import _interval_ms

logger = logging.getLogger(__name__)

# Market-specific tick-size floors (used for min-gap calculation)
_MARKET_TICK_FLOORS: Dict[str, float] = {
    "crypto":  0.000001,
    "forex":   0.00001,
    "equity":  0.01,
    "futures": 0.01,
}


class DataValidator:
    """
    Stateless collection of candle-sequence validation utilities.

    All methods are pure functions; they accept data and return results
    without storing state between calls.
    """

    # ------------------------------------------------------------------
    # Price-sequence validation
    # ------------------------------------------------------------------

    def validate_price_sequence(
        self,
        candles: List[OHLCV],
    ) -> ValidationResult:
        """
        Run a comprehensive validation over an ordered candle sequence.

        Checks performed per candle:
        * OHLC relationships (``high >= max(open, close)``, etc.)
        * Non-negative, finite values

        Checks performed across the sequence:
        * Monotonically increasing timestamps (no duplicates)
        * Uniform interval spacing
        * Consecutive close-to-open price continuity (extreme jumps)

        Parameters
        ----------
        candles:
            Chronologically ordered OHLCV list.

        Returns
        -------
        ValidationResult
            Contains ``valid``, ``issues`` list, error / warning counts.
        """
        result = ValidationResult(valid=True)

        if not candles:
            result.add_issue("Empty candle list", is_error=False)
            return result

        # --- Per-candle checks ------------------------------------------
        seen_ts: set = set()
        for i, c in enumerate(candles):
            prefix = f"candle[{i}] ts={c.timestamp_ms}"

            # Duplicate timestamp
            if c.timestamp_ms in seen_ts:
                result.add_issue(f"{prefix}: duplicate timestamp")
            seen_ts.add(c.timestamp_ms)

            # Finite values
            import math
            for attr in ("open", "high", "low", "close", "volume"):
                v = getattr(c, attr)
                if math.isnan(v) or math.isinf(v):
                    result.add_issue(f"{prefix}: {attr}={v} is NaN/Inf")

            # OHLC integrity
            if c.high < max(c.open, c.close):
                result.add_issue(
                    f"{prefix}: high={c.high} < max(open={c.open}, close={c.close})"
                )
            if c.low > min(c.open, c.close):
                result.add_issue(
                    f"{prefix}: low={c.low} > min(open={c.open}, close={c.close})"
                )
            if c.high < c.low:
                result.add_issue(
                    f"{prefix}: high={c.high} < low={c.low}"
                )
            if c.volume < 0:
                result.add_issue(f"{prefix}: negative volume={c.volume}")

        # --- Sequence-level checks ---------------------------------------
        if len(candles) < 2:
            return result

        sorted_candles = sorted(candles, key=lambda c: c.timestamp_ms)

        # Compute gaps
        gaps = [
            sorted_candles[i + 1].timestamp_ms - sorted_candles[i].timestamp_ms
            for i in range(len(sorted_candles) - 1)
        ]

        # Non-monotonic timestamps
        for i, gap in enumerate(gaps):
            if gap <= 0:
                result.add_issue(
                    f"Non-monotonic timestamp between candle[{i}] and candle[{i+1}]: "
                    f"gap={gap}ms"
                )

        # Interval uniformity: flag gaps > 1.5x the modal interval
        positive_gaps = [g for g in gaps if g > 0]
        if positive_gaps:
            modal_gap = statistics.mode(positive_gaps)
            for i, gap in enumerate(gaps):
                if gap > modal_gap * 1.5:
                    result.add_issue(
                        f"Gap between candle[{i}] and [{i+1}] is {gap}ms "
                        f"(expected ~{modal_gap}ms)",
                        is_error=False,
                    )

        # Extreme close-to-open jump (> 20%)
        for i in range(len(sorted_candles) - 1):
            prev_close = sorted_candles[i].close
            next_open  = sorted_candles[i + 1].open
            if prev_close > 0:
                pct = abs(next_open - prev_close) / prev_close
                if pct > 0.20:
                    result.add_issue(
                        f"Extreme close-to-open jump at candle[{i+1}]: "
                        f"prev_close={prev_close} next_open={next_open} "
                        f"change={pct:.1%}",
                        is_error=False,
                    )

        return result

    # ------------------------------------------------------------------
    # Missing candle detection
    # ------------------------------------------------------------------

    def detect_missing_candles(
        self,
        candles: List[OHLCV],
        interval: str,
        market: str = "crypto",
    ) -> List[datetime]:
        """
        Return expected timestamps that are absent from *candles*.

        For non-24/7 markets (equity) gaps during weekends / market
        closures are excluded.

        Parameters
        ----------
        candles:
            Candle list to check (need not be sorted).
        interval:
            Expected cadence, e.g. ``"1m"``, ``"1h"``.
        market:
            Market type – used to skip legitimate non-trading periods.

        Returns
        -------
        List[datetime]
            UTC datetimes of missing candles, sorted chronologically.
        """
        if len(candles) < 2:
            return []

        step_ms = _interval_ms(interval)
        present = {c.timestamp_ms for c in candles}
        first_ts = min(present)
        last_ts  = max(present)

        missing: List[datetime] = []
        ts = first_ts + step_ms

        while ts < last_ts:
            if ts not in present:
                dt = datetime.fromtimestamp(ts / 1_000.0, tz=timezone.utc)
                # For equity/forex markets skip weekends
                if market in ("equity", "forex") and dt.weekday() >= 5:
                    ts += step_ms
                    continue
                missing.append(dt)
            ts += step_ms

        return missing

    # ------------------------------------------------------------------
    # Volume anomaly detection
    # ------------------------------------------------------------------

    def check_volume_anomaly(
        self,
        candles: List[OHLCV],
        threshold: float = 5.0,
    ) -> List[OHLCV]:
        """
        Return candles whose volume exceeds *threshold* × the rolling
        median of the surrounding window.

        A rolling window of 20 candles is used to establish the local
        baseline, making the check robust to regime changes.

        Parameters
        ----------
        candles:
            Chronologically ordered OHLCV list.
        threshold:
            Multiplier over the rolling median to flag as anomalous.

        Returns
        -------
        List[OHLCV]
            Subset of *candles* with anomalous volume.
        """
        if len(candles) < 5:
            return []

        window = 20
        anomalies: List[OHLCV] = []
        volumes = [c.volume for c in candles]

        for i, candle in enumerate(candles):
            start = max(0, i - window)
            end   = i  # exclude current candle from baseline
            if end - start < 3:
                continue

            local_vols = volumes[start:end]
            median_vol = statistics.median(local_vols)

            if median_vol <= 0:
                continue

            if candle.volume > threshold * median_vol:
                anomalies.append(candle)

        return anomalies

    # ------------------------------------------------------------------
    # Price spike detection
    # ------------------------------------------------------------------

    def check_price_spike(
        self,
        candles: List[OHLCV],
        threshold_pct: float = 0.15,
    ) -> List[OHLCV]:
        """
        Return candles where the intra-candle price range or the
        close-to-close change exceeds *threshold_pct*.

        Both metrics are checked:
        1. ``(high - low) / low > threshold_pct`` – intra-bar spike.
        2. ``|close - prev_close| / prev_close > threshold_pct`` –
           bar-to-bar price jump.

        Parameters
        ----------
        candles:
            Chronologically ordered OHLCV list.
        threshold_pct:
            Fractional threshold (0.15 = 15%).

        Returns
        -------
        List[OHLCV]
            Candles flagged as price spikes.
        """
        if not candles:
            return []

        anomalies: List[OHLCV] = []
        sorted_candles = sorted(candles, key=lambda c: c.timestamp_ms)

        for i, candle in enumerate(sorted_candles):
            # Intra-bar range
            if candle.low > 0:
                intra_range = (candle.high - candle.low) / candle.low
                if intra_range > threshold_pct:
                    anomalies.append(candle)
                    continue

            # Bar-to-bar close change
            if i > 0:
                prev_close = sorted_candles[i - 1].close
                if prev_close > 0:
                    pct_change = abs(candle.close - prev_close) / prev_close
                    if pct_change > threshold_pct:
                        anomalies.append(candle)

        return anomalies
