"""
NEXUS ALPHA - Take Profit Manager
====================================
Multi-target take-profit system at 1R, 2R, 3R.
Partial exits: 33% / 33% / 34% of position at each level.
"""

from __future__ import annotations

import logging
from typing import Tuple

logger = logging.getLogger(__name__)

# Partial close fractions at each TP level
TP_FRACTIONS = {
    1: 0.33,  # Close 33% at TP1
    2: 0.33,  # Close 33% at TP2
    3: 0.34,  # Close remaining 34% at TP3
}


class TakeProfitManager:
    """
    Calculates and manages multi-level take-profit targets.

    Targets are set as multiples of the initial risk (R):
      - TP1 = 1R  (risk_reward × 1)
      - TP2 = 2R
      - TP3 = 3R

    Partial exits release position size at each level to lock in gains
    while letting the remainder run toward larger targets.
    """

    # ------------------------------------------------------------------
    # Target calculation
    # ------------------------------------------------------------------

    def calculate_targets(
        self,
        entry_price: float,
        stop_price: float,
        direction: str,
        risk_reward: float = 2.0,
    ) -> Tuple[float, float, float]:
        """
        Calculate three take-profit levels at 1R, 2R, and 3R.

        Parameters
        ----------
        entry_price : float
            Trade entry price.
        stop_price : float
            Initial stop-loss price (defines 1R risk distance).
        direction : str
            "long" or "short".
        risk_reward : float
            Unused parameter kept for API compatibility.  Targets are
            always at 1R/2R/3R; the parameter was originally the min R:R
            gate before placing the trade.

        Returns
        -------
        Tuple[float, float, float]
            (tp1, tp2, tp3) price levels.
        """
        if entry_price <= 0:
            raise ValueError(f"entry_price must be positive: {entry_price}")

        # 1R = distance from entry to stop
        risk_distance = abs(entry_price - stop_price)
        if risk_distance <= 0:
            raise ValueError(
                f"entry_price ({entry_price}) and stop_price ({stop_price}) are equal"
            )

        if direction.lower() == "long":
            tp1 = entry_price + risk_distance        # +1R
            tp2 = entry_price + 2.0 * risk_distance  # +2R
            tp3 = entry_price + 3.0 * risk_distance  # +3R
        else:  # short
            tp1 = entry_price - risk_distance
            tp2 = entry_price - 2.0 * risk_distance
            tp3 = entry_price - 3.0 * risk_distance
            # Prices must be positive
            tp1 = max(0.0, tp1)
            tp2 = max(0.0, tp2)
            tp3 = max(0.0, tp3)

        logger.debug(
            "calculate_targets: entry=%.5f stop=%.5f dir=%s 1R=%.5f → "
            "tp1=%.5f tp2=%.5f tp3=%.5f",
            entry_price, stop_price, direction, risk_distance, tp1, tp2, tp3,
        )
        return tp1, tp2, tp3

    # ------------------------------------------------------------------
    # TP hit detection
    # ------------------------------------------------------------------

    def check_tp_hit(
        self,
        current_price: float,
        tp_prices: Tuple[float, float, float],
        direction: str,
    ) -> int:
        """
        Return the highest TP level that has been hit (0 = none).

        Parameters
        ----------
        current_price : float
            Latest market price.
        tp_prices : Tuple[float, float, float]
            (tp1, tp2, tp3) levels from ``calculate_targets``.
        direction : str
            "long" or "short".

        Returns
        -------
        int
            0 = no TP hit, 1 = TP1 hit, 2 = TP2 hit, 3 = TP3 hit.
            Returns the HIGHEST level hit so the caller can route directly.
        """
        tp1, tp2, tp3 = tp_prices
        highest_hit = 0

        if direction.lower() == "long":
            if current_price >= tp1:
                highest_hit = 1
            if current_price >= tp2:
                highest_hit = 2
            if current_price >= tp3:
                highest_hit = 3
        else:
            if current_price <= tp1:
                highest_hit = 1
            if current_price <= tp2:
                highest_hit = 2
            if current_price <= tp3:
                highest_hit = 3

        if highest_hit:
            logger.debug(
                "check_tp_hit: price=%.5f hit TP%d (%.5f)",
                current_price, highest_hit, tp_prices[highest_hit - 1],
            )
        return highest_hit

    # ------------------------------------------------------------------
    # Exit size calculation
    # ------------------------------------------------------------------

    def get_exit_size(self, tp_level: int, total_position: float) -> float:
        """
        Return the number of units to close at a given TP level.

        Parameters
        ----------
        tp_level : int
            1, 2, or 3.
        total_position : float
            ORIGINAL (full) position size in units at trade inception.
            This is used as the denominator so fractions are consistent
            regardless of how much has already been closed.

        Returns
        -------
        float
            Units to close.  The caller should cap this at the current
            remaining open position.

        Notes
        -----
        Fractions: TP1=33%, TP2=33%, TP3=34% of the ORIGINAL position.
        Each call returns the fraction for that specific level – cumulative
        tracking is the caller's responsibility.
        """
        fraction = TP_FRACTIONS.get(tp_level, 0.0)
        if fraction <= 0:
            logger.warning("get_exit_size: invalid tp_level=%d", tp_level)
            return 0.0

        exit_units = total_position * fraction
        logger.debug(
            "get_exit_size: tp_level=%d total=%.6f fraction=%.0f%% → %.6f units",
            tp_level, total_position, fraction * 100, exit_units,
        )
        return exit_units

    # ------------------------------------------------------------------
    # Logging helper
    # ------------------------------------------------------------------

    def partial_take_profit_log(
        self,
        trade_id: str,
        tp_level: int,
        price: float,
        units_closed: float,
    ) -> None:
        """
        Log a partial take-profit event.

        Parameters
        ----------
        trade_id : str
            Unique trade identifier.
        tp_level : int
            TP level hit (1, 2, or 3).
        price : float
            Execution price of the partial close.
        units_closed : float
            Number of units closed in this partial exit.
        """
        logger.info(
            "PARTIAL_TP | trade_id=%s | tp_level=%d | price=%.5f | units_closed=%.6f",
            trade_id, tp_level, price, units_closed,
        )
