"""
NEXUS ALPHA — Application Settings
===================================
Pydantic-Settings based configuration loading from environment variables.
All sensitive values are typed as SecretStr and never logged in plaintext.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# Nested sub-models
# ---------------------------------------------------------------------------


class BinanceSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BINANCE_", extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), alias="BINANCE_API_KEY")
    secret: SecretStr = Field(default=SecretStr(""), alias="BINANCE_SECRET")
    testnet: bool = Field(default=True, alias="BINANCE_TESTNET")

    @property
    def base_url(self) -> str:
        return "https://testnet.binance.vision" if self.testnet else "https://api.binance.com"


class BybitSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BYBIT_", extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), alias="BYBIT_API_KEY")
    secret: SecretStr = Field(default=SecretStr(""), alias="BYBIT_SECRET")
    testnet: bool = Field(default=True, alias="BYBIT_TESTNET")


class OKXSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OKX_", extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), alias="OKX_API_KEY")
    secret: SecretStr = Field(default=SecretStr(""), alias="OKX_SECRET")
    passphrase: SecretStr = Field(default=SecretStr(""), alias="OKX_PASSPHRASE")
    sandbox: bool = Field(default=True, alias="OKX_SANDBOX")


class OandaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="OANDA_", extra="ignore")

    account_id: str = Field(default="", alias="OANDA_ACCOUNT_ID")
    access_token: SecretStr = Field(default=SecretStr(""), alias="OANDA_ACCESS_TOKEN")
    practice: bool = Field(default=True, alias="OANDA_PRACTICE")

    @property
    def api_url(self) -> str:
        return (
            "https://api-fxpractice.oanda.com"
            if self.practice
            else "https://api-fxtrade.oanda.com"
        )

    @property
    def stream_url(self) -> str:
        return (
            "https://stream-fxpractice.oanda.com"
            if self.practice
            else "https://stream-fxtrade.oanda.com"
        )


class ZerodhaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ZERODHA_", extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), alias="ZERODHA_API_KEY")
    api_secret: SecretStr = Field(default=SecretStr(""), alias="ZERODHA_API_SECRET")
    user_id: str = Field(default="", alias="ZERODHA_USER_ID")
    totp_secret: SecretStr = Field(default=SecretStr(""), alias="ZERODHA_TOTP_SECRET")


class AlpacaSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ALPACA_", extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), alias="ALPACA_API_KEY")
    secret_key: SecretStr = Field(default=SecretStr(""), alias="ALPACA_SECRET_KEY")
    paper: bool = Field(default=True, alias="ALPACA_PAPER")

    @property
    def base_url(self) -> str:
        return (
            "https://paper-api.alpaca.markets"
            if self.paper
            else "https://api.alpaca.markets"
        )


class IBKRSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="IBKR_", extra="ignore")

    host: str = Field(default="127.0.0.1", alias="IBKR_HOST")
    port: int = Field(default=7497, alias="IBKR_PORT")
    client_id: int = Field(default=1, alias="IBKR_CLIENT_ID")


class AnthropicSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), alias="ANTHROPIC_API_KEY")
    default_model: str = "claude-3-5-sonnet-20241022"
    max_tokens: int = 8192
    temperature: float = 0.1


class OpenAISettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), alias="OPENAI_API_KEY")
    default_model: str = "gpt-4o-mini"
    max_tokens: int = 4096
    temperature: float = 0.1


class QwenSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    api_key: SecretStr = Field(default=SecretStr(""), alias="QWEN_API_KEY")
    default_model: str = "qwen-turbo"


class OllamaSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    model: str = Field(default="llama3:8b", alias="OLLAMA_MODEL")


class LLMSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    anthropic: AnthropicSettings = Field(default_factory=AnthropicSettings)
    openai: OpenAISettings = Field(default_factory=OpenAISettings)
    qwen: QwenSettings = Field(default_factory=QwenSettings)
    ollama: OllamaSettings = Field(default_factory=OllamaSettings)
    max_daily_cost_usd: float = Field(default=10.0, alias="MAX_DAILY_LLM_COST_USD")

    @field_validator("max_daily_cost_usd")
    @classmethod
    def cost_must_be_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("MAX_DAILY_LLM_COST_USD must be positive")
        return v


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    url: SecretStr = Field(
        default=SecretStr("postgresql+asyncpg://postgres:postgres@localhost:5432/nexus"),
        alias="DATABASE_URL",
    )
    supabase_url: str = Field(default="", alias="SUPABASE_URL")
    supabase_anon_key: SecretStr = Field(default=SecretStr(""), alias="SUPABASE_ANON_KEY")
    supabase_service_key: SecretStr = Field(
        default=SecretStr(""), alias="SUPABASE_SERVICE_KEY"
    )
    pool_size: int = 10
    max_overflow: int = 20
    pool_timeout: int = 30
    pool_recycle: int = 1800


class RedisSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    max_connections: int = 20


class TelegramSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    bot_token: SecretStr = Field(default=SecretStr(""), alias="TELEGRAM_BOT_TOKEN")
    chat_id: str = Field(default="", alias="TELEGRAM_CHAT_ID")

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token.get_secret_value()) and bool(self.chat_id)


class DiscordSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    webhook_url: SecretStr = Field(default=SecretStr(""), alias="DISCORD_WEBHOOK_URL")

    @property
    def is_configured(self) -> bool:
        return bool(self.webhook_url.get_secret_value())


class AlertSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    telegram: TelegramSettings = Field(default_factory=TelegramSettings)
    discord: DiscordSettings = Field(default_factory=DiscordSettings)
    rate_limit_seconds: int = 60  # Min seconds between same-type alerts


class ExchangeSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    binance: BinanceSettings = Field(default_factory=BinanceSettings)
    bybit: BybitSettings = Field(default_factory=BybitSettings)
    okx: OKXSettings = Field(default_factory=OKXSettings)
    oanda: OandaSettings = Field(default_factory=OandaSettings)
    zerodha: ZerodhaSettings = Field(default_factory=ZerodhaSettings)
    alpaca: AlpacaSettings = Field(default_factory=AlpacaSettings)
    ibkr: IBKRSettings = Field(default_factory=IBKRSettings)


class DataFeedSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    whale_alert_api_key: SecretStr = Field(
        default=SecretStr(""), alias="WHALE_ALERT_API_KEY"
    )


class DeploymentSettings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    vps_host: str = Field(default="", alias="VPS_HOST")
    vps_user: str = Field(default="ubuntu", alias="VPS_USER")


# ---------------------------------------------------------------------------
# Root settings object
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    """
    Central configuration for NEXUS ALPHA.

    Load order (later overrides earlier):
      1. Field defaults
      2. .env file
      3. Actual environment variables
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Runtime
    environment: Literal["paper", "live"] = Field(default="paper", alias="ENVIRONMENT")
    paper_mode: bool = Field(default=True, alias="PAPER_MODE")
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"] = Field(
        default="INFO", alias="LOG_LEVEL"
    )

    # Nested sub-settings
    exchanges: ExchangeSettings = Field(default_factory=ExchangeSettings)
    llm: LLMSettings = Field(default_factory=LLMSettings)
    database: DatabaseSettings = Field(default_factory=DatabaseSettings)
    redis: RedisSettings = Field(default_factory=RedisSettings)
    alerts: AlertSettings = Field(default_factory=AlertSettings)
    data_feeds: DataFeedSettings = Field(default_factory=DataFeedSettings)
    deployment: DeploymentSettings = Field(default_factory=DeploymentSettings)

    @model_validator(mode="after")
    def validate_live_mode(self) -> "Settings":
        """Prevent accidental live trading without explicit confirmation."""
        if self.environment == "live" and self.paper_mode:
            raise ValueError(
                "Conflict: ENVIRONMENT=live but PAPER_MODE=true. "
                "Set PAPER_MODE=false to explicitly enable live trading."
            )
        if self.environment == "paper" and not self.paper_mode:
            # Auto-correct: paper env should always have paper_mode=True
            object.__setattr__(self, "paper_mode", True)
        return self

    @property
    def is_live(self) -> bool:
        return self.environment == "live" and not self.paper_mode

    @property
    def is_paper(self) -> bool:
        return not self.is_live


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_settings: Settings | None = None


def get_settings() -> Settings:
    """Return the cached Settings singleton."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Force re-load of settings (useful in tests)."""
    global _settings
    _settings = None
