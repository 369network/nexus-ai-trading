"""
NSE Gap Trading Strategy for NEXUS ALPHA.

Trades gap openings on the NSE (National Stock Exchange of India).

Logic:
    - On market open (9:15 IST): detect gap vs previous close
    - Gap up > 0.5%: buy on pullback to VWAP within first 30 minutes
    - Gap down > 0.5%: sell on bounce to VWAP within first 30 minutes
    - Pre-open (9:00-9:15 IST): use indicative price to estimate gap direction
    - Exit: by 3:00 PM IST (before MIS square-off at 3:15 PM)

All timestamps are in IST (UTC+5:30).
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

IST_OFFSET = timedelta(hours=5, minutes=30)
MARKET_OPEN_IST = time(9, 15)
PRE_OPEN_IST = time(9, 0)
ENTRY_WINDOW_END_IST = time(9, 45)   # 30 min after open
EXIT_BY_IST = time(15, 0)            # MIS square-off
MARKET_CLOSE_IST = time(15, 30)


def utc_to_ist(utc_dt: datetime) -> datetime:
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(
        timezone(IST_OFFSET)
    ).replace(tzinfo=None)


class NSEGapTradingStrategy(BaseStrategy):
    """
    Intraday gap + VWAP revert strategy for NSE equity/index instruments.

    Default Parameters
    ------------------
    gap_threshold_pct : float   0.5   — minimum gap % to trigger
    vwap_entry_tolerance : float 0.001 — 0.1% tolerance for VWAP entry
    atr_period : int            14
    atr_stop_mult : float       1.5
    tp_rr : float               2.0
    base_size_pct : float       0.02
    entry_window_minutes : int  30
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "gap_threshold_pct": 0.5,
        "vwap_entry_tolerance": 0.001,
        "atr_period": 14,
        "atr_stop_mult": 1.5,
        "tp_rr": 2.0,
        "base_size_pct": 0.02,
        "entry_window_minutes": 30,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="NSEGapTrading",
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

        symbol: str = market_data.get("symbol", "UNKNOWN")
        ohlcv = market_data.get("ohlcv")
        prev_close: float = float(market_data.get("previous_close", 0.0))

        if ohlcv is None or prev_close <= 0:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
            volumes = self._to_array(ohlcv, "volume")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        if len(closes) < 2:
            return None

        now_ist = utc_to_ist(datetime.utcnow())
        ist_time = now_ist.time()

        # Must be within entry window
        if not (MARKET_OPEN_IST <= ist_time <= ENTRY_WINDOW_END_IST):
            logger.debug("[%s] Outside entry window: %s", self.name, ist_time)
            return None

        # Gap calculation
        open_price = float(ohlcv["open"][0] if hasattr(ohlcv["open"], "__getitem__") else ohlcv["open"])
        gap_pct = (open_price - prev_close) / prev_close * 100.0

        # VWAP
        vwap = self._compute_vwap(closes, highs, lows, volumes)
        close = float(closes[-1])
        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0

        indicators = {
            "close": close,
            "open_price": open_price,
            "prev_close": prev_close,
            "gap_pct": gap_pct,
            "vwap": vwap,
            "atr": atr_val,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        # Direction based on gap type
        if gap_pct > 0:
            # Gap up → buy on pullback to VWAP
            direction = SignalDirection.LONG
            entry = vwap
            stop = entry - atr_val * self.params["atr_stop_mult"]
            risk = entry - stop
            tp1 = entry + risk * self.params["tp_rr"]
            tp2 = entry + risk * self.params["tp_rr"] * 1.5
            tp3 = entry + risk * self.params["tp_rr"] * 2.0
        else:
            # Gap down → short on bounce to VWAP
            direction = SignalDirection.SHORT
            entry = vwap
            stop = entry + atr_val * self.params["atr_stop_mult"]
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
            confidence=min(0.85, abs(gap_pct) / 3.0),
            metadata={
                "gap_pct": gap_pct,
                "open_price": open_price,
                "prev_close": prev_close,
                "vwap": vwap,
                "atr": atr_val,
                "exit_by_ist": EXIT_BY_IST.strftime("%H:%M"),
                "mis_squareoff": True,
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] Gap %s %.2f%% | entry=%.2f | stop=%.2f",
            self.name, direction.value, gap_pct, entry, stop,
        )
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        gap_pct = ind.get("gap_pct", 0.0)
        threshold = self.params["gap_threshold_pct"]

        if abs(gap_pct) < threshold:
            logger.debug("[%s] Gap %.2f%% < threshold %.2f%%", self.name, gap_pct, threshold)
            return False

        # Price must have pulled back toward VWAP
        close = ind.get("close", 0.0)
        vwap = ind.get("vwap", 0.0)
        tol = close * self.params["vwap_entry_tolerance"]

        if abs(close - vwap) > tol * 5:  # 5x tolerance for proximity
            logger.debug(
                "[%s] Price %.2f not near VWAP %.2f (diff=%.4f)",
                self.name, close, vwap, abs(close - vwap),
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

        # MIS square-off before 3:00 PM IST
        now_ist = utc_to_ist(datetime.utcnow())
        if now_ist.time() >= EXIT_BY_IST:
            return "mis_squareoff_3pm"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "gap_threshold_pct": (float, 0.1, 5.0),
            "vwap_entry_tolerance": (float, 0.0001, 0.01),
            "atr_period": (int, 5, 30),
            "atr_stop_mult": (float, 0.5, 3.0),
            "tp_rr": (float, 1.0, 5.0),
            "base_size_pct": (float, 0.001, 0.1),
            "entry_window_minutes": (int, 5, 60),
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
    def _compute_vwap(
        closes: np.ndarray,
        highs: np.ndarray,
        lows: np.ndarray,
        volumes: np.ndarray,
    ) -> float:
        """Compute VWAP from available intraday bars."""
        if len(closes) == 0:
            return 0.0
        typical = (highs + lows + closes) / 3.0
        total_vol = np.sum(volumes)
        if total_vol == 0:
            return float(closes[-1])
        return float(np.sum(typical * volumes) / total_vol)

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
