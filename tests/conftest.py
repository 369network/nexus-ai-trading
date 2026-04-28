"""
NEXUS ALPHA - pytest fixtures
Shared test fixtures for unit and integration tests.
"""

from __future__ import annotations

import asyncio
import math
import random
from datetime import datetime, timezone
from typing import Any, Dict, Generator, List
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Event loop fixture (asyncio)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def event_loop():
    """Session-scoped asyncio event loop."""
    policy = asyncio.get_event_loop_policy()
    loop = policy.new_event_loop()
    yield loop
    loop.close()


# ---------------------------------------------------------------------------
# Sample OHLCV data (100 candles of realistic BTC price action)
# ---------------------------------------------------------------------------

def _generate_btc_candles(n: int = 100, start_price: float = 50_000.0) -> List[Dict[str, Any]]:
    """Generate n candles of realistic BTC-like OHLCV data."""
    random.seed(42)  # Deterministic for tests
    candles = []
    price = start_price
    ts_ms = int(datetime(2024, 1, 1, 0, 0, tzinfo=timezone.utc).timestamp() * 1000)
    interval_ms = 3_600_000  # 1 hour in ms

    for i in range(n):
        # Random walk with slight upward drift
        change_pct = random.gauss(0.001, 0.015)  # mean 0.1%, std 1.5%
        close = price * (1 + change_pct)

        # Realistic intrabar range
        high  = max(price, close) * (1 + abs(random.gauss(0, 0.005)))
        low   = min(price, close) * (1 - abs(random.gauss(0, 0.005)))
        open_ = price

        # Volume: higher volume on bigger moves
        base_volume = 1500 + random.gauss(0, 300)
        volume = base_volume * (1 + abs(change_pct) * 10)

        candles.append({
            "timestamp":  ts_ms,
            "open":   round(open_, 2),
            "high":   round(high, 2),
            "low":    round(low, 2),
            "close":  round(close, 2),
            "volume": round(max(volume, 100), 2),
        })

        price = close
        ts_ms += interval_ms

    return candles


@pytest.fixture
def sample_ohlcv_data() -> List[Dict[str, Any]]:
    """100 candles of realistic BTC/USDT OHLCV data."""
    return _generate_btc_candles(n=100)


@pytest.fixture
def extended_ohlcv_data() -> List[Dict[str, Any]]:
    """500 candles for strategies that need more history."""
    return _generate_btc_candles(n=500)


@pytest.fixture
def trending_up_data() -> List[Dict[str, Any]]:
    """Strong uptrend data for trend-following tests."""
    random.seed(99)
    candles = []
    price = 40_000.0
    ts_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    for i in range(100):
        change_pct = random.gauss(0.01, 0.008)  # Strong positive drift
        close = price * (1 + change_pct)
        high  = max(price, close) * 1.003
        low   = min(price, close) * 0.997
        volume = 2000 + abs(change_pct) * 50000

        candles.append({
            "timestamp": ts_ms + i * 3_600_000,
            "open": round(price, 2), "high": round(high, 2),
            "low": round(low, 2), "close": round(close, 2),
            "volume": round(volume, 2),
        })
        price = close

    return candles


@pytest.fixture
def ranging_data() -> List[Dict[str, Any]]:
    """Mean-reverting data for ranging market tests."""
    random.seed(77)
    candles = []
    price = 50_000.0
    ts_ms = int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    for i in range(100):
        # Mean-reverting around 50000 with oscillation
        deviation = (price - 50_000) / 50_000
        change_pct = -deviation * 0.3 + random.gauss(0, 0.008)
        close = price * (1 + change_pct)
        high  = max(price, close) * 1.002
        low   = min(price, close) * 0.998
        volume = 1200 + random.gauss(0, 200)

        candles.append({
            "timestamp": ts_ms + i * 3_600_000,
            "open": round(price, 2), "high": round(high, 2),
            "low": round(low, 2), "close": round(close, 2),
            "volume": round(max(volume, 100), 2),
        })
        price = close

    return candles


