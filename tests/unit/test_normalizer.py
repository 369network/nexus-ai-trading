"""
NEXUS ALPHA - Unit Tests: Data Normalizer
Tests candle normalisation, timestamp conversion, gap filling, and outlier detection.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Inline normalizer (mirrors src/data/normalizer.py)
# ---------------------------------------------------------------------------

EXPECTED_TIMEFRAME_SECONDS = {
    "1m":  60,
    "3m":  180,
    "5m":  300,
    "15m": 900,
    "30m": 1800,
    "1h":  3600,
    "4h":  14400,
    "1d":  86400,
    "1w":  604800,
}

OUTLIER_PRICE_THRESHOLD = 5.0   # Price must be within 5x of previous close


def normalize_binance_candle(raw: Dict[str, Any], symbol: str, timeframe: str) -> Dict[str, Any]:
    """
    Normalise a Binance kline to NEXUS ALPHA standard format.
    Binance format: [open_ts, open, high, low, close, volume, close_ts, ...]
    """
    if isinstance(raw, list):
        return {
            "timestamp":  int(raw[0]),        # ms UTC
            "open":       float(raw[1]),
            "high":       float(raw[2]),
            "low":        float(raw[3]),
            "close":      float(raw[4]),
            "volume":     float(raw[5]),
            "symbol":     symbol,
            "timeframe":  timeframe,
            "market":     "crypto",
            "is_closed":  True,
        }
    # Already dict-form
    return {
        "timestamp": int(raw.get("t", raw.get("timestamp", 0))),
        "open":   float(raw.get("o", raw.get("open", 0))),
        "high":   float(raw.get("h", raw.get("high", 0))),
        "low":    float(raw.get("l", raw.get("low", 0))),
        "close":  float(raw.get("c", raw.get("close", 0))),
        "volume": float(raw.get("v", raw.get("volume", 0))),
        "symbol":    symbol,
        "timeframe": timeframe,
        "market":    "crypto",
        "is_closed": raw.get("x", True),
    }


def convert_ist_to_utc_ms(ist_datetime_str: str) -> int:
    """
    Convert IST (UTC+5:30) datetime string to UTC milliseconds.
    Input format: 'YYYY-MM-DD HH:MM:SS'
    """
    dt_naive = datetime.strptime(ist_datetime_str, "%Y-%m-%d %H:%M:%S")
    # IST is UTC+5:30
    ist_offset = timedelta(hours=5, minutes=30)
    dt_utc = dt_naive - ist_offset
    dt_utc = dt_utc.replace(tzinfo=timezone.utc)
    return int(dt_utc.timestamp() * 1000)


def utc_ms_to_datetime(ts_ms: int) -> datetime:
    """Convert UTC millisecond timestamp to timezone-aware datetime."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc)


def fill_gaps(
    candles: List[Dict[str, Any]],
    timeframe: str,
    fill_method: str = "forward_fill",
) -> List[Dict[str, Any]]:
    """
    Detect and fill gaps in candle series.
    Returns augmented list with synthetic candles for missing intervals.
    """
    if len(candles) < 2:
        return candles

    tf_ms = EXPECTED_TIMEFRAME_SECONDS.get(timeframe, 3600) * 1000
    tolerance = 0.1  # 10% tolerance for floating point

    result = [candles[0]]

    for i in range(1, len(candles)):
        prev = result[-1]
        curr = candles[i]
        expected_ts = prev["timestamp"] + tf_ms
        actual_ts   = curr["timestamp"]

        # Check for gap
        gap_count = round((actual_ts - prev["timestamp"]) / tf_ms) - 1
        if gap_count > 0:
            # Fill the gap
            for j in range(1, gap_count + 1):
                fill_ts = prev["timestamp"] + j * tf_ms
                if fill_method == "forward_fill":
                    fill_candle = {
                        **prev,
                        "timestamp": fill_ts,
                        "open":   prev["close"],
                        "high":   prev["close"],
                        "low":    prev["close"],
                        "close":  prev["close"],
                        "volume": 0.0,
                        "is_synthetic": True,
                    }
                elif fill_method == "linear":
                    frac = j / (gap_count + 1)
                    price = prev["close"] + (curr["open"] - prev["close"]) * frac
                    fill_candle = {
                        **prev,
                        "timestamp": fill_ts,
                        "open": price, "high": price,
                        "low": price, "close": price,
                        "volume": 0.0,
                        "is_synthetic": True,
                    }
                else:
                    fill_candle = {**prev, "timestamp": fill_ts, "is_synthetic": True}
                result.append(fill_candle)

        result.append(curr)

    return result


