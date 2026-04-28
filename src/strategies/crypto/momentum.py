"""
Crypto Momentum Strategy for NEXUS ALPHA.

Entry conditions (all must pass):
    1. Price breaks above 20-period Donchian high on 4h chart
    2. Volume > 1.5x 20-period average
    3. RSI(14) between 50 and 70
    4. MACD histogram expanding (current > previous)
    5. Daily trend = UP (SMA50 > SMA200)
    6. Fear & Greed index > 30
    7. LLM consensus >= SLIGHT_BUY

Exit:
    - Stop loss: entry - ATR * 2.5
    - TP1: +1R, TP2: +2R, TP3: trailing ATR
    - Time exit: 72 hours after entry

Risk adjustment:
    - If funding rate > 0.05%, reduce position size by half.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    BacktestMetric,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

# LLM consensus levels that qualify as at least SLIGHT_BUY
_BULLISH_CONSENSUS = {"SLIGHT_BUY", "MODERATE_BUY", "STRONG_BUY"}


class CryptoMomentumStrategy(BaseStrategy):
    """
    Momentum breakout strategy for cryptocurrency perpetual futures.

    Default Parameters
    ------------------
    donchian_period : int       20-period Donchian channel
    volume_multiplier : float   Volume must exceed this multiple of avg
    rsi_low : float             RSI lower bound (inclusive)
    rsi_high : float            RSI upper bound (inclusive)
    atr_period : int            ATR period for stop calculation
    atr_stop_mult : float       Stop = entry - ATR * multiplier
    tp1_r : float               TP1 in R multiples (1R)
    tp2_r : float               TP2 in R multiples (2R)
    sma_fast : int              Fast SMA for trend (50)
    sma_slow : int              Slow SMA for trend (200)
    fear_greed_min : int        Minimum Fear & Greed to allow entry (>30)
    max_funding_rate : float    Reduce size if funding rate exceeds this
    time_exit_hours : int       Force exit after N hours (72)
    base_size_pct : float       Base position size as fraction of risk budget
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "donchian_period": 20,
        "volume_multiplier": 1.5,
        "rsi_low": 50.0,
        "rsi_high": 70.0,
        "atr_period": 14,
        "atr_stop_mult": 2.5,
        "tp1_r": 1.0,
        "tp2_r": 2.0,
        "sma_fast": 50,
        "sma_slow": 200,
        "fear_greed_min": 30,
        "max_funding_rate": 0.05,  # percent
        "time_exit_hours": 72,
        "base_size_pct": 0.02,    # 2% of account per trade
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="CryptoMomentum",
            market="crypto",
            primary_timeframe="4h",
            confirmation_timeframe="1d",
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
        indicators: Dict[str, Any] = market_data.get("indicators", {})
        sentiment: Dict[str, Any] = market_data.get("sentiment", {})
        ohlcv = market_data.get("ohlcv")  # expected: pd.DataFrame or dict of arrays

        if ohlcv is None:
            logger.warning("[%s] Missing OHLCV data for %s", self.name, symbol)
            return None

        # Extract arrays — support both DataFrame and dict of lists/arrays
        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
            volumes = self._to_array(ohlcv, "volume")
        except (KeyError, TypeError) as exc:
            logger.error("[%s] OHLCV extraction failed: %s", self.name, exc)
            return None

        if len(closes) < self.params["sma_slow"] + 10:
            logger.debug("[%s] Insufficient history for %s", self.name, symbol)
            return None

        # Pre-compute indicators
        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        rsi = self.compute_rsi(closes, 14)
        _, _, macd_hist = self.compute_macd(closes)
        dc_upper, _ = self.compute_donchian(highs, lows, self.params["donchian_period"])
        sma_fast = self.compute_sma(closes, self.params["sma_fast"])
        sma_slow = self.compute_sma(closes, self.params["sma_slow"])

        # Attach computed values to indicators dict for check_entry_conditions
        indicators["rsi"] = float(rsi[-1]) if not np.isnan(rsi[-1]) else 0.0
        indicators["macd_hist"] = float(macd_hist[-1]) if not np.isnan(macd_hist[-1]) else 0.0
        indicators["macd_hist_prev"] = float(macd_hist[-2]) if not np.isnan(macd_hist[-2]) else 0.0
        indicators["dc_upper"] = float(dc_upper[-1]) if not np.isnan(dc_upper[-1]) else 0.0
        indicators["sma_fast"] = float(sma_fast[-1]) if not np.isnan(sma_fast[-1]) else 0.0
        indicators["sma_slow"] = float(sma_slow[-1]) if not np.isnan(sma_slow[-1]) else 0.0
        indicators["atr"] = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0
        indicators["volume_avg"] = float(np.mean(volumes[-self.params["donchian_period"]:])) if len(volumes) >= self.params["donchian_period"] else 0.0
        indicators["volume_current"] = float(volumes[-1])
        indicators["close"] = float(closes[-1])
        indicators["high"] = float(highs[-1])

        # Attach sentiment
        indicators["fear_greed"] = sentiment.get("fear_greed", 50)
        indicators["llm_consensus"] = sentiment.get("llm_consensus", "HOLD")
        indicators["funding_rate"] = sentiment.get("funding_rate", 0.0)

        full_data = {**market_data, "indicators": indicators}

        if not self.check_entry_conditions(full_data):
            return None

        # Build signal
        entry_price = indicators["close"]
        atr_val = indicators["atr"]
        stop_loss = entry_price - atr_val * self.params["atr_stop_mult"]
        risk = entry_price - stop_loss
        tp1 = entry_price + risk * self.params["tp1_r"]
        tp2 = entry_price + risk * self.params["tp2_r"]
        tp3 = entry_price + risk * 3.5  # trailing target

        # Adjust size for high funding rate
        size_pct = self.params["base_size_pct"]
        funding_rate = indicators["funding_rate"]
        if funding_rate > self.params["max_funding_rate"]:
            size_pct *= 0.5
            logger.info(
                "[%s] Funding rate %.4f%% > threshold; halving position size",
                self.name, funding_rate,
            )

        # Confidence: more conditions above threshold → higher confidence
        confidence = self._compute_confidence(indicators)

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=SignalDirection.LONG,
            strength=self._map_strength(confidence),
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=size_pct,
            timeframe=self.primary_timeframe,
            confidence=confidence,
            metadata={
                "rsi": indicators["rsi"],
                "atr": atr_val,
                "funding_rate": funding_rate,
                "fear_greed": indicators["fear_greed"],
                "llm_consensus": indicators["llm_consensus"],
                "donchian_upper": indicators["dc_upper"],
            },
        )
        self.record_signal(signal)
        logger.info("[%s] Signal generated for %s: %s", self.name, symbol, signal.direction)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})
        passed = True

        # 1. Donchian breakout — current close > 20-period high
        close = ind.get("close", 0.0)
        dc_upper = ind.get("dc_upper", float("inf"))
        if close <= dc_upper:
            logger.debug("[%s] Cond1 FAIL: close %.4f not above Donchian %.4f", self.name, close, dc_upper)
            passed = False

        # 2. Volume > 1.5x average
        vol_current = ind.get("volume_current", 0.0)
        vol_avg = ind.get("volume_avg", 1.0)
        if vol_avg > 0 and vol_current < self.params["volume_multiplier"] * vol_avg:
            logger.debug("[%s] Cond2 FAIL: volume %.2f < %.2f * avg", self.name, vol_current, self.params["volume_multiplier"])
            passed = False

        # 3. RSI between 50 and 70
        rsi = ind.get("rsi", 0.0)
        if not (self.params["rsi_low"] <= rsi <= self.params["rsi_high"]):
            logger.debug("[%s] Cond3 FAIL: RSI %.2f not in [%.0f, %.0f]", self.name, rsi, self.params["rsi_low"], self.params["rsi_high"])
            passed = False

        # 4. MACD histogram expanding
        hist = ind.get("macd_hist", 0.0)
        hist_prev = ind.get("macd_hist_prev", 0.0)
        if hist <= hist_prev:
            logger.debug("[%s] Cond4 FAIL: MACD hist not expanding %.5f <= %.5f", self.name, hist, hist_prev)
            passed = False

        # 5. Daily trend: SMA50 > SMA200
        sma_fast = ind.get("sma_fast", 0.0)
        sma_slow = ind.get("sma_slow", 0.0)
        if sma_fast <= sma_slow:
            logger.debug("[%s] Cond5 FAIL: SMA50 %.4f not above SMA200 %.4f", self.name, sma_fast, sma_slow)
            passed = False

        # 6. Fear & Greed > 30
        fg = ind.get("fear_greed", 0)
        if fg <= self.params["fear_greed_min"]:
            logger.debug("[%s] Cond6 FAIL: Fear & Greed %d <= %d", self.name, fg, self.params["fear_greed_min"])
            passed = False

        # 7. LLM consensus >= SLIGHT_BUY
        consensus = ind.get("llm_consensus", "HOLD")
        if consensus not in _BULLISH_CONSENSUS:
            logger.debug("[%s] Cond7 FAIL: LLM consensus %s not bullish", self.name, consensus)
            passed = False

        return passed

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        close = ind.get("close", position.get("entry_price", 0.0))
        atr = ind.get("atr", 0.0)

        entry = position.get("entry_price", 0.0)
        stop_loss = position.get("stop_loss", entry - atr * self.params["atr_stop_mult"])
        tp1 = position.get("take_profit_1", entry + atr * self.params["atr_stop_mult"] * self.params["tp1_r"])
        tp2 = position.get("take_profit_2", entry + atr * self.params["atr_stop_mult"] * self.params["tp2_r"])

        # Stop loss
        if close <= stop_loss:
            return "stop_loss_hit"

        # TP1
        if position.get("tp1_filled") is not True and close >= tp1:
            return "take_profit_1"

        # TP2
        if position.get("tp1_filled") is True and close >= tp2:
            return "take_profit_2"

        # Trailing TP3: use ATR trail
        if position.get("tp2_filled") is True:
            trailing_stop = close - atr * 1.5
            highest = position.get("highest_price", close)
            trail_at = highest - atr * 1.5
            if close <= trail_at:
                return "take_profit_3_trailing"

        # Time exit
        entry_time = position.get("entry_time")
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            elapsed = datetime.utcnow() - entry_time
            if elapsed > timedelta(hours=self.params["time_exit_hours"]):
                return "time_exit_72h"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        required = {
            "donchian_period": (int, 5, 200),
            "volume_multiplier": (float, 1.0, 5.0),
            "rsi_low": (float, 30.0, 60.0),
            "rsi_high": (float, 55.0, 85.0),
            "atr_period": (int, 5, 50),
            "atr_stop_mult": (float, 1.0, 5.0),
            "tp1_r": (float, 0.5, 3.0),
            "tp2_r": (float, 1.0, 6.0),
            "sma_fast": (int, 10, 100),
            "sma_slow": (int, 50, 500),
            "fear_greed_min": (int, 0, 50),
            "max_funding_rate": (float, 0.0, 0.5),
            "time_exit_hours": (int, 1, 168),
            "base_size_pct": (float, 0.001, 0.1),
        }
        for key, (typ, lo, hi) in required.items():
            val = params.get(key)
            if val is None:
                continue  # Missing keys keep defaults
            try:
                val = typ(val)
            except (TypeError, ValueError):
                logger.error("[%s] Param %s must be %s", self.name, key, typ)
                return False
            if not (lo <= val <= hi):
                logger.error("[%s] Param %s=%s out of range [%s, %s]", self.name, key, val, lo, hi)
                return False
        if "rsi_low" in params and "rsi_high" in params:
            if params["rsi_low"] >= params["rsi_high"]:
                logger.error("[%s] rsi_low must be < rsi_high", self.name)
                return False
        if "sma_fast" in params and "sma_slow" in params:
            if params["sma_fast"] >= params["sma_slow"]:
                logger.error("[%s] sma_fast must be < sma_slow", self.name)
                return False
        return True

    def backtest_metric(self) -> Dict[str, Any]:
        return self._build_backtest_metric_from_history().to_dict()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        """Convert OHLCV source (DataFrame or dict) to numpy array."""
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract column '{col}' from {type(ohlcv)}")

    def _compute_confidence(self, ind: Dict[str, Any]) -> float:
        """Score 0–1 based on how strongly all conditions pass."""
        score = 0.0
        total = 7.0

        # 1. Donchian breakout margin
        close = ind.get("close", 0.0)
        dc = ind.get("dc_upper", close)
        if close > dc:
            score += min(1.0, (close - dc) / max(dc * 0.01, 1e-9))

        # 2. Volume ratio
        vol_ratio = ind.get("volume_current", 0) / max(ind.get("volume_avg", 1), 1e-9)
        score += min(1.0, (vol_ratio - 1.5) / 1.5 + 0.5) if vol_ratio >= 1.5 else 0.0

        # 3. RSI centrality
        rsi = ind.get("rsi", 0.0)
        if 50 <= rsi <= 70:
            score += 1.0 - abs(rsi - 60) / 10.0

        # 4. MACD expansion magnitude
        hist_diff = ind.get("macd_hist", 0) - ind.get("macd_hist_prev", 0)
        score += min(1.0, abs(hist_diff) * 100) if hist_diff > 0 else 0.0

        # 5. Trend strength
        sf = ind.get("sma_fast", 0.0)
        ss = ind.get("sma_slow", 1.0)
        score += min(1.0, (sf - ss) / max(ss * 0.05, 1e-9)) if sf > ss else 0.0

        # 6. Fear & Greed
        fg = ind.get("fear_greed", 0)
        score += min(1.0, (fg - 30) / 70) if fg > 30 else 0.0

        # 7. LLM consensus weight
        consensus_map = {
            "STRONG_BUY": 1.0, "MODERATE_BUY": 0.75,
            "SLIGHT_BUY": 0.5, "HOLD": 0.0,
        }
        score += consensus_map.get(ind.get("llm_consensus", "HOLD"), 0.0)

        return min(1.0, score / total)

    @staticmethod
    def _map_strength(confidence: float) -> SignalStrength:
        if confidence >= 0.75:
            return SignalStrength.STRONG
        if confidence >= 0.5:
            return SignalStrength.MODERATE
        return SignalStrength.SLIGHT
