"""
NEXUS ALPHA — Structured Logging
=================================
Structlog-based logging with:
- JSON output in production (machine-readable)
- Console renderer in development (human-readable with colors)
- Request-ID / context binding
- Async-safe processors
- Automatic exception formatting
"""

from __future__ import annotations

import logging
import sys
import uuid
from contextvars import ContextVar
from typing import Any

import structlog
from structlog.types import EventDict, Processor, WrappedLogger

# ---------------------------------------------------------------------------
# Context variable for request/task tracking across async calls
# ---------------------------------------------------------------------------
_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_trade_id_var: ContextVar[str] = ContextVar("trade_id", default="")
_agent_var: ContextVar[str] = ContextVar("agent", default="")


def set_request_id(request_id: str | None = None) -> str:
    """Set (or generate) a request ID in the current async context."""
    rid = request_id or str(uuid.uuid4())[:8]
    _request_id_var.set(rid)
    return rid


def set_trade_id(trade_id: str) -> None:
    """Bind a trade ID to the current context."""
    _trade_id_var.set(trade_id)


def set_agent(agent_name: str) -> None:
    """Bind an agent name to the current context."""
    _agent_var.set(agent_name)


def clear_context() -> None:
    """Clear all bound context variables."""
    _request_id_var.set("")
    _trade_id_var.set("")
    _agent_var.set("")


# ---------------------------------------------------------------------------
# Custom processors
# ---------------------------------------------------------------------------


def _add_context_vars(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Inject context-var values (request_id, trade_id, agent) into every log record."""
    rid = _request_id_var.get()
    tid = _trade_id_var.get()
    agent = _agent_var.get()

    if rid:
        event_dict["request_id"] = rid
    if tid:
        event_dict["trade_id"] = tid
    if agent:
        event_dict["agent"] = agent

    return event_dict


def _censor_secrets(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Redact common secret key patterns to prevent accidental logging."""
    _SECRET_KEYS = frozenset(
        {
            "api_key",
            "secret",
            "secret_key",
            "api_secret",
            "password",
            "token",
            "access_token",
            "private_key",
            "totp_secret",
            "webhook_url",
        }
    )
    for key in list(event_dict.keys()):
        if any(s in key.lower() for s in _SECRET_KEYS):
            event_dict[key] = "***REDACTED***"
    return event_dict


def _drop_color_message_key(
    logger: WrappedLogger, method_name: str, event_dict: EventDict
) -> EventDict:
    """Remove Uvicorn's duplicate 'color_message' key if present."""
    event_dict.pop("color_message", None)
    return event_dict


# ---------------------------------------------------------------------------
# Setup function
# ---------------------------------------------------------------------------


def setup_logging(
    log_level: str = "INFO",
    json_output: bool | None = None,
    service_name: str = "nexus-alpha",
) -> None:
    """
    Configure structlog for the entire application.

    Args:
        log_level: Logging level string (DEBUG, INFO, WARNING, ERROR, CRITICAL).
        json_output: True = JSON (production), False = console (dev).
                     If None, auto-detect based on TTY.
        service_name: Identifies this process in logs.
    """
    level = getattr(logging, log_level.upper(), logging.INFO)

    # Auto-detect output format if not specified
    if json_output is None:
        json_output = not sys.stderr.isatty()

    # ---------------------------------------------------------------------------
    # Shared processors (run for every log event)
    # ---------------------------------------------------------------------------
    shared_processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        _add_context_vars,
        _censor_secrets,
        _drop_color_message_key,
        structlog.stdlib.add_logger_name,
        structlog.stdlib.add_log_level,
        structlog.stdlib.PositionalArgumentsFormatter(),
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
    ]

    if json_output:
        # Production: JSON output for log aggregation systems
        final_processors: list[Processor] = [
            *shared_processors,
            structlog.processors.dict_tracebacks,
            structlog.processors.JSONRenderer(),
        ]
    else:
        # Development: coloured console output
        final_processors = [
            *shared_processors,
            structlog.stdlib.ExceptionRenderer(),
            structlog.dev.ConsoleRenderer(
                colors=True,
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ]

    structlog.configure(
        processors=final_processors,
        wrapper_class=structlog.make_filtering_bound_logger(level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # ---------------------------------------------------------------------------
    # Configure stdlib logging to route through structlog
    # ---------------------------------------------------------------------------
    stdlib_level = logging.getLevelName(level)

    # Root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(message)s"))
    handler.setLevel(level)

    # Remove any existing handlers
    root_logger.handlers.clear()
    root_logger.addHandler(handler)

    # Quieten noisy third-party loggers
    _quiet_loggers = [
        "urllib3",
        "httpx",
        "httpcore",
        "asyncio",
        "websockets",
        "ccxt",
        "telegram",
        "apscheduler",
        "sqlalchemy.engine",
        "hpack",
    ]
    for name in _quiet_loggers:
        logging.getLogger(name).setLevel(logging.WARNING)

    # Log startup
    log = get_logger(__name__)
    log.info(
        "Logging configured",
        level=log_level,
        json_output=json_output,
        service=service_name,
    )


# ---------------------------------------------------------------------------
# Factory helper
# ---------------------------------------------------------------------------


def get_logger(name: str, **initial_values: Any) -> structlog.BoundLogger:
    """
    Return a structlog BoundLogger pre-bound with name and optional context.

    Usage:
        log = get_logger(__name__, module="data_collector")
        log.info("starting", symbol="BTC/USDT")
    """
    logger = structlog.get_logger(name)
    if initial_values:
        logger = logger.bind(**initial_values)
    return logger  # type: ignore[return-value]


def get_trade_logger(trade_id: str, symbol: str, market: str) -> structlog.BoundLogger:
    """Return a logger pre-bound with trade context fields."""
    return get_logger(
        "nexus.trade",
        trade_id=trade_id,
        symbol=symbol,
        market=market,
    )


def get_agent_logger(agent_name: str) -> structlog.BoundLogger:
    """Return a logger pre-bound with agent name."""
    return get_logger(f"nexus.agent.{agent_name}", agent=agent_name)
