# src/signals/onchain_signals.py
"""On-chain signal generator for crypto markets."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class OnChainSignalGenerator:
    """Generates on-chain sentiment signals for crypto assets.

    For non-crypto markets, returns 0.0 immediately.

    Signal composition:
    - Whale alerts    : -0.3 to +0.3
    - Exchange flows  : -0.4 to +0.4
    - Funding rate    : -0.3 to +0.3

    Total range: -1.0 to +1.0
    """

    # Crypto markets that support on-chain signals
    _CRYPTO_MARKETS = {"crypto", "defi", "nft"}

    def generate(
        self,
        symbol: str,
        market: str = "crypto",
        onchain_data: Optional[Dict[str, Any]] = None,
    ) -> float:
        """Generate an on-chain signal for *symbol*.

        Parameters
        ----------
        symbol:
            Trading symbol (e.g. "BTC/USDT").
        market:
            Market type. For non-crypto markets, returns 0.0.
        onchain_data:
            Dict with keys: exchange_flow, whale_activity, funding_rate,
            oi_change, ls_ratio, exchange_reserves, etc.

        Returns
        -------
        float
            -1.0 to +1.0 on-chain signal, or 0.0 for non-crypto markets.
        """
        if market.lower() not in self._CRYPTO_MARKETS:
            logger.debug("Non-crypto market %r — returning neutral on-chain signal", market)
            return 0.0

        if not onchain_data:
            logger.debug("No on-chain data for %s — returning 0", symbol)
            return 0.0

        whale_signal = self._whale_signal(onchain_data)
        flow_signal = self._exchange_flow_signal(onchain_data)
        funding_signal = self._funding_rate_signal(onchain_data)

        total = whale_signal + flow_signal + funding_signal

        logger.debug(
            "OnChain %s: whale=%.2f flow=%.2f funding=%.2f total=%.2f",
            symbol, whale_signal, flow_signal, funding_signal, total,
        )

        return max(-1.0, min(1.0, total))

    # ------------------------------------------------------------------
    # Sub-signal components
    # ------------------------------------------------------------------

    def _whale_signal(self, data: Dict[str, Any]) -> float:
        """Whale activity signal (-0.3 to +0.3).

        Positive signal: whale buying / accumulation
        Negative signal: whale selling / distribution
        """
        whale = data.get("whale_activity", "")
        whale_str = str(whale).lower()

        # Text-based interpretation
        if any(w in whale_str for w in ["buy", "accum", "transfer to cold", "outflow"]):
            return 0.3
        elif any(w in whale_str for w in ["sell", "distribut", "transfer to exchange", "inflow"]):
            return -0.3
        elif any(w in whale_str for w in ["neutral", "mixed", "none"]):
            return 0.0

        # Numeric interpretation if provided as float
        if isinstance(whale, (int, float)):
            # Positive = buying pressure, negative = selling
            return max(-0.3, min(0.3, float(whale) * 0.3))

        return 0.0

    def _exchange_flow_signal(self, data: Dict[str, Any]) -> float:
        """Exchange flow signal (-0.4 to +0.4).

        Negative flow (outflow > inflow) = bullish (coins leaving exchanges)
        Positive flow (inflow > outflow) = bearish (coins entering exchanges)
        """
        flow = data.get("exchange_flow", 0)

        if not isinstance(flow, (int, float)):
            # Try to parse string like "-5000 BTC"
            try:
                flow = float(str(flow).split()[0])
            except (ValueError, IndexError):
                return 0.0

        flow = float(flow)

        # Normalise: assume typical daily flow is ±10k BTC (or equivalent)
        # Clip the signal to ±0.4
        if abs(flow) < 100:  # small flow — negligible
            return 0.0

        # Outflow (negative) = bullish (+)
        # Inflow (positive) = bearish (-)
        raw = -flow / max(abs(flow), 1) * 0.4
        return max(-0.4, min(0.4, raw))

    def _funding_rate_signal(self, data: Dict[str, Any]) -> float:
        """Funding rate contrarian signal (-0.3 to +0.3).

        High positive funding = overleveraged longs = bearish contrarian (-0.3)
        High negative funding = overleveraged shorts = bullish contrarian (+0.3)
        Near-zero funding = neutral
        """
        funding = data.get("funding_rate", 0)

        if not isinstance(funding, (int, float)):
            try:
                funding = float(str(funding).replace("%", ""))
            except (ValueError, TypeError):
                return 0.0

        funding = float(funding)

        # Typical range: -0.1% to +0.1% per 8h
        # Extreme: > 0.05% = overleveraged longs (bearish signal)
        if funding > 0.05:
            # Scale: 0.05% → -0.15, 0.1% → -0.3
            return max(-0.3, -min(0.3, (funding - 0.03) * 6))
        elif funding < -0.02:
            # Scale: -0.02% → +0.1, -0.05% → +0.3
            return min(0.3, max(0.0, (-funding - 0.01) * 8))
        else:
            return 0.0  # neutral funding zone

    # ------------------------------------------------------------------
    # Additional signals (used when data is available)
    # ------------------------------------------------------------------

    def _oi_signal(self, data: Dict[str, Any]) -> float:
        """Open interest change signal.

        Large OI increase with price rising = leveraged longs (risky, bearish)
        Large OI decrease (liquidations) = potential reversal signal
        """
        oi_change = data.get("oi_change", 0)
        if not isinstance(oi_change, (int, float)):
            return 0.0
        oi_change = float(oi_change)

        if oi_change > 30:
            return -0.2  # OI spike — overleveraged
        elif oi_change < -20:
            return 0.2  # OI drop — liquidation cleanup, potential bottom
        return 0.0
