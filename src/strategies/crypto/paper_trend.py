"""
Crypto Paper Trend Strategy for NEXUS ALPHA.

A lightweight paper-trading strategy designed to generate realistic trade
signals under normal market conditions — not just extreme breakouts.

Entry conditions (3 of 4 must pass):
    1. RSI(14) between 35 and 75              — trend momentum zone
    2. MACD histogram > 0 (positive momentum) — bullish bias
    3. Close within 3% of SMA(20)             — not in free-fall
    4. Fear & Greed > 15                      — not extreme panic

SHORT conditions (inverted):
    1. RSI(14) between 25 and 58
    2. MACD histogram < 0 (negative momentum)
    3. Close < SMA(20) * 1.02
    4. Fear & Greed < 70

Exit:
    - Stop loss: entry ± ATR * 2.0
    - TP1: ±1.5R, TP2: ±2.5R
    - Time exit: 48 hours

Purpose: paper-mode demo that exercises the full trade pipeline
(execution, DB persistence, fee + slippage) with realistic fills.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)


class CryptoPaperTrendStrategy(BaseStrategy):
    """
    Paper-trading trend-following strategy for crypto.

    Uses a relaxed 3-of-4 condition gate so signals fire regularly
    under normal market conditions, exercising the full paper pipeline.

    Default Parameters
    ------------------
    rsi_long_low  : float   35.0  RSI lower bound for LONG
    rsi_long_high : float   75.0  RSI upper bound for LONG
    rsi_short_low : float   25.0  RSI lower bound for SHORT
    rsi_short_high: float   58.0  RSI upper bound for SHORT
    sma_period    : int     20    SMA period for trend reference
    sma_tolerance : float   0.03  Close must be within this % of SMA
    atr_period    : int     14
    atr_stop_mult : float   2.0
    tp1_r         : float   1.5
    tp2_r         : float   2.5
    fear_greed_min: int     15    Minimum F&G for LONG
    fear_greed_max: int     70    Maximum F&G for SHORT
    time_exit_hours: int    48
    base_size_pct : float   0.02  2% of risk budget per trade
    min_conditions: int     3     Min conditions that must pass (of 4)
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "rsi_long_low":    35.0,
        "rsi_long_high":   75.0,
        "rsi_short_low":   25.0,
        "rsi_short_high":  58.0,
        "sma_period":      20,
        "sma_tolerance":   0.03,   # 3% — within this range of SMA20
        "atr_period":      14,
        "atr_stop_mult":   2.0,
        "tp1_r":           1.5,
        "tp2_r":           2.5,
        "fear_greed_min":  15,
        "fear_greed_max":  70,
        "time_exit_hours": 48,
        "base_size_pct":   0.02,
        "min_conditions":  3,      # fire on 3-of-4 conditions
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="CryptoPaperTrend",
            market="crypto",
            primary_timeframe="4h",
            confirmation_timeframe="1h",
            params=merged,
        )

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    def generate_signal(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[TradeSignal]:
        if not self._is_enabled:
            return None

        symbol: str = market_data.get("symbol", "UNKNOWN")
        ohlcv = market_data.get("ohlcv")
        sentiment: Dict[str, Any] = market_data.get("sentiment", {})

        if ohlcv is None:
            return None

        try:
            closes  = self._to_array(ohlcv, "close")
            highs   = self._to_array(ohlcv, "high")
            lows    = self._to_array(ohlcv, "low")
        except (KeyError, TypeError) as exc:
            logger.error("[%s] OHLCV extraction failed: %s", self.name, exc)
            return None

        min_bars = max(self.params["sma_period"] + 5, 30)
        if len(closes) < min_bars:
            logger.debug("[%s] Insufficient bars for %s (%d < %d)",
                         self.name, symbol, len(closes), min_bars)
            return None

        # Compute indicators
        rsi        = self.compute_rsi(closes, 14)
        _, _, macd_hist = self.compute_macd(closes)
        sma        = self.compute_sma(closes, self.params["sma_period"])
        atr        = self.compute_atr(highs, lows, closes, self.params["atr_period"])

        rsi_val   = float(rsi[-1])       if not np.isnan(rsi[-1])       else 50.0
        hist_val  = float(macd_hist[-1]) if not np.isnan(macd_hist[-1]) else 0.0
        sma_val   = float(sma[-1])       if not np.isnan(sma[-1])       else closes[-1]
        atr_val   = float(atr[-1])       if not np.isnan(atr[-1])       else closes[-1] * 0.01
        close_val = float(closes[-1])
        fg        = sentiment.get("fear_greed", 50)

        ind = {
            "rsi": rsi_val, "macd_hist": hist_val, "sma": sma_val,
            "atr": atr_val, "close": close_val, "fear_greed": fg,
        }

        # Try LONG first, then SHORT
        direction = self._detect_direction(ind)
        if direction is None:
            return None

        entry_price = close_val
        if direction == SignalDirection.LONG:
            stop_loss = entry_price - atr_val * self.params["atr_stop_mult"]
            risk      = entry_price - stop_loss
            tp1       = entry_price + risk * self.params["tp1_r"]
            tp2       = entry_price + risk * self.params["tp2_r"]
            tp3       = entry_price + risk * 3.5
        else:
            stop_loss = entry_price + atr_val * self.params["atr_stop_mult"]
            risk      = stop_loss - entry_price
            tp1       = entry_price - risk * self.params["tp1_r"]
            tp2       = entry_price - risk * self.params["tp2_r"]
            tp3       = entry_price - risk * 3.5

        # confidence: fraction of 4 conditions that passed
        conditions_passed = self._count_conditions(ind, direction)
        confidence = conditions_passed / 4.0

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=direction,
            strength=self._map_strength(confidence),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=self.params["base_size_pct"],
            timeframe=self.primary_timeframe,
            confidence=confidence,
            metadata={
                "rsi": rsi_val,
                "macd_hist": hist_val,
                "sma": sma_val,
                "atr": atr_val,
                "fear_greed": fg,
                "conditions_passed": conditions_passed,
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] %s signal on %s: entry=%.2f sl=%.2f tp1=%.2f "
            "rsi=%.1f macd=%.2f conds=%d/4",
            self.name, direction.value, symbol,
            entry_price, stop_loss, tp1, rsi_val, hist_val, conditions_passed,
        )
        return signal

    def _detect_direction(self, ind: Dict[str, Any]) -> Optional[SignalDirection]:
        """Return LONG, SHORT, or None based on min_conditions gate."""
        # LONG takes priority if conditions met
        long_count = self._count_conditions(ind, SignalDirection.LONG)
        if long_count >= self.params["min_conditions"]:
            return SignalDirection.LONG

        short_count = self._count_conditions(ind, SignalDirection.SHORT)
        if short_count >= self.params["min_conditions"]:
            return SignalDirection.SHORT

        logger.debug(
            "[%s] No signal: LONG=%d/4 SHORT=%d/4 (need %d)",
            self.name, long_count, short_count, self.params["min_conditions"],
        )
        return None

    def _count_conditions(
        self, ind: Dict[str, Any], direction: SignalDirection
    ) -> int:
        rsi   = ind["rsi"]
        hist  = ind["macd_hist"]
        sma   = ind["sma"]
        close = ind["close"]
        fg    = ind["fear_greed"]
        tol   = self.params["sma_tolerance"]

        if direction == SignalDirection.LONG:
            c1 = self.params["rsi_long_low"]  <= rsi  <= self.params["rsi_long_high"]
            c2 = hist > 0
            c3 = close >= sma * (1 - tol)   # within tol% below SMA20 (or above)
            c4 = fg   > self.params["fear_greed_min"]
        else:  # SHORT
            c1 = self.params["rsi_short_low"] <= rsi  <= self.params["rsi_short_high"]
            c2 = hist < 0
            c3 = close <= sma * (1 + tol)   # within tol% above SMA20 (or below)
            c4 = fg   < self.params["fear_greed_max"]

        count = sum([c1, c2, c3, c4])
        logger.debug(
            "[%s] %s conditions: RSI=%s MACD=%s SMA=%s FG=%s → %d/4",
            self.name, direction.value, c1, c2, c3, c4, count,
        )
        return count

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})
        return (
            self._count_conditions(ind, SignalDirection.LONG)  >= self.params["min_conditions"]
            or
            self._count_conditions(ind, SignalDirection.SHORT) >= self.params["min_conditions"]
        )

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind   = market_data.get("indicators", {})
        close = ind.get("close", position.get("entry_price", 0.0))
        atr   = ind.get("atr",   0.0)

        entry     = position.get("entry_price", 0.0)
        stop_loss = position.get("stop_loss",   entry - atr * self.params["atr_stop_mult"])
        tp1       = position.get("take_profit_1", entry + atr * self.params["tp1_r"])
        tp2       = position.get("take_profit_2", entry + atr * self.params["tp2_r"])

        direction = position.get("direction", "LONG")
        is_long   = direction in ("LONG", "long", "buy")

        if is_long:
            if close <= stop_loss:               return "stop_loss_hit"
            if not position.get("tp1_filled") and close >= tp1: return "take_profit_1"
            if position.get("tp1_filled") and close >= tp2:     return "take_profit_2"
        else:
            if close >= stop_loss:               return "stop_loss_hit"
            if not position.get("tp1_filled") and close <= tp1: return "take_profit_1"
            if position.get("tp1_filled") and close <= tp2:     return "take_profit_2"

        entry_time = position.get("entry_time")
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            if datetime.utcnow() - entry_time > timedelta(hours=self.params["time_exit_hours"]):
                return "time_exit_48h"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        return True   # permissive for paper mode

    def backtest_metric(self) -> Dict[str, Any]:
        return self._build_backtest_metric_from_history().to_dict()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")

    @staticmethod
    def _map_strength(confidence: float) -> SignalStrength:
        if confidence >= 0.9:
            return SignalStrength.STRONG
        if confidence >= 0.7:
            return SignalStrength.MODERATE
        return SignalStrength.SLIGHT
