"""
Liquidation Cascade Strategy for NEXUS ALPHA.

Monitors the real-time liquidation stream from Binance.

Logic:
    - LONG setup: when >$10M in liquidations occur in 5 minutes (capitulation),
      wait for RSI < 25 and price at strong support, then enter long for bounce.
    - SHORT setup: if cascade is accelerating (multiple waves), enter momentum short.
    - Very tight risk: ATR * 1.5 stop loss.

Liquidation data must be provided via market_data['sentiment']['liquidations'].
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


class LiquidationCascadeStrategy(BaseStrategy):
    """
    Liquidation cascade exploitation strategy for crypto perpetuals.

    Monitors liquidation volume spikes to identify extreme sentiment
    reversals (for bounce longs) or acceleration patterns (for momentum shorts).

    Default Parameters
    ------------------
    liq_window_minutes : int        5    — detection window
    liq_threshold_usd : float       10e6 — $10M threshold
    rsi_oversold_long : float       25.0 — RSI must be < this for long
    rsi_overbought_short : float    75.0 — RSI must be > this for short momentum
    atr_stop_mult : float           1.5  — very tight stop
    cascade_waves : int             2    — # of waves for short momentum
    wave_window_minutes : int       15   — window to detect multi-wave
    wave_usd_threshold : float      5e6  — each wave must be > $5M
    support_lookback : int          48   — periods to find strong support
    base_size_pct : float           0.01 — tight risk, smaller size
    min_settle_seconds : int        60   — wait N seconds after spike settles
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "liq_window_minutes": 5,
        "liq_threshold_usd": 10_000_000.0,
        "rsi_oversold_long": 25.0,
        "rsi_overbought_short": 75.0,
        "atr_stop_mult": 1.5,
        "cascade_waves": 2,
        "wave_window_minutes": 15,
        "wave_usd_threshold": 5_000_000.0,
        "support_lookback": 48,
        "base_size_pct": 0.01,
        "min_settle_seconds": 60,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="LiquidationCascade",
            market="crypto",
            primary_timeframe="5m",
            confirmation_timeframe="1h",
            params=merged,
        )

    # ------------------------------------------------------------------

    def generate_signal(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[TradeSignal]:
        if not self._is_enabled:
            return None

        symbol: str = market_data.get("symbol", "UNKNOWN")
        sentiment: Dict[str, Any] = market_data.get("sentiment", {})
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

        min_bars = self.params["support_lookback"] + 20
        if len(closes) < min_bars:
            return None

        # Liquidation data from stream
        liquidations: List[Dict[str, Any]] = sentiment.get("liquidations", [])
        now = datetime.utcnow()

        # Compute indicators
        rsi = self.compute_rsi(closes, 14)
        atr = self.compute_atr(highs, lows, closes, 14)
        support_level = self._find_support(lows, self.params["support_lookback"])

        current_rsi = float(rsi[-1]) if not np.isnan(rsi[-1]) else 50.0
        current_atr = float(atr[-1]) if not np.isnan(atr[-1]) else 0.0
        current_close = float(closes[-1])

        indicators = {
            "rsi": current_rsi,
            "atr": current_atr,
            "close": current_close,
            "support_level": support_level,
        }
        full_data = {**market_data, "indicators": indicators}

        # Analyse liquidation pattern
        liq_analysis = self._analyse_liquidations(liquidations, now)
        indicators["liq_analysis"] = liq_analysis

        if not self.check_entry_conditions(full_data):
            return None

        direction = liq_analysis.get("direction", SignalDirection.LONG)

        if direction == SignalDirection.LONG:
            stop_loss = current_close - current_atr * self.params["atr_stop_mult"]
            risk = current_close - stop_loss
            tp1 = current_close + risk * 1.5
            tp2 = current_close + risk * 2.5
            tp3 = current_close + risk * 4.0
        else:
            stop_loss = current_close + current_atr * self.params["atr_stop_mult"]
            risk = stop_loss - current_close
            tp1 = current_close - risk * 1.5
            tp2 = current_close - risk * 2.5
            tp3 = current_close - risk * 4.0

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=direction,
            strength=SignalStrength.MODERATE,
            entry_price=current_close,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp3,
            size_pct=self.params["base_size_pct"],
            timeframe=self.primary_timeframe,
            confidence=liq_analysis.get("confidence", 0.6),
            metadata={
                "rsi": current_rsi,
                "atr": current_atr,
                "liq_usd_5min": liq_analysis.get("total_usd", 0),
                "cascade_waves": liq_analysis.get("wave_count", 0),
                "support_level": support_level,
                "setup_type": liq_analysis.get("setup_type", "bounce"),
            },
        )
        self.record_signal(signal)
        logger.info(
            "[%s] %s signal | liq=$%.1fM | RSI=%.1f | support=%.4f",
            self.name, direction.value,
            liq_analysis.get("total_usd", 0) / 1e6,
            current_rsi, support_level,
        )
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})
        liq = ind.get("liq_analysis", {})

        if not liq.get("spike_detected", False):
            logger.debug("[%s] No liquidation spike detected", self.name)
            return False

        if not liq.get("settled", False):
            logger.debug("[%s] Liquidation spike not yet settled", self.name)
            return False

        setup_type = liq.get("setup_type", "none")

        if setup_type == "bounce":
            # Long: RSI oversold + at support
            rsi = ind.get("rsi", 100.0)
            close = ind.get("close", float("inf"))
            support = ind.get("support_level", 0.0)

            if rsi >= self.params["rsi_oversold_long"]:
                logger.debug("[%s] Bounce FAIL: RSI %.2f not oversold", self.name, rsi)
                return False

            if support > 0 and close > support * 1.01:
                logger.debug("[%s] Bounce FAIL: price not at support", self.name)
                return False

            return True

        elif setup_type == "momentum_short":
            # Short: cascade accelerating
            if liq.get("wave_count", 0) < self.params["cascade_waves"]:
                logger.debug("[%s] Short FAIL: not enough cascade waves", self.name)
                return False
            return True

        return False

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        close = ind.get("close", position.get("entry_price", 0.0))
        stop_loss = position.get("stop_loss", 0.0)
        tp1 = position.get("take_profit_1", float("inf"))
        direction = position.get("direction", "LONG")

        if direction == "LONG":
            if close <= stop_loss:
                return "stop_loss_hit"
            if close >= tp1:
                return "take_profit_1"
        else:
            if close >= stop_loss:
                return "stop_loss_hit"
            if close <= tp1:
                return "take_profit_1"

        # If RSI recovers above 50 for long, consider exit
        rsi = ind.get("rsi", 50.0)
        if direction == "LONG" and rsi > 55:
            return "rsi_recovered"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "liq_window_minutes": (int, 1, 60),
            "liq_threshold_usd": (float, 1e5, 1e9),
            "rsi_oversold_long": (float, 10.0, 35.0),
            "rsi_overbought_short": (float, 65.0, 90.0),
            "atr_stop_mult": (float, 0.5, 3.0),
            "cascade_waves": (int, 1, 10),
            "wave_window_minutes": (int, 5, 60),
            "wave_usd_threshold": (float, 1e5, 1e8),
            "support_lookback": (int, 10, 200),
            "base_size_pct": (float, 0.001, 0.05),
            "min_settle_seconds": (int, 10, 300),
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
    # Private helpers
    # ------------------------------------------------------------------

    def _analyse_liquidations(
        self, liquidations: List[Dict[str, Any]], now: datetime
    ) -> Dict[str, Any]:
        """
        Analyse liquidation stream to detect spikes and cascade patterns.

        Each liquidation entry expected:
            {'timestamp': str (ISO), 'usd': float, 'side': 'long'/'short'}
        """
        result = {
            "spike_detected": False,
            "settled": False,
            "setup_type": "none",
            "direction": SignalDirection.LONG,
            "total_usd": 0.0,
            "wave_count": 0,
            "confidence": 0.5,
        }

        if not liquidations:
            return result

        window = timedelta(minutes=self.params["liq_window_minutes"])
        settle_td = timedelta(seconds=self.params["min_settle_seconds"])
        wave_window = timedelta(minutes=self.params["wave_window_minutes"])

        # Parse timestamps
        parsed: List[Dict[str, Any]] = []
        for liq in liquidations:
            try:
                ts = liq.get("timestamp", "")
                if isinstance(ts, str):
                    ts = datetime.fromisoformat(ts)
                parsed.append({**liq, "dt": ts})
            except Exception:
                continue

        if not parsed:
            return result

        # Last spike window
        cutoff = now - window
        recent = [l for l in parsed if l["dt"] >= cutoff]
        total_usd = sum(float(l.get("usd", 0)) for l in recent)
        result["total_usd"] = total_usd

        if total_usd < self.params["liq_threshold_usd"]:
            return result

        result["spike_detected"] = True

        # Check if settled: no new liquidations in last min_settle_seconds
        if parsed:
            latest_liq = max(l["dt"] for l in parsed)
            if (now - latest_liq) >= settle_td:
                result["settled"] = True

        # Determine dominant liquidation side
        long_liqs = sum(float(l.get("usd", 0)) for l in recent if l.get("side") == "long")
        short_liqs = sum(float(l.get("usd", 0)) for l in recent if l.get("side") == "short")

        # Detect cascade waves over longer window
        wave_cutoff = now - wave_window
        wave_data = [l for l in parsed if l["dt"] >= wave_cutoff]
        wave_count = self._count_waves(wave_data, self.params["wave_usd_threshold"])
        result["wave_count"] = wave_count

        if wave_count >= self.params["cascade_waves"] and long_liqs > short_liqs:
            result["setup_type"] = "momentum_short"
            result["direction"] = SignalDirection.SHORT
            result["confidence"] = min(0.85, 0.5 + wave_count * 0.1)
        elif result["settled"] and long_liqs > short_liqs:
            # Longs got liquidated → potential bounce for longs
            result["setup_type"] = "bounce"
            result["direction"] = SignalDirection.LONG
            result["confidence"] = min(0.80, total_usd / self.params["liq_threshold_usd"] * 0.3)
        elif result["settled"] and short_liqs > long_liqs:
            result["setup_type"] = "bounce"
            result["direction"] = SignalDirection.SHORT
            result["confidence"] = 0.55

        return result

    @staticmethod
    def _count_waves(liquidations: List[Dict[str, Any]], threshold: float) -> int:
        """Count distinct $-volume waves above threshold."""
        if not liquidations:
            return 0
        # Sort by time and bucket into 1-minute bins
        sorted_liqs = sorted(liquidations, key=lambda x: x["dt"])
        waves = 0
        bucket_usd = 0.0
        bucket_start = sorted_liqs[0]["dt"] if sorted_liqs else datetime.utcnow()
        bucket_duration = timedelta(minutes=1)

        for liq in sorted_liqs:
            if liq["dt"] - bucket_start > bucket_duration:
                if bucket_usd >= threshold:
                    waves += 1
                bucket_usd = float(liq.get("usd", 0))
                bucket_start = liq["dt"]
            else:
                bucket_usd += float(liq.get("usd", 0))

        if bucket_usd >= threshold:
            waves += 1

        return waves

    @staticmethod
    def _find_support(lows: np.ndarray, lookback: int) -> float:
        """Identify key support level as the strongest low in lookback bars."""
        window = lows[-lookback:]
        if len(window) == 0:
            return 0.0
        # Use the 10th percentile of lows as support
        return float(np.percentile(window, 10))

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")
