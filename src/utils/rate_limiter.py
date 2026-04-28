"""
NEXUS ALPHA — Async Token Bucket Rate Limiter
=============================================
Per-instance (not singleton), thread-safe async rate limiter.
Supports weighted requests (e.g., heavy API calls consume more tokens).

Usage:
    limiter = RateLimiter(rate=10, capacity=10)   # 10 req/s, burst up to 10
    async with limiter.acquire(weight=1):
        response = await exchange.fetch_order_book(...)

    # Or as a simple call
    await limiter.wait(weight=2)
    response = await exchange.fetch_trades(...)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import AsyncIterator

from src.utils.logging import get_logger

log = get_logger(__name__)


@dataclass
class RateLimiterConfig:
    """Configuration for a rate limiter instance."""

    name: str
    rate: float         # Tokens replenished per second
    capacity: float     # Maximum token bucket capacity
    initial_tokens: float | None = None   # If None, starts at capacity


# ---------------------------------------------------------------------------
# Core token bucket implementation
# ---------------------------------------------------------------------------


class RateLimiter:
    """
    Async token bucket rate limiter.

    Thread-safe via asyncio.Lock — safe for concurrent coroutines.
    Per-instance, no module-level state shared between limiters.

    Args:
        rate: Number of tokens added per second (refill rate).
        capacity: Maximum tokens the bucket can hold (burst limit).
        name: Identifier for logging.
        initial_tokens: Starting token count. Defaults to full capacity.
    """

    def __init__(
        self,
        rate: float,
        capacity: float,
        name: str = "default",
        initial_tokens: float | None = None,
    ) -> None:
        self._rate = rate
        self._capacity = capacity
        self._tokens = initial_tokens if initial_tokens is not None else capacity
        self._last_refill = time.monotonic()
        self._lock = asyncio.Lock()
        self._name = name
        self._total_waits = 0
        self._total_wait_seconds = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def wait(self, weight: float = 1.0) -> None:
        """
        Consume ``weight`` tokens, waiting if necessary until they are available.

        Args:
            weight: How many tokens this request consumes. Heavy API calls
                    (e.g., fetching order books) can set weight > 1.
        """
        if weight > self._capacity:
            raise ValueError(
                f"Requested weight {weight} exceeds bucket capacity {self._capacity}"
            )

        async with self._lock:
            self._refill()

            if self._tokens < weight:
                wait_time = (weight - self._tokens) / self._rate
                self._total_waits += 1
                self._total_wait_seconds += wait_time

                log.debug(
                    "Rate limiter waiting",
                    limiter=self._name,
                    wait_seconds=round(wait_time, 3),
                    tokens_available=round(self._tokens, 2),
                    weight=weight,
                )

                # Release lock during sleep to allow other tasks to check state
                # (they will also sleep, which is correct)
                self._lock.release()
                try:
                    await asyncio.sleep(wait_time)
                finally:
                    await self._lock.acquire()
                    # Refill again after sleeping
                    self._refill()

            self._tokens -= weight

    def acquire(self, weight: float = 1.0) -> "_AcquireContextManager":
        """
        Async context manager version of wait().

        Usage:
            async with limiter.acquire():
                response = await api_call()
        """
        return _AcquireContextManager(self, weight)

    def try_acquire(self, weight: float = 1.0) -> bool:
        """
        Non-blocking attempt to consume tokens.

        Returns True if tokens were consumed, False if insufficient tokens.
        Does NOT wait. Use for non-critical rate checking.
        """
        self._refill()
        if self._tokens >= weight:
            self._tokens -= weight
            return True
        return False

    # ------------------------------------------------------------------
    # Properties / introspection
    # ------------------------------------------------------------------

    @property
    def available_tokens(self) -> float:
        """Current token count (approximate, without locking)."""
        self._refill()
        return self._tokens

    @property
    def rate(self) -> float:
        return self._rate

    @property
    def capacity(self) -> float:
        return self._capacity

    @property
    def stats(self) -> dict[str, float]:
        return {
            "total_waits": self._total_waits,
            "total_wait_seconds": round(self._total_wait_seconds, 3),
            "avg_wait_seconds": (
                round(self._total_wait_seconds / self._total_waits, 3)
                if self._total_waits > 0
                else 0.0
            ),
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _refill(self) -> None:
        """Add tokens based on elapsed time since last refill."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        added = elapsed * self._rate
        self._tokens = min(self._capacity, self._tokens + added)
        self._last_refill = now


class _AcquireContextManager:
    """Async context manager returned by RateLimiter.acquire()."""

    __slots__ = ("_limiter", "_weight")

    def __init__(self, limiter: RateLimiter, weight: float) -> None:
        self._limiter = limiter
        self._weight = weight

    async def __aenter__(self) -> None:
        await self._limiter.wait(self._weight)

    async def __aexit__(self, *args: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Pre-configured exchange rate limiters
# ---------------------------------------------------------------------------


class ExchangeRateLimiters:
    """
    Factory that creates per-exchange rate limiters based on known API limits.
    Instantiate once per exchange connection, not as a singleton.
    """

    _CONFIGS: dict[str, RateLimiterConfig] = {
        "binance": RateLimiterConfig(
            name="binance",
            rate=10.0,      # 1200 requests/min = 20/s; use 10 to be conservative
            capacity=20.0,
        ),
        "binance_futures": RateLimiterConfig(
            name="binance_futures",
            rate=8.0,
            capacity=16.0,
        ),
        "bybit": RateLimiterConfig(
            name="bybit",
            rate=5.0,       # 120 req/min = 2/s; burst allowed
            capacity=10.0,
        ),
        "okx": RateLimiterConfig(
            name="okx",
            rate=5.0,
            capacity=10.0,
        ),
        "oanda": RateLimiterConfig(
            name="oanda",
            rate=2.0,       # Conservative for forex broker
            capacity=5.0,
        ),
        "alpaca": RateLimiterConfig(
            name="alpaca",
            rate=3.0,
            capacity=6.0,
        ),
        "zerodha": RateLimiterConfig(
            name="zerodha",
            rate=1.0,       # Kite API: 1 request/second
            capacity=3.0,
        ),
        "supabase": RateLimiterConfig(
            name="supabase",
            rate=20.0,
            capacity=40.0,
        ),
        "anthropic": RateLimiterConfig(
            name="anthropic",
            rate=1.0,       # Conservative to avoid 429s
            capacity=3.0,
        ),
        "openai": RateLimiterConfig(
            name="openai",
            rate=2.0,
            capacity=5.0,
        ),
        "whale_alert": RateLimiterConfig(
            name="whale_alert",
            rate=0.5,       # 30 req/min = 0.5/s
            capacity=2.0,
        ),
    }

    @classmethod
    def for_exchange(cls, exchange_id: str) -> RateLimiter:
        """
        Create a new RateLimiter instance for the given exchange.

        Args:
            exchange_id: Exchange identifier (e.g., 'binance', 'oanda').

        Returns:
            New RateLimiter instance with exchange-appropriate limits.
        """
        config = cls._CONFIGS.get(
            exchange_id,
            RateLimiterConfig(name=exchange_id, rate=2.0, capacity=5.0),
        )
        return RateLimiter(
            rate=config.rate,
            capacity=config.capacity,
            name=config.name,
            initial_tokens=config.initial_tokens,
        )

    @classmethod
    def custom(
        cls,
        name: str,
        rate: float,
        capacity: float,
        initial_tokens: float | None = None,
    ) -> RateLimiter:
        """Create a custom rate limiter."""
        return RateLimiter(
            rate=rate,
            capacity=capacity,
            name=name,
            initial_tokens=initial_tokens,
        )
