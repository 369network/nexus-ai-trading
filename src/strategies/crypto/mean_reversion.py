"""
Crypto Mean Reversion Strategy for NEXUS ALPHA.

Entry conditions:
    1. RSI(14) < 28
    2. Price at or below lower Bollinger Band (20, 2.0)
    3. Volume declining (current volume < 20-period average)
    4. Bullish divergence on RSI or MACD histogram
    5. Higher timeframe (daily) must NOT be in downtrend

Exit conditions:
    - RSI crosses above 55
    - Price reaches Bollinger Band midline (20 SMA)
    - Stop: entry - 1.5 * ATR (tight)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

import numpy as np

from src.strategies.base_strategy import (
    BaseStrategy,
    SignalDirection,
    SignalStrength,
    TradeSignal,
)

logger = logging.getLogger(__name__)


class CryptoMeanReversionStrategy(BaseStrategy):
    """
    Mean reversion strategy for cryptocurrency markets.

    Looks for oversold conditions at the lower Bollinger Band
    with bullish RSI/MACD divergence as confirmation, only when the
    higher timeframe is not in a sustained downtrend.

    Default Parameters
    ------------------
    rsi_period : int        14
    rsi_oversold : float    28.0
    rsi_exit : float        55.0
    bb_period : int         20
    bb_std : float          2.0
    atr_period : int        14
    atr_stop_mult : float   1.5
    divergence_lookback : int  5  bars to check for divergence
    daily_sma_period : int  50   for trend confirmation on daily
    base_size_pct : float   0.015
    """

    DEFAULT_PARAMS: Dict[str, Any] = {
        "rsi_period": 14,
        "rsi_oversold": 28.0,
        "rsi_exit": 55.0,
        "bb_period": 20,
        "bb_std": 2.0,
        "atr_period": 14,
        "atr_stop_mult": 1.5,
        "divergence_lookback": 5,
        "daily_sma_period": 50,
        "base_size_pct": 0.015,
    }

    def __init__(self, params: Optional[Dict[str, Any]] = None) -> None:
        merged = {**self.DEFAULT_PARAMS, **(params or {})}
        super().__init__(
            name="CryptoMeanReversion",
            market="crypto",
            primary_timeframe="1h",
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
        daily_ohlcv = market_data.get("daily_ohlcv")

        if ohlcv is None:
            return None

        try:
            closes = self._to_array(ohlcv, "close")
            highs = self._to_array(ohlcv, "high")
            lows = self._to_array(ohlcv, "low")
            volumes = self._to_array(ohlcv, "volume")
        except (KeyError, TypeError) as exc:
            logger.error("[%s] OHLCV error: %s", self.name, exc)
            return None

        min_bars = max(self.params["bb_period"], self.params["rsi_period"] + 1, 50)
        if len(closes) < min_bars:
            logger.debug("[%s] Insufficient bars for %s", self.name, symbol)
            return None

        # Compute indicators
        rsi = self.compute_rsi(closes, self.params["rsi_period"])
        bb_upper, bb_mid, bb_lower = self.compute_bollinger_bands(
            closes, self.params["bb_period"], self.params["bb_std"]
        )
        atr = self.compute_atr(highs, lows, closes, self.params["atr_period"])
        _, _, macd_hist = self.compute_macd(closes)

        # Higher TF trend check
        daily_trend_down = False
        if daily_ohlcv is not None:
            try:
                daily_closes = self._to_array(daily_ohlcv, "close")
                if len(daily_closes) >= self.params["daily_sma_period"]:
                    daily_sma = self.compute_sma(daily_closes, self.params["daily_sma_period"])
                    # Downtrend: price below SMA and SMA sloping down
                    if daily_closes[-1] < daily_sma[-1] and daily_sma[-1] < daily_sma[-5]:
                        daily_trend_down = True
            except Exception as exc:
                logger.warning("[%s] Daily OHLCV error: %s", self.name, exc)

        indicators = {
            "rsi": float(rsi[-1]) if not np.isnan(rsi[-1]) else 100.0,
            "bb_lower": float(bb_lower[-1]) if not np.isnan(bb_lower[-1]) else 0.0,
            "bb_mid": float(bb_mid[-1]) if not np.isnan(bb_mid[-1]) else 0.0,
            "close": float(closes[-1]),
            "atr": float(atr[-1]) if not np.isnan(atr[-1]) else 0.0,
            "volume_current": float(volumes[-1]),
            "volume_avg": float(np.mean(volumes[-self.params["bb_period"]:])),
            "macd_hist": list(macd_hist[-self.params["divergence_lookback"]:]),
            "rsi_series": list(rsi[-self.params["divergence_lookback"]:]),
            "close_series": list(closes[-self.params["divergence_lookback"]:]),
            "daily_trend_down": daily_trend_down,
        }

        full_data = {**market_data, "indicators": indicators}
        if not self.check_entry_conditions(full_data):
            return None

        entry_price = indicators["close"]
        atr_val = indicators["atr"]
        stop_loss = entry_price - atr_val * self.params["atr_stop_mult"]
        risk = entry_price - stop_loss
        tp1 = indicators["bb_mid"]  # Bollinger midline
        tp2 = tp1 + risk * 0.5

        signal = TradeSignal(
            strategy_name=self.name,
            market=self.market,
            symbol=symbol,
            direction=SignalDirection.LONG,
            strength=SignalStrength.MODERATE,
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit_1=tp1,
            take_profit_2=tp2,
            take_profit_3=tp2 + risk,
            size_pct=self.params["base_size_pct"],
            timeframe=self.primary_timeframe,
            confidence=0.65,
            metadata={
                "rsi": indicators["rsi"],
                "bb_lower": indicators["bb_lower"],
                "bb_mid": indicators["bb_mid"],
                "atr": atr_val,
            },
        )
        self.record_signal(signal)
        return signal

    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        ind = market_data.get("indicators", {})

        # Gate: higher TF must not be in downtrend
        if ind.get("daily_trend_down", False):
            logger.debug("[%s] Cond0 FAIL: daily trend is DOWN", self.name)
            return False

        passed = True

        # 1. RSI < 28
        rsi = ind.get("rsi", 100.0)
        if rsi >= self.params["rsi_oversold"]:
            logger.debug("[%s] Cond1 FAIL: RSI %.2f >= %.2f", self.name, rsi, self.params["rsi_oversold"])
            passed = False

        # 2. Price at or below lower BB
        close = ind.get("close", float("inf"))
        bb_lower = ind.get("bb_lower", 0.0)
        if close > bb_lower * 1.001:  # 0.1% tolerance
            logger.debug("[%s] Cond2 FAIL: close %.4f > BB_lower %.4f", self.name, close, bb_lower)
            passed = False

        # 3. Volume declining
        vol_current = ind.get("volume_current", float("inf"))
        vol_avg = ind.get("volume_avg", 0.0)
        if vol_avg > 0 and vol_current >= vol_avg:
            logger.debug("[%s] Cond3 FAIL: volume not declining", self.name)
            passed = False

        # 4. Bullish divergence on RSI or MACD
        rsi_series = ind.get("rsi_series", [])
        close_series = ind.get("close_series", [])
        macd_hist = ind.get("macd_hist", [])
        divergence = self._check_bullish_divergence(close_series, rsi_series, macd_hist)
        if not divergence:
            logger.debug("[%s] Cond4 FAIL: no bullish divergence detected", self.name)
            passed = False

        return passed

    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        ind = market_data.get("indicators", {})
        close = ind.get("close", position.get("entry_price", 0.0))
        rsi = ind.get("rsi", 50.0)
        bb_mid = ind.get("bb_mid", position.get("take_profit_1", float("inf")))
        stop_loss = position.get("stop_loss", 0.0)

        if close <= stop_loss:
            return "stop_loss_hit"

        if rsi >= self.params["rsi_exit"]:
            return "rsi_exit_55"

        if close >= bb_mid:
            return "bb_midline_reached"

        return None

    def validate_params(self, params: Dict[str, Any]) -> bool:
        constraints = {
            "rsi_period": (int, 5, 30),
            "rsi_oversold": (float, 10.0, 35.0),
            "rsi_exit": (float, 45.0, 75.0),
            "bb_period": (int, 10, 50),
            "bb_std": (float, 1.0, 4.0),
            "atr_period": (int, 5, 30),
            "atr_stop_mult": (float, 0.5, 3.0),
            "divergence_lookback": (int, 3, 20),
            "daily_sma_period": (int, 20, 200),
            "base_size_pct": (float, 0.001, 0.1),
        }
        for key, (typ, lo, hi) in constraints.items():
            val = params.get(key)
            if val is None:
                continue
            try:
                val = typ(val)
            except (TypeError, ValueError):
                logger.error("[%s] Param %s wrong type", self.name, key)
                return False
            if not (lo <= val <= hi):
                logger.error("[%s] Param %s=%s out of range", self.name, key, val)
                return False
        return True

    def backtest_metric(self) -> Dict[str, Any]:
        return self._build_backtest_metric_from_history().to_dict()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _to_array(ohlcv: Any, col: str) -> np.ndarray:
        if hasattr(ohlcv, "__getitem__"):
            data = ohlcv[col]
            if hasattr(data, "values"):
                return data.values.astype(float)
            return np.array(data, dtype=float)
        raise TypeError(f"Cannot extract '{col}' from {type(ohlcv)}")

    def _check_bullish_divergence(
        self,
        prices: List[float],
        rsi_vals: List[float],
        macd_hist_vals: List[float],
    ) -> bool:
        """
        Detect bullish divergence: price makes lower low while
        RSI or MACD histogram makes higher low.
        """
        if len(prices) < 3 or len(rsi_vals) < 3:
            return False

        # Find recent price low vs earlier price low
        price_arr = np.array(prices, dtype=float)
        if np.any(np.isnan(price_arr)):
            return False

        mid = len(price_arr) // 2
        price_early_low = np.min(price_arr[:mid])
        price_recent_low = np.min(price_arr[mid:])

        # Price must make lower low
        if price_recent_low >= price_early_low:
            return False

        # RSI divergence check
        rsi_arr = np.array(rsi_vals, dtype=float)
        valid_rsi = rsi_arr[~np.isnan(rsi_arr)]
        if len(valid_rsi) >= 4:
            rsi_early_low = np.min(valid_rsi[:len(valid_rsi) // 2])
            rsi_recent_low = np.min(valid_rsi[len(valid_rsi) // 2:])
            if rsi_recent_low > rsi_early_low:
                return True  # RSI bullish divergence

        # MACD histogram divergence check
        macd_arr = np.array(macd_hist_vals, dtype=float)
        valid_macd = macd_arr[~np.isnan(macd_arr)]
        if len(valid_macd) >= 4:
            macd_early_low = np.min(valid_macd[:len(valid_macd) // 2])
            macd_recent_low = np.min(valid_macd[len(valid_macd) // 2:])
            if macd_recent_low > macd_early_low:
                return True  # MACD bullish divergence

        return False
