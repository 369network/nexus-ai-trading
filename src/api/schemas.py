"""
NEXUS ALPHA — API Pydantic v2 Schemas
=======================================
Request/response models for the FastAPI server with camelCase aliases,
field validation, and Swagger example values.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ---------------------------------------------------------------------------
# Base model with camelCase alias support
# ---------------------------------------------------------------------------


def _to_camel(snake: str) -> str:
    """Convert snake_case to camelCase."""
    components = snake.split("_")
    return components[0] + "".join(x.title() for x in components[1:])


class _Base(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=_to_camel,
        json_encoders={datetime: lambda v: v.isoformat()},
    )


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(_Base):
    status: str = Field(..., examples=["ok"])
    version: str = Field(..., examples=["0.1.0"])
    uptime_seconds: float = Field(..., examples=[3600.5])
    database: str = Field(..., examples=["connected"])
    timestamp: datetime


# ---------------------------------------------------------------------------
# Portfolio
# ---------------------------------------------------------------------------


class PositionSummary(_Base):
    symbol: str = Field(..., examples=["BTCUSDT"])
    direction: str = Field(..., examples=["LONG"])
    size: float = Field(..., examples=[0.05])
    entry_price: float = Field(..., examples=[65000.0])
    current_price: float = Field(..., examples=[66200.0])
    unrealized_pnl: float = Field(..., examples=[60.0])
    unrealized_pnl_pct: float = Field(..., examples=[1.85])
    market: str = Field(..., examples=["crypto"])
    opened_at: datetime


class PortfolioSummaryResponse(_Base):
    total_equity: float = Field(..., examples=[105000.0])
    cash: float = Field(..., examples=[55000.0])
    positions_value: float = Field(..., examples=[50000.0])
    unrealized_pnl: float = Field(..., examples=[1250.0])
    unrealized_pnl_pct: float = Field(..., examples=[2.5])
    realized_pnl_today: float = Field(..., examples=[450.0])
    total_pnl: float = Field(..., examples=[5000.0])
    total_pnl_pct: float = Field(..., examples=[5.0])
    peak_equity: float = Field(..., examples=[106000.0])
    drawdown_pct: float = Field(..., examples=[0.94])
    open_positions: List[PositionSummary] = Field(default_factory=list)
    n_open_positions: int = Field(..., examples=[3])
    daily_pnl_series: List[Dict[str, Any]] = Field(default_factory=list)
    mode: str = Field(..., examples=["paper"])
    timestamp: datetime


# ---------------------------------------------------------------------------
# Trades
# ---------------------------------------------------------------------------


class TradeResponse(_Base):
    trade_id: str = Field(..., examples=["abc12345"])
    symbol: str = Field(..., examples=["BTCUSDT"])
    market: str = Field(..., examples=["crypto"])
    direction: str = Field(..., examples=["LONG"])
    status: str = Field(..., examples=["closed"])
    entry_time: datetime
    exit_time: Optional[datetime] = None
    entry_price: float = Field(..., examples=[65000.0])
    exit_price: Optional[float] = Field(None, examples=[66200.0])
    size: float = Field(..., examples=[0.05])
    realized_pnl: Optional[float] = Field(None, examples=[60.0])
    commission: float = Field(..., examples=[6.5])
    slippage: float = Field(..., examples=[1.2])
    exit_reason: Optional[str] = Field(None, examples=["take_profit"])
    signal_id: Optional[str] = Field(None, examples=["sig-9999"])


class TradeListResponse(_Base):
    trades: List[TradeResponse]
    total: int = Field(..., examples=[42])
    page: int = Field(..., examples=[1])
    limit: int = Field(..., examples=[50])


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------


class AgentVoteSchema(_Base):
    agent_name: str = Field(..., examples=["TrendFollower"])
    vote: str = Field(..., examples=["LONG"])
    confidence: float = Field(..., ge=0.0, le=1.0, examples=[0.78])
    reasoning: str = Field(..., examples=["Momentum positive, RSI not overbought"])
    model_used: Optional[str] = Field(None, examples=["claude-3-5-sonnet"])


class SignalResponse(_Base):
    signal_id: str = Field(..., examples=["sig-abc123"])
    symbol: str = Field(..., examples=["BTCUSDT"])
    market: str = Field(..., examples=["crypto"])
    timeframe: str = Field(..., examples=["1h"])
    action: str = Field(..., examples=["BUY"])
    confidence: float = Field(..., ge=0.0, le=1.0, examples=[0.74])
    entry_price: Optional[float] = Field(None, examples=[65100.0])
    stop_loss: Optional[float] = Field(None, examples=[63500.0])
    take_profit: Optional[float] = Field(None, examples=[68000.0])
    size_fraction: float = Field(..., examples=[0.25])
    agent_votes: List[AgentVoteSchema] = Field(default_factory=list)
    consensus_method: str = Field(..., examples=["weighted_majority"])
    generated_at: datetime
    expires_at: Optional[datetime] = None
    status: str = Field(..., examples=["pending"])
    metadata: Dict[str, Any] = Field(default_factory=dict)


class SignalListResponse(_Base):
    signals: List[SignalResponse]
    total: int = Field(..., examples=[15])
    limit: int = Field(..., examples=[50])


# ---------------------------------------------------------------------------
# Agent debates
# ---------------------------------------------------------------------------


class DebateRoundSchema(_Base):
    round_number: int = Field(..., examples=[1])
    agent_name: str = Field(..., examples=["ContrarianHedge"])
    argument: str = Field(..., examples=["RSI divergence suggests short-term reversal"])
    position: str = Field(..., examples=["SHORT"])
    confidence: float = Field(..., examples=[0.65])


class AgentDebateResponse(_Base):
    debate_id: str = Field(..., examples=["deb-xyz789"])
    symbol: str = Field(..., examples=["ETHUSDT"])
    market: str = Field(..., examples=["crypto"])
    final_verdict: str = Field(..., examples=["LONG"])
    final_confidence: float = Field(..., examples=[0.71])
    rounds: List[DebateRoundSchema] = Field(default_factory=list)
    duration_ms: int = Field(..., examples=[4200])
    created_at: datetime


class DebateListResponse(_Base):
    debates: List[AgentDebateResponse]
    total: int = Field(..., examples=[8])
    limit: int = Field(..., examples=[20])


# ---------------------------------------------------------------------------
# Risk
# ---------------------------------------------------------------------------


class CircuitBreakerStatus(_Base):
    symbol: str = Field(..., examples=["BTCUSDT"])
    is_tripped: bool = Field(..., examples=[False])
    reason: Optional[str] = Field(None, examples=["Volatility spike"])
    tripped_at: Optional[datetime] = None
    auto_reset_at: Optional[datetime] = None


class RiskMetricsResponse(_Base):
    total_equity: float = Field(..., examples=[105000.0])
    peak_equity: float = Field(..., examples=[107000.0])
    current_drawdown_pct: float = Field(..., examples=[1.87])
    max_drawdown_pct: float = Field(..., examples=[4.2])
    daily_pnl: float = Field(..., examples=[450.0])
    daily_pnl_pct: float = Field(..., examples=[0.43])
    daily_loss_limit_pct: float = Field(..., examples=[3.0])
    daily_loss_remaining_pct: float = Field(..., examples=[2.57])
    weekly_pnl_pct: float = Field(..., examples=[1.2])
    open_positions_count: int = Field(..., examples=[3])
    max_position_size_pct: float = Field(..., examples=[10.0])
    total_exposure_pct: float = Field(..., examples=[28.5])
    var_95_pct: Optional[float] = Field(None, examples=[1.8])
    sharpe_rolling_30d: Optional[float] = Field(None, examples=[1.45])
    circuit_breakers: List[CircuitBreakerStatus] = Field(default_factory=list)
    system_status: str = Field(..., examples=["normal"])
    emergency_stop_active: bool = Field(..., examples=[False])
    timestamp: datetime


# ---------------------------------------------------------------------------
# Performance
# ---------------------------------------------------------------------------


class PerformanceDataPoint(_Base):
    date: str = Field(..., examples=["2024-01-15"])
    equity: float = Field(..., examples=[103500.0])
    daily_pnl: float = Field(..., examples=[350.0])
    daily_return_pct: float = Field(..., examples=[0.34])
    cumulative_return_pct: float = Field(..., examples=[3.5])
    drawdown_pct: float = Field(..., examples=[0.5])


class PerformanceResponse(_Base):
    period_days: int = Field(..., examples=[30])
    total_return_pct: float = Field(..., examples=[5.2])
    sharpe_ratio: float = Field(..., examples=[1.35])
    sortino_ratio: float = Field(..., examples=[1.82])
    max_drawdown_pct: float = Field(..., examples=[3.1])
    win_rate_pct: float = Field(..., examples=[58.3])
    total_trades: int = Field(..., examples=[47])
    profit_factor: float = Field(..., examples=[1.65])
    data_points: List[PerformanceDataPoint] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Feature flags
# ---------------------------------------------------------------------------


class FeatureFlagResponse(_Base):
    flag_name: str = Field(..., examples=["dream_mode"])
    enabled: bool = Field(..., examples=[False])
    description: str = Field(..., examples=["Enable Dream Mode strategy optimization"])
    last_updated: Optional[datetime] = None
    updated_by: Optional[str] = Field(None, examples=["admin"])


class FeatureFlagsListResponse(_Base):
    flags: Dict[str, bool]
    count: int = Field(..., examples=[8])


class FeatureFlagUpdateRequest(_Base):
    enabled: bool = Field(..., examples=[True])

    @field_validator("enabled")
    @classmethod
    def validate_bool(cls, v: bool) -> bool:
        return v


class FeatureFlagUpdateResponse(_Base):
    flag_name: str
    enabled: bool
    updated_at: datetime
    message: str = Field(..., examples=["Flag updated successfully"])


# ---------------------------------------------------------------------------
# Emergency stop
# ---------------------------------------------------------------------------


class EmergencyStopRequest(_Base):
    reason: str = Field(..., min_length=5, max_length=500, examples=["Manual emergency stop triggered by operator"])
    close_positions: bool = Field(default=True, examples=[True])
    operator: Optional[str] = Field(None, examples=["risk_team"])


class EmergencyStopResponse(_Base):
    acknowledged: bool = Field(..., examples=[True])
    reason: str
    positions_closed: int = Field(..., examples=[3])
    timestamp: datetime
    message: str = Field(..., examples=["Emergency stop activated. All positions will be closed."])


# ---------------------------------------------------------------------------
# Circuit breakers (list)
# ---------------------------------------------------------------------------


class CircuitBreakersResponse(_Base):
    circuit_breakers: List[CircuitBreakerStatus]
    any_tripped: bool = Field(..., examples=[False])
    timestamp: datetime


# ---------------------------------------------------------------------------
# WebSocket message schemas
# ---------------------------------------------------------------------------


class WSSignalMessage(_Base):
    """Schema for WebSocket signal stream messages."""
    type: str = Field(default="signal", examples=["signal"])
    data: SignalResponse
    timestamp: datetime


class WSTradeMessage(_Base):
    """Schema for WebSocket trade stream messages."""
    type: str = Field(default="trade", examples=["trade"])
    data: TradeResponse
    timestamp: datetime


class WSRiskMessage(_Base):
    """Schema for WebSocket risk stream messages (sent every 5s)."""
    type: str = Field(default="risk_update", examples=["risk_update"])
    data: RiskMetricsResponse
    timestamp: datetime


# ---------------------------------------------------------------------------
# Error response
# ---------------------------------------------------------------------------


class ErrorResponse(_Base):
    error: str = Field(..., examples=["Not found"])
    detail: Optional[str] = Field(None, examples=["Trade with id 'abc' does not exist"])
    code: int = Field(..., examples=[404])
    timestamp: datetime
