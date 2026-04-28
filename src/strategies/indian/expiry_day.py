"""
NSE Expiry Day Options Strategy for NEXUS ALPHA.

Exploits max pain and IV crush phenomena on weekly/monthly expiry days.

Logic:
    - Weekly expiry: every Thursday
    - Monthly expiry: last Thursday of the month
    - Max pain effect: price gravitates toward max pain strike near expiry
    - IV > 15: sell OTM options to collect time decay
    - Delta-neutral spreads near expiry
"""

from __future__ import annotations

import calendar
import logging
from datetime import datetime, timedelta, timezone
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


def utc_to_ist(utc_dt: datetime) -> datetime:
    return utc_dt.replace(tzinfo=timezone.utc).astimezone(
        timezone(IST_OFFSET)
    ).replace(tzinfo=None)


def is_expiry_day(dt: datetime) -> tuple[bool, str]:
    """Check if the IST date is a weekly or monthly expiry day."""
    ist = utc_to_ist(dt)
    # Thursday = weekday 3
    if ist.weekday() != 3:
        return False, "none"
    # Is it the last Thursday? (Monthly expiry)
    last_thursday = _last_thursday_of_month(ist.year, ist.month)
    if ist.date() == last_thursday:
        return True, "monthly"
    return True, "weekly"


def _last_thursday_of_month(year: int, month: int):
    """Return the date of the last Thursday in the given month."""
    from datetime import date
    last_day = calendar.monthrange(year, month)[1]
    last = date(year, month, last_day)
    # Walk back to Thursday
    offset = (last.weekday() - 3) % 7
    return last - timedelta(days=offset)


class NSEExpiryDayStrategy(BaseStrategy):
    """
    NSE Options Expiry Day strategy.

    Combines max pain analysis, IV assessment and delta-neutral
    spread construction to exploit expiry day dynamics.

    Default Parameters
    ------------------
    min_iv : float              15.0  — minimum IV to sell premium
    max_pain_dev_threshold : float  2.0  — % dev from max pain to expect mean-revert
    otm_strike_distance_pct : float 1.5  — how far OTM to sell options (%)
    position_size_contracts : int  1    — lot size (abstract)
    base_size_pct : float       0.01
    entry_time_limit_ist : str  "14:00"  — no new entries after this
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "min_iv": 15.0,
        "max_pain_dev_threshold": 2.0,
        "otm_strike_distance_pct": 1.5,
        "position_size_contracts": 1,
        "base_size_pct": 0.01,
        "entry_time_limit_ist": "14:00",
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="NSEExpiryDay",
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
        now = datetime.utcnow()
        expiry_today, expiry_type = is_expiry_day(now)

        if not expiry_today:
            logger.debug("[%s] Not an expiry day", self.name)
            return None

        now_ist = utc_to_ist(now)
        entry_limit_str = self.params["entry_time_limit_ist"]
        entry_limit_h, entry_limit_m = map(int, entry_limit_str.split(":"))
        from datetime import time
        if now_ist.time() >= time(entry_limit_h, entry_limit_m):
            logger.debug("[%s] Past entry time limit %s", self.name, entry_limit_str)
            return None

        option_chain = market_data.get("option_chain", {})
        ohlcv = market_data.get("ohlcv")

        if ohlcv is None:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        close = float(closes[-1])
        max_pain = float(option_chain.get("max_pain", close))
        iv = float(option_chain.get("iv_current", 20.0))
        pcr = float(option_chain.get("pcr", 1.0))

        # Max pain deviation
        max_pain_dev_pct = (close - max_pain) / max_pain * 100.0 if max_pain > 0 else 0.0

        indicators = {
            "close": close,
            "max_pain": max_pain,
            "max_pain_dev_pct": max_pain_dev_pct,
            "iv": iv,
            "pcr": pcr,
            "expiry_type": expiry_type,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        # Strategy selection
        setup = self._select_setup(indicators)
        if setup is None:
            return None

        entry = close
        # For options strategies, stop/tp are approximate underlying levels
        if setup["type"] == "sell_otm_premium":
            call_strike = close * (1 + self.params["otm_strike_distance_pct"] / 100)
            put_strike = close * (1 - self.params["otm_strike_distance_pct"] / 100)
            direction = SignalDirection.SHORT  # Net short volatility
            stop = close * 1.03  # 3% underlying adverse move
            tp1 = close  # Premium collected; exit near expiry
            tp2 = close * 0.98
            tp3 = close * 0.97
        elif setup["type"] == "max_pain_long":
            direction = SignalDirection.LONG
            stop = close * 0.985
            risk = close - stop
            tp1 = max_pain
            tp2 = max_pain + risk * 0.5
            tp3 = max_pain + risk
        else:
            direction = SignalDirection.SHORT
            stop = close * 1.015
            risk = stop - close
            tp1 = max_pain
            tp2 = max_pain - risk * 0.5
            tp3 = max_pain - risk

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
                "setup_type": setup["type"],
                "expiry_type": expiry_type,
                "max_pain": max_pain,
                "max_pain_dev_pct": max_pain_dev_pct,
                "iv": iv,
                "pcr": pcr,
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] %s expiry | setup=%s | max_pain=%.2f | IV=%.1f",
            self.name, expiry_type, setup["type"], max_pain, iv,
        )
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        iv = ind.get("iv", 0.0)
        max_pain_dev = abs(ind.get("max_pain_dev_pct", 0.0))

        # Either sell premium (IV > threshold) or trade max pain pull
        can_sell_premium = iv > self.params["min_iv"]
        can_trade_max_pain = max_pain_dev > self.params["max_pain_dev_threshold"]

        if not (can_sell_premium or can_trade_max_pain):
            logger.debug(
                "[%s] IV=%.1f < %.1f and max_pain_dev=%.1f%% < %.1f%%",
                self.name, iv, self.params["min_iv"],
                max_pain_dev, self.params["max_pain_dev_threshold"],
            )
            return False

        return True

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        close = ind.get("close", 0.0)
        stop = position.get("stop_loss", 0.0)
        direction = position.get("direction", "LONG")
        now = datetime.utcnow()

        if direction == "LONG" and close <= stop:
            return "stop_loss_hit"
        if direction == "SHORT" and close >= stop:
            return "stop_loss_hit"

        # Force exit near expiry
        now_ist = utc_to_ist(now)
        from datetime import time
        if now_ist.time() >= time(15, 0):
            return "expiry_force_exit_3pm"

        # Max pain reached
        max_pain = ind.get("max_pain", close)
        max_pain_dev = abs((close - max_pain) / max_pain * 100) if max_pain > 0 else 0
        if max_pain_dev < 0.2:
            return "max_pain_reached"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "min_iv": (float, 5.0, 100.0),
            "max_pain_dev_threshold": (float, 0.1, 10.0),
            "otm_strike_distance_pct": (float, 0.5, 5.0),
            "position_size_contracts": (int, 1, 100),
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
    def _select_setup(ind: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Choose the most appropriate options setup for today's expiry."""
        iv = ind.get("iv", 0.0)
        max_pain_dev = ind.get("max_pain_dev_pct", 0.0)
        close = ind.get("close", 0.0)
        max_pain = ind.get("max_pain", close)

        if iv > 20.0:
            return {"type": "sell_otm_premium", "rationale": "IV crush on expiry"}

        if max_pain_dev > 2.0:
            if close > max_pain:
                return {"type": "max_pain_short", "rationale": "Price above max pain"}
            else:
                return {"type": "max_pain_long", "rationale": "Price below max pain"}

        return None

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
