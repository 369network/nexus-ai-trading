"""
Sector Rotation Strategy for NEXUS ALPHA.

Rotates capital into the strongest 2-3 sectors vs S&P 500 on a weekly basis.
Underweights or shorts the weakest sectors.

Sector ETFs used:
    XLK (Tech), XLF (Financials), XLE (Energy), XLV (Healthcare),
    XLU (Utilities), XLI (Industrials), XLB (Materials),
    XLC (Communications), XLY (Consumer Disc.), XLRE (Real Estate)

Relative strength measured as 4-week total return vs SPY.
Rebalances monthly on the first trading day of the month.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)

SECTOR_ETFS = [
    "XLK", "XLF", "XLE", "XLV", "XLU",
    "XLI", "XLB", "XLC", "XLY", "XLRE",
]

BENCHMARK = "SPY"
TOP_N = 3    # Overweight top N sectors
BOTTOM_N = 2  # Underweight bottom N sectors


class SectorRotationStrategy(BaseStrategy):
    """
    Monthly sector rotation strategy based on relative strength vs SPY.

    Default Parameters
    ------------------
    rs_lookback_days : int      28    — 4-week relative strength window
    top_n_long : int            3     — sectors to go long
    bottom_n_short : int        2     — sectors to underweight/short
    rebalance_day : int         1     — day of month to rebalance (1 = 1st)
    min_rs_score : float        0.02  — minimum RS difference to trade
    atr_period : int            14
    atr_stop_mult : float       2.5
    base_size_pct : float       0.02
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "rs_lookback_days": 28,
        "top_n_long": TOP_N,
        "bottom_n_short": BOTTOM_N,
        "rebalance_day": 1,
        "min_rs_score": 0.02,
        "atr_period": 14,
        "atr_stop_mult": 2.5,
        "base_size_pct": 0.02,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="SectorRotation",
            market="us",
            primary_timeframe="1d",
            confirmation_timeframe="1w",
            params=merged,
        )
        self._last_rebalance_month: Optional[int] = None
        self._current_positions: Dict[str, SignalDirection] = {}

    # ------------------------------------------------------------------

    def generate_signal(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[TradeSignal]:
        if not self._is_enabled:
            return None

        now = datetime.utcnow()
        # Rebalance on the configured day of month
        if not self._should_rebalance(now):
            return None

        sector_data: Dict[str, Any] = market_data.get("sector_data", {})
        spy_data = sector_data.get(BENCHMARK, {})

        if not sector_data:
            logger.warning("[%s] No sector_data provided", self.name)
            return None

        # Compute relative strength for each sector
        rs_scores = self._compute_rs_scores(sector_data, spy_data)
        if not rs_scores:
            return None

        # Rank sectors
        ranked = sorted(rs_scores.items(), key=lambda x: x[1], reverse=True)
        top_sectors = [s for s, _ in ranked[: self.params["top_n_long"]]]
        bottom_sectors = [s for s, _ in ranked[-self.params["bottom_n_short"]:]]

        logger.info("[%s] Rebalance | Top: %s | Bottom: %s", self.name, top_sectors, bottom_sectors)

        # Generate a signal for the highest RS sector
        if not top_sectors:
            return None

        best_sector = top_sectors[0]
        best_rs = rs_scores[best_sector]

        if best_rs < self.params["min_rs_score"]:
            logger.debug("[%s] Best RS %.4f < min %.4f", self.name, best_rs, self.params["min_rs_score"])
            return None

        sector_ohlcv = sector_data.get(best_sector, {}).get("ohlcv")
        close = 100.0  # Fallback
        atr_val = 2.0

        if sector_ohlcv is not None:
            try:
                closes = self._to_array(sector_ohlcv, "close")
                highs = self._to_array(sector_ohlcv, "high")
                lows = self._to_array(sector_ohlcv, "low")
                close = float(closes[-1])
                atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
                atr_val = float(atr[-1]) if not np.isnan(atr[-1]) else atr_val
            except Exception:
                pass

        stop = close - atr_val * self.params["atr_stop_mult"]
        risk = close - stop
        tp1 = close + risk * 2.0
        tp2 = close + risk * 3.0
        tp3 = close + risk * 4.0

        self._last_rebalance_month = now.month

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=best_sector,
            direction=SignalDirection.LONG,
            strength=SignalStrength.MODERATE,
            entry_price=close,
            stop_loss=stop,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=self.params["base_size_pct"],
            timeframe=self.primary_timeframe,
            confidence=min(0.85, 0.5 + best_rs * 5),
            metadata={
                "rs_score": best_rs,
                "top_sectors": top_sectors,
                "bottom_sectors": bottom_sectors,
                "all_rs_scores": rs_scores,
                "rebalance_date": now.strftime("%Y-%m-%d"),
            },
        )
        self.record_signal(signal)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        # Entry logic is handled in generate_signal for this strategy
        return True

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        close = ind.get("close", 0.0)
        stop = position.get("stop_loss", 0.0)
        direction = position.get("direction", "LONG")

        if direction == "LONG" and close <= stop:
            return "stop_loss_hit"
        if direction == "SHORT" and close >= stop:
            return "stop_loss_hit"

        # Monthly rebalance exit
        now = datetime.utcnow()
        if self._should_rebalance(now):
            return "monthly_rebalance"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "rs_lookback_days": (int, 5, 252),
            "top_n_long": (int, 1, 5),
            "bottom_n_short": (int, 0, 5),
            "rebalance_day": (int, 1, 28),
            "min_rs_score": (float, 0.0, 0.5),
            "atr_period": (int, 5, 30),
            "atr_stop_mult": (float, 0.5, 5.0),
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

    # ------------------------------------------------------------------

    def _should_rebalance(self, now: datetime) -> bool:
        """Return True on the configured rebalance day and only once per month."""
        if now.day != self.params["rebalance_day"]:
            return False
        if self._last_rebalance_month == now.month:
            return False  # Already rebalanced this month
        return True

    def _compute_rs_scores(
        self,
        sector_data: Dict[str, Any],
        spy_data: Dict[str, Any],
    ) -> Dict[str, float]:
        """
        Compute 4-week relative strength as:
            RS = (sector_return - spy_return) over lookback_days
        """
        lookback = self.params["rs_lookback_days"]
        spy_ohlcv = spy_data.get("ohlcv")
        spy_return = 0.0

        if spy_ohlcv is not None:
            try:
                spy_closes = self._to_array(spy_ohlcv, "close")
                if len(spy_closes) >= lookback:
                    spy_return = (spy_closes[-1] - spy_closes[-lookback]) / spy_closes[-lookback]
            except Exception:
                pass

        scores: Dict[str, float] = {}
        for sector in SECTOR_ETFS:
            if sector == BENCHMARK:
                continue
            data = sector_data.get(sector, {})
            ohlcv = data.get("ohlcv") if isinstance(data, dict) else None
            if ohlcv is None:
                scores[sector] = 0.0
                continue
            try:
                closes = self._to_array(ohlcv, "close")
                if len(closes) >= lookback:
                    sector_return = (closes[-1] - closes[-lookback]) / closes[-lookback]
                    scores[sector] = sector_return - spy_return
                else:
                    scores[sector] = 0.0
            except Exception:
                scores[sector] = 0.0

        return scores

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
