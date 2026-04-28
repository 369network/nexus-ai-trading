"""
FII/DII Flow Strategy for NEXUS ALPHA.

Uses institutional flow data from NSE/BSE to generate directional signals
for Nifty50 / BankNifty index trades.

Thresholds:
    - FII net buy > INR 2000 Cr: bullish
    - FII net sell > INR 2000 Cr: bearish
    - DII buying when FII selling: support signal (reduces downside)

Flow data is updated daily after market hours (post 4:00 PM IST).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)
FLOW_THRESHOLD_CR = 2000.0  # INR Crores
DII_SUPPORT_THRESHOLD_CR = 500.0


def utc_to_ist(utc_dt: datetime) -> datetime:
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(
        timezone(IST_OFFSET)
    ).replace(tzinfo=None)


class FIIDIIFlowStrategy(BaseStrategy):
    """
    Institutional flow-based trend strategy for Indian equity indices.

    Generates signals based on net FII and DII buying/selling.
    Best used as a filter/overlay for other index strategies.

    Default Parameters
    ------------------
    fii_threshold_cr : float    2000.0  — threshold in INR Crores
    dii_support_threshold_cr : float  500.0
    lookback_days : int         5       — average flow over N days
    atr_period : int            14
    atr_stop_mult : float       2.0
    tp_rr : float               2.5
    base_size_pct : float       0.015
    use_3day_avg : bool         True    — use 3-day flow average for stability
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "fii_threshold_cr": FLOW_THRESHOLD_CR,
        "dii_support_threshold_cr": DII_SUPPORT_THRESHOLD_CR,
        "lookback_days": 5,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "tp_rr": 2.5,
        "base_size_pct": 0.015,
        "use_3day_avg": True,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="FIIDIIFlow",
            market="indian",
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

        symbol: str = market_data.get("symbol", "NIFTY50")
        ohlcv = market_data.get("ohlcv")
        flow_data: Dict[str, Any] = market_data.get("flow_data", {})

        if ohlcv is None or not flow_data:
            logger.debug("[%s] Missing OHLCV or flow data", self.name)
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        if len(closes) < self.params["atr_period"] + 5:
            return None

        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        close = float(closes[-1])
        atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

        # Flow analysis
        fii_flow = self._compute_avg_flow(flow_data, "fii", self.params["lookback_days"])
        dii_flow = self._compute_avg_flow(flow_data, "dii", self.params["lookback_days"])

        indicators = {
            "close": close,
            "atr": atr_val,
            "fii_flow": fii_flow,
            "dii_flow": dii_flow,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        # Direction from FII flow
        direction = self._determine_direction(fii_flow, dii_flow)
        if direction is None:
            return None

        if direction == SignalDirection.LONG:
            stop = close - atr_val * self.params["atr_stop_mult"]
            risk = close - stop
        else:
            stop = close + atr_val * self.params["atr_stop_mult"]
            risk = stop - close

        tp1 = close + risk * self.params["tp_rr"] if direction == SignalDirection.LONG else close - risk * self.params["tp_rr"]
        tp2 = close + risk * self.params["tp_rr"] * 1.5 if direction == SignalDirection.LONG else close - risk * self.params["tp_rr"] * 1.5
        tp3 = close + risk * self.params["tp_rr"] * 2.0 if direction == SignalDirection.LONG else close - risk * self.params["tp_rr"] * 2.0

        # Reduce confidence if DII counterflow
        confidence = 0.7
        if fii_flow > 0 and dii_flow < 0:
            confidence = 0.55  # DII selling against FII buying
        elif fii_flow < 0 and dii_flow > self.params["dii_support_threshold_cr"]:
            confidence = 0.55  # DII supporting but FII still selling

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
                "fii_avg_flow_cr": fii_flow,
                "dii_avg_flow_cr": dii_flow,
                "net_flow_cr": fii_flow + dii_flow,
                "lookback_days": self.params["lookback_days"],
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] %s | FII=%.0fCr DII=%.0fCr | confidence=%.2f",
            self.name, direction.value, fii_flow, dii_flow, confidence,
        )
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})
        fii_flow = ind.get("fii_flow", 0.0)
        threshold = self.params["fii_threshold_cr"]

        if abs(fii_flow) < threshold:
            logger.debug(
                "[%s] FII flow %.0fCr < threshold %.0fCr",
                self.name, fii_flow, threshold,
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
        fii_flow = ind.get("fii_flow", 0.0)
        threshold = self.params["fii_threshold_cr"]

        if direction == "LONG":
            if close <= stop:
                return "stop_loss_hit"
            if close >= tp1:
                return "take_profit_1"
            # FII turns bearish
            if fii_flow < -threshold:
                return "fii_flow_reversal"
        else:
            if close >= stop:
                return "stop_loss_hit"
            if close <= tp1:
                return "take_profit_1"
            if fii_flow > threshold:
                return "fii_flow_reversal"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "fii_threshold_cr": (float, 100.0, 20000.0),
            "dii_support_threshold_cr": (float, 50.0, 5000.0),
            "lookback_days": (int, 1, 30),
            "atr_period": (int, 5, 30),
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

    # ------------------------------------------------------------------

    @staticmethod
    def _compute_avg_flow(
        flow_data: Dict[str, Any], entity: str, lookback: int
    ) -> float:
        """
        Compute average net flow in INR Crores over lookback days.
        flow_data expected: {'fii': [day1, day2, ...], 'dii': [...]}
        where each day value is net buy (positive) or sell (negative) in Cr.
        """
        history = flow_data.get(entity, [])
        if not history:
            return 0.0
        recent = history[-lookback:] if len(history) >= lookback else history
        return float(np.mean(recent))

    def _determine_direction(
        self, fii_flow: float, dii_flow: float
    ) -> Optional[SignalDirection]:
        threshold = self.params["fii_threshold_cr"]
        dii_threshold = self.params["dii_support_threshold_cr"]

        if fii_flow > threshold:
            return SignalDirection.LONG
        if fii_flow < -threshold:
            # Check DII support
            if dii_flow > dii_threshold:
                logger.info(
                    "[%s] FII selling but DII buying; signal weakened → hold",
                    self.name,
                )
                return None  # Mixed signal; no trade
            return SignalDirection.SHORT
        return None

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
