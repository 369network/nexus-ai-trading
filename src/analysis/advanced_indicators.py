# src/analysis/advanced_indicators.py
"""Advanced technical indicators for NEXUS ALPHA."""

from __future__ import annotations

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Ichimoku Cloud
# ---------------------------------------------------------------------------

def ichimoku(
    df: pd.DataFrame,
    tenkan_period: int = 9,
    kijun_period: int = 26,
    senkou_b_period: int = 52,
    displacement: int = 26,
) -> pd.DataFrame:
    """Compute Ichimoku Cloud components.

    Returns DataFrame with columns:
        tenkan, kijun, senkou_a, senkou_b, chikou
    """
    h = df["high"]
    lo = df["low"]
    c = df["close"]

    tenkan = (h.rolling(tenkan_period).max() + lo.rolling(tenkan_period).min()) / 2
    kijun = (h.rolling(kijun_period).max() + lo.rolling(kijun_period).min()) / 2
    senkou_a = ((tenkan + kijun) / 2).shift(displacement)
    senkou_b = (
        (h.rolling(senkou_b_period).max() + lo.rolling(senkou_b_period).min()) / 2
    ).shift(displacement)
    chikou = c.shift(-displacement)

    return pd.DataFrame(
        {
            "tenkan": tenkan,
            "kijun": kijun,
            "senkou_a": senkou_a,
            "senkou_b": senkou_b,
            "chikou": chikou,
        },
        index=df.index,
    )


# ---------------------------------------------------------------------------
# Supertrend
# ---------------------------------------------------------------------------

def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """Compute the Supertrend indicator.

    Returns DataFrame with columns:
        supertrend  – price level
        direction   – 1 (uptrend) or -1 (downtrend)
    """
    h = df["high"].values
    lo = df["low"].values
    c = df["close"].values
    n = len(c)

    # True range
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))

    # Smooth ATR with Wilder's method
    atr = np.zeros(n)
    atr[period - 1] = tr[:period].mean()
    for i in range(period, n):
        atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period

    # Basic upper/lower bands
    hl2 = (h + lo) / 2
    basic_upper = hl2 + multiplier * atr
    basic_lower = hl2 - multiplier * atr

    final_upper = np.zeros(n)
    final_lower = np.zeros(n)
    direction = np.ones(n, dtype=int)  # 1 = uptrend
    st = np.zeros(n)

    final_upper[0] = basic_upper[0]
    final_lower[0] = basic_lower[0]
    st[0] = final_upper[0]

    for i in range(1, n):
        final_upper[i] = (
            basic_upper[i]
            if (basic_upper[i] < final_upper[i - 1] or c[i - 1] > final_upper[i - 1])
            else final_upper[i - 1]
        )
        final_lower[i] = (
            basic_lower[i]
            if (basic_lower[i] > final_lower[i - 1] or c[i - 1] < final_lower[i - 1])
            else final_lower[i - 1]
        )

        if st[i - 1] == final_upper[i - 1]:
            direction[i] = -1 if c[i] > final_upper[i] else 1
        else:
            direction[i] = 1 if c[i] < final_lower[i] else -1

        st[i] = final_lower[i] if direction[i] == -1 else final_upper[i]

    return pd.DataFrame(
        {"supertrend": st, "direction": direction},
        index=df.index,
    )


# ---------------------------------------------------------------------------
# Squeeze Momentum (TTM Squeeze)
# ---------------------------------------------------------------------------

