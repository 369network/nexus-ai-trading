"""
Option Chain Analysis Strategy for NEXUS ALPHA.

Uses NSE option chain data to generate directional signals for index.

Signals:
    - PCR (Put-Call Ratio) > 1.5: strong bullish (too many puts, oversold)
    - PCR < 0.5: strong bearish (too many calls, overbought)
    - Max pain deviation > 2%: expect gravitational pull toward max pain
    - High OI buildup at strikes → use as S/R levels for intraday

Option chain data must be provided in market_data['option_chain'].
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


class OptionChainStrategy(BaseStrategy):
    """
    Option chain sentiment analysis strategy for NSE indices.

    Default Parameters
    ------------------
    pcr_bullish_threshold : float   1.5
    pcr_bearish_threshold : float   0.5
    max_pain_dev_threshold : float  2.0   %
    oi_sr_lookback : int            5     top N OI strikes for S/R
    atr_period : int                14
    atr_stop_mult : float           1.5
    tp_rr : float                   2.0
    base_size_pct : float           0.015
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "pcr_bullish_threshold": 1.5,
        "pcr_bearish_threshold": 0.5,
        "max_pain_dev_threshold": 2.0,
        "oi_sr_lookback": 5,
        "atr_period": 14,
        "atr_stop_mult": 1.5,
        "tp_rr": 2.0,
        "base_size_pct": 0.015,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="OptionChain",
            market="indian",
            primary_timeframe="5m",
            confirmation_timeframe="1d",
            params=merged,
        )

    # ------------------------------------------------------------------

    def generate_signal(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[TradeSignal]:
        if not self._is_enabled:
            return None

        symbol: str = market_data.get("symbol", "NIFTY50")
        ohlcv = market_data.get("ohlcv")
        option_chain: Dict[str, Any] = market_data.get("option_chain", {})

        if ohlcv is None or not option_chain:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        if len(closes) < 10:
            return None

        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        close = float(closes[-1])
        atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

        # Parse option chain
        pcr = float(option_chain.get("pcr", 1.0))
        max_pain = float(option_chain.get("max_pain", close))
        strikes = option_chain.get("strikes", [])

        max_pain_dev_pct = (close - max_pain) / max_pain * 100.0 if max_pain > 0 else 0.0

        # Top OI strikes for S/R
        sr_levels = self._extract_oi_sr_levels(strikes, close, self.params["oi_sr_lookback"])

        indicators = {
            "close": close,
            "atr": atr_val,
            "pcr": pcr,
            "max_pain": max_pain,
            "max_pain_dev_pct": max_pain_dev_pct,
            "sr_levels": sr_levels,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        direction = self._determine_direction(pcr, max_pain_dev_pct)
        if direction is None:
            return None

        if direction == SignalDirection.LONG:
            stop = close - atr_val * self.params["atr_stop_mult"]
            # TP at nearest resistance S/R level above, or 2R
            risk = close - stop
            resistance = self._find_nearest_level(sr_levels, close, "above")
            tp1 = resistance if resistance else close + risk * self.params["tp_rr"]
            tp2 = close + risk * self.params["tp_rr"] * 1.5
            tp3 = max_pain if max_pain > close else close + risk * self.params["tp_rr"] * 2.0
        else:
            stop = close + atr_val * self.params["atr_stop_mult"]
            risk = stop - close
            support = self._find_nearest_level(sr_levels, close, "below")
            tp1 = support if support else close - risk * self.params["tp_rr"]
            tp2 = close - risk * self.params["tp_rr"] * 1.5
            tp3 = max_pain if max_pain < close else close - risk * self.params["tp_rr"] * 2.0

        confidence = self._compute_confidence(pcr, max_pain_dev_pct)

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=direction,
            strength=self._map_confidence_to_strength(confidence),
            entry_price=close,
            stop_loss=stop,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=self.params["base_size_pct"],
            timeframe=self.primary_timeframe,
            confidence=confidence,
            metadata={
                "pcr": pcr,
                "max_pain": max_pain,
                "max_pain_dev_pct": max_pain_dev_pct,
                "oi_sr_levels": sr_levels[:5],
            },
        )
        self.record_signal(signal)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})
        pcr = ind.get("pcr", 1.0)
        max_pain_dev = abs(ind.get("max_pain_dev_pct", 0.0))

        # PCR extreme
        pcr_signal = (
            pcr >= self.params["pcr_bullish_threshold"]
            or pcr <= self.params["pcr_bearish_threshold"]
        )
        # Max pain pull
        max_pain_signal = max_pain_dev >= self.params["max_pain_dev_threshold"]

        if not (pcr_signal or max_pain_signal):
            logger.debug(
                "[%s] PCR=%.2f, max_pain_dev=%.2f%% — no signal",
                self.name, pcr, max_pain_dev,
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
        pcr = ind.get("pcr", 1.0)

        if direction == "LONG":
            if close <= stop:
                return "stop_loss_hit"
            if close >= tp1:
                return "take_profit_1"
            if pcr < self.params["pcr_bearish_threshold"]:
                return "pcr_turned_bearish"
        else:
            if close >= stop:
                return "stop_loss_hit"
            if close <= tp1:
                return "take_profit_1"
            if pcr > self.params["pcr_bullish_threshold"]:
                return "pcr_turned_bullish"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "pcr_bullish_threshold": (float, 1.0, 5.0),
            "pcr_bearish_threshold": (float, 0.1, 0.9),
            "max_pain_dev_threshold": (float, 0.5, 10.0),
            "oi_sr_lookback": (int, 1, 20),
            "atr_period": (int, 5, 30),
            "atr_stop_mult": (float, 0.5, 3.0),
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

    def _determine_direction(self, pcr: float, max_pain_dev: float) -> Optional[SignalDirection]:
        score = 0.0
        if pcr >= self.params["pcr_bullish_threshold"]:
            score += 1.0
        if pcr <= self.params["pcr_bearish_threshold"]:
            score -= 1.0
        if max_pain_dev > self.params["max_pain_dev_threshold"]:
            score += 0.5  # Gravitate toward max pain (up)
        if max_pain_dev < -self.params["max_pain_dev_threshold"]:
            score -= 0.5

        if score > 0:
            return SignalDirection.LONG
        if score < 0:
            return SignalDirection.SHORT
        return None

    def _compute_confidence(self, pcr: float, max_pain_dev: float) -> float:
        score = 0.0
        if pcr >= self.params["pcr_bullish_threshold"]:
            score += min(1.0, (pcr - self.params["pcr_bullish_threshold"]) / 1.0 * 0.5 + 0.5)
        elif pcr <= self.params["pcr_bearish_threshold"]:
            score += min(1.0, (self.params["pcr_bearish_threshold"] - pcr) / 0.5 * 0.5 + 0.5)
        if abs(max_pain_dev) >= self.params["max_pain_dev_threshold"]:
            score = max(score, min(0.85, abs(max_pain_dev) / 5.0))
        return min(0.9, score)

    @staticmethod
    def _extract_oi_sr_levels(
        strikes: List[Dict[str, Any]], close: float, top_n: int
    ) -> List[float]:
        """Extract top N OI strikes as S/R levels."""
        if not strikes:
            return []
        # Sort by total OI (call + put)
        scored = []
        for s in strikes:
            strike = float(s.get("strike", 0))
            call_oi = float(s.get("call_oi", 0))
            put_oi = float(s.get("put_oi", 0))
            scored.append((strike, call_oi + put_oi))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [strike for strike, _ in scored[:top_n]]

    @staticmethod
    def _find_nearest_level(
        levels: List[float], price: float, direction: str
    ) -> Optional[float]:
        if not levels:
            return None
        if direction == "above":
            above = [l for l in levels if l > price]
            return min(above) if above else None
        else:
            below = [l for l in levels if l < price]
            return max(below) if below else None

    @staticmethod
    def _map_confidence_to_strength(confidence: float) -> SignalStrength:
        if confidence >= 0.75:
            return SignalStrength.STRONG
        if confidence >= 0.5:
            return SignalStrength.MODERATE
        return SignalStrength.SLIGHT

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