# ---------------------------------------------------------------------------
# Pre-computed indicators fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_indicators(sample_ohlcv_data) -> Dict[str, Any]:
    """Pre-computed indicator values on the sample OHLCV data."""
    closes  = [c["close"] for c in sample_ohlcv_data]
    highs   = [c["high"]  for c in sample_ohlcv_data]
    lows    = [c["low"]   for c in sample_ohlcv_data]
    volumes = [c["volume"] for c in sample_ohlcv_data]

    # RSI (14)
    def _rsi(closes, period=14):
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(d, 0) for d in deltas]
        losses = [-min(d, 0) for d in deltas]
        ag = sum(gains[:period]) / period
        al = sum(losses[:period]) / period
        for i in range(period, len(deltas)):
            ag = (ag * (period-1) + gains[i]) / period
            al = (al * (period-1) + losses[i]) / period
        rs = ag / al if al > 0 else 100
        return round(100 - 100 / (1 + rs), 2)

    # ATR (14)
    def _atr(highs, lows, closes, period=14):
        trs = [max(h-l, abs(h-closes[i-1]), abs(l-closes[i-1]))
               for i, (h, l) in enumerate(zip(highs[1:], lows[1:]), 1)]
        return round(sum(trs[-period:]) / period, 2)

    # EMA
    def _ema(closes, period):
        k = 2 / (period + 1)
        ema = sum(closes[:period]) / period
        for p in closes[period:]:
            ema = p * k + ema * (1 - k)
        return round(ema, 2)

    # Bollinger Bands (20, 2)
    def _bb(closes, period=20, std_mult=2.0):
        window = closes[-period:]
        mean = sum(window) / period
        var  = sum((x - mean)**2 for x in window) / period
        std  = var ** 0.5
        return {
            "upper": round(mean + std_mult * std, 2),
            "middle": round(mean, 2),
            "lower": round(mean - std_mult * std, 2),
        }

    rsi = _rsi(closes)
    atr = _atr(highs, lows, closes)
    bb  = _bb(closes)

    return {
        "rsi_14":      rsi,
        "ema_8":       _ema(closes, 8),
        "ema_21":      _ema(closes, 21),
        "ema_50":      _ema(closes, 50),
        "atr_14":      atr,
        "atr_pct":     round(atr / closes[-1] * 100, 4),
        "bb_upper":    bb["upper"],
        "bb_middle":   bb["middle"],
        "bb_lower":    bb["lower"],
        "bb_pct":      round((closes[-1] - bb["lower"]) / (bb["upper"] - bb["lower"]) * 100, 2),
        "volume_ratio": round(volumes[-1] / (sum(volumes[-20:]) / 20), 3),
        "close":       closes[-1],
        "open":        sample_ohlcv_data[-1]["open"],
        "high":        sample_ohlcv_data[-1]["high"],
        "low":         sample_ohlcv_data[-1]["low"],
    }


# ---------------------------------------------------------------------------
# Paper portfolio fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def paper_portfolio() -> Dict[str, Any]:
    """Clean paper trading portfolio state."""
    return {
        "equity":           100_000.0,
        "cash":             100_000.0,
        "open_positions":   {},
        "closed_trades":    [],
        "daily_pnl":        0.0,
        "total_pnl":        0.0,
        "peak_equity":      100_000.0,
        "drawdown_pct":     0.0,
        "total_trades":     0,
        "winning_trades":   0,
        "execution_mode":   "paper",
    }


# ---------------------------------------------------------------------------
# Mock Supabase client
# ---------------------------------------------------------------------------

class MockSupabaseTable:
    def __init__(self, name: str, data: List[Dict] = None):
        self.name = name
        self._data = data or []
        self._query = self._data[:]

    def select(self, *args, **kwargs):
        return self

    def insert(self, row, *args, **kwargs):
        if isinstance(row, list):
            self._data.extend(row)
        else:
            self._data.append(row)
        return self

    def upsert(self, row, *args, **kwargs):
        return self.insert(row)

    def update(self, row, *args, **kwargs):
        return self

    def delete(self):
        return self

    def eq(self, col, val):
        self._query = [r for r in self._query if r.get(col) == val]
        return self

    def gte(self, col, val):
        self._query = [r for r in self._query if r.get(col, 0) >= val]
        return self

    def lte(self, col, val):
        self._query = [r for r in self._query if r.get(col, 0) <= val]
        return self

    def is_(self, col, val):
        if val == "null":
            self._query = [r for r in self._query if r.get(col) is None]
        return self

    def order(self, col, desc=False):
        self._query = sorted(self._query, key=lambda r: r.get(col, ""), reverse=desc)
        return self

    def limit(self, n):
        self._query = self._query[:n]
        return self

    def execute(self):
        result = MagicMock()
        result.data  = self._query[:]
        result.count = len(self._query)
        self._query  = self._data[:]  # Reset for next call
        return result


