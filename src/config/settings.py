"""
NEXUS ALPHA - Configuration Settings
=====================================
Loads all configuration from environment variables with sensible defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Dict, List

import logging

logger = logging.getLogger(__name__)


@dataclass
class MarketConfig:
    name: str
    symbols: List[str]
    timeframes: List[str]
    enabled: bool = True


@dataclass
class Settings:
    # Core
    paper_mode: bool
    log_level: str

    # Supabase
    supabase_url: str
    supabase_anon_key: str
    supabase_service_key: str
    database_url: str

    # LLM providers
    anthropic_api_key: str
    openai_api_key: str
    qwen_api_key: str
    ollama_host: str
    ollama_model: str
    max_daily_llm_cost: float

    # Exchange: Bybit
    bybit_api_key: str
    bybit_secret: str
    bybit_testnet: bool

    # Exchange: Binance
    binance_api_key: str
    binance_secret: str
    binance_testnet: bool

    # Notifications
    telegram_bot_token: str
    telegram_chat_id: str
    discord_webhook_url: str

    # Markets
    enabled_markets: Dict[str, MarketConfig]

    # Cache
    redis_url: str

    # Paper trading capital (must be last — has default, others above don't)
    initial_capital: float = 5_000.0


def _bool_env(key: str, default: bool) -> bool:
    """Parse a boolean environment variable."""
    val = os.getenv(key, "").strip().lower()
    if not val:
        return default
    return val in ("1", "true", "yes", "on")


def _float_env(key: str, default: float) -> float:
    """Parse a float environment variable."""
    try:
        return float(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default


def _build_default_markets() -> Dict[str, MarketConfig]:
    """Build the default enabled markets (crypto only)."""
    return {
        "crypto": MarketConfig(
            name="crypto",
            symbols=["BTC/USDT", "ETH/USDT"],
            timeframes=["15m", "1h", "4h"],
            enabled=True,
        )
    }


async def load_settings() -> Settings:
    """
    Load all settings from environment variables.

    Tries to load a .env file if present (via python-dotenv if installed).
    Falls back to os.environ otherwise.
    """
    # Attempt to load .env file
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        load_dotenv()
    except ImportError:
        logger.debug("python-dotenv not installed — reading from environment only")

    # Parse enabled markets from env or use defaults
    enabled_markets = _build_default_markets()

    settings = Settings(
        # Core
        paper_mode=_bool_env("PAPER_MODE", True),
        log_level=os.getenv("LOG_LEVEL", "INFO").upper(),

        # Supabase
        supabase_url=os.getenv("SUPABASE_URL", ""),
        supabase_anon_key=os.getenv("SUPABASE_ANON_KEY", ""),
        supabase_service_key=os.getenv("SUPABASE_SERVICE_KEY", ""),
        database_url=os.getenv("DATABASE_URL", ""),

        # LLM providers
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        openai_api_key=os.getenv("OPENAI_API_KEY", ""),
        qwen_api_key=os.getenv("QWEN_API_KEY", ""),
        ollama_host=os.getenv("OLLAMA_HOST", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "llama3.2"),
        max_daily_llm_cost=_float_env("MAX_DAILY_LLM_COST", 5.0),

        # Bybit
        bybit_api_key=os.getenv("BYBIT_API_KEY", ""),
        bybit_secret=os.getenv("BYBIT_SECRET", ""),
        bybit_testnet=_bool_env("BYBIT_TESTNET", True),

        # Binance
        binance_api_key=os.getenv("BINANCE_API_KEY", ""),
        binance_secret=os.getenv("BINANCE_SECRET", ""),
        binance_testnet=_bool_env("BINANCE_TESTNET", True),

        # Notifications
        telegram_bot_token=os.getenv("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=os.getenv("TELEGRAM_CHAT_ID", ""),
        discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL", ""),

        # Paper trading capital (override via INITIAL_CAPITAL env var)
        initial_capital=_float_env("INITIAL_CAPITAL", 5_000.0),

        # Markets
        enabled_markets=enabled_markets,

        # Cache
        redis_url=os.getenv("REDIS_URL", "redis://localhost:6379/0"),
    )

    logger.info(
        "Settings loaded: paper_mode=%s, log_level=%s, markets=%s",
        settings.paper_mode,
        settings.log_level,
        list(settings.enabled_markets.keys()),
    )
    return settings
