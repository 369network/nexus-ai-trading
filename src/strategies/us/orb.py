"""
Opening Range Breakout (ORB) Strategy for NEXUS ALPHA.

US Equity market strategy.

Logic:
    - 09:30-09:45 ET: establish opening range (high/low of first 15 minutes)
    - Breakout above range high → BUY (stop at range low)
    - Breakout below range low → SELL SHORT (stop at range high)
    - Volume must be > 1.5x average volume
    - Only trade in first 2 hours (09:30-11:30 ET)
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

ET_OFFSET = timedelta(hours=-4)  # EDT (summer); use -5 for EST
ORB_START_ET = time(9, 30)
ORB_END_ET = time(9, 45)
TRADE_WINDOW_END_ET = time(11, 30)


def utc_to_et(utc_dt: datetime) -> datetime:
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(
        timezone(ET_OFFSET)
    ).replace(tzinfo=None)


class OpeningRangeBreakoutStrategy(BaseStrategy):
    """
    ORB strategy for US equities and ETFs.

    Default Parameters
    ------------------
    orb_minutes : int           15    — opening range duration
    volume_multiplier : float   1.5
    atr_period : int            14
    tp_rr : float               2.0
    base_size_pct : float       0.02
    retest_entry : bool         True  — enter on retest of range boundary
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "orb_minutes": 15,
        "volume_multiplier": 1.5,
        "atr_period": 14,
        "tp_rr": 2.0,
        "base_size_pct": 0.02,
        "retest_entry": True,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="OpeningRangeBreakout",
            market="us",
            primary_timeframe="5m",
            confirmation_timeframe="1h",
            params=merged,
        )
        # Session state — reset each trading day
        self._orb_high: Optional[float] = None
        self._orb_low: Optional[float] = None
        self._orb_set: bool = False
        self._orb_date: Optional[str] = None

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
            volumes = self._to_array(ohlcv, "volume")
            opens = self._to_array(ohlcv, "open")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        if len(closes) < 3:
            return None

        now_et = utc_to_et(datetime.utcnow())
        et_time = now_et.time()
        today_str = now_et.strftime("%Y-%m-%d")

        # Reset ORB on new day
        if self._orb_date != today_str:
            self._orb_high = None
            self._orb_low = None
            self._orb_set = False
            self._orb_date = today_str

        # Within trade window
        if et_time < ORB_START_ET or et_time > TRADE_WINDOW_END_ET:
            logger.debug("[%s] Outside trade window: %s", self.name, et_time)
            return None

        # Set ORB during first 15 minutes
        if et_time <= ORB_END_ET:
            # Use all bars from 9:30 to now
            orb_bars = market_data.get("orb_bars", {})
            orb_highs = self._to_array(orb_bars, "high") if orb_bars else highs[-3:]
            orb_lows = self._to_array(orb_bars, "low") if orb_bars else lows[-3:]
            self._orb_high = float(np.max(orb_highs))
            self._orb_low = float(np.min(orb_lows))
            logger.debug(
                "[%s] ORB set: high=%.4f low=%.4f", self.name, self._orb_high, self._orb_low
            )
            return None  # No trade during range formation

        if self._orb_high is None or self._orb_low is None:
            # Try to extract from passed data
            orb_info = market_data.get("opening_range", {})
            self._orb_high = float(orb_info.get("high", 0.0))
            self._orb_low = float(orb_info.get("low", 0.0))
            if self._orb_high <= 0 or self._orb_low <= 0:
                logger.debug("[%s] ORB not set yet", self.name)
                return None

        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        close = float(closes[-1])
        atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

        vol_current = float(volumes[-1])
        vol_avg = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else vol_current

        indicators = {
            "close": close,
            "atr": atr_val,
            "orb_high": self._orb_high,
            "orb_low": self._orb_low,
            "volume_current": vol_current,
            "volume_avg": vol_avg,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        # Direction
        breakout_up = close > self._orb_high
        direction = SignalDirection.LONG if breakout_up else SignalDirection.SHORT

        if direction == SignalDirection.LONG:
            entry = close
            stop = self._orb_low
            risk = entry - stop
            tp1 = entry + risk * self.params["tp_rr"]
            tp2 = entry + risk * self.params["tp_rr"] * 1.5
            tp3 = entry + risk * self.params["tp_rr"] * 2.0
        else:
            entry = close
            stop = self._orb_high
            risk = stop - entry
            tp1 = entry - risk * self.params["tp_rr"]
            tp2 = entry - risk * self.params["tp_rr"] * 1.5
            tp3 = entry - risk * self.params["tp_rr"] * 2.0

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
            confidence=0.65,
            metadata={
                "orb_high": self._orb_high,
                "orb_low": self._orb_low,
                "orb_range": self._orb_high - self._orb_low,
                "atr": atr_val,
                "volume_ratio": vol_current / max(vol_avg, 1),
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] ORB %s | high=%.4f low=%.4f | entry=%.4f",
            self.name, direction.value, self._orb_high, self._orb_low, entry,
        )
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        close = ind.get("close", 0.0)
        orb_high = ind.get("orb_high", float("inf"))
        orb_low = ind.get("orb_low", 0.0)
        vol_current = ind.get("volume_current", 0.0)
        vol_avg = ind.get("volume_avg", 1.0)

        # Must break ORB
        breakout = close > orb_high or close < orb_low
        if not breakout:
            logger.debug(
                "[%s] No ORB breakout: close=%.4f range=[%.4f, %.4f]",
                self.name, close, orb_low, orb_high,
            )
            return False

        # Volume confirmation
        vol_ratio = vol_current / max(vol_avg, 1.0)
        if vol_ratio < self.params["volume_multiplier"]:
            logger.debug("[%s] Volume ratio %.2f < %.2f", self.name, vol_ratio, self.params["volume_multiplier"])
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
                return "stop_loss_orb_low"
            if close >= tp1:
                return "take_profit_1"
        else:
            if close >= stop:
                return "stop_loss_orb_high"
            if close <= tp1:
                return "take_profit_1"

        # End of trade window
        now_et = utc_to_et(datetime.utcnow())
        if now_et.time() >= TRADE_WINDOW_END_ET:
            return "trade_window_close_1130"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "orb_minutes": (int, 5, 60),
            "volume_multiplier": (float, 1.0, 5.0),
            "atr_period": (int, 5, 30),
            "tp_rr": (float, 1.0, 5.0),
            "base_size_pct": (float, 0.001, 0.1),
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
