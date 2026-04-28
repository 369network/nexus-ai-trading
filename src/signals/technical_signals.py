# src/signals/technical_signals.py
"""Technical signal generator — converts indicator data to a -1 to +1 score."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


class TechnicalSignalGenerator:
    """Generate a normalised technical signal from OHLCV + indicator data.

    The signal is a weighted sum of multiple sub-signals, each normalised
    to the range [-1.0, +1.0].
    """

    # Sub-signal weights (must sum to 1.0)
    _WEIGHTS = {
        "trend": 0.30,      # MA stack alignment
        "momentum": 0.25,   # RSI + MACD + Stochastic
        "volume": 0.15,     # Volume ratio + OBV trend
        "bb_position": 0.15, # Bollinger Band %B
        "atr_trend": 0.15,  # ATR direction (volatility expanding/contracting)
    }

    def generate(
        self,
        df: pd.DataFrame,
        market: str,
        symbol: str,
    ) -> float:
        """Generate a technical signal score for the current bar.

        Parameters
        ----------
        df:
            OHLCV DataFrame with pre-computed indicator columns.
        market:
            Market type (used for market-specific adjustments).
        symbol:
            Trading symbol (for logging).

        Returns
        -------
        float
            -1.0 (strong bearish) to +1.0 (strong bullish).
        """
        if len(df) < 20:
            logger.warning("Insufficient bars for technical signal: %s", symbol)
            return 0.0

        sub_signals = {
            "trend": self._trend_signal(df),
            "momentum": self._momentum_signal(df),
            "volume": self._volume_signal(df),
            "bb_position": self._bb_signal(df),
            "atr_trend": self._atr_signal(df),
        }

        weighted_sum = sum(
            sub_signals[name] * self._WEIGHTS[name]
            for name in sub_signals
        )

        logger.debug(
            "TechnicalSignal %s: %s → fused=%.3f",
            symbol,
            {k: f"{v:.2f}" for k, v in sub_signals.items()},
            weighted_sum,
        )

        return max(-1.0, min(1.0, weighted_sum))

    # ------------------------------------------------------------------
    # Sub-signal components
    # ------------------------------------------------------------------

    def _trend_signal(self, df: pd.DataFrame) -> float:
        """MA stack alignment signal.

        +1 = price > SMA20 > SMA50 > SMA200 (fully bullish stack)
        -1 = price < SMA20 < SMA50 < SMA200 (fully bearish stack)
        """
        close = float(df["close"].iloc[-1])
        score = 0.0
        max_score = 0.0

        for col, weight in [("sma20", 1.0), ("sma50", 0.8), ("sma200", 1.2)]:
            if col in df.columns:
                val = df[col].iloc[-1]
                if pd.notna(val) and float(val) > 0:
                    max_score += weight
                    score += weight if close > float(val) else -weight

        # EMA confirmation
        if "ema9" in df.columns and "ema21" in df.columns:
            e9 = df["ema9"].iloc[-1]
            e21 = df["ema21"].iloc[-1]
            if pd.notna(e9) and pd.notna(e21):
                max_score += 0.5
                score += 0.5 if float(e9) > float(e21) else -0.5

        return score / max_score if max_score > 0 else 0.0

    def _momentum_signal(self, df: pd.DataFrame) -> float:
        """RSI + MACD + Stochastic composite momentum signal."""
        signals = []

        # RSI (14): <30 oversold (bullish), >70 overbought (bearish)
        rsi = self._get_last(df, "rsi14", 50)
        if rsi <= 20:
            signals.append(1.0)
        elif rsi <= 30:
            signals.append(0.6)
        elif rsi <= 45:
            signals.append(0.3)
        elif rsi >= 80:
            signals.append(-1.0)
        elif rsi >= 70:
            signals.append(-0.6)
        elif rsi >= 55:
            signals.append(-0.3)
        else:
            # 45–55: small trend signal from direction
            signals.append((rsi - 50) / 50 * 0.3)  # [-0.3, +0.3]

        # MACD histogram direction
        macd_hist = self._get_last(df, "macd_hist", 0)
        macd_hist_prev = self._get_nth_last(df, "macd_hist", 2, 0)
        if macd_hist > 0 and macd_hist > macd_hist_prev:
            signals.append(0.7)  # positive and growing
        elif macd_hist > 0:
            signals.append(0.3)  # positive but shrinking
        elif macd_hist < 0 and macd_hist < macd_hist_prev:
            signals.append(-0.7)  # negative and growing
        elif macd_hist < 0:
            signals.append(-0.3)

        # Stochastic K vs D crossover
        stoch_k = self._get_last(df, "stoch_k", 50)
        stoch_d = self._get_last(df, "stoch_d", 50)
        if stoch_k < 20:
            signals.append(0.5)  # oversold zone
        elif stoch_k > 80:
            signals.append(-0.5)  # overbought zone
        elif stoch_k > stoch_d:
            signals.append(0.2)  # K above D = bullish crossover
        else:
            signals.append(-0.2)

        return sum(signals) / len(signals) if signals else 0.0

    def _volume_signal(self, df: pd.DataFrame) -> float:
        """Volume ratio and OBV trend signal."""
        signals = []

        # Volume ratio: high volume on up-moves = bullish
        vol_ratio = self._get_last(df, "volume_ratio", 1.0)
        close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-2]) if len(df) >= 2 else close
        is_up_bar = close > prev_close

        if vol_ratio > 2.0:
            signals.append(0.7 if is_up_bar else -0.7)  # high volume directional
        elif vol_ratio > 1.5:
            signals.append(0.4 if is_up_bar else -0.4)
        elif vol_ratio < 0.5:
            signals.append(0.0)  # low volume = indecisive
        else:
            signals.append(0.1 if is_up_bar else -0.1)

        # OBV slope (rising = buying pressure)
        if "obv" in df.columns and len(df) >= 5:
            obv_recent = df["obv"].iloc[-5:].values
            if not any(np.isnan(obv_recent)):
                obv_slope = np.polyfit(range(5), obv_recent, 1)[0]
                max_obv = max(abs(obv_recent.max()), abs(obv_recent.min()), 1)
                normalised_slope = np.clip(obv_slope * 5 / max_obv, -1, 1)
                signals.append(float(normalised_slope))

        return sum(signals) / len(signals) if signals else 0.0

    def _bb_signal(self, df: pd.DataFrame) -> float:
        """Bollinger Band %B position signal.

        %B < 0.1 = near lower band → bullish
        %B > 0.9 = near upper band → bearish
        %B around 0.5 → neutral
        """
        bb_pct = self._get_last(df, "bb_pct", 0.5)
        bb_pct = max(0.0, min(1.0, bb_pct))

        if bb_pct <= 0.05:
            return 1.0
        elif bb_pct <= 0.2:
            return 0.6
        elif bb_pct <= 0.35:
            return 0.2
        elif bb_pct >= 0.95:
            return -1.0
        elif bb_pct >= 0.8:
            return -0.6
        elif bb_pct >= 0.65:
            return -0.2
        else:
            return 0.0

    def _atr_signal(self, df: pd.DataFrame) -> float:
        """ATR trend signal.

        Rising ATR + price up = strong bullish trend
        Rising ATR + price down = strong bearish trend
        Falling ATR = consolidation → weak signal
        """
        if "atr14" not in df.columns or len(df) < 5:
            return 0.0

        atr_recent = df["atr14"].iloc[-5:].dropna().values
        if len(atr_recent) < 2:
            return 0.0

        atr_slope = float(atr_recent[-1]) - float(atr_recent[0])
        close = float(df["close"].iloc[-1])
        prev_close = float(df["close"].iloc[-5]) if len(df) >= 5 else close
        price_up = close > prev_close

        if atr_slope > 0:
            return 0.4 if price_up else -0.4  # volatility expanding in trend direction
        else:
            return 0.0  # consolidation — no directional signal

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    @staticmethod
    def _get_last(df: pd.DataFrame, col: str, default: float) -> float:
        if col not in df.columns:
            return default
        val = df[col].iloc[-1]
        return float(val) if pd.notna(val) else default

    @staticmethod
    def _get_nth_last(
        df: pd.DataFrame, col: str, n: int, default: float
    ) -> float:
        if col not in df.columns or len(df) < n:
            return default
        val = df[col].iloc[-n]
        return float(val) if pd.notna(val) else default