def detect_outliers(
    candles: List[Dict[str, Any]],
    price_threshold: float = OUTLIER_PRICE_THRESHOLD,
) -> List[int]:
    """
    Return indices of candles where price is an outlier.
    An outlier is defined as a price > threshold × previous close.
    """
    if len(candles) < 2:
        return []

    outlier_indices = []
    for i in range(1, len(candles)):
        prev_close = candles[i-1]["close"]
        curr_close = candles[i]["close"]
        if prev_close <= 0:
            continue
        ratio = curr_close / prev_close
        if ratio > price_threshold or ratio < (1 / price_threshold):
            outlier_indices.append(i)

    return outlier_indices


def validate_ohlcv(candle: Dict[str, Any]) -> List[str]:
    """Return list of validation errors for a candle."""
    errors = []
    o = candle.get("open", 0)
    h = candle.get("high", 0)
    l = candle.get("low", 0)
    c = candle.get("close", 0)
    v = candle.get("volume", 0)

    if h < l:
        errors.append(f"high ({h}) < low ({l})")
    if h < o:
        errors.append(f"high ({h}) < open ({o})")
    if h < c:
        errors.append(f"high ({h}) < close ({c})")
    if l > o:
        errors.append(f"low ({l}) > open ({o})")
    if l > c:
        errors.append(f"low ({l}) > close ({c})")
    if v < 0:
        errors.append(f"volume ({v}) < 0")
    if o <= 0 or h <= 0 or l <= 0 or c <= 0:
        errors.append("zero or negative price")

    return errors


# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def binance_raw_kline():
    """Standard Binance kline list format."""
    return [
        1704067200000,  # open_ts (2024-01-01 00:00:00 UTC)
        "43250.50",     # open
        "43890.00",     # high
        "43100.75",     # low
        "43750.25",     # close
        "1234.56789",   # volume
        1704070799999,  # close_ts
        "53891234.56",  # quote volume
        1500,           # trades
        "617.28",       # taker buy base volume
        "26945617.28",  # taker buy quote volume
        "0",            # ignore
    ]


@pytest.fixture
def binance_ws_raw():
    """Binance WebSocket kline format (dict)."""
    return {
        "t": 1704067200000,
        "T": 1704070799999,
        "s": "BTCUSDT",
        "i": "1h",
        "o": "43250.50",
        "c": "43750.25",
        "h": "43890.00",
        "l": "43100.75",
        "v": "1234.56",
        "n": 1500,
        "x": True,  # is candle closed
        "q": "53891234.56",
        "V": "617.28",
        "Q": "26945617.28",
        "B": "0",
    }


@pytest.fixture
def candle_series_with_gap():
    """Series of 1h candles with a 2-candle gap at index 3."""
    ts_ms = 1704067200000  # 2024-01-01 00:00 UTC
    hour  = 3_600_000
    candles = []
    price = 50_000.0

    times = [0, 1, 2, 5, 6, 7]  # Gap between indices 2 and 5 (missing 3 and 4)
    for i in times:
        candles.append({
            "timestamp": ts_ms + i * hour,
            "open":   price,
            "high":   price * 1.01,
            "low":    price * 0.99,
            "close":  price * 1.002,
            "volume": 1000.0,
            "is_synthetic": False,
        })
        price *= 1.002

    return candles


@pytest.fixture
def candle_with_spike(sample_ohlcv_data):
    """Series with a 10x price spike at the end."""
    candles = sample_ohlcv_data.copy()
    last = candles[-1].copy()
    last["close"] = candles[-2]["close"] * 10  # 10x spike
    candles.append(last)
    return candles


