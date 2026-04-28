"""
Forex Carry Trade Strategy for NEXUS ALPHA.

Mechanics:
    - Go long high-yield currency / short low-yield currency
    - Interest rate differentials sourced from config (update monthly)
    - Entry: rate differential > 2% AND momentum alignment on 4h
    - Preferred pairs: AUD/JPY, NZD/JPY in risk-on environments
    - Exit: VIX spike (risk-off) OR rate differential narrows

Rate differential data must be supplied in market_data['rate_differential']
or loaded from config.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

# Pairs where base currency is the high-yield currency
HIGH_YIELD_PAIRS = {"AUD/JPY", "NZD/JPY", "AUD/CHF", "NZD/CHF", "AUD/USD"}

# VIX thresholds for risk environment detection
VIX_RISK_OFF = 25.0
VIX_HIGH_ALERT = 30.0


class CarryTradeStrategy(BaseStrategy):
    """
    Interest rate carry trade strategy for Forex markets.

    Default Parameters
    ------------------
    min_rate_differential : float  2.0   % annualised
    exit_rate_differential : float 1.0   % — exit when differential narrows to this
    vix_risk_off_threshold : float 25.0  — exit all carry on VIX spike
    momentum_ema_period : int      20    — EMA for trend alignment
    atr_period : int               14
    atr_stop_mult : float          2.0
    tp_rr : float                  3.0
    base_size_pct : float          0.01
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "min_rate_differential": 2.0,
        "exit_rate_differential": 1.0,
        "vix_risk_off_threshold": 25.0,
        "momentum_ema_period": 20,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "tp_rr": 3.0,
        "base_size_pct": 0.01,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="CarryTrade",
            market="forex",
            primary_timeframe="4h",
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

        if len(closes) < self.params["momentum_ema_period"] + 5:
            return None

        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        ema = self.compute_ema(closes, self.params["momentum_ema_period"])

        # Rate differential from market data or config
        rate_differential: float = float(
            market_data.get("rate_differential", 0.0)
        )

        # Risk environment
        sentiment = market_data.get("sentiment", {})
        vix: float = float(sentiment.get("vix", 15.0))
        risk_on: bool = vix < self.params["vix_risk_off_threshold"]

        # Momentum alignment
        close = float(closes[-1])
        ema_val = float(ema[-1]) if not np.isnan(ema[-1]) else close
        momentum_long = close > ema_val  # trending up
        momentum_short = close < ema_val

        # Determine direction based on pair type
        is_high_yield_base = symbol.upper() in HIGH_YIELD_PAIRS
        if is_high_yield_base:
            # Long high-yield base (e.g. AUD/JPY long = buy AUD sell JPY)
            direction = SignalDirection.LONG if (momentum_long and risk_on) else None
        else:
            direction = SignalDirection.SHORT if (momentum_short and not risk_on) else None

        indicators = {
            "close": close,
            "atr": float(atr[-1]) if not np.isnan(atr[-1]) else 0.0,
            "ema": ema_val,
            "rate_differential": rate_differential,
            "vix": vix,
            "risk_on": risk_on,
            "direction": direction,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        direction = indicators["direction"]
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
            confidence=min(0.9, rate_differential / 5.0),
            metadata={
                "rate_differential": rate_differential,
                "vix": vix,
                "risk_on": risk_on,
                "carry_per_day_approx": rate_differential / 365,
            },
        )
        self.record_signal(signal)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        # Require minimum rate differential
        diff = ind.get("rate_differential", 0.0)
        if diff < self.params["min_rate_differential"]:
            logger.debug("[%s] Rate diff %.2f%% < %.2f%%", self.name, diff, self.params["min_rate_differential"])
            return False

        # Risk-on environment required for high-yield longs
        if not ind.get("risk_on", True):
            logger.debug("[%s] Risk-off environment (VIX=%.1f)", self.name, ind.get("vix", 0))
            return False

        # Direction must be set
        if ind.get("direction") is None:
            logger.debug("[%s] No directional alignment", self.name)
            return False

        return True

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        sentiment = market_data.get("sentiment", {})
        close = ind.get("close", position.get("entry_price", 0.0))
        stop = position.get("stop_loss", 0.0)
        tp1 = position.get("take_profit_1", float("inf"))
        direction = position.get("direction", "LONG")

        # Stop loss
        if direction == "LONG" and close <= stop:
            return "stop_loss_hit"
        if direction == "SHORT" and close >= stop:
            return "stop_loss_hit"

        # VIX risk-off spike
        vix = float(sentiment.get("vix", 15.0))
        if vix >= self.params["vix_risk_off_threshold"]:
            return "risk_off_vix_spike"

        # Rate differential narrowed
        current_diff = float(market_data.get("rate_differential", 99.0))
        if current_diff < self.params["exit_rate_differential"]:
            return "rate_differential_narrowed"

        # TP
        if direction == "LONG" and close >= tp1:
            return "take_profit_1"
        if direction == "SHORT" and close <= tp1:
            return "take_profit_1"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "min_rate_differential": (float, 0.1, 10.0),
            "exit_rate_differential": (float, 0.0, 5.0),
            "vix_risk_off_threshold": (float, 15.0, 50.0),
            "momentum_ema_period": (int, 5, 100),
            "atr_period": (int, 5, 50),
            "atr_stop_mult": (float, 0.5, 5.0),
            "tp_rr": (float, 1.0, 6.0),
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

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
