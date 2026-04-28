"""
Forex Breakout Strategy for NEXUS ALPHA.

Entry conditions:
    - 20-period Donchian channel breakout on 4h chart
    - Only trade during London session (07:00-16:00 UTC)
      or New York session (13:00-22:00 UTC)
    - Spread tightening used as volume proxy for breakout confirmation
    - Avoid entries within 30 minutes of scheduled high-impact news events
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timezone
from typing import Any, Dict, List, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

# Session windows in UTC (hour, minute)
LONDON_SESSION = (time(7, 0), time(16, 0))
NY_SESSION = (time(13, 0), time(22, 0))


class ForexBreakoutStrategy(BaseStrategy):
    """
    Donchian channel breakout strategy for major Forex pairs.

    Default Parameters
    ------------------
    donchian_period : int       20
    atr_period : int            14
    atr_stop_mult : float       1.5
    tp_rr : float               2.0  — TP at 2R
    spread_tight_factor : float 0.8  — spread must be < N * baseline to confirm
    news_buffer_minutes : int   30
    base_size_pct : float       0.01
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "donchian_period": 20,
        "atr_period": 14,
        "atr_stop_mult": 1.5,
        "tp_rr": 2.0,
        "spread_tight_factor": 0.8,
        "news_buffer_minutes": 30,
        "base_size_pct": 0.01,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="ForexBreakout",
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

        if len(closes) < self.params["donchian_period"] + 5:
            return None

        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        dc_upper, dc_lower = self.compute_donchian(highs, lows, self.params["donchian_period"])

        market_session = context.get("market_session", {})
        news_events: List[Dict[str, Any]] = market_data.get("news_events", [])
        spread_info: Dict[str, float] = market_data.get("spread_info", {})

        indicators = {
            "close": float(closes[-1]),
            "high": float(highs[-1]),
            "low": float(lows[-1]),
            "dc_upper": float(dc_upper[-1]) if not np.isnan(dc_upper[-1]) else 0.0,
            "dc_lower": float(dc_lower[-1]) if not np.isnan(dc_lower[-1]) else float("inf"),
            "dc_upper_prev": float(dc_upper[-2]) if not np.isnan(dc_upper[-2]) else 0.0,
            "dc_lower_prev": float(dc_lower[-2]) if not np.isnan(dc_lower[-2]) else float("inf"),
            "atr": float(atr[-1]) if not np.isnan(atr[-1]) else 0.0,
            "session_active": self._is_session_active(),
            "news_clear": self._is_news_clear(news_events),
            "spread_tight": self._is_spread_tight(spread_info),
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        close = indicators["close"]
        atr_val = indicators["atr"]
        dc_upper_val = indicators["dc_upper"]
        dc_lower_val = indicators["dc_lower"]

        # Determine direction from breakout
        breakout_up = close > dc_upper_val
        direction = SignalDirection.LONG if breakout_up else SignalDirection.SHORT

        if direction == SignalDirection.LONG:
            entry = close
            stop = entry - atr_val * self.params["atr_stop_mult"]
            risk = entry - stop
            tp1 = entry + risk * self.params["tp_rr"]
            tp2 = entry + risk * self.params["tp_rr"] * 1.5
            tp3 = entry + risk * self.params["tp_rr"] * 2.0
        else:
            entry = close
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
            confidence=0.65,
            metadata={
                "dc_upper": dc_upper_val,
                "dc_lower": dc_lower_val,
                "atr": atr_val,
                "session": "london_or_ny",
            },
        )
        self.record_signal(signal)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        # Session filter
        if not ind.get("session_active", False):
            logger.debug("[%s] Outside London/NY session", self.name)
            return False

        # News filter
        if not ind.get("news_clear", True):
            logger.debug("[%s] High-impact news within 30 min", self.name)
            return False

        close = ind.get("close", 0.0)
        dc_upper = ind.get("dc_upper", float("inf"))
        dc_lower = ind.get("dc_lower", 0.0)

        # Breakout — current close must pierce Donchian boundary
        breakout_up = close > dc_upper
        breakout_down = close < dc_lower

        if not (breakout_up or breakout_down):
            logger.debug(
                "[%s] No Donchian breakout: close=%.5f dc_upper=%.5f dc_lower=%.5f",
                self.name, close, dc_upper, dc_lower,
            )
            return False

        # Spread confirmation
        if not ind.get("spread_tight", True):
            logger.debug("[%s] Spread not tight; rejecting breakout", self.name)
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

        # Session close exit — exit before weekend or session end
        if not self._is_session_active():
            return "session_closed"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "donchian_period": (int, 5, 100),
            "atr_period": (int, 5, 50),
            "atr_stop_mult": (float, 0.5, 4.0),
            "tp_rr": (float, 1.0, 5.0),
            "spread_tight_factor": (float, 0.3, 1.0),
            "news_buffer_minutes": (int, 0, 120),
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
    def _is_session_active() -> bool:
        now = datetime.now(timezone.utc).time().replace(tzinfo=None)
        in_london = LONDON_SESSION[0] <= now <= LONDON_SESSION[1]
        in_ny = NY_SESSION[0] <= now <= NY_SESSION[1]
        return in_london or in_ny

    def _is_news_clear(self, news_events: List[Dict[str, Any]]) -> bool:
        """Return True if no high-impact event within buffer window."""
        now = datetime.utcnow()
        buffer = self.params["news_buffer_minutes"] * 60  # seconds
        for event in news_events:
            if event.get("impact", "").upper() not in ("HIGH", "VERY_HIGH"):
                continue
            try:
                event_time = event.get("time")
                if isinstance(event_time, str):
                    event_time = datetime.fromisoformat(event_time)
                diff = abs((event_time - now).total_seconds())
                if diff <= buffer:
                    return False
            except Exception:
                continue
        return True

    def _is_spread_tight(self, spread_info: Dict[str, float]) -> bool:
        """Return True if current spread is below spread_tight_factor * baseline."""
        current = spread_info.get("current_pips", 0.0)
        baseline = spread_info.get("baseline_pips", current)
        if baseline <= 0:
            return True
        return current <= baseline * self.params["spread_tight_factor"]

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