# ---------------------------------------------------------------------------
# Timestamp Tests
# ---------------------------------------------------------------------------

class TestTimestampNormalization:
    def test_binance_kline_timestamp_is_utc_ms(self, binance_raw_kline):
        """Binance timestamp should be preserved as UTC ms integer."""
        normalized = normalize_binance_candle(binance_raw_kline, "BTCUSDT", "1h")
        ts_ms = normalized["timestamp"]
        assert isinstance(ts_ms, int)
        assert ts_ms == 1704067200000

    def test_binance_kline_ts_converts_to_correct_utc(self, binance_raw_kline):
        """2024-01-01 00:00:00 UTC = 1704067200000 ms."""
        normalized = normalize_binance_candle(binance_raw_kline, "BTCUSDT", "1h")
        dt = utc_ms_to_datetime(normalized["timestamp"])
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 1
        assert dt.hour == 0
        assert dt.minute == 0
        assert dt.tzinfo is not None

    def test_binance_ws_dict_normalized_correctly(self, binance_ws_raw):
        """WebSocket dict format normalizes same as REST list format."""
        normalized = normalize_binance_candle(binance_ws_raw, "BTCUSDT", "1h")
        assert normalized["timestamp"] == 1704067200000
        assert abs(normalized["open"] - 43250.50) < 0.01

    def test_ist_to_utc_offset_is_5h30m(self):
        """IST (UTC+5:30) 09:30:00 = UTC 04:00:00."""
        ist_str = "2024-01-15 09:30:00"
        ts_ms = convert_ist_to_utc_ms(ist_str)
        dt_utc = utc_ms_to_datetime(ts_ms)
        assert dt_utc.hour == 4
        assert dt_utc.minute == 0
        assert dt_utc.second == 0

    def test_ist_midnight_converts_correctly(self):
        """IST midnight = previous day UTC 18:30."""
        ist_str = "2024-01-15 00:00:00"
        ts_ms = convert_ist_to_utc_ms(ist_str)
        dt_utc = utc_ms_to_datetime(ts_ms)
        assert dt_utc.day == 14  # Previous day
        assert dt_utc.hour == 18
        assert dt_utc.minute == 30

    def test_utc_ms_round_trip(self):
        """Converting to ms and back should preserve time."""
        original = datetime(2024, 6, 15, 12, 30, 45, tzinfo=timezone.utc)
        ts_ms = int(original.timestamp() * 1000)
        recovered = utc_ms_to_datetime(ts_ms)
        assert abs((recovered - original).total_seconds()) < 0.001


# ---------------------------------------------------------------------------
# Gap Filling Tests
# ---------------------------------------------------------------------------

class TestGapFilling:
    def test_gap_creates_synthetic_candles(self, candle_series_with_gap):
        """Gap of 2 candles should create 2 synthetic candles."""
        before_count = len(candle_series_with_gap)
        filled = fill_gaps(candle_series_with_gap, "1h")
        assert len(filled) == before_count + 2, (
            f"Expected {before_count + 2} candles, got {len(filled)}"
        )

    def test_filled_candles_marked_synthetic(self, candle_series_with_gap):
        """Gap-filled candles must have is_synthetic=True."""
        filled = fill_gaps(candle_series_with_gap, "1h")
        synthetic = [c for c in filled if c.get("is_synthetic")]
        assert len(synthetic) == 2

    def test_filled_candles_have_sequential_timestamps(self, candle_series_with_gap):
        """After filling, timestamps should be monotonically increasing."""
        filled = fill_gaps(candle_series_with_gap, "1h")
        timestamps = [c["timestamp"] for c in filled]
        for i in range(1, len(timestamps)):
            assert timestamps[i] > timestamps[i-1], (
                f"Timestamp {timestamps[i]} not > {timestamps[i-1]}"
            )

    def test_no_gap_unchanged(self, sample_ohlcv_data):
        """Series without gaps should be returned unchanged."""
        # Ensure sequential timestamps
        tf_ms = 3_600_000
        candles = []
        ts = 1704067200000
        for c in sample_ohlcv_data[:10]:
            new_c = {**c, "timestamp": ts}
            candles.append(new_c)
            ts += tf_ms

        filled = fill_gaps(candles, "1h")
        assert len(filled) == len(candles)

    def test_forward_fill_uses_previous_close(self, candle_series_with_gap):
        """Forward fill should use previous candle's close price."""
        filled = fill_gaps(candle_series_with_gap, "1h", fill_method="forward_fill")
        synthetic = [c for c in filled if c.get("is_synthetic")]
        for sc in synthetic:
            # All OHLC should be the same (forward-filled)
            assert sc["open"] == sc["high"] == sc["low"] == sc["close"]
            assert sc["volume"] == 0.0

    def test_single_candle_not_modified(self):
        """Single candle has no gaps to fill."""
        candle = [{
            "timestamp": 1704067200000,
            "open": 50000, "high": 51000,
            "low": 49000, "close": 50500,
            "volume": 1000,
        }]
        filled = fill_gaps(candle, "1h")
        assert len(filled) == 1


