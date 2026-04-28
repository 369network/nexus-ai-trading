"""
Forex News Scalp Strategy for NEXUS ALPHA.

Trades high-impact economic events (NFP, CPI, FOMC) immediately after
data release. Enters on the breakout of the pre-event 15-minute range.

Logic:
    - Identify upcoming high-impact event from calendar
    - Record pre-event range (high/low of ~5-minute window before release)
    - After event: enter on 1-min candle break of pre-event range
    - Stop: 15 pips (tight)
    - Target: 30 pips
    - Only active within 3 minutes post-release
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

# High-impact event identifiers
HIGH_IMPACT_EVENTS = {
    "NFP", "NON_FARM_PAYROLLS", "CPI", "CORE_CPI",
    "FOMC", "FED_FUNDS_RATE", "ECB_RATE", "BOE_RATE",
    "BOJ_RATE", "GDP", "UNEMPLOYMENT_RATE", "PPI",
    "RETAIL_SALES", "ISM_MANUFACTURING", "ISM_SERVICES",
}

# 1 pip in price terms per pair type
PIP_SIZE: Dict[str, float] = {
    "default": 0.0001,
    "JPY": 0.01,
}


class ForexNewsScalpStrategy(BaseStrategy):
    """
    High-impact news event breakout scalping strategy.

    Requires an event calendar (list of upcoming events with timestamps).
    The pre-event range must be computed by the data provider and passed
    in market_data['pre_event_range'].

    Default Parameters
    ------------------
    stop_pips : int             15
    target_pips : int           30
    entry_window_seconds : int  180  — only enter within 3 min of release
    pre_event_window_minutes : int  5  — window before event to build range
    min_move_pips : int         5    — minimum initial move to confirm breakout
    base_size_pct : float       0.005 — small size for scalp
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "stop_pips": 15,
        "target_pips": 30,
        "entry_window_seconds": 180,
        "pre_event_window_minutes": 5,
        "min_move_pips": 5,
        "base_size_pct": 0.005,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="ForexNewsScalp",
            market="forex",
            primary_timeframe="1m",
            confirmation_timeframe="5m",
            params=merged,
        )

    # ------------------------------------------------------------------

    def generate_signal(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[TradeSignal]:
        if not self._is_enabled:
            return None

        symbol: str = market_data.get("symbol", "UNKNOWN")
        news_events: List[Dict[str, Any]] = market_data.get("news_events", [])
        ohlcv = market_data.get("ohlcv")

        if ohlcv is None or not news_events:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        if len(closes) < 2:
            return None

        pip = self._get_pip_size(symbol)
        close = float(closes[-1])
        now = datetime.utcnow()

        # Find the most recent high-impact event that just released
        event = self._find_active_event(news_events, now)
        if event is None:
            return None

        # Pre-event range
        pre_range = market_data.get("pre_event_range", {})
        range_high = float(pre_range.get("high", 0.0))
        range_low = float(pre_range.get("low", 0.0))

        if range_high <= 0 or range_low <= 0 or range_high <= range_low:
            # Estimate from recent 1-min bars
            bars_needed = self.params["pre_event_window_minutes"]
            if len(highs) >= bars_needed:
                range_high = float(np.max(highs[-bars_needed:-1]))
                range_low = float(np.min(lows[-bars_needed:-1]))
            else:
                logger.debug("[%s] Cannot determine pre-event range", self.name)
                return None

        indicators = {
            "close": close,
            "range_high": range_high,
            "range_low": range_low,
            "pip": pip,
            "event_name": event.get("name", "UNKNOWN"),
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        # Direction: breakout above → long; below → short
        stop_price = self.params["stop_pips"] * pip
        target_price = self.params["target_pips"] * pip

        if close > range_high:
            direction = SignalDirection.LONG
            entry = close
            stop = entry - stop_price
            tp1 = entry + target_price
            tp2 = entry + target_price * 1.5
            tp3 = entry + target_price * 2.0
        else:
            direction = SignalDirection.SHORT
            entry = close
            stop = entry + stop_price
            tp1 = entry - target_price
            tp2 = entry - target_price * 1.5
            tp3 = entry - target_price * 2.0

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
            confidence=0.55,
            metadata={
                "event": event.get("name"),
                "pre_event_high": range_high,
                "pre_event_low": range_low,
                "stop_pips": self.params["stop_pips"],
                "target_pips": self.params["target_pips"],
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] News scalp %s | event=%s | entry=%.5f | stop=%.5f | tp1=%.5f",
            self.name, direction.value, event.get("name"), entry, stop, tp1,
        )
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        close = ind.get("close", 0.0)
        range_high = ind.get("range_high", float("inf"))
        range_low = ind.get("range_low", 0.0)
        pip = ind.get("pip", 0.0001)

        # Must break out of the range
        breakout_up = close > range_high
        breakout_down = close < range_low

        if not (breakout_up or breakout_down):
            logger.debug(
                "[%s] No breakout: close=%.5f range=[%.5f, %.5f]",
                self.name, close, range_low, range_high,
            )
            return False

        # Minimum move confirmation
        if breakout_up:
            move_pips = (close - range_high) / pip
        else:
            move_pips = (range_low - close) / pip

        if move_pips < self.params["min_move_pips"]:
            logger.debug(
                "[%s] Move %.1f pips < min %d pips",
                self.name, move_pips, self.params["min_move_pips"],
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
        entry_time = position.get("entry_time")

        if direction == "LONG":
            if close <= stop:
                return "stop_loss_hit"
            if close >= tp1:
                return "take_profit_30pips"
        else:
            if close >= stop:
                return "stop_loss_hit"
            if close <= tp1:
                return "take_profit_30pips"

        # Time-based scalp exit: close within 10 minutes if not hit target
        if entry_time:
            if isinstance(entry_time, str):
                entry_time = datetime.fromisoformat(entry_time)
            elapsed = (datetime.utcnow() - entry_time).total_seconds()
            if elapsed > 600:  # 10 minutes
                return "scalp_time_exit_10min"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "stop_pips": (int, 5, 100),
            "target_pips": (int, 10, 200),
            "entry_window_seconds": (int, 30, 600),
            "pre_event_window_minutes": (int, 1, 30),
            "min_move_pips": (int, 1, 50),
            "base_size_pct": (float, 0.001, 0.02),
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
        if "stop_pips" in params and "target_pips" in params:
            if params["stop_pips"] >= params["target_pips"]:
                return False
        return True

    def backtest_metric(self) -> Dict[str, Any]:
        return self._build_backtest_metric_from_history().to_dict()

    # ------------------------------------------------------------------

    def _find_active_event(
        self, events: List[Dict[str, Any]], now: datetime
    ) -> Optional[Dict[str, Any]]:
        """
        Find the most recent high-impact event that was released within
        the entry_window_seconds and has not expired.
        """
        window = timedelta(seconds=self.params["entry_window_seconds"])
        best: Optional[Dict[str, Any]] = None
        best_diff = timedelta.max

        for event in events:
            name = event.get("name", "").upper().replace(" ", "_")
            impact = event.get("impact", "").upper()
            if name not in HIGH_IMPACT_EVENTS and impact not in ("HIGH", "VERY_HIGH"):
                continue
            try:
                event_time = event.get("time")
                if isinstance(event_time, str):
                    event_time = datetime.fromisoformat(event_time)
                diff = now - event_time
                if timedelta(0) <= diff <= window:
                    if diff < best_diff:
                        best_diff = diff
                        best = event
            except Exception:
                continue

        return best

    @staticmethod
    def _get_pip_size(symbol: str) -> float:
        if "JPY" in symbol.upper():
            return PIP_SIZE["JPY"]
        return PIP_SIZE["default"]

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
