"""
Regime Adapter for NEXUS ALPHA Learning System.

Responds to market regime changes by adjusting:
    - Active strategy set (which strategies are enabled)
    - Risk parameters (position sizes, stop widths)
    - Watchlist composition (which symbols to monitor)

Regimes: trending_up, trending_down, ranging, high_volatility
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Valid regimes
VALID_REGIMES = {"trending_up", "trending_down", "ranging", "high_volatility"}

# Strategy sets per regime and market
REGIME_STRATEGY_MAP: Dict[str, Dict[str, List[str]]] = {
    "trending_up": {
        "crypto": ["CryptoMomentum", "LiquidationCascade"],
        "forex": ["ForexBreakout", "CarryTrade"],
        "commodities": ["CommoditySeasonal"],
        "indian": ["NSEGapTrading", "FIIDIIFlow"],
        "us": ["OpeningRangeBreakout", "SectorRotation", "EarningsPlay"],
    },
    "trending_down": {
        "crypto": ["CryptoMomentum", "FundingRateArb"],
        "forex": ["ForexBreakout", "ForexNewsScalp"],
        "commodities": ["ContangoBackwardation"],
        "indian": ["NSEGapTrading", "FIIDIIFlow", "OptionChain"],
        "us": ["OpeningRangeBreakout", "EarningsPlay"],
    },
    "ranging": {
        "crypto": ["CryptoMeanReversion", "FundingRateArb"],
        "forex": ["ForexRange", "CarryTrade"],
        "commodities": ["ContangoBackwardation", "CommoditySeasonal"],
        "indian": ["OptionChain", "NSEExpiryDay"],
        "us": ["EarningsPlay", "SectorRotation"],
    },
    "high_volatility": {
        "crypto": ["FundingRateArb", "LiquidationCascade"],
        "forex": ["ForexNewsScalp"],
        "commodities": ["CommoditySeasonal"],
        "indian": ["NSEExpiryDay", "OptionChain"],
        "us": ["EarningsPlay"],
    },
}

# Risk adjustments per regime (multiplier on base size/stop)
REGIME_RISK_ADJUSTMENTS: Dict[str, Dict[str, float]] = {
    "trending_up": {
        "size_multiplier": 1.0,
        "stop_multiplier": 1.0,
        "max_concurrent_positions": 6,
    },
    "trending_down": {
        "size_multiplier": 0.8,
        "stop_multiplier": 1.1,
        "max_concurrent_positions": 4,
    },
    "ranging": {
        "size_multiplier": 0.9,
        "stop_multiplier": 0.9,
        "max_concurrent_positions": 5,
    },
    "high_volatility": {
        "size_multiplier": 0.5,
        "stop_multiplier": 1.5,
        "max_concurrent_positions": 2,
    },
}

# Watchlist additions/removals per regime transition
REGIME_WATCHLIST_CHANGES: Dict[str, Dict[str, Any]] = {
    "trending_up": {
        "add": ["high_beta_crypto", "growth_sectors", "commodity_longs"],
        "remove": ["defensive_sectors", "inverse_etfs"],
    },
    "trending_down": {
        "add": ["defensive_sectors", "inverse_etfs", "gold", "usd"],
        "remove": ["high_beta_crypto", "growth_sectors"],
    },
    "ranging": {
        "add": ["option_strategies", "mean_reversion_candidates"],
        "remove": ["momentum_plays"],
    },
    "high_volatility": {
        "add": ["vix_plays", "safe_havens"],
        "remove": ["illiquid_pairs", "small_caps"],
    },
}


@dataclass
class RegimeAdaptation:
    """Result of a regime change — describes what should change."""
    old_regime: str
    new_regime: str
    market: str
    strategy_adjustments: Dict[str, List[str]]  # {enable: [...], disable: [...]}
    risk_adjustments: Dict[str, float]
    watchlist_changes: Dict[str, List[str]]
    notes: List[str] = field(default_factory=list)


class RegimeAdapter:
    """
    Adapts trading system configuration when market regime changes.

    Parameters
    ----------
    memory : optional
        MediumTermMemory instance for logging transitions.
    """

    def __init__(self, memory: Optional[Any] = None) -> None:
        self._memory = memory
        self._current_regimes: Dict[str, str] = {}  # market → current regime
        logger.info("RegimeAdapter initialised")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def on_regime_change(
        self,
        old_regime: str,
        new_regime: str,
        market: str,
    ) -> RegimeAdaptation:
        """
        Process a regime transition and return required system adaptations.

        Parameters
        ----------
        old_regime : str
            Previous market regime.
        new_regime : str
            Newly detected market regime.
        market : str
            Market affected: 'crypto', 'forex', 'commodities', 'indian', 'us'.

        Returns
        -------
        RegimeAdaptation
            Describes exactly what needs to change.
        """
        if new_regime not in VALID_REGIMES:
            logger.warning("Unknown regime: %s; defaulting to 'ranging'", new_regime)
            new_regime = "ranging"

        if old_regime == new_regime:
            logger.debug("No regime change detected for %s: still %s", market, new_regime)
            return self._no_change_adaptation(old_regime, market)

        logger.info("Regime change [%s]: %s → %s", market, old_regime, new_regime)
        self._current_regimes[market] = new_regime

        # Determine which strategies to enable/disable
        old_strategies = set(REGIME_STRATEGY_MAP.get(old_regime, {}).get(market, []))
        new_strategies = set(REGIME_STRATEGY_MAP.get(new_regime, {}).get(market, []))
        to_enable = list(new_strategies - old_strategies)
        to_disable = list(old_strategies - new_strategies)

        # Risk adjustments
        risk_adj = dict(REGIME_RISK_ADJUSTMENTS.get(new_regime, {}))

        # Watchlist changes
        watchlist = dict(REGIME_WATCHLIST_CHANGES.get(new_regime, {}))

        # Generate human-readable notes
        notes = self._generate_notes(old_regime, new_regime, market, to_enable, to_disable, risk_adj)

        adaptation = RegimeAdaptation(
            old_regime=old_regime,
            new_regime=new_regime,
            market=market,
            strategy_adjustments={
                "enable": to_enable,
                "disable": to_disable,
            },
            risk_adjustments=risk_adj,
            watchlist_changes=watchlist,
            notes=notes,
        )

        # Log to medium-term memory
        if self._memory is not None:
            try:
                self._memory.store_regime_transition(
                    market=market,
                    old_regime=old_regime,
                    new_regime=new_regime,
                    trigger="auto_detection",
                    metadata={
                        "enable": to_enable,
                        "disable": to_disable,
                        "risk_multipliers": risk_adj,
                    },
                )
            except Exception as exc:
                logger.warning("Could not log regime transition to memory: %s", exc)

        return adaptation

    def get_current_strategy_set(self, regime: str, market: str) -> List[str]:
        """
        Return the list of enabled strategy names for a given regime and market.

        Parameters
        ----------
        regime : str
            Market regime.
        market : str
            Market.

        Returns
        -------
        List[str]
            Strategy names that should be active.
        """
        if regime not in VALID_REGIMES:
            regime = "ranging"
        return list(REGIME_STRATEGY_MAP.get(regime, {}).get(market, []))

    def get_risk_adjustments(self, regime: str) -> Dict[str, float]:
        """Return risk adjustment multipliers for the given regime."""
        return dict(REGIME_RISK_ADJUSTMENTS.get(regime, {
            "size_multiplier": 1.0,
            "stop_multiplier": 1.0,
            "max_concurrent_positions": 5,
        }))

    def get_current_regime(self, market: str) -> str:
        """Return the last known regime for a market."""
        return self._current_regimes.get(market, "ranging")

    def apply_risk_adjustments(
        self,
        base_params: Dict[str, Any],
        regime: str,
    ) -> Dict[str, Any]:
        """
        Apply regime-specific risk multipliers to strategy params.

        Parameters
        ----------
        base_params : dict
            Original strategy parameters.
        regime : str
            Current market regime.

        Returns
        -------
        dict
            Modified parameters with risk adjustments applied.
        """
        adj = self.get_risk_adjustments(regime)
        updated = dict(base_params)

        size_mult = adj.get("size_multiplier", 1.0)
        stop_mult = adj.get("stop_multiplier", 1.0)

        if "base_size_pct" in updated:
            updated["base_size_pct"] = round(
                float(updated["base_size_pct"]) * size_mult, 6
            )
        if "atr_stop_mult" in updated:
            updated["atr_stop_mult"] = round(
                float(updated["atr_stop_mult"]) * stop_mult, 3
            )

        logger.debug(
            "Applied risk adjustments for regime=%s: size×%.2f stop×%.2f",
            regime, size_mult, stop_mult,
        )
        return updated

    def detect_regime(self, market_data: Dict[str, Any]) -> str:
        """
        Simple regime detection from market indicators.

        Uses ADX for trend strength and ATR for volatility.
        This is a lightweight fallback; production should use a dedicated
        RegimeDetector module.

        Parameters
        ----------
        market_data : dict
            Contains 'indicators' with adx, atr, close, sma_fast, sma_slow.

        Returns
        -------
        str
            One of: 'trending_up', 'trending_down', 'ranging', 'high_volatility'
        """
        ind = market_data.get("indicators", {})
        adx = float(ind.get("adx", 20.0))
        atr_pct = float(ind.get("atr_pct", 0.02))  # ATR as % of price
        close = float(ind.get("close", 1.0))
        sma_fast = float(ind.get("sma_fast", close))
        sma_slow = float(ind.get("sma_slow", close))

        # High volatility: ATR % > 3%
        if atr_pct > 0.03:
            return "high_volatility"

        # Trending vs ranging based on ADX
        if adx >= 25:
            if sma_fast >= sma_slow:
                return "trending_up"
            else:
                return "trending_down"
        else:
            return "ranging"

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _no_change_adaptation(regime: str, market: str) -> RegimeAdaptation:
        return RegimeAdaptation(
            old_regime=regime,
            new_regime=regime,
            market=market,
            strategy_adjustments={"enable": [], "disable": []},
            risk_adjustments=REGIME_RISK_ADJUSTMENTS.get(regime, {}),
            watchlist_changes={},
            notes=["No regime change; no adaptations required"],
        )

    @staticmethod
    def _generate_notes(
        old_regime: str,
        new_regime: str,
        market: str,
        to_enable: List[str],
        to_disable: List[str],
        risk_adj: Dict[str, float],
    ) -> List[str]:
        notes = [
            f"Market {market} regime changed: {old_regime} → {new_regime}",
        ]
        if to_enable:
            notes.append(f"Enabling strategies: {', '.join(to_enable)}")
        if to_disable:
            notes.append(f"Disabling strategies: {', '.join(to_disable)}")
        size_mult = risk_adj.get("size_multiplier", 1.0)
        stop_mult = risk_adj.get("stop_multiplier", 1.0)
        if size_mult != 1.0:
            notes.append(f"Position sizes scaled by {size_mult:.0%}")
        if stop_mult != 1.0:
            notes.append(f"Stop multipliers scaled by {stop_mult:.0%}")
        max_pos = risk_adj.get("max_concurrent_positions", 5)
        notes.append(f"Max concurrent positions: {max_pos}")
        return notes

    def __repr__(self) -> str:
        return (
            f"<RegimeAdapter markets={list(self._current_regimes.keys())} "
            f"current_regimes={self._current_regimes}>"
        )