class MockSupabaseClient:
    def __init__(self):
        self._tables: Dict[str, MockSupabaseTable] = {}

    def table(self, name: str) -> MockSupabaseTable:
        if name not in self._tables:
            self._tables[name] = MockSupabaseTable(name)
        t = self._tables[name]
        t._query = t._data[:]  # Reset query state
        return t

    async def connect(self):
        pass

    async def close(self):
        pass

    async def health_check(self) -> bool:
        return True

    async def upsert_candle(self, **kwargs) -> None:
        pass

    async def store_signal(self, **kwargs) -> str:
        return "mock-signal-id-1234"

    async def store_trade(self, **kwargs) -> str:
        return "mock-trade-id-5678"

    async def store_agent_decisions(self, **kwargs) -> None:
        pass

    async def fetch_candles(self, **kwargs) -> List[Dict]:
        return []


@pytest.fixture
def mock_supabase() -> MockSupabaseClient:
    """Mock Supabase client for unit tests."""
    return MockSupabaseClient()


# ---------------------------------------------------------------------------
# Mock LLM fixture
# ---------------------------------------------------------------------------

def _make_mock_llm_response(
    vote: str = "LONG",
    confidence: float = 0.72,
    reasoning: str = "Mock reasoning for test.",
) -> Dict[str, Any]:
    return {
        "vote":        vote,
        "confidence":  confidence,
        "reasoning":   reasoning,
        "key_factors": {"test": True},
        "model_used":  "mock-model",
        "latency_ms":  10,
    }


class MockLLMEnsemble:
    """Deterministic mock LLM ensemble."""

    def __init__(self, default_vote="LONG", default_confidence=0.72):
        self.default_vote       = default_vote
        self.default_confidence = default_confidence
        self.call_count         = 0
        self.models             = ["mock-gpt4", "mock-claude"]

    async def query_agent(
        self,
        agent_name: str,
        prompt: str,
        **kwargs,
    ) -> Dict[str, Any]:
        self.call_count += 1
        # Alternate votes for variety in tests
        if agent_name == "RiskSentinel":
            vote = "NEUTRAL"
            conf = 0.6
        else:
            vote = self.default_vote
            conf = self.default_confidence
        return _make_mock_llm_response(vote=vote, confidence=conf)

    async def init(self):
        pass


@pytest.fixture
def mock_llm() -> MockLLMEnsemble:
    """Deterministic mock LLM ensemble."""
    return MockLLMEnsemble()


@pytest.fixture
def mock_llm_bearish() -> MockLLMEnsemble:
    """Mock LLM that votes SHORT."""
    return MockLLMEnsemble(default_vote="SHORT", default_confidence=0.78)


@pytest.fixture
def mock_llm_neutral() -> MockLLMEnsemble:
    """Mock LLM that votes NEUTRAL."""
    return MockLLMEnsemble(default_vote="NEUTRAL", default_confidence=0.40)


# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_settings() -> MagicMock:
    """Mock Settings object with sensible defaults."""
    settings = MagicMock()
    settings.paper_mode = True
    settings.supabase_url = "https://mock.supabase.co"
    settings.supabase_service_key = "mock-service-key"

    # Risk settings
    settings.max_position_size_pct  = 10.0
    settings.daily_loss_limit_pct   = 3.0
    settings.weekly_loss_limit_pct  = 8.0
    settings.drawdown_pause_pct     = 15.0
    settings.drawdown_stop_pct      = 25.0
    settings.max_open_positions     = 5
    settings.risk_pct_per_trade     = 1.0

    # Market settings
    settings.enabled_markets = {
        "crypto": MagicMock(
            symbols=["BTCUSDT", "ETHUSDT"],
            timeframes=["1h", "4h"],
            market_class="crypto",
        )
    }

    # Signal settings
    settings.min_agent_confidence   = 0.5
    settings.edge_filter_min_ev     = 0.002
    settings.min_kelly_fraction     = 0.01

    return settings
