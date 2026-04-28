"""
NEXUS ALPHA — SQLAlchemy 2.0 Async ORM Models
===============================================
All database models matching the PRD schema.
Uses SQLAlchemy 2.0 async mapped classes.

Tables:
  - market_data (partitioned by market)
  - signals
  - trades
  - agent_decisions
  - risk_events
  - portfolio_snapshots
  - model_performance
  - memory_entries
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, MappedColumn, mapped_column, relationship
from sqlalchemy.sql import func


# ---------------------------------------------------------------------------
# Shared enumerations
# ---------------------------------------------------------------------------

MarketType = SAEnum(
    "crypto", "forex", "commodities", "indian_stocks", "us_stocks",
    name="market_type",
)

SignalType = SAEnum(
    "entry", "exit", "scale_in", "scale_out", "stop_adjust",
    name="signal_type",
)

DirectionType = SAEnum("long", "short", "flat", name="direction_type")

TradeStatus = SAEnum(
    "pending", "open", "closed", "cancelled", "error",
    name="trade_status",
)

OrderType = SAEnum(
    "market", "limit", "stop", "stop_limit", "trailing_stop",
    name="order_type",
)

RiskEventSeverity = SAEnum(
    "info", "warning", "critical", "emergency",
    name="risk_event_severity",
)

MemoryTier = SAEnum("short", "medium", "long", name="memory_tier")

AgentDecisionType = SAEnum(
    "trade_signal", "risk_assessment", "portfolio_rebalance",
    "circuit_breaker", "market_analysis", "strategy_selection",
    name="agent_decision_type",
)


# ---------------------------------------------------------------------------
# Declarative base
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """Base class for all NEXUS ALPHA ORM models."""
    pass


# ---------------------------------------------------------------------------
# 1. MarketData (partitioned by market)
# ---------------------------------------------------------------------------


class MarketData(Base):
    """
    OHLCV candle data for all instruments.
    Partitioned by market in Postgres for query performance.
    """

    __tablename__ = "market_data"

    __table_args__ = (
        UniqueConstraint("market", "symbol", "interval", "timestamp",
                         name="uq_market_data_symbol_interval_ts"),
        Index("ix_market_data_symbol_ts", "symbol", "timestamp"),
        Index("ix_market_data_market_symbol", "market", "symbol"),
        Index("ix_market_data_timestamp", "timestamp"),
        # Note: PARTITION BY LIST (market) is created via SQL migration
        {"postgresql_partition_by": "LIST (market)"},
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    interval: Mapped[str] = mapped_column(String(10), nullable=False)  # 1m, 5m, 15m, 1h, 4h, 1d
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    open: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    high: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    low: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    close: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    volume: Mapped[float] = mapped_column(Numeric(30, 8), nullable=False)
    vwap: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    trades_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Exchange where data was sourced
    source_exchange: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<MarketData {self.market}/{self.symbol} {self.interval} "
            f"@ {self.timestamp} close={self.close}>"
        )


# ---------------------------------------------------------------------------
# 2. Signal
# ---------------------------------------------------------------------------


class Signal(Base):
    """
    AI-generated trading signals from agent analysis.
    Each signal may (or may not) result in a Trade.
    """

    __tablename__ = "signals"

    __table_args__ = (
        Index("ix_signals_market_symbol", "market", "symbol"),
        Index("ix_signals_created_at", "created_at"),
        Index("ix_signals_strategy", "strategy"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    signal_type: Mapped[str] = mapped_column(SignalType, nullable=False)
    direction: Mapped[str] = mapped_column(DirectionType, nullable=False)

    # Price levels
    strength: Mapped[float] = mapped_column(Float, nullable=False)  # 0.0–1.0
    entry_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    stop_loss: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    take_profit_2: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)

    # Analysis context
    timeframe: Mapped[str] = mapped_column(String(10), nullable=False)
    agent_id: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    model_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Flexible metadata (indicators, scores, etc.)
    metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Lifecycle
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Relationships
    trades: Mapped[list["Trade"]] = relationship(back_populates="signal")

    def __repr__(self) -> str:
        return (
            f"<Signal {self.market}/{self.symbol} {self.direction} "
            f"str={self.strength:.2f} via={self.strategy}>"
        )


# ---------------------------------------------------------------------------
# 3. Trade
# ---------------------------------------------------------------------------


class Trade(Base):
    """
    Executed or simulated trade record.
    Linked to a Signal that generated it.
    """

    __tablename__ = "trades"

    __table_args__ = (
        Index("ix_trades_status", "status"),
        Index("ix_trades_market_symbol", "market", "symbol"),
        Index("ix_trades_opened_at", "opened_at"),
        Index("ix_trades_signal_id", "signal_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    signal_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), ForeignKey("signals.id", ondelete="SET NULL"), nullable=True
    )
    market: Mapped[str] = mapped_column(String(30), nullable=False)
    symbol: Mapped[str] = mapped_column(String(30), nullable=False)
    side: Mapped[str] = mapped_column(DirectionType, nullable=False)
    order_type: Mapped[str] = mapped_column(OrderType, nullable=False)
    status: Mapped[str] = mapped_column(TradeStatus, nullable=False, default="pending")

    # Order details
    size: Mapped[float] = mapped_column(Numeric(30, 8), nullable=False)
    entry_price: Mapped[float] = mapped_column(Numeric(20, 8), nullable=False)
    exit_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    stop_loss: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    take_profit: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)

    # Fills
    filled_price: Mapped[Optional[float]] = mapped_column(Numeric(20, 8), nullable=True)
    fill_timestamp: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    slippage_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # P&L
    pnl_usd: Mapped[Optional[float]] = mapped_column(Numeric(20, 4), nullable=True)
    pnl_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    commission_usd: Mapped[Optional[float]] = mapped_column(Numeric(20, 4), nullable=True)
    net_pnl_usd: Mapped[Optional[float]] = mapped_column(Numeric(20, 4), nullable=True)

    # Context
    strategy: Mapped[str] = mapped_column(String(50), nullable=False)
    exchange: Mapped[str] = mapped_column(String(30), nullable=False)
    exchange_order_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    paper_trade: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    # Timing
    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    closed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    holding_seconds: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    # Flexible metadata
    metadata: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Relationships
    signal: Mapped[Optional["Signal"]] = relationship(back_populates="trades")

    def __repr__(self) -> str:
        return (
            f"<Trade {self.market}/{self.symbol} {self.side} "
            f"size={self.size} status={self.status}>"
        )


# ---------------------------------------------------------------------------
# 4. AgentDecision
# ---------------------------------------------------------------------------


class AgentDecision(Base):
    """
    Audit log of AI agent decisions.
    Tracks every LLM call for cost tracking, calibration, and post-mortems.
    """

    __tablename__ = "agent_decisions"

    __table_args__ = (
        Index("ix_agent_decisions_agent_name", "agent_name"),
        Index("ix_agent_decisions_created_at", "created_at"),
        Index("ix_agent_decisions_market", "market"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False)
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    market: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    decision_type: Mapped[str] = mapped_column(AgentDecisionType, nullable=False)

    # LLM inputs/outputs (stored for audit + learning)
    input_context: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    output_reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    structured_output: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Quality metrics
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    action_taken: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    outcome: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)  # correct|incorrect|unknown

    # Cost tracking
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    llm_cost_usd: Mapped[Optional[float]] = mapped_column(Numeric(10, 6), nullable=True)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<AgentDecision agent={self.agent_name} type={self.decision_type} "
            f"model={self.model_id}>"
        )


# ---------------------------------------------------------------------------
# 5. RiskEvent
# ---------------------------------------------------------------------------


class RiskEvent(Base):
    """
    Records risk management events: circuit breakers, drawdown warnings,
    position limit hits, correlation spikes, etc.
    """

    __tablename__ = "risk_events"

    __table_args__ = (
        Index("ix_risk_events_event_type", "event_type"),
        Index("ix_risk_events_created_at", "created_at"),
        Index("ix_risk_events_severity", "severity"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    circuit_breaker_id: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    market: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    severity: Mapped[str] = mapped_column(RiskEventSeverity, nullable=False)

    # Trigger data
    trigger_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    threshold_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Response
    action_taken: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    positions_affected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Details
    details: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    resolved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<RiskEvent {self.event_type} severity={self.severity} "
            f"cb={self.circuit_breaker_id}>"
        )


# ---------------------------------------------------------------------------
# 6. PortfolioSnapshot
# ---------------------------------------------------------------------------


class PortfolioSnapshot(Base):
    """
    Periodic snapshots of portfolio state (equity, P&L, exposure).
    Used for performance tracking and reporting.
    """

    __tablename__ = "portfolio_snapshots"

    __table_args__ = (
        Index("ix_portfolio_snapshots_snapshot_at", "snapshot_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    snapshot_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # Capital
    total_equity: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    cash_balance: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    invested_value: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)

    # P&L
    unrealized_pnl: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    realized_pnl_today: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    realized_pnl_total: Mapped[float] = mapped_column(Numeric(20, 4), nullable=False)
    daily_return_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Positions
    positions_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    market_exposure: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Risk metrics
    drawdown_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    peak_equity: Mapped[Optional[float]] = mapped_column(Numeric(20, 4), nullable=True)
    sharpe_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sortino_ratio: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Environment
    paper_mode: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    def __repr__(self) -> str:
        return (
            f"<PortfolioSnapshot equity={self.total_equity:.2f} "
            f"at={self.snapshot_at}>"
        )


# ---------------------------------------------------------------------------
# 7. ModelPerformance (Brier scores + accuracy tracking)
# ---------------------------------------------------------------------------


class ModelPerformance(Base):
    """
    Tracks LLM/ML model prediction quality over time.
    Brier scores measure probability calibration.
    """

    __tablename__ = "model_performance"

    __table_args__ = (
        Index("ix_model_performance_model_id", "model_id"),
        Index("ix_model_performance_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    model_id: Mapped[str] = mapped_column(String(100), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False)
    market: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)

    # Evaluation period
    period: Mapped[str] = mapped_column(String(20), nullable=False)  # daily | weekly | monthly
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Calibration metrics
    brier_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    brier_skill_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Classification metrics
    accuracy: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    precision: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    recall: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    f1_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    # Sample counts
    total_predictions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    correct_predictions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Financial performance of model's signals
    avg_trade_pnl_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    win_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<ModelPerformance model={self.model_id} brier={self.brier_score:.4f} "
            f"period={self.period}>"
        )


# ---------------------------------------------------------------------------
# 8. MemoryEntry (Agent Memory — short/medium/long tier)
# ---------------------------------------------------------------------------


class MemoryEntry(Base):
    """
    Agent memory storage with three-tier retention system.
    short:  high-detail recent context (24h)
    medium: compressed summaries (30 days)
    long:   distilled lessons learned (1 year)
    """

    __tablename__ = "memory_entries"

    __table_args__ = (
        Index("ix_memory_entries_agent_name", "agent_name"),
        Index("ix_memory_entries_memory_tier", "memory_tier"),
        Index("ix_memory_entries_market", "market"),
        Index("ix_memory_entries_created_at", "created_at"),
        Index("ix_memory_entries_expires_at", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    agent_name: Mapped[str] = mapped_column(String(50), nullable=False)
    memory_tier: Mapped[str] = mapped_column(MemoryTier, nullable=False)

    # Content
    content: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Context tags
    market: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    symbol: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    tags: Mapped[Optional[dict]] = mapped_column(JSONB, nullable=True)

    # Quality / relevance
    importance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    access_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_accessed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Retention
    expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # For semantic search (pgvector — dimension set in migration)
    # embedding: Mapped[Optional[list[float]]] = mapped_column(Vector(1536), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    def __repr__(self) -> str:
        return (
            f"<MemoryEntry agent={self.agent_name} tier={self.memory_tier} "
            f"importance={self.importance_score:.2f}>"
        )
