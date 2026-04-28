"""
Commodity Seasonal Strategy for NEXUS ALPHA.

Seasonal biases (from commodities.yaml):
    Gold:   Strong Jan-Feb, Aug-Sep, Nov-Dec
    Oil:    Spring driving season (Mar-May), refinery demand patterns
    Silver: Follows gold with amplification

The seasonal bias adds/subtracts 0.3 from signal fusion score
and must be confirmed with technical indicators before entry.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

# Seasonal bias map: {commodity_keyword: [(month_start, month_end, direction, strength)]}
SEASONAL_BIASES: Dict[str, List[Tuple[int, int, str, float]]] = {
    "GOLD": [
        (1, 2, "LONG", 0.3),   # Jan-Feb: strong
        (8, 9, "LONG", 0.3),   # Aug-Sep: strong
        (11, 12, "LONG", 0.3), # Nov-Dec: strong
        (3, 5, "SHORT", -0.1), # Mar-May: seasonally weak
    ],
    "SILVER": [
        (1, 2, "LONG", 0.25),
        (8, 9, "LONG", 0.25),
        (11, 12, "LONG", 0.25),
    ],
    "OIL": [
        (3, 5, "LONG", 0.3),   # Spring driving season
        (7, 8, "LONG", 0.2),   # Summer peak demand
        (11, 1, "SHORT", -0.1),# Refinery maintenance
    ],
    "CRUDE": [
        (3, 5, "LONG", 0.3),
        (7, 8, "LONG", 0.2),
    ],
    "NATURAL_GAS": [
        (10, 11, "LONG", 0.25), # Pre-winter storage builds
        (3, 4, "SHORT", -0.2),  # Post-winter demand drop
    ],
}


class CommoditySeasonalStrategy(BaseStrategy):
    """
    Seasonal pattern strategy for commodity CFDs/futures.

    Seasonal score is combined with RSI, MACD and trend to
    produce a composite entry/exit decision.

    Default Parameters
    ------------------
    seasonal_weight : float     0.3   — additive bias to composite score
    min_composite_score : float 0.5   — entry threshold
    rsi_period : int            14
    rsi_neutral_low : float     40.0
    rsi_neutral_high : float    60.0
    atr_period : int            14
    atr_stop_mult : float       2.0
    tp_rr : float               2.5
    trend_sma_period : int      50
    base_size_pct : float       0.01
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "seasonal_weight": 0.3,
        "min_composite_score": 0.5,
        "rsi_period": 14,
        "rsi_neutral_low": 40.0,
        "rsi_neutral_high": 60.0,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "tp_rr": 2.5,
        "trend_sma_period": 50,
        "base_size_pct": 0.01,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="CommoditySeasonal",
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
        if ohlcv is None:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        min_bars = max(self.params["trend_sma_period"] + 10, 60)
        if len(closes) < min_bars:
            return None

        rsi = self.compute_rsi(closes, self.params["rsi_period"])
        _, _, macd_hist = self.compute_macd(closes)
        sma = self.compute_sma(closes, self.params["trend_sma_period"])
        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])

        close = float(closes[-1])
        seasonal_score, seasonal_direction = self._get_seasonal_bias(symbol)

        indicators = {
            "close": close,
            "rsi": float(rsi[-1]) if not np.isnan(rsi[-1]) else 50.0,
            "macd_hist": float(macd_hist[-1]) if not np.isnan(macd_hist[-1]) else 0.0,
            "macd_hist_prev": float(macd_hist[-2]) if not np.isnan(macd_hist[-2]) else 0.0,
            "sma": float(sma[-1]) if not np.isnan(sma[-1]) else close,
            "atr": float(atr[-1]) if not np.isnan(atr[-1]) else 0.0,
            "seasonal_score": seasonal_score,
            "seasonal_direction": seasonal_direction,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        direction = (
            SignalDirection.LONG
            if seasonal_direction == "LONG"
            else SignalDirection.SHORT
        )
        atr_val = indicators["atr"]

        if direction == SignalDirection.LONG:
            stop = close - atr_val * self.params["atr_stop_mult"]
            risk = close - stop
            tp1 = close + risk * self.params["tp_rr"]
            tp2 = close + risk * self.params["tp_rr"] * 1.5
            tp3 = close + risk * self.params["tp_rr"] * 2.0
        else:
            stop = close + atr_val * self.params["atr_stop_mult"]
            risk = stop - close
            tp1 = close - risk * self.params["tp_rr"]
            tp2 = close - risk * self.params["tp_rr"] * 1.5
            tp3 = close - risk * self.params["tp_rr"] * 2.0

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
            confidence=min(0.85, 0.5 + abs(seasonal_score)),
            metadata={
                "seasonal_score": seasonal_score,
                "seasonal_direction": seasonal_direction,
                "month": datetime.utcnow().month,
                "rsi": indicators["rsi"],
            },
        )
        self.record_signal(signal)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        seasonal_score = ind.get("seasonal_score", 0.0)
        seasonal_direction = ind.get("seasonal_direction", "NEUTRAL")

        if seasonal_direction == "NEUTRAL" or abs(seasonal_score) < self.params["seasonal_weight"] * 0.5:
            logger.debug("[%s] No seasonal bias this month", self.name)
            return False

        # Technical alignment
        rsi = ind.get("rsi", 50.0)
        close = ind.get("close", 0.0)
        sma = ind.get("sma", close)
        macd_hist = ind.get("macd_hist", 0.0)
        macd_prev = ind.get("macd_hist_prev", 0.0)

        composite = seasonal_score

        # RSI alignment
        if seasonal_direction == "LONG" and rsi > self.params["rsi_neutral_low"]:
            composite += 0.2
        elif seasonal_direction == "SHORT" and rsi < self.params["rsi_neutral_high"]:
            composite += 0.2

        # Trend alignment
        if seasonal_direction == "LONG" and close > sma:
            composite += 0.2
        elif seasonal_direction == "SHORT" and close < sma:
            composite += 0.2

        # MACD momentum
        if seasonal_direction == "LONG" and macd_hist > macd_prev:
            composite += 0.1
        elif seasonal_direction == "SHORT" and macd_hist < macd_prev:
            composite += 0.1

        if composite < self.params["min_composite_score"]:
            logger.debug(
                "[%s] Composite score %.2f < threshold %.2f",
                self.name, composite, self.params["min_composite_score"],
            )
            return False

        return True

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        close = ind.get("close", 0.0)
        stop = position.get("stop_loss", 0.0)
        tp1 = position.get("take_profit_1", float("inf"))
        direction = position.get("direction", "LONG")

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

        # Seasonal reversal — if season changes against position
        symbol = position.get("symbol", "")
        new_score, new_dir = self._get_seasonal_bias(symbol)
        pos_dir = "LONG" if direction in ("LONG", SignalDirection.LONG) else "SHORT"
        if new_dir not in ("NEUTRAL", pos_dir) and abs(new_score) >= self.params["seasonal_weight"]:
            return "seasonal_reversal"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "seasonal_weight": (float, 0.0, 1.0),
            "min_composite_score": (float, 0.1, 1.0),
            "rsi_period": (int, 5, 30),
            "rsi_neutral_low": (float, 20.0, 50.0),
            "rsi_neutral_high": (float, 50.0, 80.0),
            "atr_period": (int, 5, 30),
            "atr_stop_mult": (float, 0.5, 5.0),
            "tp_rr": (float, 1.0, 6.0),
            "trend_sma_period": (int, 10, 200),
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

    def _get_seasonal_bias(self, symbol: str) -> Tuple[float, str]:
        """
        Return (score, direction) for current month based on the symbol.
        score > 0 → bullish, score < 0 → bearish, direction = 'LONG'/'SHORT'/'NEUTRAL'
        """
        month = datetime.utcnow().month
        sym_upper = symbol.upper()

        best_match: Optional[str] = None
        for key in SEASONAL_BIASES:
            if key in sym_upper:
                best_match = key
                break

        if best_match is None:
            return 0.0, "NEUTRAL"

        biases = SEASONAL_BIASES[best_match]
        total_score = 0.0
        dominant_direction = "NEUTRAL"
        best_abs = 0.0

        for (m_start, m_end, direction, weight) in biases:
            # Handle wrap-around (e.g. Nov-Jan)
            if m_start <= m_end:
                active = m_start <= month <= m_end
            else:
                active = month >= m_start or month <= m_end

            if active:
                signed = weight if direction == "LONG" else -abs(weight)
                total_score += signed
                if abs(signed) > best_abs:
                    best_abs = abs(signed)
                    dominant_direction = direction

        return total_score, dominant_direction

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