# ---------------------------------------------------------------------------
# Outlier Detection Tests
# ---------------------------------------------------------------------------

class TestOutlierDetection:
    def test_detects_10x_price_spike(self, candle_with_spike):
        """A 10x price spike should be flagged as an outlier."""
        outliers = detect_outliers(candle_with_spike, price_threshold=5.0)
        assert len(candle_with_spike) - 1 in outliers, (
            "Last candle (10x spike) should be detected as outlier"
        )

    def test_normal_price_movement_not_flagged(self, sample_ohlcv_data):
        """Normal price movements (<5% per candle) should not be outliers."""
        outliers = detect_outliers(sample_ohlcv_data, price_threshold=5.0)
        assert len(outliers) == 0, (
            f"Normal data should have no outliers, found: {outliers}"
        )

    def test_downward_spike_detected(self):
        """A 10x downward spike should also be detected."""
        candles = [
            {"close": 50000.0},
            {"close": 50500.0},
            {"close": 50200.0},
            {"close": 4000.0},    # -92% — outlier
        ]
        outliers = detect_outliers(candles, price_threshold=5.0)
        assert 3 in outliers

    def test_exactly_at_threshold_not_flagged(self):
        """Price exactly 5x previous close is not above threshold."""
        candles = [
            {"close": 10000.0},
            {"close": 50000.0},   # exactly 5x
        ]
        outliers = detect_outliers(candles, price_threshold=5.0)
        assert 1 not in outliers  # 5x == threshold, not strictly greater

    def test_empty_series_has_no_outliers(self):
        outliers = detect_outliers([], price_threshold=5.0)
        assert outliers == []

    def test_single_candle_has_no_outliers(self):
        candles = [{"close": 50000.0}]
        outliers = detect_outliers(candles)
        assert outliers == []


# ---------------------------------------------------------------------------
# OHLCV Validation Tests
# ---------------------------------------------------------------------------

class TestOHLCVValidation:
    def test_valid_candle_has_no_errors(self):
        candle = {
            "open": 50000, "high": 51000, "low": 49000, "close": 50500, "volume": 1000
        }
        errors = validate_ohlcv(candle)
        assert errors == []

    def test_high_less_than_low_is_error(self):
        candle = {
            "open": 50000, "high": 48000, "low": 49000, "close": 50000, "volume": 100
        }
        errors = validate_ohlcv(candle)
        assert any("high" in e and "low" in e for e in errors)

    def test_high_less_than_close_is_error(self):
        candle = {
            "open": 50000, "high": 50000, "low": 49000, "close": 51000, "volume": 100
        }
        errors = validate_ohlcv(candle)
        assert any("high" in e and "close" in e for e in errors)

    def test_negative_volume_is_error(self):
        candle = {
            "open": 50000, "high": 51000, "low": 49000, "close": 50500, "volume": -10
        }
        errors = validate_ohlcv(candle)
        assert any("volume" in e for e in errors)

    def test_zero_price_is_error(self):
        candle = {
            "open": 0, "high": 0, "low": 0, "close": 0, "volume": 100
        }
        errors = validate_ohlcv(candle)
        assert len(errors) > 0
