"""
NEXUS ALPHA - Regime Risk Adjuster
=====================================
Scales position sizes and stop-loss ATR multipliers based on the
current market regime detected by the analysis layer.

Regime → Size multiplier:
  TRENDING_UP / TRENDING_DOWN : 1.00 (full)
  RANGING                     : 0.70
  HIGH_VOLATILITY             : 0.50
  LOW_VOLATILITY              : 1.20
  BREAKOUT / BREAKDOWN        : 0.80

Regime → Stop ATR multiplier adjustment:
  HIGH_VOLATILITY : widen by 30%  (× 1.30)
  RANGING         : tighten by 20% (× 0.80)
  others          : no change      (× 1.00)
"""

from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MarketRegime enum
# ---------------------------------------------------------------------------

class MarketRegime(str, Enum):
    """Possible market regime classifications."""
    TRENDING_UP    = "TRENDING_UP"
    TRENDING_DOWN  = "TRENDING_DOWN"
    RANGING        = "RANGING"
    HIGH_VOLATILITY = "HIGH_VOLATILITY"
    LOW_VOLATILITY = "LOW_VOLATILITY"
    BREAKOUT       = "BREAKOUT"
    BREAKDOWN      = "BREAKDOWN"
    UNKNOWN        = "UNKNOWN"


# ---------------------------------------------------------------------------
# Lookup tables
# ---------------------------------------------------------------------------

SIZE_MULTIPLIERS: dict[MarketRegime, float] = {
    MarketRegime.TRENDING_UP:    1.0,
    MarketRegime.TRENDING_DOWN:  1.0,
    MarketRegime.RANGING:        0.7,
    MarketRegime.HIGH_VOLATILITY: 0.5,
    MarketRegime.LOW_VOLATILITY: 1.2,
    MarketRegime.BREAKOUT:       0.8,
    MarketRegime.BREAKDOWN:      0.8,
    MarketRegime.UNKNOWN:        0.7,   # be conservative on unknown
}

STOP_ATR_ADJUSTMENTS: dict[MarketRegime, float] = {
    MarketRegime.TRENDING_UP:    1.0,
    MarketRegime.TRENDING_DOWN:  1.0,
    MarketRegime.RANGING:        0.8,   # tighter – price bounces predictably
    MarketRegime.HIGH_VOLATILITY: 1.3,  # wider – price swings are large
    MarketRegime.LOW_VOLATILITY: 1.0,
    MarketRegime.BREAKOUT:       1.0,
    MarketRegime.BREAKDOWN:      1.0,
    MarketRegime.UNKNOWN:        1.0,
}


# ---------------------------------------------------------------------------
# RegimeRiskAdjuster
# ---------------------------------------------------------------------------

class RegimeRiskAdjuster:
    """
    Applies regime-specific adjustments to position sizes and stop distances.

    This class is stateless – all methods are pure functions of their inputs.
    It is designed to be called after the base position size has been
    computed by ``PositionSizer.final_size()``.
    """

    @staticmethod
    def adjust_size(base_size: float, regime: MarketRegime) -> float:
        """
        Scale the base position size by the regime multiplier.

        Parameters
        ----------
        base_size : float
            Unscaled position size (units, notional, or fraction – any unit).
        regime : MarketRegime
            Current market regime classification.

        Returns
        -------
        float
            Regime-adjusted position size.  Always ≥ 0.
        """
        if base_size <= 0:
            return 0.0

        multiplier = SIZE_MULTIPLIERS.get(regime, 0.7)
        adjusted = base_size * multiplier

        if multiplier != 1.0:
            logger.debug(
                "RegimeRiskAdjuster.adjust_size: regime=%s mult=%.2f "
                "base=%.6f → adjusted=%.6f",
                regime.value, multiplier, base_size, adjusted,
            )

        return max(0.0, adjusted)

    @staticmethod
    def adjust_stop(base_stop_atr_mult: float, regime: MarketRegime) -> float:
        """
        Adjust the ATR multiplier used for stop placement.

        Parameters
        ----------
        base_stop_atr_mult : float
            The standard ATR multiplier for the stop distance
            (e.g. 2.5 for crypto, 2.0 for forex).
        regime : MarketRegime
            Current market regime.

        Returns
        -------
        float
            Adjusted ATR multiplier.

        Examples
        --------
        >>> adjuster = RegimeRiskAdjuster()
        >>> adjuster.adjust_stop(2.5, MarketRegime.HIGH_VOLATILITY)
        3.25   # 2.5 × 1.30
        >>> adjuster.adjust_stop(2.0, MarketRegime.RANGING)
        1.6    # 2.0 × 0.80
        """
        if base_stop_atr_mult <= 0:
            return base_stop_atr_mult

        adjustment = STOP_ATR_ADJUSTMENTS.get(regime, 1.0)
        adjusted_mult = base_stop_atr_mult * adjustment

        if adjustment != 1.0:
            logger.debug(
                "RegimeRiskAdjuster.adjust_stop: regime=%s adj=%.2f "
                "base_mult=%.2f → adjusted_mult=%.2f",
                regime.value, adjustment, base_stop_atr_mult, adjusted_mult,
            )

        return max(0.1, adjusted_mult)   # never allow zero-width stop
