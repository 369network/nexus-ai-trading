"""
NEXUS ALPHA - Stop Loss Manager
=================================
ATR-based initial stops, trailing stops (ratchet only, never worse),
breakeven promotion, and stop-hit detection.
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# ATR multipliers by market
# ---------------------------------------------------------------------------

# Initial stop distance = ATR × multiplier
INITIAL_ATR_MULT: dict[str, float] = {
    "crypto":      2.5,
    "forex":       2.0,
    "commodities": 2.0,
    "stocks_in":   1.5,
    "stocks_us":   1.5,
    "stocks":      1.5,
}

# Trailing stop distance = ATR × multiplier
TRAILING_ATR_MULT: dict[str, float] = {
    "crypto":      2.0,
    "forex":       1.5,
    "commodities": 1.5,
    "stocks_in":   1.5,
    "stocks_us":   1.5,
    "stocks":      1.5,
}

# Profit threshold to move stop to breakeven (1R = 1× risk)
BREAKEVEN_TRIGGER_R = 1.0


class StopLossManager:
    """
    Manages initial placement, trailing updates, and breakeven promotion
    of stop-loss orders across all supported markets.

    All calculations assume price ∈ ℝ⁺ and ATR > 0.
    Direction is either ``"long"`` or ``"short"``.
    """

    # ------------------------------------------------------------------
    # Initial stop
    # ------------------------------------------------------------------

    def calculate_initial_stop(
        self,
        entry_price: float,
        atr: float,
        market: str,
        direction: str,
    ) -> float:
        """
        Calculate the initial stop-loss price using ATR multiples.

        Parameters
        ----------
        entry_price : float
            Trade entry price.
        atr : float
            Current Average True Range in price units.
        market : str
            Market segment: "crypto" | "forex" | "commodities" | "stocks".
        direction : str
            "long" or "short".

        Returns
        -------
        float
            Initial stop price.  Always on the losing side of the entry.
        """
        if atr <= 0:
            raise ValueError(f"ATR must be positive, got {atr}")
        if entry_price <= 0:
            raise ValueError(f"entry_price must be positive, got {entry_price}")

        mult = INITIAL_ATR_MULT.get(market, INITIAL_ATR_MULT["stocks"])
        stop_distance = atr * mult

        if direction.lower() == "long":
            stop_price = entry_price - stop_distance
        else:
            stop_price = entry_price + stop_distance

        stop_price = max(0.0, stop_price)  # never negative
        logger.debug(
            "initial_stop: entry=%.5f atr=%.5f market=%s dir=%s mult=%.1f → stop=%.5f",
            entry_price, atr, market, direction, mult, stop_price,
        )
        return stop_price

    # ------------------------------------------------------------------
    # Trailing stop
    # ------------------------------------------------------------------

    def update_trailing_stop(
        self,
        current_price: float,
        current_stop: float,
        atr: float,
        market: str,
        direction: str,
    ) -> float:
        """
        Ratchet the trailing stop toward the current price.

        The stop only moves in the direction of profit – it is NEVER
        widened.  The trailing distance is ``ATR × market_multiplier``.

        Parameters
        ----------
        current_price : float
            Latest market price.
        current_stop : float
            Existing stop price (may be initial or previously trailed).
        atr : float
            Current ATR value.
        market : str
            Market segment key.
        direction : str
            "long" or "short".

        Returns
        -------
        float
            New stop price (≥ current_stop for longs, ≤ for shorts).
        """
        if atr <= 0:
            return current_stop

        mult = TRAILING_ATR_MULT.get(market, TRAILING_ATR_MULT["stocks"])
        trail_distance = atr * mult

        if direction.lower() == "long":
            proposed_stop = current_price - trail_distance
            # Only move UP (never worse for a long)
            new_stop = max(current_stop, proposed_stop)
        else:
            proposed_stop = current_price + trail_distance
            # Only move DOWN (never worse for a short)
            new_stop = min(current_stop, proposed_stop)

        if new_stop != current_stop:
            logger.debug(
                "trailing_stop: price=%.5f old_stop=%.5f → new_stop=%.5f (dist=%.5f)",
                current_price, current_stop, new_stop, trail_distance,
            )
        return new_stop

    # ------------------------------------------------------------------
    # Stop-hit detection
    # ------------------------------------------------------------------

    def check_stop_hit(
        self,
        current_price: float,
        stop_price: float,
        direction: str,
    ) -> bool:
        """
        Return True if the stop price has been triggered.

        Parameters
        ----------
        current_price : float
            Latest market price (use the worse of bid/ask for safety).
        stop_price : float
            Active stop price.
        direction : str
            "long" or "short".

        Returns
        -------
        bool
            True if price has breached (or equalled) the stop.
        """
        if direction.lower() == "long":
            return current_price <= stop_price
        else:
            return current_price >= stop_price

    # ------------------------------------------------------------------
    # Breakeven stop
    # ------------------------------------------------------------------

    def calculate_breakeven_stop(
        self,
        entry_price: float,
        current_pnl_pct: float,
    ) -> Optional[float]:
        """
        Return the breakeven stop price once a trade is +1R in profit.

        The breakeven level is the entry price itself, meaning the trade
        is protected from a loss once +1R is reached.

        Parameters
        ----------
        entry_price : float
            Original trade entry price.
        current_pnl_pct : float
            Current open profit as a fraction of entry price (positive = profit).
            E.g. 0.02 means the trade is up 2%.

        Returns
        -------
        Optional[float]
            Entry price (breakeven) when ``current_pnl_pct >= 0.01``
            (proxy for 1R), or ``None`` if the threshold has not been reached.

        Notes
        -----
        The caller is responsible for knowing the initial risk (1R) in pct
        terms and mapping ``current_pnl_pct`` accordingly.  The conventional
        trigger is +1R; here we use 1% open profit as the 1R proxy for
        simplicity.  A richer implementation would pass `initial_risk_pct`.
        """
        # Threshold: trade must show at least 1R profit
        # We define 1R ≡ avg_loss_pct proxy ≈ 1% (caller should pass initial risk)
        one_r_threshold = 0.01  # 1% as a reasonable default proxy for 1R

        if current_pnl_pct >= one_r_threshold:
            logger.debug(
                "breakeven_stop: pnl=%.3f%% ≥ 1R threshold → breakeven at entry=%.5f",
                current_pnl_pct * 100, entry_price,
            )
            return entry_price
        return None
