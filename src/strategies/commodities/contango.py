"""
Contango/Backwardation Strategy for NEXUS ALPHA.

Mechanics:
    - Contango  > 0.5%: Sell spot/front-month (roll-down benefits shorts)
    - Backwardation > 0.3%: Buy spot/front-month (roll-up benefits longs)
    - Exit: when differential normalizes

For OANDA CFDs: uses price deviation from historical norm as proxy
when direct futures differential is unavailable.
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


class ContangoBackwardationStrategy(BaseStrategy):
    """
    Futures term structure exploitation strategy.

    Trades the roll-yield advantage from contango/backwardation
    in commodity futures or uses historical price deviation as proxy.

    Default Parameters
    ------------------
    contango_threshold : float   0.5   % — enter short when contango > this
    backwardation_threshold : float  0.3 % — enter long when backwardation > this
    exit_differential : float    0.1   % — exit when differential < this
    lookback_norm : int          90    — days for historical norm calculation
    atr_period : int             14
    atr_stop_mult : float        2.0
    tp_rr : float                2.0
    base_size_pct : float        0.01
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "contango_threshold": 0.5,
        "backwardation_threshold": 0.3,
        "exit_differential": 0.1,
        "lookback_norm": 90,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "tp_rr": 2.0,
        "base_size_pct": 0.01,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="ContangoBackwardation",
            market="commodities",
            primary_timeframe="1d",
            confirmation_timeframe="1w",
            params=merged,
        )

    # ------------------------------------------------------------------

    def generate_signal(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[TradeSignal]:
        if not self._is_enabled:
            return None

        symbol: str = market_data.get("symbol", "UNKNOWN")
        ohlcv = market_data.get("ohlcv")
        futures_data = market_data.get("futures_curve", {})

        if ohlcv is None:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        if len(closes) < self.params["lookback_norm"] + 10:
            return None

        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        close = float(closes[-1])
        atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

        # Get term structure differential
        differential, structure_type = self._get_term_structure(
            closes, futures_data, close
        )

        indicators = {
            "close": close,
            "atr": atr_val,
            "differential": differential,
            "structure_type": structure_type,  # 'contango', 'backwardation', 'neutral'
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        if structure_type == "contango":
            direction = SignalDirection.SHORT
            stop = close + atr_val * self.params["atr_stop_mult"]
            risk = stop - close
            tp1 = close - risk * self.params["tp_rr"]
            tp2 = close - risk * self.params["tp_rr"] * 1.5
            tp3 = close - risk * self.params["tp_rr"] * 2.0
        else:  # backwardation
            direction = SignalDirection.LONG
            stop = close - atr_val * self.params["atr_stop_mult"]
            risk = close - stop
            tp1 = close + risk * self.params["tp_rr"]
            tp2 = close + risk * self.params["tp_rr"] * 1.5
            tp3 = close + risk * self.params["tp_rr"] * 2.0

        confidence = min(0.85, abs(differential) / 2.0)

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=direction,
            strength=SignalStrength.MODERATE,
            entry_price=close,
            stop_loss=stop,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=self.params["base_size_pct"],
            timeframe=self.primary_timeframe,
            confidence=confidence,
            metadata={
                "structure_type": structure_type,
                "differential_pct": differential,
                "atr": atr_val,
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] %s signal | %s | differential=%.3f%%",
            self.name, direction.value, structure_type, differential,
        )
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})
        structure = ind.get("structure_type", "neutral")
        diff = abs(ind.get("differential", 0.0))

        if structure == "contango" and diff >= self.params["contango_threshold"]:
            return True
        if structure == "backwardation" and diff >= self.params["backwardation_threshold"]:
            return True

        logger.debug(
            "[%s] Differential %.3f%% insufficient for structure=%s",
            self.name, diff, structure,
        )
        return False

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        close = ind.get("close", 0.0)
        stop = position.get("stop_loss", 0.0)
        tp1 = position.get("take_profit_1", float("inf"))
        direction = position.get("direction", "LONG")
        differential = abs(ind.get("differential", 1.0))

        if direction == "LONG":
            if close <= stop:
                return "stop_loss_hit"
            if close >= tp1:
                return "take_profit_1"
        else:
            if close >= stop:
                return "stop_loss_hit"
            if close <= tp1:
                return "take_profit_1"

        if differential < self.params["exit_differential"]:
            return "differential_normalized"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "contango_threshold": (float, 0.05, 5.0),
            "backwardation_threshold": (float, 0.05, 5.0),
            "exit_differential": (float, 0.0, 1.0),
            "lookback_norm": (int, 10, 365),
            "atr_period": (int, 5, 30),
            "atr_stop_mult": (float, 0.5, 5.0),
            "tp_rr": (float, 1.0, 5.0),
            "base_size_pct": (float, 0.001, 0.05),
        }
        for key, (typ, lo, hi) in constraints.items():
            val = params.get(key)
            if val is None:
                continue
            try:
                val = typ(val)
            except (TypeError, ValueError):
                return False
            if not (lo <= val <= hi):
                return False
        return True

    def backtest_metric(self) -> Dict[str, Any]:
        return self._build_backtest_metric_from_history().to_dict()

    # ------------------------------------------------------------------

    def _get_term_structure(
        self,
        closes: np.ndarray,
        futures_data: Dict[str, Any],
        current_price: float,
    ) -> tuple[float, str]:
        """
        Determine term structure differential in %.

        Priority:
        1. Use front vs second month from futures_data if available
        2. Fall back to price deviation from historical norm
        """
        front = futures_data.get("front_month_price")
        second = futures_data.get("second_month_price")

        if front and second and front > 0:
            diff_pct = (second - front) / front * 100.0
            if diff_pct > 0:
                return diff_pct, "contango"
            elif diff_pct < 0:
                return abs(diff_pct), "backwardation"
            return 0.0, "neutral"

        # Fallback: deviation from lookback mean
        lookback = min(self.params["lookback_norm"], len(closes) - 1)
        hist_mean = float(np.mean(closes[-lookback - 1 : -1]))
        if hist_mean <= 0:
            return 0.0, "neutral"

        deviation_pct = (current_price - hist_mean) / hist_mean * 100.0

        # Positive deviation (overvalued vs history) → contango proxy → short
        # Negative deviation (undervalued) → backwardation proxy → long
        if deviation_pct > 0:
            return deviation_pct, "contango"
        elif deviation_pct < 0:
            return abs(deviation_pct), "backwardation"
        return 0.0, "neutral"

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
