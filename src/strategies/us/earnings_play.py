"""
Earnings Play Strategy for NEXUS ALPHA.

Trades earnings events in two modes:
    1. Pre-earnings: IV crush play — sell straddle/strangle if IV rank > 50%
    2. Post-earnings: gap breakout — buy breakout if gap > 3% with volume

Risk: strict 5% maximum loss per earnings trade.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

MAX_LOSS_PCT = 0.05  # 5% max loss per trade


class EarningsPlayStrategy(BaseStrategy):
    """
    Dual-mode earnings strategy for US equities.

    Mode 1 (pre-earnings, 1-3 days before): Sell options if IV rank high.
    Mode 2 (post-earnings): Buy gap breakout if clean gap with volume.

    Default Parameters
    ------------------
    iv_rank_threshold : float    50.0  — minimum IV rank to sell premium
    gap_threshold_pct : float    3.0   — minimum gap % for post-earnings buy
    pre_earnings_days : int      3     — look ahead window
    volume_confirmation_mult : float  1.5
    max_loss_pct : float         0.05
    atr_period : int             14
    atr_stop_mult : float        2.0
    tp_rr : float                2.0
    base_size_pct : float        0.02
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "iv_rank_threshold": 50.0,
        "gap_threshold_pct": 3.0,
        "pre_earnings_days": 3,
        "volume_confirmation_mult": 1.5,
        "max_loss_pct": MAX_LOSS_PCT,
        "atr_period": 14,
        "atr_stop_mult": 2.0,
        "tp_rr": 2.0,
        "base_size_pct": 0.02,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="EarningsPlay",
            market="us",
            primary_timeframe="1d",
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
        earnings_data: Dict[str, Any] = market_data.get("earnings_data", {})

        if ohlcv is None:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
            volumes = self._to_array(ohlcv, "volume")
        except Exception as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        if len(closes) < self.params["atr_period"] + 5:
            return None

        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        close = float(closes[-1])
        atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0
        vol_current = float(volumes[-1])
        vol_avg = float(np.mean(volumes[-20:])) if len(volumes) >= 20 else vol_current

        # Earnings context
        earnings_date = earnings_data.get("date")
        iv_rank = float(earnings_data.get("iv_rank", 0.0))
        post_earnings = earnings_data.get("post_earnings", False)
        prev_close = float(earnings_data.get("prev_close", close))

        indicators = {
            "close": close,
            "atr": atr_val,
            "iv_rank": iv_rank,
            "post_earnings": post_earnings,
            "prev_close": prev_close,
            "volume_current": vol_current,
            "volume_avg": vol_avg,
            "gap_pct": (close - prev_close) / prev_close * 100.0 if prev_close > 0 else 0.0,
            "earnings_date": earnings_date,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        # Select mode
        gap_pct = indicators["gap_pct"]
        if post_earnings and abs(gap_pct) >= self.params["gap_threshold_pct"]:
            return self._build_gap_signal(symbol, indicators, atr_val, close, gap_pct)
        elif not post_earnings and iv_rank >= self.params["iv_rank_threshold"]:
            return self._build_iv_crush_signal(symbol, indicators, atr_val, close)

        return None

    def _build_gap_signal(
        self, symbol: str, ind: Dict[str, Any],
        atr_val: float, close: float, gap_pct: float
    ) -> TradeSignal:
        direction = SignalDirection.LONG if gap_pct > 0 else SignalDirection.SHORT

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

        # Cap size for 5% max loss
        size_pct = min(
            self.params["base_size_pct"],
            self.params["max_loss_pct"] / max((atr_val * self.params["atr_stop_mult"] / close), 0.001),
        )

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
            size_pct=size_pct,
            timeframe=self.primary_timeframe,
            confidence=min(0.8, abs(gap_pct) / 10.0),
            metadata={
                "mode": "post_earnings_gap",
                "gap_pct": gap_pct,
                "atr": atr_val,
                "max_loss_pct": self.params["max_loss_pct"],
            },
        )
        self.record_signal(signal)
        return signal

    def _build_iv_crush_signal(
        self, symbol: str, ind: Dict[str, Any], atr_val: float, close: float
    ) -> TradeSignal:
        # Short volatility — selling straddle/strangle
        # Represented as SHORT underlying (proxy for net short vol)
        direction = SignalDirection.SHORT
        stop = close * (1 + self.params["max_loss_pct"])  # 5% adverse move
        tp1 = close * (1 - 0.02)  # Modest underlying movement
        tp2 = close * (1 - 0.03)
        tp3 = close * (1 - 0.04)

        size_pct = min(self.params["base_size_pct"], 0.01)

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=direction,
            strength=SignalStrength.SLIGHT,
            entry_price=close,
            stop_loss=stop,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=size_pct,
            timeframe=self.primary_timeframe,
            confidence=0.6,
            metadata={
                "mode": "iv_crush_pre_earnings",
                "iv_rank": ind.get("iv_rank"),
                "max_loss_pct": self.params["max_loss_pct"],
                "strategy_note": "Sell straddle/strangle; signal proxies short vol",
            },
        )
        self.record_signal(signal)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})
        post_earnings = ind.get("post_earnings", False)
        iv_rank = ind.get("iv_rank", 0.0)
        gap_pct = abs(ind.get("gap_pct", 0.0))
        vol_current = ind.get("volume_current", 0.0)
        vol_avg = ind.get("volume_avg", 1.0)

        if post_earnings:
            if gap_pct < self.params["gap_threshold_pct"]:
                logger.debug("[%s] Post-earnings gap %.2f%% < threshold", self.name, gap_pct)
                return False
            vol_ratio = vol_current / max(vol_avg, 1.0)
            if vol_ratio < self.params["volume_confirmation_mult"]:
                logger.debug("[%s] Volume insufficient for gap: ratio=%.2f", self.name, vol_ratio)
                return False
            return True
        else:
            earnings_date = ind.get("earnings_date")
            if earnings_date:
                if isinstance(earnings_date, str):
                    earnings_date = datetime.fromisoformat(earnings_date)
                days_to_earnings = (earnings_date.date() - datetime.utcnow().date()).days
                if not (0 < days_to_earnings <= self.params["pre_earnings_days"]):
                    logger.debug("[%s] Earnings in %d days; outside window", self.name, days_to_earnings)
                    return False
            if iv_rank < self.params["iv_rank_threshold"]:
                logger.debug("[%s] IV rank %.1f < threshold", self.name, iv_rank)
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
        entry = position.get("entry_price", close)

        # Max loss check
        loss_pct = abs(close - entry) / max(entry, 1) if direction == "LONG" and close < entry else abs(close - entry) / max(entry, 1) if direction == "SHORT" and close > entry else 0
        if loss_pct >= self.params["max_loss_pct"]:
            return "max_loss_5pct_triggered"

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

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "iv_rank_threshold": (float, 10.0, 100.0),
            "gap_threshold_pct": (float, 1.0, 15.0),
            "pre_earnings_days": (int, 1, 7),
            "volume_confirmation_mult": (float, 1.0, 5.0),
            "max_loss_pct": (float, 0.01, 0.10),
            "atr_period": (int, 5, 30),
            "atr_stop_mult": (float, 0.5, 5.0),
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

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
