"""
NEXUS ALPHA - Slippage Model
==============================
Estimates execution slippage as a function of market, order size,
and daily traded volume.  Used by the paper trader and for pre-trade
cost analysis.
"""

from __future__ import annotations

import logging
import math
from typing import Dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Base slippage constants by market (fraction of trade value)
# ---------------------------------------------------------------------------

# Format: (base_slippage_pct, volume_impact_coefficient)
# Final slippage = base + coef × sqrt(size_usd / daily_volume_usd)
SLIPPAGE_MODELS: Dict[str, tuple[float, float]] = {
    "crypto":      (0.001,  0.05),   # 0.10% base + volume impact
    "forex":       (0.0002, 0.02),   # 0.02% base (tight spreads)
    "commodities": (0.0005, 0.04),   # 0.05% base
    "stocks_in":   (0.0005, 0.06),   # 0.05% base (NSE)
    "stocks_us":   (0.0003, 0.03),   # 0.03% base (US markets)
    "stocks":      (0.0003, 0.03),   # generic alias
}

# Minimum and maximum slippage bounds
MIN_SLIPPAGE_PCT = 0.0001   # 0.01%
MAX_SLIPPAGE_PCT = 0.03     # 3.0% (emergency / illiquid market cap)


class SlippageModel:
    """
    Market-impact slippage estimator using a square-root price-impact model.

    The square-root model is widely used in academic and practitioner
    literature (Almgren & Chriss, Kyle):

        slippage = base + coef × √(order_size / daily_volume)

    Larger orders consume more of the daily liquidity and incur proportionally
    higher market impact.
    """

    def estimate(
        self,
        market: str,
        size_usd: float,
        daily_volume_usd: float,
    ) -> float:
        """
        Estimate the expected round-trip slippage for an order.

        Parameters
        ----------
        market : str
            Market segment: "crypto" | "forex" | "commodities" |
            "stocks_in" | "stocks_us".
        size_usd : float
            USD notional value of the order.
        daily_volume_usd : float
            Typical daily traded volume for the instrument in USD.
            Pass 1.0 if unknown (uses only base slippage).

        Returns
        -------
        float
            Estimated one-way slippage as a decimal fraction.
            E.g. 0.002 = 0.2% slippage (cost on entry).
            The caller should apply this to both entry and exit if
            computing full round-trip cost.
        """
        if size_usd <= 0:
            return 0.0

        base_pct, coef = SLIPPAGE_MODELS.get(market, SLIPPAGE_MODELS["stocks"])

        # Volume impact term (square-root price impact)
        if daily_volume_usd > 0:
            volume_ratio = size_usd / daily_volume_usd
            volume_impact = coef * math.sqrt(volume_ratio)
        else:
            volume_impact = 0.0

        total_slippage = base_pct + volume_impact
        clamped = max(MIN_SLIPPAGE_PCT, min(MAX_SLIPPAGE_PCT, total_slippage))

        logger.debug(
            "SlippageModel.estimate: market=%s size=$%.2f vol=$%.0f "
            "base=%.4f%% impact=%.4f%% → total=%.4f%%",
            market, size_usd, daily_volume_usd,
            base_pct * 100, volume_impact * 100, clamped * 100,
        )

        return clamped

    def apply_slippage(
        self,
        price: float,
        market: str,
        size_usd: float,
        daily_volume_usd: float,
        direction: str,
    ) -> float:
        """
        Return the effective fill price after slippage.

        Buy orders are filled at a slightly higher price; sell orders
        at a slightly lower price.

        Parameters
        ----------
        price : float
            Mid-market or intended execution price.
        market : str
            Market segment.
        size_usd : float
            Order notional in USD.
        daily_volume_usd : float
            Daily volume for the instrument.
        direction : str
            "buy"/"long" or "sell"/"short".

        Returns
        -------
        float
            Simulated fill price including slippage.
        """
        slip_pct = self.estimate(market, size_usd, daily_volume_usd)

        if direction.lower() in ("buy", "long"):
            fill_price = price * (1.0 + slip_pct)
        else:
            fill_price = price * (1.0 - slip_pct)

        return fill_price

    def estimate_fee(self, market: str, notional_usd: float) -> float:
        """
        Estimate the exchange/broker fee for a trade.

        Parameters
        ----------
        market : str
            Market segment.
        notional_usd : float
            Trade notional in USD.

        Returns
        -------
        float
            Estimated fee in USD.
        """
        fee_rates: Dict[str, float] = {
            "crypto":      0.001,    # 0.10% maker/taker
            "forex":       0.00002,  # 0.002% (pip-spread captured separately)
            "commodities": 0.00005,  # 0.005%
            "stocks_in":   0.0002,   # 0.02% NSE brokerage
            "stocks_us":   0.0001,   # ~$0.005/share; approx 0.01% on notional
            "stocks":      0.0001,
        }
        rate = fee_rates.get(market, 0.001)
        return notional_usd * rate
