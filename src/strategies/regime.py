"""
NEXUS ALPHA - Market Regime Detector
=======================================
Detects the current market regime for a symbol using technical indicator
snapshots.  Used to adapt signal generation and position sizing to the
prevailing market environment.

Regimes:
  trending_up   - clear uptrend (ADX strong, price above key MAs)
  trending_down - clear downtrend (ADX strong, price below key MAs)
  ranging       - sideways market (ADX weak, tight Bollinger Bands)
  volatile      - high volatility without clear direction
  unknown       - insufficient data
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any, Deque, Dict, Optional

logger = logging.getLogger(__name__)

# Thresholds for regime classification
_ADX_TRENDING_THRESHOLD = 25.0     # ADX above this = trending
_ADX_STRONG_TREND       = 35.0     # ADX above this = strong trend
_BB_WIDTH_TIGHT         = 0.03     # BB width below this = tight/ranging
_BB_WIDTH_VOLATILE      = 0.08     # BB width above this = volatile
_ATR_VOLATILE_MULT      = 2.0      # ATR/price ratio multiplier for volatile check
_REGIME_HISTORY_LEN     = 5        # Smooth over last N regimes

# Valid regime strings
REGIMES = frozenset({
    "trending_up",
    "trending_down",
    "ranging",
    "volatile",
    "unknown",
})


class MarketRegimeDetector:
    """
    Detects market regime from indicator snapshots.

    Uses ADX (trend strength), Bollinger Band width (volatility proxy),
    and moving average alignment (direction) to classify the current regime.

    Parameters
    ----------
    symbol : str
        Trading symbol.
    settings : Settings
        Application settings (reserved for per-market config overrides).
    smoothing : int
        Number of recent regimes to consider when smoothing.  Higher values
        produce more stable but slower-reacting regime labels.
    """

    def __init__(
        self,
        symbol: str,
        settings: Any,
        smoothing: int = _REGIME_HISTORY_LEN,
    ) -> None:
        self._symbol = symbol
        self._settings = settings
        self._smoothing = smoothing
        self._history: Deque[str] = deque(maxlen=smoothing)
        self._current_regime: str = "unknown"

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def update(self, indicators: Dict[str, Any]) -> str:
        """
        Classify the regime from the latest indicator snapshot.

        Parameters
        ----------
        indicators : dict
            Flat dict of indicator values for the current candle.
            Expected keys (all optional): adx, di_plus, di_minus,
            bb_width, sma20, sma50, sma200, close, atr14, rsi14.

        Returns
        -------
        str
            One of: "trending_up", "trending_down", "ranging", "volatile", "unknown"
        """
        if not indicators:
            return self._current_regime

        try:
            regime = self._classify(indicators)
        except Exception as exc:
            logger.warning(
                "MarketRegimeDetector: classification failed for %s: %s",
                self._symbol, exc,
            )
            regime = "unknown"

        self._history.append(regime)
        smoothed = self._smooth()
        self._current_regime = smoothed

        logger.debug(
            "MarketRegimeDetector: %s regime=%s (raw=%s)",
            self._symbol, smoothed, regime,
        )
        return smoothed

    @property
    def current_regime(self) -> str:
        """Return the latest smoothed regime."""
        return self._current_regime

    # ------------------------------------------------------------------
    # Classification logic
    # ------------------------------------------------------------------

    def _classify(self, ind: Dict[str, Any]) -> str:
        """Classify regime from indicators using a rule-based approach."""

        adx      = _get_float(ind, "adx")
        di_plus  = _get_float(ind, "di_plus")
        di_minus = _get_float(ind, "di_minus")
        bb_width = _get_float(ind, "bb_width")
        close    = _get_float(ind, "close")
        sma20    = _get_float(ind, "sma20")
        sma50    = _get_float(ind, "sma50")
        sma200   = _get_float(ind, "sma200")
        atr14    = _get_float(ind, "atr14")
        rsi14    = _get_float(ind, "rsi14")

        # --- Volatile check (BB width or large ATR) ---
        if bb_width is not None and bb_width > _BB_WIDTH_VOLATILE:
            return "volatile"

        if close is not None and atr14 is not None and close > 0:
            atr_pct = atr14 / close
            if atr_pct > 0.04:  # ATR > 4% of price = volatile
                return "volatile"

        # --- Trending check via ADX ---
        if adx is not None and adx >= _ADX_TRENDING_THRESHOLD:
            if di_plus is not None and di_minus is not None:
                # Use DI direction
                if di_plus > di_minus:
                    return "trending_up"
                else:
                    return "trending_down"
            # Fallback: use MA alignment for direction
            if close is not None and sma50 is not None:
                if close > sma50:
                    return "trending_up"
                else:
                    return "trending_down"

        # --- MA alignment without strong ADX ---
        if close is not None and sma20 is not None and sma50 is not None:
            if close > sma20 > sma50:
                # Weak trend up
                if adx is not None and adx > 20:
                    return "trending_up"
            elif close < sma20 < sma50:
                if adx is not None and adx > 20:
                    return "trending_down"

        # --- Ranging (tight BB or weak ADX) ---
        if bb_width is not None and bb_width < _BB_WIDTH_TIGHT:
            return "ranging"

        if adx is not None and adx < 20:
            return "ranging"

        # --- RSI extremes can indicate trending ---
        if rsi14 is not None:
            if rsi14 > 65:
                return "trending_up"
            if rsi14 < 35:
                return "trending_down"

        return "ranging"  # Default to ranging when uncertain

    def _smooth(self) -> str:
        """Return the most common regime from recent history."""
        if not self._history:
            return "unknown"

        counts: Dict[str, int] = {}
        for r in self._history:
            counts[r] = counts.get(r, 0) + 1

        return max(counts, key=lambda k: counts[k])


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _get_float(d: Dict[str, Any], key: str) -> Optional[float]:
    """Safely retrieve a float from a dict, returning None for missing/NaN."""
    val = d.get(key)
    if val is None:
        return None
    try:
        f = float(val)
        if f != f:  # NaN check
            return None
        return f
    except (TypeError, ValueError):
        return None
