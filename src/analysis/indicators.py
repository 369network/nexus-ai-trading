# src/analysis/indicators.py
"""Technical indicator computation for NEXUS ALPHA.

Uses TA-Lib for C-backed indicators where available, with pandas-ta as fallback.
All functions accept a standard OHLCV DataFrame and return it with added columns.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Try to import TA-Lib (faster, C-backed)
try:
    import talib
    _TALIB_AVAILABLE = True
    logger.debug("TA-Lib available — using C-backed indicators")
except ImportError:
    _TALIB_AVAILABLE = False
    logger.warning("TA-Lib not available — falling back to pandas-ta")

# Try pandas-ta as fallback
try:
    import pandas_ta as ta
    _PANDAS_TA_AVAILABLE = True
except ImportError:
    _PANDAS_TA_AVAILABLE = False
    logger.warning("pandas-ta not available — some indicators will be NaN")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def compute_indicators(
    df: pd.DataFrame,
    config: Optional[Dict[str, Any]] = None,
) -> pd.DataFrame:
    """Compute all standard technical indicators and append them to *df*.

    Parameters
    ----------
    df:
        OHLCV DataFrame with columns: open, high, low, close, volume.
        Index should be a DatetimeIndex.
    config:
        Optional dict with override parameters, e.g. ``{"rsi_period": 14}``.

    Returns
    -------
    pd.DataFrame
        Original DataFrame with added indicator columns.  Never modifies input
        in-place; returns a copy.
    """
    cfg = config or {}
    df = df.copy()

    # Ensure column names are lowercase
    df.columns = [c.lower() for c in df.columns]

    # Extract numpy arrays once for performance
    o = df["open"].values.astype(float)
    h = df["high"].values.astype(float)
    lo = df["low"].values.astype(float)
    c = df["close"].values.astype(float)
    v = df["volume"].values.astype(float)

    df = _add_rsi(df, c, cfg)
    df = _add_macd(df, c, cfg)
    df = _add_bollinger(df, c, cfg)
    df = _add_sma(df, c)
    df = _add_ema(df, c)
    df = _add_atr(df, h, lo, c, cfg)
    df = _add_volume_ratio(df, v, cfg)
    df = _add_vwap(df, h, lo, c, v)
    df = _add_stochastic(df, h, lo, c, cfg)
    df = _add_williams_r(df, h, lo, c, cfg)
    df = _add_cci(df, h, lo, c, cfg)
    df = _add_mfi(df, h, lo, c, v, cfg)
    df = _add_obv(df, c, v)
    df = _add_adx(df, h, lo, c, cfg)
    df = _add_price_vs_ma(df)

    return df


# ---------------------------------------------------------------------------
# Individual indicator functions
# ---------------------------------------------------------------------------

def _add_rsi(df: pd.DataFrame, c: np.ndarray, cfg: dict) -> pd.DataFrame:
    for period in [cfg.get("rsi_period", 14), 7, 21]:
        col = f"rsi{period}"
        if _TALIB_AVAILABLE:
            df[col] = talib.RSI(c, timeperiod=period)
        elif _PANDAS_TA_AVAILABLE:
            df[col] = ta.rsi(pd.Series(c), length=period).values
        else:
            df[col] = _rsi_manual(c, period)
    return df


def _add_macd(df: pd.DataFrame, c: np.ndarray, cfg: dict) -> pd.DataFrame:
    fast = cfg.get("macd_fast", 12)
    slow = cfg.get("macd_slow", 26)
    signal = cfg.get("macd_signal", 9)

    if _TALIB_AVAILABLE:
        macd, macd_sig, macd_hist = talib.MACD(c, fastperiod=fast, slowperiod=slow, signalperiod=signal)
        df["macd_line"] = macd
        df["macd_signal"] = macd_sig
        df["macd_hist"] = macd_hist
    elif _PANDAS_TA_AVAILABLE:
        result = ta.macd(pd.Series(c), fast=fast, slow=slow, signal=signal)
        if result is not None:
            df["macd_line"] = result.iloc[:, 0].values
            df["macd_signal"] = result.iloc[:, 2].values
            df["macd_hist"] = result.iloc[:, 1].values
        else:
            df[["macd_line", "macd_signal", "macd_hist"]] = np.nan
    else:
        ema_fast = _ema_manual(c, fast)
        ema_slow = _ema_manual(c, slow)
        macd_line = ema_fast - ema_slow
        df["macd_line"] = macd_line
        df["macd_signal"] = _ema_manual(macd_line, signal)
        df["macd_hist"] = df["macd_line"] - df["macd_signal"]
    return df


def _add_bollinger(df: pd.DataFrame, c: np.ndarray, cfg: dict) -> pd.DataFrame:
    period = cfg.get("bb_period", 20)
    std_mult = cfg.get("bb_std", 2)

    if _TALIB_AVAILABLE:
        upper, mid, lower = talib.BBANDS(c, timeperiod=period, nbdevup=std_mult, nbdevdn=std_mult)
        df["bb_upper"] = upper
        df["bb_mid"] = mid
        df["bb_lower"] = lower
    else:
        s = pd.Series(c)
        df["bb_mid"] = s.rolling(period).mean().values
        std = s.rolling(period).std().values
        df["bb_upper"] = df["bb_mid"] + std_mult * std
        df["bb_lower"] = df["bb_mid"] - std_mult * std

    df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / df["bb_mid"]
    bb_range = df["bb_upper"] - df["bb_lower"]
    df["bb_pct"] = pd.Series(c, index=df.index).sub(df["bb_lower"]).div(bb_range.replace(0, np.nan))
    return df


def _add_sma(df: pd.DataFrame, c: np.ndarray) -> pd.DataFrame:
    s = pd.Series(c)
    for period in [20, 50, 200]:
        if _TALIB_AVAILABLE:
            df[f"sma{period}"] = talib.SMA(c, timeperiod=period)
        else:
            df[f"sma{period}"] = s.rolling(period).mean().values
    return df


def _add_ema(df: pd.DataFrame, c: np.ndarray) -> pd.DataFrame:
    s = pd.Series(c)
    for period in [9, 21, 55]:
        if _TALIB_AVAILABLE:
            df[f"ema{period}"] = talib.EMA(c, timeperiod=period)
        else:
            df[f"ema{period}"] = s.ewm(span=period, adjust=False).mean().values
    return df


def _add_atr(
    df: pd.DataFrame,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cfg: dict,
) -> pd.DataFrame:
    for period in [cfg.get("atr_period", 14), 7]:
        if _TALIB_AVAILABLE:
            df[f"atr{period}"] = talib.ATR(h, lo, c, timeperiod=period)
        elif _PANDAS_TA_AVAILABLE:
            result = ta.atr(pd.Series(h), pd.Series(lo), pd.Series(c), length=period)
            df[f"atr{period}"] = result.values if result is not None else np.nan
        else:
            df[f"atr{period}"] = _atr_manual(h, lo, c, period)
    return df


def _add_volume_ratio(
    df: pd.DataFrame, v: np.ndarray, cfg: dict
) -> pd.DataFrame:
    period = cfg.get("vol_ratio_period", 20)
    avg_vol = pd.Series(v).rolling(period).mean().values
    df["volume_ratio"] = np.where(avg_vol > 0, v / avg_vol, 1.0)
    df["avg_volume"] = avg_vol
    return df


def _add_vwap(
    df: pd.DataFrame,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    v: np.ndarray,
) -> pd.DataFrame:
    """Intraday VWAP that resets daily."""
    typical = (h + lo + c) / 3
    tp_vol = typical * v

    if isinstance(df.index, pd.DatetimeIndex):
        dates = df.index.date
        cum_tpvol = np.zeros(len(df))
        cum_vol = np.zeros(len(df))

        current_date = None
        running_tpvol = 0.0
        running_vol = 0.0

        for i, d in enumerate(dates):
            if d != current_date:
                current_date = d
                running_tpvol = 0.0
                running_vol = 0.0
            running_tpvol += tp_vol[i]
            running_vol += v[i]
            cum_tpvol[i] = running_tpvol
            cum_vol[i] = running_vol

        df["vwap"] = np.where(cum_vol > 0, cum_tpvol / cum_vol, c)
    else:
        # No date index — compute simple rolling VWAP
        cumtpv = pd.Series(tp_vol).cumsum().values
        cumv = pd.Series(v).cumsum().values
        df["vwap"] = np.where(cumv > 0, cumtpv / cumv, c)
    return df


def _add_stochastic(
    df: pd.DataFrame,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cfg: dict,
) -> pd.DataFrame:
    k_period = cfg.get("stoch_k", 14)
    d_period = cfg.get("stoch_d", 3)

    if _TALIB_AVAILABLE:
        k, d = talib.STOCH(h, lo, c, fastk_period=k_period, slowk_period=d_period, slowd_period=d_period)
        df["stoch_k"] = k
        df["stoch_d"] = d
    elif _PANDAS_TA_AVAILABLE:
        result = ta.stoch(pd.Series(h), pd.Series(lo), pd.Series(c), k=k_period, d=d_period)
        if result is not None and not result.empty:
            df["stoch_k"] = result.iloc[:, 0].values
            df["stoch_d"] = result.iloc[:, 1].values
        else:
            df["stoch_k"] = df["stoch_d"] = np.nan
    else:
        low_min = pd.Series(lo).rolling(k_period).min().values
        high_max = pd.Series(h).rolling(k_period).max().values
        k = np.where((high_max - low_min) > 0, (c - low_min) / (high_max - low_min) * 100, 50)
        df["stoch_k"] = k
        df["stoch_d"] = pd.Series(k).rolling(d_period).mean().values
    return df


def _add_williams_r(
    df: pd.DataFrame,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cfg: dict,
) -> pd.DataFrame:
    period = cfg.get("willr_period", 14)
    if _TALIB_AVAILABLE:
        df["williams_r"] = talib.WILLR(h, lo, c, timeperiod=period)
    else:
        high_max = pd.Series(h).rolling(period).max().values
        low_min = pd.Series(lo).rolling(period).min().values
        df["williams_r"] = np.where(
            (high_max - low_min) > 0,
            (high_max - c) / (high_max - low_min) * -100,
            -50,
        )
    return df


def _add_cci(
    df: pd.DataFrame,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cfg: dict,
) -> pd.DataFrame:
    period = cfg.get("cci_period", 20)
    if _TALIB_AVAILABLE:
        df["cci"] = talib.CCI(h, lo, c, timeperiod=period)
    else:
        typical = (h + lo + c) / 3
        tp_series = pd.Series(typical)
        tp_mean = tp_series.rolling(period).mean()
        tp_std = tp_series.rolling(period).std()
        df["cci"] = ((tp_series - tp_mean) / (0.015 * tp_std)).values
    return df


def _add_mfi(
    df: pd.DataFrame,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    v: np.ndarray,
    cfg: dict,
) -> pd.DataFrame:
    period = cfg.get("mfi_period", 14)
    if _TALIB_AVAILABLE:
        df["mfi"] = talib.MFI(h, lo, c, v, timeperiod=period)
    elif _PANDAS_TA_AVAILABLE:
        result = ta.mfi(pd.Series(h), pd.Series(lo), pd.Series(c), pd.Series(v), length=period)
        df["mfi"] = result.values if result is not None else np.nan
    else:
        typical = (h + lo + c) / 3
        money_flow = typical * v
        # Positive / negative money flow
        direction = np.sign(np.diff(typical, prepend=typical[0]))
        pos_flow = np.where(direction > 0, money_flow, 0.0)
        neg_flow = np.where(direction <= 0, money_flow, 0.0)
        pos_sum = pd.Series(pos_flow).rolling(period).sum().values
        neg_sum = pd.Series(neg_flow).rolling(period).sum().values
        # Guard against divide-by-zero when neg_sum == 0 (all positive flow)
        with np.errstate(divide='ignore', invalid='ignore'):
            mfr = np.where(neg_sum > 0, pos_sum / neg_sum, 100.0)
        df["mfi"] = 100 - 100 / (1 + mfr)
    return df


def _add_obv(
    df: pd.DataFrame, c: np.ndarray, v: np.ndarray
) -> pd.DataFrame:
    if _TALIB_AVAILABLE:
        df["obv"] = talib.OBV(c, v)
    else:
        direction = np.sign(np.diff(c, prepend=c[0]))
        signed_vol = direction * v
        df["obv"] = np.cumsum(signed_vol)
    return df


def _add_adx(
    df: pd.DataFrame,
    h: np.ndarray,
    lo: np.ndarray,
    c: np.ndarray,
    cfg: dict,
) -> pd.DataFrame:
    period = cfg.get("adx_period", 14)
    if _TALIB_AVAILABLE:
        df["adx"] = talib.ADX(h, lo, c, timeperiod=period)
        df["di_plus"] = talib.PLUS_DI(h, lo, c, timeperiod=period)
        df["di_minus"] = talib.MINUS_DI(h, lo, c, timeperiod=period)
    elif _PANDAS_TA_AVAILABLE:
        result = ta.adx(pd.Series(h), pd.Series(lo), pd.Series(c), length=period)
        if result is not None and not result.empty:
            df["adx"] = result.iloc[:, 0].values
            df["di_plus"] = result.iloc[:, 1].values
            df["di_minus"] = result.iloc[:, 2].values
        else:
            df["adx"] = df["di_plus"] = df["di_minus"] = np.nan
    else:
        # Simplified ADX manual calculation
        df["adx"] = np.nan
        df["di_plus"] = np.nan
        df["di_minus"] = np.nan
    return df


def _add_price_vs_ma(df: pd.DataFrame) -> pd.DataFrame:
    """Add percentage deviation of close from key moving averages."""
    c = df["close"].values
    for period in [20, 50, 200]:
        ma_col = f"sma{period}"
        if ma_col in df.columns:
            ma = df[ma_col].values
            df[f"price_vs_sma{period}"] = np.where(
                ma > 0, (c - ma) / ma * 100, 0
            )
    return df


# ---------------------------------------------------------------------------
# Manual fallback implementations (pure numpy/pandas)
# ---------------------------------------------------------------------------

def _rsi_manual(c: np.ndarray, period: int) -> np.ndarray:
    delta = np.diff(c, prepend=c[0])
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)

    avg_gain = pd.Series(gain).ewm(com=period - 1, adjust=False).mean().values
    avg_loss = pd.Series(loss).ewm(com=period - 1, adjust=False).mean().values

    # Safe division: pre-compute with avg_loss=1 where zero to avoid numpy warning,
    # then mask results. np.errstate suppresses the warning for the remaining
    # edge-cases that slip through np.where's lazy evaluation.
    safe_loss = np.where(avg_loss > 0, avg_loss, 1.0)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = np.where(avg_loss > 0, avg_gain / safe_loss, 100.0)
    return 100 - 100 / (1 + rs)


def _ema_manual(c: np.ndarray, period: int) -> np.ndarray:
    return pd.Series(c).ewm(span=period, adjust=False).mean().values


def _atr_manual(
    h: np.ndarray, lo: np.ndarray, c: np.ndarray, period: int
) -> np.ndarray:
    prev_c = np.roll(c, 1)
    prev_c[0] = c[0]
    tr = np.maximum(h - lo, np.maximum(np.abs(h - prev_c), np.abs(lo - prev_c)))
    return pd.Series(tr).ewm(com=period - 1, adjust=False).mean().values
