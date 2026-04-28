"""
Strategy Registry — loads strategy instances for a given market.
"""
from __future__ import annotations
import logging
from typing import Any, List

logger = logging.getLogger(__name__)


class StrategyRegistry:
    """Discovers and instantiates strategy classes for each market."""

    @staticmethod
    def get_strategies_for_market(
        market: str,
        config: Any,
        settings: Any,
    ) -> List[Any]:
        """Return a list of initialised strategy instances for the market."""
        market = market.lower()
        strategies: List[Any] = []

        paper_mode = getattr(settings, "paper_mode", True)

        strategy_modules = {
            "crypto": [
                ("src.strategies.crypto.momentum",          "CryptoMomentumStrategy"),
                ("src.strategies.crypto.mean_reversion",    "CryptoMeanReversionStrategy"),
                ("src.strategies.crypto.funding_arb",       "FundingRateArbStrategy"),
                ("src.strategies.crypto.liquidation_cascade","LiquidationCascadeStrategy"),
                # Paper-mode trend strategy: fires under normal market conditions
                # to exercise the full trade pipeline (exec, fees, slippage, DB).
                *([("src.strategies.crypto.paper_trend", "CryptoPaperTrendStrategy")]
                  if paper_mode else []),
            ],
            "forex": [
                ("src.strategies.forex.breakout",       "ForexBreakoutStrategy"),
                ("src.strategies.forex.range_trading",  "ForexRangeStrategy"),
                ("src.strategies.forex.carry_trade",    "CarryTradeStrategy"),
                ("src.strategies.forex.news_scalp",     "ForexNewsScalpStrategy"),
            ],
            "indian_stocks": [
                ("src.strategies.indian.gap_trading",   "NSEGapTradingStrategy"),
                ("src.strategies.indian.fii_dii_flow",  "FIIDIIFlowStrategy"),
                ("src.strategies.indian.option_chain",  "OptionChainStrategy"),
                ("src.strategies.indian.expiry_day",    "NSEExpiryDayStrategy"),
            ],
            "us_stocks": [
                ("src.strategies.us.orb",              "OpeningRangeBreakoutStrategy"),
                ("src.strategies.us.earnings_play",    "EarningsPlayStrategy"),
                ("src.strategies.us.sector_rotation",  "SectorRotationStrategy"),
            ],
            "commodities": [
                ("src.strategies.commodities.seasonal",  "CommoditySeasonalStrategy"),
                ("src.strategies.commodities.contango",  "ContangoBackwardationStrategy"),
            ],
        }

        for module_path, class_name in strategy_modules.get(market, []):
            try:
                import importlib
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                # Strategies accept optional params dict; use DEFAULT_PARAMS.
                strategy = cls()
                strategies.append(strategy)
                logger.debug("Loaded strategy: %s", class_name)
            except Exception as exc:
                logger.warning(
                    "Could not load strategy %s.%s: %s",
                    module_path, class_name, exc
                )

        if not strategies:
            logger.warning(
                "No strategies loaded for market '%s' — using BaseStrategy stub",
                market,
            )

        return strategies
