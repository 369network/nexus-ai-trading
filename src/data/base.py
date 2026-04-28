"""
NEXUS ALPHA - Shared Base Types
================================
Foundational dataclasses and abstract interfaces for the multi-market
AI trading system data layer.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# OHLCV candle
# ---------------------------------------------------------------------------

@dataclass
class OHLCV:
    """Unified Open-High-Low-Close-Volume candle across all markets."""

    timestamp_ms: int          # UTC epoch milliseconds (open time)
    open: float
    high: float
    low: float
    close: float
    volume: float              # base-asset volume
    quote_volume: float        # quote-asset volume
    trades: int                # number of trades in the period
    vwap: float                # volume-weighted average price
    taker_buy_volume: float    # aggressive buy volume (base asset)

    source: str                # e.g. "binance", "oanda", "kite"
    market: str                # e.g. "spot", "futures", "forex", "equity"
    symbol: str                # normalised symbol, e.g. "BTCUSDT", "EUR_USD"
    interval: str              # e.g. "1m", "5m", "1h", "1d"
    complete: bool = True      # False while the candle is still forming

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    @property
    def timestamp_s(self) -> float:
        """Unix timestamp in seconds."""
        return self.timestamp_ms / 1_000.0

    @property
    def body_size(self) -> float:
        """Absolute candle body size."""
        return abs(self.close - self.open)

    @property
    def wick_upper(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def wick_lower(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_bullish(self) -> bool:
        return self.close >= self.open

    def is_valid(self) -> bool:
        """Quick validity guard (non-NaN, sane OHLCV relationships)."""
        for v in (self.open, self.high, self.low, self.close, self.volume):
            if math.isnan(v) or math.isinf(v):
                return False
        if self.high < self.open or self.high < self.close:
            return False
        if self.low > self.open or self.low > self.close:
            return False
        if self.volume < 0:
            return False
        return True

    def __repr__(self) -> str:  # noqa: D401
        return (
            f"OHLCV({self.symbol} {self.interval} "
            f"@{self.timestamp_ms} O={self.open} H={self.high} "
            f"L={self.low} C={self.close} V={self.volume})"
        )


# ---------------------------------------------------------------------------
# Tick (best bid/ask snapshot)
# ---------------------------------------------------------------------------

@dataclass
class Tick:
    """Real-time best-bid/ask snapshot for any tradeable instrument."""

    timestamp: float      # Unix timestamp (seconds, sub-second precision OK)
    instrument: str       # e.g. "EUR_USD", "NIFTY25MAY18FUT"
    bid: float
    ask: float
    mid: float
    spread: float         # ask - bid in price units
    tradeable: bool       # whether the instrument is currently tradeable
    source: str           # data provider identifier
    market: str           # "forex", "crypto", "equity", "futures"

    @classmethod
    def from_bid_ask(
        cls,
        timestamp: float,
        instrument: str,
        bid: float,
        ask: float,
        *,
        tradeable: bool = True,
        source: str = "",
        market: str = "",
    ) -> "Tick":
        """Factory that auto-computes mid and spread."""
        return cls(
            timestamp=timestamp,
            instrument=instrument,
            bid=bid,
            ask=ask,
            mid=(bid + ask) / 2.0,
            spread=ask - bid,
            tradeable=tradeable,
            source=source,
            market=market,
        )

    def __repr__(self) -> str:
        return (
            f"Tick({self.instrument} bid={self.bid} ask={self.ask} "
            f"spread={self.spread:.5f} @{self.timestamp:.3f})"
        )


# ---------------------------------------------------------------------------
# OrderBook
# ---------------------------------------------------------------------------

@dataclass
class OrderBook:
    """Level-2 order book snapshot."""

    symbol: str
    bids: List[Tuple[Decimal, Decimal]]   # [(price, qty), ...] best bid first
    asks: List[Tuple[Decimal, Decimal]]   # [(price, qty), ...] best ask first
    timestamp: float                       # Unix timestamp (seconds)
    source: str

    @property
    def best_bid(self) -> Optional[Decimal]:
        return self.bids[0][0] if self.bids else None

    @property
    def best_ask(self) -> Optional[Decimal]:
        return self.asks[0][0] if self.asks else None

    @property
    def mid_price(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return float((self.best_bid + self.best_ask) / 2)

    @property
    def spread(self) -> Optional[float]:
        if self.best_bid is None or self.best_ask is None:
            return None
        return float(self.best_ask - self.best_bid)

    def bid_depth(self, levels: int = 5) -> float:
        """Total bid volume in the top N levels."""
        return float(sum(qty for _, qty in self.bids[:levels]))

    def ask_depth(self, levels: int = 5) -> float:
        """Total ask volume in the top N levels."""
        return float(sum(qty for _, qty in self.asks[:levels]))

    def imbalance(self, levels: int = 5) -> float:
        """Order book imbalance in [-1, 1]; positive = more bids."""
        b = self.bid_depth(levels)
        a = self.ask_depth(levels)
        total = b + a
        if total == 0:
            return 0.0
        return (b - a) / total

    def __repr__(self) -> str:
        return (
            f"OrderBook({self.symbol} bid={self.best_bid} ask={self.best_ask} "
            f"levels={len(self.bids)}x{len(self.asks)} @{self.timestamp:.3f})"
        )


# ---------------------------------------------------------------------------
# Abstract base provider
# ---------------------------------------------------------------------------

class BaseDataProvider(ABC):
    """
    Abstract interface that every data provider must implement.

    Providers are responsible for fetching raw market data and returning
    it in the normalised NEXUS ALPHA types (OHLCV, OrderBook, etc.).
    """

    @abstractmethod
    async def fetch_ohlcv(
        self,
        symbol: str,
        interval: str,
        start_time: Optional[int] = None,
        end_time: Optional[int] = None,
        limit: int = 500,
    ) -> List[OHLCV]:
        """
        Fetch historical OHLCV candles for *symbol* at *interval*.

        Parameters
        ----------
        symbol:
            Exchange-native or normalised symbol string.
        interval:
            Candle interval string, e.g. ``"1m"``, ``"5m"``, ``"1h"``.
        start_time:
            Inclusive start time as UTC milliseconds epoch.
        end_time:
            Exclusive end time as UTC milliseconds epoch.
        limit:
            Maximum number of candles to return.

        Returns
        -------
        List[OHLCV]
            Chronologically ordered list of complete candles.
        """

    @abstractmethod
    async def get_order_book(
        self,
        symbol: str,
        limit: int = 20,
    ) -> OrderBook:
        """
        Fetch a current level-2 order book snapshot.

        Parameters
        ----------
        symbol:
            Exchange-native or normalised symbol string.
        limit:
            Number of price levels on each side.

        Returns
        -------
        OrderBook
            Current order book snapshot.
        """

    # ------------------------------------------------------------------
    # Optional hook – subclasses may override
    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Release any underlying resources (connections, sessions, …)."""


# ---------------------------------------------------------------------------
# Shared validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Outcome of a data validation pass."""

    valid: bool
    issues: List[str] = field(default_factory=list)
    warning_count: int = 0
    error_count: int = 0

    def add_issue(self, msg: str, *, is_error: bool = True) -> None:
        self.issues.append(msg)
        if is_error:
            self.error_count += 1
            self.valid = False
        else:
            self.warning_count += 1

    def __bool__(self) -> bool:
        return self.valid

    def summary(self) -> str:
        status = "PASS" if self.valid else "FAIL"
        return (
            f"[{status}] errors={self.error_count} warnings={self.warning_count}"
        )