def squeeze_momentum(
    df: pd.DataFrame,
    bb_period: int = 20,
    bb_mult: float = 2.0,
    kc_period: int = 20,
    kc_mult: float = 1.5,
    mom_period: int = 12,
) -> pd.DataFrame:
    """John Carter's TTM Squeeze indicator.

    Returns DataFrame with columns:
        squeeze_on    – bool, Bollinger Bands inside Keltner Channels
        squeeze_off   – bool, Bollinger Bands outside Keltner Channels
        momentum_value – raw momentum histogram
    """
    h = df["high"]
    lo = df["low"]
    c = df["close"]

    # Bollinger Bands
    bb_mid = c.rolling(bb_period).mean()
    bb_std = c.rolling(bb_period).std()
    bb_upper = bb_mid + bb_mult * bb_std
    bb_lower = bb_mid - bb_mult * bb_std

    # Keltner Channels (using ATR)
    prev_c = c.shift(1).fillna(c)
    tr = pd.concat([h - lo, (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    kc_atr = tr.rolling(kc_period).mean()
    kc_upper = bb_mid + kc_mult * kc_atr
    kc_lower = bb_mid - kc_mult * kc_atr

    squeeze_on = (bb_upper < kc_upper) & (bb_lower > kc_lower)
    squeeze_off = (bb_upper >= kc_upper) & (bb_lower <= kc_lower)

    # Momentum — delta of close from midpoint of high/low/mid of range
    delta = c - ((h.rolling(mom_period).max() + lo.rolling(mom_period).min()) / 2 + bb_mid) / 2
    momentum = delta.rolling(mom_period).mean()

    return pd.DataFrame(
        {
            "squeeze_on": squeeze_on,
            "squeeze_off": squeeze_off,
            "momentum_value": momentum,
        },
        index=df.index,
    )


# ---------------------------------------------------------------------------
# Elder Ray Index
# ---------------------------------------------------------------------------

def elder_ray(df: pd.DataFrame, period: int = 13) -> pd.DataFrame:
    """Compute Elder Ray bull and bear power.

    Returns DataFrame with columns:
        bull_power, bear_power
    """
    ema = df["close"].ewm(span=period, adjust=False).mean()
    bull_power = df["high"] - ema
    bear_power = df["low"] - ema

    return pd.DataFrame(
        {"bull_power": bull_power, "bear_power": bear_power},
        index=df.index,
    )


# ---------------------------------------------------------------------------
# Heikin-Ashi
# ---------------------------------------------------------------------------

def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Convert standard OHLC to Heikin-Ashi candles.

    Returns DataFrame with columns:
        ha_open, ha_high, ha_low, ha_close
    """
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4
    ha_open = pd.Series(np.zeros(len(df)), index=df.index, dtype=float)
    ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2

    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2

    ha_high = pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([df["low"], ha_open, ha_close], axis=1).min(axis=1)

    return pd.DataFrame(
        {
            "ha_open": ha_open,
            "ha_high": ha_high,
            "ha_low": ha_low,
            "ha_close": ha_close,
        },
        index=df.index,
    )


# ---------------------------------------------------------------------------
# Donchian Channel
# ---------------------------------------------------------------------------

def donchian_channel(df: pd.DataFrame, period: int = 20) -> pd.DataFrame:
    """Compute Donchian Channel.

    Returns DataFrame with columns:
        dc_upper, dc_mid, dc_lower
    """
    dc_upper = df["high"].rolling(period).max()
    dc_lower = df["low"].rolling(period).min()
    dc_mid = (dc_upper + dc_lower) / 2

    return pd.DataFrame(
        {"dc_upper": dc_upper, "dc_mid": dc_mid, "dc_lower": dc_lower},
        index=df.index,
    )


# ---------------------------------------------------------------------------
# Keltner Channel
# ---------------------------------------------------------------------------

def keltner_channel(
    df: pd.DataFrame,
    ema_period: int = 20,
    atr_period: int = 10,
    multiplier: float = 2.0,
) -> pd.DataFrame:
    """Compute Keltner Channels using EMA and ATR.

    Returns DataFrame with columns:
        kc_upper, kc_mid, kc_lower
    """
    kc_mid = df["close"].ewm(span=ema_period, adjust=False).mean()

    h = df["high"]
    lo = df["low"]
    prev_c = df["close"].shift(1).fillna(df["close"])
    tr = pd.concat([(h - lo), (h - prev_c).abs(), (lo - prev_c).abs()], axis=1).max(axis=1)
    atr = tr.rolling(atr_period).mean()

    kc_upper = kc_mid + multiplier * atr
    kc_lower = kc_mid - multiplier * atr

    return pd.DataFrame(
        {"kc_upper": kc_upper, "kc_mid": kc_mid, "kc_lower": kc_lower},
        index=df.index,
    )
