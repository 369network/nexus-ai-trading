"""
Forex Range Trading Strategy for NEXUS ALPHA.

Entry conditions:
    - ADX < 20 (non-trending market)
    - Price between well-defined support and resistance
    - Buy at support, sell at resistance with tight stops

Exit conditions:
    - ADX breaks above 25 (trending breakout)
    - Opposite boundary reached
    - Stop loss triggered
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)


class ForexRangeStrategy(BaseStrategy):
    """
    Range-bound trading strategy for Forex major pairs.

    Detects ranging conditions via ADX and price oscillation between
    well-defined S/R levels. Buys at support, sells at resistance.

    Default Parameters
    ------------------
    adx_period : int            14
    adx_threshold_entry : float 20.0 — must be < this to enter
    adx_threshold_exit : float  25.0 — exit when ADX breaks above
    sr_lookback : int           20   — bars to identify S/R
    sr_touch_tolerance : float  0.001 — 0.1% of price for S/R touch
    atr_period : int            14
    atr_stop_mult : float       1.0  — tight stop inside range
    base_size_pct : float       0.01
    min_range_atr_mult : float  3.0  — minimum range width in ATR units
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "adx_period": 14,
        "adx_threshold_entry": 20.0,
        "adx_threshold_exit": 25.0,
        "sr_lookback": 20,
        "sr_touch_tolerance": 0.001,
        "atr_period": 14,
        "atr_stop_mult": 1.0,
        "base_size_pct": 0.01,
        "min_range_atr_mult": 3.0,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="ForexRange",
            market="forex",
            primary_timeframe="4h",
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
        ohlcv = market_data.get("ohlcv")
        if ohlcv is None:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        lookback = self.params["sr_lookback"]
        adx_period = self.params["adx_period"]
        min_bars = max(lookback + adx_period * 2, 50)

        if len(closes) < min_bars:
            return None

        adx = self.compute_adx(highs, lows, closes, adx_period)
        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])

        current_adx = float(adx[-1]) if not np.isnan(adx[-1]) else 50.0
        current_atr = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0
        close = float(closes[-1])

        support, resistance = self._find_sr_levels(highs, lows, lookback)

        indicators = {
            "adx": current_adx,
            "atr": current_atr,
            "close": close,
            "support": support,
            "resistance": resistance,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        # Entry direction
        tol = close * self.params["sr_touch_tolerance"]
        at_support = abs(close - support) <= tol * 2
        at_resistance = abs(close - resistance) <= tol * 2

        if at_support:
            direction = SignalDirection.LONG
            entry = close
            stop = support - current_atr * self.params["atr_stop_mult"]
            tp1 = resistance
            tp2 = resistance + (resistance - support) * 0.1
            tp3 = tp2
        elif at_resistance:
            direction = SignalDirection.SHORT
            entry = close
            stop = resistance + current_atr * self.params["atr_stop_mult"]
            tp1 = support
            tp2 = support - (resistance - support) * 0.1
            tp3 = tp2
        else:
            logger.debug("[%s] Not at S/R boundary; no signal", self.name)
            return None

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=direction,
            strength=SignalStrength.MODERATE,
            entry_price=entry,
            stop_loss=stop,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=self.params["base_size_pct"],
            timeframe=self.primary_timeframe,
            confidence=max(0.4, 1.0 - current_adx / self.params["adx_threshold_entry"]),
            metadata={
                "adx": current_adx,
                "support": support,
                "resistance": resistance,
                "range_width_atr": (resistance - support) / max(current_atr, 1e-9),
            },
        )
        self.record_signal(signal)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        adx = ind.get("adx", 100.0)
        if adx >= self.params["adx_threshold_entry"]:
            logger.debug("[%s] ADX %.2f >= %.2f; not ranging", self.name, adx, self.params["adx_threshold_entry"])
            return False

        support = ind.get("support", 0.0)
        resistance = ind.get("resistance", 0.0)
        atr = ind.get("atr", 0.0)

        if support <= 0 or resistance <= 0 or resistance <= support:
            logger.debug("[%s] Invalid S/R levels", self.name)
            return False

        # Range must be wide enough to trade
        range_width = resistance - support
        min_width = atr * self.params["min_range_atr_mult"]
        if range_width < min_width:
            logger.debug("[%s] Range too narrow: %.5f < %.5f", self.name, range_width, min_width)
            return False

        close = ind.get("close", 0.0)
        tol = close * self.params["sr_touch_tolerance"]
        at_boundary = (
            abs(close - support) <= tol * 2
            or abs(close - resistance) <= tol * 2
        )
        if not at_boundary:
            logger.debug("[%s] Price not at S/R boundary", self.name)
            return False

        return True

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        adx = ind.get("adx", 0.0)
        close = ind.get("close", 0.0)
        stop = position.get("stop_loss", 0.0)
        tp1 = position.get("take_profit_1", float("inf"))
        direction = position.get("direction", "LONG")

        # ADX breakout — market no longer ranging
        if adx >= self.params["adx_threshold_exit"]:
            return "adx_breakout_exit"

        if direction == "LONG":
            if close <= stop:
                return "stop_loss_hit"
            if close >= tp1:
                return "take_profit_resistance"
        else:
            if close >= stop:
                return "stop_loss_hit"
            if close <= tp1:
                return "take_profit_support"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "adx_period": (int, 5, 30),
            "adx_threshold_entry": (float, 10.0, 30.0),
            "adx_threshold_exit": (float, 20.0, 40.0),
            "sr_lookback": (int, 5, 100),
            "sr_touch_tolerance": (float, 0.0001, 0.01),
            "atr_period": (int, 5, 30),
            "atr_stop_mult": (float, 0.3, 3.0),
            "base_size_pct": (float, 0.001, 0.05),
            "min_range_atr_mult": (float, 1.0, 10.0),
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
        if "adx_threshold_entry" in params and "adx_threshold_exit" in params:
            if params["adx_threshold_entry"] >= params["adx_threshold_exit"]:
                return False
        return True

    def backtest_metric(self) -> Dict[str, Any]:
        return self._build_backtest_metric_from_history().to_dict()

    # ------------------------------------------------------------------

    @staticmethod
    def _find_sr_levels(
        highs: np.ndarray, lows: np.ndarray, lookback: int
    ) -> Tuple[float, float]:
        """Find support (lowest low) and resistance (highest high) over lookback bars."""
        window_highs = highs[-lookback:]
        window_lows = lows[-lookback:]
        support = float(np.min(window_lows))
        resistance = float(np.max(window_highs))
        return support, resistance

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
