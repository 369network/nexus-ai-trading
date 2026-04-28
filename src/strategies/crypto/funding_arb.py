"""
Funding Rate Arbitrage Strategy for NEXUS ALPHA.

Mechanics:
    - When funding rate > 0.08%: SELL perpetual + BUY spot (collect funding)
    - When funding rate < -0.04%: BUY perpetual + SELL spot
    - Exit: when funding rate normalizes to < 0.02% (positive) or > -0.01% (negative)

The strategy tracks net premium vs fees + slippage to ensure profitability.
Requires simultaneous execution on spot and perpetual markets.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)


class FundingRateArbStrategy(BaseStrategy):
    """
    Delta-neutral funding rate arbitrage strategy.

    This strategy generates PAIRED signals: one for the perpetual leg
    and one for the spot leg, both embedded in the signal metadata.
    The executor is responsible for simultaneous placement.

    Default Parameters
    ------------------
    funding_long_threshold : float   0.08  % — enter when funding > this
    funding_short_threshold : float  -0.04 % — enter when funding < this
    exit_funding_long : float        0.02  % — exit long arb when funding < this
    exit_funding_short : float       -0.01 % — exit short arb when funding > this
    fee_rate : float                 0.04  % total round-trip fee estimate
    slippage_pct : float             0.02  % estimated slippage per leg
    min_net_premium : float          0.03  % minimum expected net profit per 8h
    base_size_pct : float            0.03  3% of account
    max_hold_hours : int             48    force exit after 48h if funding unchanged
    lookback_periods : int           8     funding rate samples to average
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "funding_long_threshold": 0.08,
        "funding_short_threshold": -0.04,
        "exit_funding_long": 0.02,
        "exit_funding_short": -0.01,
        "fee_rate": 0.04,
        "slippage_pct": 0.02,
        "min_net_premium": 0.03,
        "base_size_pct": 0.03,
        "max_hold_hours": 48,
        "lookback_periods": 8,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="FundingRateArb",
            market="crypto",
            primary_timeframe="8h",  # Funding rate settles every 8h on Binance
            confirmation_timeframe="1d",
            params=merged,
        )

    # ------------------------------------------------------------------

    def generate_signal(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[TradeSignal]:
        if not self._is_enabled:
            return None

        symbol: str = market_data.get("symbol", "UNKNOWN")
        sentiment: Dict[str, Any] = market_data.get("sentiment", {})

        # Current and historical funding rates
        funding_rate: float = float(sentiment.get("funding_rate", 0.0))
        funding_history: list = sentiment.get("funding_rate_history", [funding_rate])

        # Use lookback average for stability
        lookback = self.params["lookback_periods"]
        recent_rates = funding_history[-lookback:] if len(funding_history) >= lookback else funding_history
        avg_funding = float(np.mean(recent_rates)) if recent_rates else funding_rate

        # Spot price
        ohlcv = market_data.get("ohlcv")
        spot_price = 0.0
        if ohlcv is not None:
            try:
                closes = self._to_array(ohlcv, "close")
                spot_price = float(closes[-1])
            except Exception:
                pass

        if spot_price <= 0:
            spot_price = float(market_data.get("spot_price", 0.0))

        indicators = {
            "funding_rate": funding_rate,
            "avg_funding": avg_funding,
            "spot_price": spot_price,
        }
        full_data = {**market_data, "indicators": indicators}

        if not self.check_entry_conditions(full_data):
            return None

        # Determine direction
        if avg_funding > self.params["funding_long_threshold"]:
            # Collect positive funding: SHORT perp + LONG spot
            direction = SignalDirection.SHORT  # net direction = delta neutral, perp leg
            arb_type = "collect_positive_funding"
            entry_price = spot_price
            stop_loss = spot_price * (1 + 0.05)  # 5% perp adverse move
            tp1 = spot_price * (1 - 0.02)
            tp2 = spot_price * (1 - 0.04)
            tp3 = spot_price * (1 - 0.06)
        else:
            # Collect negative funding: LONG perp + SHORT spot
            direction = SignalDirection.LONG
            arb_type = "collect_negative_funding"
            entry_price = spot_price
            stop_loss = spot_price * (1 - 0.05)
            tp1 = spot_price * (1 + 0.02)
            tp2 = spot_price * (1 + 0.04)
            tp3 = spot_price * (1 + 0.06)

        net_premium = self._compute_net_premium(avg_funding)
        if net_premium <= 0:
            logger.debug("[%s] Net premium %.4f <= 0 after fees; skipping", self.name, net_premium)
            return None

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=direction,
            strength=SignalStrength.MODERATE,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=self.params["base_size_pct"],
            timeframe=self.primary_timeframe,
            confidence=min(0.9, net_premium / 0.1),
            metadata={
                "arb_type": arb_type,
                "funding_rate": funding_rate,
                "avg_funding_8h": avg_funding,
                "net_premium_pct": net_premium,
                "requires_simultaneous_execution": True,
                "spot_leg": {"symbol": symbol.replace("PERP", "SPOT"), "direction": "opposite"},
                "perp_leg": {"symbol": symbol, "direction": direction.value},
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] Arb signal: %s | funding=%.4f%% | net_premium=%.4f%%",
            self.name, arb_type, avg_funding, net_premium,
        )
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})
        avg_funding = ind.get("avg_funding", 0.0)
        net_premium = self._compute_net_premium(avg_funding)

        # Must be outside the neutral zone
        if not (
            avg_funding > self.params["funding_long_threshold"]
            or avg_funding < self.params["funding_short_threshold"]
        ):
            logger.debug(
                "[%s] Funding %.4f%% in neutral zone [%.4f, %.4f]",
                self.name, avg_funding,
                self.params["funding_short_threshold"],
                self.params["funding_long_threshold"],
            )
            return False

        # Must be profitable after fees
        if net_premium <= self.params["min_net_premium"]:
            logger.debug(
                "[%s] Net premium %.4f%% below minimum %.4f%%",
                self.name, net_premium, self.params["min_net_premium"],
            )
            return False

        # Spot price must be available
        if ind.get("spot_price", 0.0) <= 0:
            logger.debug("[%s] No spot price available", self.name)
            return False

        return True

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        sentiment = market_data.get("sentiment", {})
        current_funding = float(sentiment.get("funding_rate", 0.0))
        arb_type = position.get("metadata", {}).get("arb_type", "")
        entry_time = position.get("entry_time")

        # Normalize exit thresholds
        if "positive" in arb_type:
            if current_funding < self.params["exit_funding_long"]:
                return "funding_normalized_below_exit_threshold"
        elif "negative" in arb_type:
            if current_funding > self.params["exit_funding_short"]:
                return "funding_normalized_above_exit_threshold"

        # Time-based exit
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            elapsed_hours = (datetime.utcnow() - entry_time).total_seconds() / 3600
            if elapsed_hours >= self.params["max_hold_hours"]:
                return "max_hold_time_exceeded"

        # Adverse funding flip
        if "positive" in arb_type and current_funding < 0:
            return "funding_flipped_negative"
        if "negative" in arb_type and current_funding > 0:
            return "funding_flipped_positive"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "funding_long_threshold": (float, 0.01, 1.0),
            "funding_short_threshold": (float, -1.0, -0.001),
            "exit_funding_long": (float, 0.0, 0.5),
            "exit_funding_short": (float, -0.5, 0.0),
            "fee_rate": (float, 0.0, 0.5),
            "slippage_pct": (float, 0.0, 0.5),
            "min_net_premium": (float, 0.0, 0.5),
            "base_size_pct": (float, 0.001, 0.1),
            "max_hold_hours": (int, 1, 720),
            "lookback_periods": (int, 1, 48),
        }
        for key, (typ, lo, hi) in constraints.items():
            val = params.get(key)
            if val is None:
                continue
            try:
                val = typ(val)
            except (TypeError, ValueError):
                logger.error("[%s] Param %s wrong type", self.name, key)
                return False
            if not (lo <= val <= hi):
                logger.error("[%s] Param %s=%s out of range [%s, %s]", self.name, key, val, lo, hi)
                return False
        return True

    def backtest_metric(self) -> Dict[str, Any]:
        return self._build_backtest_metric_from_history().to_dict()

    # ------------------------------------------------------------------

    def _compute_net_premium(self, funding_rate: float) -> float:
        """
        Estimate net 8h premium after fees and slippage.
        Premium collected is |funding_rate|.
        Costs = fee_rate + slippage_pct (both legs).
        """
        gross = abs(funding_rate)
        total_cost = self.params["fee_rate"] + self.params["slippage_pct"] * 2
        return gross - total_cost

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> "np.ndarray":
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
