"""
NEXUS ALPHA - Position Sizer
==============================
Combines Quarter-Kelly criterion with ATR-based volatility sizing.
Applies multiple reduction layers for drawdown, funding rate, and
volatility regime, then enforces hard caps per market type.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Hard caps per market (fraction of capital)
# ---------------------------------------------------------------------------
HARD_CAPS: Dict[str, float] = {
    "crypto":       0.10,
    "forex":        0.05,
    "commodities":  0.08,
    "stocks_in":    0.15,
    "stocks_us":    0.15,
    "stocks":       0.15,   # generic alias
}

# Minimum notional to bother placing an order (USD)
MIN_NOTIONAL_USD = 10.0

# High-volatility ATR threshold (relative to price) – used internally
HIGH_VOL_THRESHOLD = 0.03  # 3% ATR/price ratio


@dataclass
class PositionSize:
    """Output of the position sizing calculation."""

    units: float           # Number of units / lots / contracts
    notional_usd: float    # Gross USD value of the position
    risk_usd: float        # USD at risk (distance to initial stop × units)
    size_pct: float        # Position notional as % of total capital (0–1)

    def is_viable(self) -> bool:
        """Return True if the position meets the minimum notional threshold."""
        return self.notional_usd >= MIN_NOTIONAL_USD and self.units > 0


class PositionSizer:
    """
    Multi-layer position sizing engine for NEXUS ALPHA.

    Sizing pipeline:
        1. Quarter-Kelly base size (win-rate / payoff adjusted)
        2. ATR volatility adjustment (normalises risk per trade)
        3. Drawdown reduction (-50% if portfolio drawdown > 10%)
        4. Funding-rate reduction (-50% if crypto funding > 0.05%)
        5. High-volatility reduction (-50% if in high-vol regime)
        6. Hard cap enforcement per market type
    """

    # ------------------------------------------------------------------
    # Static / class-level helpers
    # ------------------------------------------------------------------

    @staticmethod
    def quarter_kelly(
        win_rate: float,
        avg_win_pct: float,
        avg_loss_pct: float,
        capital: float,
    ) -> float:
        """
        Calculate the quarter-Kelly position size in USD.

        The full Kelly fraction is:
            f* = (W/R - (1-W)) / (W/R)   ... simplified Thorp formula

        where W = win_rate, R = avg_win / avg_loss (reward-to-risk ratio).
        We use one quarter of f* for conservatism.

        Parameters
        ----------
        win_rate : float
            Historical win rate, e.g. 0.55 for 55%.
        avg_win_pct : float
            Average winning trade size as a fraction, e.g. 0.02 for 2%.
        avg_loss_pct : float
            Average losing trade size as a fraction, e.g. 0.01 for 1%.
        capital : float
            Total portfolio capital in USD.

        Returns
        -------
        float
            Position size in USD (quarter-Kelly).
        """
        if capital <= 0:
            return 0.0
        if avg_loss_pct <= 0 or avg_win_pct <= 0:
            logger.warning("quarter_kelly: invalid win/loss pct, returning 0")
            return 0.0

        # Reward-to-risk ratio
        rr = avg_win_pct / avg_loss_pct

        # Full Kelly fraction
        if rr == 0:
            return 0.0
        full_kelly = (win_rate * rr - (1.0 - win_rate)) / rr
        full_kelly = max(0.0, full_kelly)  # Kelly can't be negative for position size

        # Quarter Kelly
        quarter_k = full_kelly * 0.25
        position_size_usd = quarter_k * capital

        logger.debug(
            "quarter_kelly: W=%.3f R=%.2f full_kelly=%.4f quarter=%.4f → $%.2f",
            win_rate, rr, full_kelly, quarter_k, position_size_usd,
        )
        return position_size_usd

    @staticmethod
    def atr_size(
        capital: float,
        atr: float,
        price: float,
        risk_pct_per_trade: float,
    ) -> float:
        """
        Calculate position units based on ATR-defined risk.

        Sizes the trade so that one ATR move equals exactly
        ``risk_pct_per_trade`` of capital.

        Parameters
        ----------
        capital : float
            Portfolio capital in USD.
        atr : float
            Current ATR value in price units.
        price : float
            Current asset price in USD (or quote currency).
        risk_pct_per_trade : float
            Fraction of capital to risk per trade, e.g. 0.01 for 1%.

        Returns
        -------
        float
            Number of units (shares / coins / lots).
        """
        if atr <= 0 or price <= 0 or capital <= 0:
            return 0.0

        risk_usd = capital * risk_pct_per_trade
        # Units such that: units × ATR = risk_usd
        units = risk_usd / atr
        logger.debug(
            "atr_size: capital=$%.2f atr=%.5f price=%.5f risk_pct=%.4f → %.6f units",
            capital, atr, price, risk_pct_per_trade, units,
        )
        return units

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def final_size(
        self,
        signal: Any,
        portfolio_state: Dict[str, Any],
        market_config: Dict[str, Any],
    ) -> PositionSize:
        """
        Compute the final position size after all adjustments and caps.

        Parameters
        ----------
        signal : Any
            Trading signal object.  Expected attributes:
              - market (str): "crypto" | "forex" | "commodities" | "stocks_in" | "stocks_us"
              - win_rate (float): historical win rate for this strategy/market
              - avg_win_pct (float): average win fraction
              - avg_loss_pct (float): average loss fraction
              - atr (float): current ATR in price units
              - price (float): current asset price
              - risk_pct (float, optional): per-trade risk fraction (default 0.01)
        portfolio_state : dict
            Must contain:
              - capital (float): current portfolio capital in USD
              - peak_equity (float): all-time or rolling peak equity
              - current_equity (float): current equity value
        market_config : dict
            May contain:
              - funding_rate (float): current 8h funding rate (crypto), default 0
              - volatility_regime (str): "HIGH_VOLATILITY" | "NORMAL" | etc.

        Returns
        -------
        PositionSize
            Final sized position details.
        """
        capital: float = portfolio_state.get("capital", 0.0)
        peak_equity: float = portfolio_state.get("peak_equity", capital)
        current_equity: float = portfolio_state.get("current_equity", capital)

        market: str = getattr(signal, "market", "crypto")
        win_rate: float = getattr(signal, "win_rate", 0.50)
        avg_win_pct: float = getattr(signal, "avg_win_pct", 0.02)
        avg_loss_pct: float = getattr(signal, "avg_loss_pct", 0.01)
        atr: float = getattr(signal, "atr", 0.0)
        price: float = getattr(signal, "price", 1.0)
        risk_pct: float = getattr(signal, "risk_pct", 0.01)

        funding_rate: float = market_config.get("funding_rate", 0.0)
        vol_regime: str = market_config.get("volatility_regime", "NORMAL")

        if capital <= 0:
            logger.warning("final_size: capital is zero or negative")
            return PositionSize(units=0.0, notional_usd=0.0, risk_usd=0.0, size_pct=0.0)

        # ----------------------------------------------------------------
        # Step 1: Quarter-Kelly base notional
        # ----------------------------------------------------------------
        kelly_notional = self.quarter_kelly(win_rate, avg_win_pct, avg_loss_pct, capital)

        # ----------------------------------------------------------------
        # Step 2: ATR-based units and notional
        # ----------------------------------------------------------------
        if atr > 0 and price > 0:
            atr_units = self.atr_size(capital, atr, price, risk_pct)
            atr_notional = atr_units * price
        else:
            # Fallback: use Kelly notional directly
            atr_notional = kelly_notional
            atr_units = kelly_notional / price if price > 0 else 0.0

        # Blend: take the more conservative of Kelly vs ATR, then scale
        # We use Kelly as the base ceiling and ATR as the structural guide
        base_notional = min(kelly_notional, atr_notional)
        if base_notional <= 0:
            base_notional = atr_notional if atr_notional > 0 else kelly_notional

        multiplier = 1.0
        reduction_reasons = []

        # ----------------------------------------------------------------
        # Step 3: Drawdown reduction (−50% if drawdown > 10%)
        # ----------------------------------------------------------------
        if peak_equity > 0:
            drawdown_pct = (peak_equity - current_equity) / peak_equity
            if drawdown_pct > 0.10:
                multiplier *= 0.50
                reduction_reasons.append(f"drawdown={drawdown_pct:.1%}")

        # ----------------------------------------------------------------
        # Step 4: Funding rate reduction (−50% if > 0.05%, crypto only)
        # ----------------------------------------------------------------
        if market == "crypto" and abs(funding_rate) > 0.0005:
            multiplier *= 0.50
            reduction_reasons.append(f"funding={funding_rate:.4%}")

        # ----------------------------------------------------------------
        # Step 5: High-volatility regime reduction (−50%)
        # ----------------------------------------------------------------
        if vol_regime == "HIGH_VOLATILITY":
            multiplier *= 0.50
            reduction_reasons.append("high_vol_regime")

        adjusted_notional = base_notional * multiplier

        # ----------------------------------------------------------------
        # Step 6: Hard cap enforcement
        # ----------------------------------------------------------------
        cap_key = market if market in HARD_CAPS else "stocks"
        hard_cap_fraction = HARD_CAPS.get(cap_key, 0.10)
        hard_cap_notional = capital * hard_cap_fraction

        if adjusted_notional > hard_cap_notional:
            logger.debug(
                "final_size: capping notional from $%.2f to $%.2f (hard_cap=%.0f%% for %s)",
                adjusted_notional, hard_cap_notional, hard_cap_fraction * 100, market,
            )
            adjusted_notional = hard_cap_notional

        # ----------------------------------------------------------------
        # Derive final units and risk
        # ----------------------------------------------------------------
        final_units = adjusted_notional / price if price > 0 else 0.0
        risk_usd = final_units * atr if atr > 0 else adjusted_notional * avg_loss_pct
        size_pct = adjusted_notional / capital if capital > 0 else 0.0

        if reduction_reasons:
            logger.info(
                "final_size: reductions applied [%s] → notional=$%.2f (%.1f%% of capital)",
                ", ".join(reduction_reasons), adjusted_notional, size_pct * 100,
            )

        return PositionSize(
            units=final_units,
            notional_usd=adjusted_notional,
            risk_usd=risk_usd,
            size_pct=size_pct,
        )
