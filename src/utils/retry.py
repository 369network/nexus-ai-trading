"""
NEXUS ALPHA — Retry Utilities
==============================
Decorator and helpers for retrying async operations with configurable
exponential backoff, jitter, and per-attempt logging.

Uses tenacity under the hood for robust retry semantics.
"""

from __future__ import annotations

import asyncio
import functools
import random
from typing import Any, Callable, Sequence, Type, TypeVar

from tenacity import (
    AsyncRetrying,
    RetryError,
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random_exponential,
)

from src.utils.logging import get_logger

log = get_logger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Default exceptions to retry on
_DEFAULT_RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    ConnectionError,
    TimeoutError,
    asyncio.TimeoutError,
    OSError,
)


# ---------------------------------------------------------------------------
# Primary decorator
# ---------------------------------------------------------------------------


def retry_with_backoff(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exceptions: Sequence[type[Exception]] | None = None,
    jitter: bool = True,
    reraise: bool = True,
) -> Callable[[F], F]:
    """
    Decorator: retry an async function with exponential backoff.

    Args:
        max_retries: Maximum number of retry attempts (not counting first try).
        base_delay: Initial backoff delay in seconds.
        max_delay: Maximum backoff delay in seconds.
        exceptions: Exception types to catch and retry. Defaults to common
                    network/connection errors.
        jitter: If True, add random jitter to delay (prevents thundering herd).
        reraise: If True, re-raise the last exception after all retries
                 exhausted. If False, return None on final failure.

    Usage:
        @retry_with_backoff(max_retries=5, base_delay=2.0, exceptions=[httpx.HTTPError])
        async def fetch_candles(symbol: str) -> list[dict]:
            ...
    """
    retry_exceptions = tuple(exceptions) if exceptions else _DEFAULT_RETRY_EXCEPTIONS

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            attempt = 0
            last_exception: Exception | None = None

            while attempt <= max_retries:
                try:
                    return await func(*args, **kwargs)

                except retry_exceptions as exc:
                    last_exception = exc
                    attempt += 1

                    if attempt > max_retries:
                        log.error(
                            "All retries exhausted",
                            function=func.__qualname__,
                            max_retries=max_retries,
                            error=str(exc),
                            error_type=type(exc).__name__,
                        )
                        if reraise:
                            raise
                        return None

                    # Calculate delay with exponential backoff
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    if jitter:
                        delay = delay * (0.5 + random.random() * 0.5)

                    log.warning(
                        "Retrying after error",
                        function=func.__qualname__,
                        attempt=attempt,
                        max_retries=max_retries,
                        delay_seconds=round(delay, 2),
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )

                    await asyncio.sleep(delay)

                except Exception as exc:
                    # Non-retryable exception — propagate immediately
                    log.error(
                        "Non-retryable error in decorated function",
                        function=func.__qualname__,
                        error=str(exc),
                        error_type=type(exc).__name__,
                    )
                    raise

            # Should not reach here, but satisfy mypy
            if reraise and last_exception:
                raise last_exception
            return None

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Tenacity-based variant for more complex retry policies
# ---------------------------------------------------------------------------


def retry_tenacity(
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: Sequence[type[Exception]] | None = None,
    random_wait: bool = True,
) -> Callable[[F], F]:
    """
    Decorator using tenacity library with full retry policy control.
    Suitable for complex scenarios (e.g., specific exception types, callbacks).

    Args:
        max_retries: Max retry attempts.
        base_delay: Minimum wait seconds.
        max_delay: Maximum wait seconds.
        exceptions: Exception types to retry on.
        random_wait: Use randomized exponential wait (prevents thundering herd).
    """
    import logging as stdlib_logging

    retry_exceptions = tuple(exceptions) if exceptions else _DEFAULT_RETRY_EXCEPTIONS

    wait_strategy = (
        wait_random_exponential(min=base_delay, max=max_delay)
        if random_wait
        else wait_exponential(multiplier=base_delay, max=max_delay)
    )

    def decorator(func: F) -> F:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(max_retries + 1),
                wait=wait_strategy,
                retry=retry_if_exception_type(retry_exceptions),
                before_sleep=before_sleep_log(
                    stdlib_logging.getLogger(func.__module__),
                    stdlib_logging.WARNING,
                ),
                reraise=True,
            ):
                with attempt:
                    return await func(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator


# ---------------------------------------------------------------------------
# Context-manager based retry (for use inside async code without decorators)
# ---------------------------------------------------------------------------


async def retry_call(
    coro_func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: Sequence[type[Exception]] | None = None,
    **kwargs: Any,
) -> Any:
    """
    Retry an async callable imperatively (without decorating).

    Args:
        coro_func: Async function to call.
        *args: Positional arguments to pass to coro_func.
        max_retries: Max retries.
        base_delay: Starting delay.
        max_delay: Max delay.
        exceptions: Exception types to retry.
        **kwargs: Keyword arguments to pass to coro_func.

    Returns:
        Result of coro_func on success.

    Raises:
        The last caught exception after all retries exhausted.
    """
    decorated = retry_with_backoff(
        max_retries=max_retries,
        base_delay=base_delay,
        max_delay=max_delay,
        exceptions=list(exceptions) if exceptions else None,
    )(coro_func)
    return await decorated(*args, **kwargs)


# ---------------------------------------------------------------------------
# Exchange-specific retry configurations
# ---------------------------------------------------------------------------


def exchange_retry(exchange_id: str = "generic") -> Callable[[F], F]:
    """
    Return a retry decorator pre-configured for exchange API calls.
    Different exchanges have different error patterns and rate limits.
    """
    # Import here to avoid circular imports
    try:
        import ccxt
        exchange_exceptions: list[type[Exception]] = [
            ccxt.NetworkError,
            ccxt.ExchangeNotAvailable,
            ccxt.RequestTimeout,
            ccxt.DDoSProtection,
            ConnectionError,
            TimeoutError,
            asyncio.TimeoutError,
        ]
    except ImportError:
        exchange_exceptions = [ConnectionError, TimeoutError, asyncio.TimeoutError]

    configs: dict[str, dict[str, Any]] = {
        "binance": {"max_retries": 5, "base_delay": 0.5, "max_delay": 30.0},
        "bybit": {"max_retries": 4, "base_delay": 1.0, "max_delay": 30.0},
        "okx": {"max_retries": 4, "base_delay": 1.0, "max_delay": 30.0},
        "oanda": {"max_retries": 3, "base_delay": 2.0, "max_delay": 60.0},
        "alpaca": {"max_retries": 3, "base_delay": 1.0, "max_delay": 30.0},
        "zerodha": {"max_retries": 3, "base_delay": 2.0, "max_delay": 60.0},
        "generic": {"max_retries": 3, "base_delay": 1.0, "max_delay": 30.0},
    }

    cfg = configs.get(exchange_id, configs["generic"])
    return retry_with_backoff(exceptions=exchange_exceptions, **cfg)
