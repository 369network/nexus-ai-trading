"""Initial schema — all NEXUS ALPHA tables.

Revision ID: 001_initial_schema
Revises:
Create Date: 2026-04-27 00:00:00.000000

Creates all database tables defined in src/db/models.py:
  - market_data (partitioned by market)
  - signals
  - trades
  - agent_decisions
  - risk_events
  - portfolio_snapshots
  - model_performance
  - memory_entries

Also creates:
  - All indexes for query performance
  - Enum types
  - Partition tables for market_data
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ---------------------------------------------------------------------------
# Revision metadata
# ---------------------------------------------------------------------------

revision = "001_initial_schema"
down_revision = None          # First migration — no parent
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
    """Create all tables, types, indexes, and partitions."""

    # ------------------------------------------------------------------
    # 1. PostgreSQL Enum types
    # ------------------------------------------------------------------

    market_type = postgresql.ENUM(
        "crypto", "forex", "commodities", "indian_stocks", "us_stocks",
        name="market_type",
        create_type=True,
    )

    signal_type = postgresql.ENUM(
        "entry", "exit", "scale_in", "scale_out", "stop_adjust",
        name="signal_type",
        create_type=True,
    )

    direction_type = postgresql.ENUM(
        "long", "short", "flat",
        name="direction_type",
        create_type=True,
    )

    trade_status = postgresql.ENUM(
        "pending", "open", "closed", "cancelled", "error",
        name="trade_status",
        create_type=True,
    )

    order_type = postgresql.ENUM(
        "market", "limit", "stop", "stop_limit", "trailing_stop",
        name="order_type",
        create_type=True,
    )

    risk_event_severity = postgresql.ENUM(
        "info", "warning", "critical", "emergency",
        name="risk_event_severity",
        create_type=True,
    )

    memory_tier = postgresql.ENUM(
        "short", "medium", "long",
        name="memory_tier",
        create_type=True,
    )

    agent_decision_type = postgresql.ENUM(
        "trade_signal", "risk_assessment", "portfolio_rebalance",
        "circuit_breaker", "market_analysis", "strategy_selection",
        name="agent_decision_type",
        create_type=True,
    )

    # Create enum types in the database
    for enum_type in [
        market_type, signal_type, direction_type, trade_status,
        order_type, risk_event_severity, memory_tier, agent_decision_type,
    ]:
        enum_type.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 2. market_data — partitioned by market
    #    Partition strategy: LIST partitioning on the market column.
    #    Each market gets its own partition for targeted query performance.
    # ------------------------------------------------------------------

    # Parent partitioned table (cannot store rows directly)
    op.execute("""
        CREATE TABLE IF NOT EXISTS market_data (
            id           UUID NOT NULL DEFAULT gen_random_uuid(),
            market       VARCHAR(30) NOT NULL,
            symbol       VARCHAR(30) NOT NULL,
            interval     VARCHAR(10) NOT NULL,
            timestamp    TIMESTAMPTZ NOT NULL,
            open         NUMERIC(20, 8) NOT NULL,
            high         NUMERIC(20, 8) NOT NULL,
            low          NUMERIC(20, 8) NOT NULL,
            close        NUMERIC(20, 8) NOT NULL,
            volume       NUMERIC(30, 8) NOT NULL,
            vwap         NUMERIC(20, 8),
            trades_count INTEGER,
            source_exchange VARCHAR(30),
            created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            PRIMARY KEY (id, market)
        ) PARTITION BY LIST (market)
    """)

    # Create one partition per market
    _market_partitions = [
        ("crypto", "crypto"),
        ("forex", "forex"),
        ("commodities", "commodities"),
        ("indian_stocks", "indian_stocks"),
        ("us_stocks", "us_stocks"),
    ]

    for partition_name, market_value in _market_partitions:
        op.execute(f"""
            CREATE TABLE IF NOT EXISTS market_data_{partition_name}
            PARTITION OF market_data
            FOR VALUES IN ('{market_value}')
        """)

    # Indexes on the parent table (automatically apply to partitions in PG 11+)
    op.create_index(
        "uq_market_data_symbol_interval_ts",
        "market_data",
        ["market", "symbol", "interval", "timestamp"],
        unique=True,
    )
    op.create_index(
        "ix_market_data_symbol_ts",
        "market_data",
        ["symbol", "timestamp"],
    )
    op.create_index(
        "ix_market_data_market_symbol",
        "market_data",
        ["market", "symbol"],
    )
    op.create_index(
        "ix_market_data_timestamp",
        "market_data",
        ["timestamp"],
    )

    # ------------------------------------------------------------------
    # 3. signals
    # ------------------------------------------------------------------

    op.create_table(
        "signals",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("market", sa.String(30), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("strategy", sa.String(50), nullable=False),
        sa.Column("signal_type", sa.Enum(
            "entry", "exit", "scale_in", "scale_out", "stop_adjust",
            name="signal_type", create_type=False,
        ), nullable=False),
        sa.Column("direction", sa.Enum(
            "long", "short", "flat",
            name="direction_type", create_type=False,
        ), nullable=False),
        sa.Column("strength", sa.Float, nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("stop_loss", sa.Numeric(20, 8), nullable=True),
        sa.Column("take_profit", sa.Numeric(20, 8), nullable=True),
        sa.Column("take_profit_2", sa.Numeric(20, 8), nullable=True),
        sa.Column("timeframe", sa.String(10), nullable=False),
        sa.Column("agent_id", sa.String(50), nullable=True),
        sa.Column("model_id", sa.String(100), nullable=True),
        sa.Column("reasoning", sa.Text, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_index("ix_signals_market_symbol", "signals", ["market", "symbol"])
    op.create_index("ix_signals_created_at", "signals", ["created_at"])
    op.create_index("ix_signals_strategy", "signals", ["strategy"])

    # ------------------------------------------------------------------
    # 4. trades
    # ------------------------------------------------------------------

    op.create_table(
        "trades",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("signal_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("signals.id", ondelete="SET NULL"), nullable=True),
        sa.Column("market", sa.String(30), nullable=False),
        sa.Column("symbol", sa.String(30), nullable=False),
        sa.Column("side", sa.Enum(
            "long", "short", "flat",
            name="direction_type", create_type=False,
        ), nullable=False),
        sa.Column("order_type", sa.Enum(
            "market", "limit", "stop", "stop_limit", "trailing_stop",
            name="order_type", create_type=False,
        ), nullable=False),
        sa.Column("status", sa.Enum(
            "pending", "open", "closed", "cancelled", "error",
            name="trade_status", create_type=False,
        ), nullable=False, server_default="pending"),
        sa.Column("size", sa.Numeric(30, 8), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 8), nullable=False),
        sa.Column("exit_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("stop_loss", sa.Numeric(20, 8), nullable=True),
        sa.Column("take_profit", sa.Numeric(20, 8), nullable=True),
        sa.Column("filled_price", sa.Numeric(20, 8), nullable=True),
        sa.Column("fill_timestamp", sa.DateTime(timezone=True), nullable=True),
        sa.Column("slippage_pct", sa.Float, nullable=True),
        sa.Column("pnl_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column("pnl_pct", sa.Float, nullable=True),
        sa.Column("commission_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column("net_pnl_usd", sa.Numeric(20, 4), nullable=True),
        sa.Column("strategy", sa.String(50), nullable=False),
        sa.Column("exchange", sa.String(30), nullable=False),
        sa.Column("exchange_order_id", sa.String(100), nullable=True),
        sa.Column("paper_trade", sa.Boolean, nullable=False, server_default="true"),
        sa.Column("opened_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("holding_seconds", sa.BigInteger, nullable=True),
        sa.Column("metadata", postgresql.JSONB, nullable=True),
    )

    op.create_index("ix_trades_status", "trades", ["status"])
    op.create_index("ix_trades_market_symbol", "trades", ["market", "symbol"])
    op.create_index("ix_trades_opened_at", "trades", ["opened_at"])
    op.create_index("ix_trades_signal_id", "trades", ["signal_id"])
    op.create_index("ix_trades_strategy", "trades", ["strategy"])
    op.create_index("ix_trades_paper_trade", "trades", ["paper_trade"])

    # ------------------------------------------------------------------
    # 5. agent_decisions
    # ------------------------------------------------------------------

    op.create_table(
        "agent_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.String(50), nullable=False),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("market", sa.String(30), nullable=True),
        sa.Column("symbol", sa.String(30), nullable=True),
        sa.Column("decision_type", sa.Enum(
            "trade_signal", "risk_assessment", "portfolio_rebalance",
            "circuit_breaker", "market_analysis", "strategy_selection",
            name="agent_decision_type", create_type=False,
        ), nullable=False),
        sa.Column("input_context", sa.Text, nullable=True),
        sa.Column("output_reasoning", sa.Text, nullable=True),
        sa.Column("structured_output", postgresql.JSONB, nullable=True),
        sa.Column("confidence", sa.Float, nullable=True),
        sa.Column("action_taken", sa.String(100), nullable=True),
        sa.Column("outcome", sa.String(50), nullable=True),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("llm_cost_usd", sa.Numeric(10, 6), nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_index("ix_agent_decisions_agent_name", "agent_decisions", ["agent_name"])
    op.create_index("ix_agent_decisions_created_at", "agent_decisions", ["created_at"])
    op.create_index("ix_agent_decisions_market", "agent_decisions", ["market"])
    op.create_index("ix_agent_decisions_decision_type", "agent_decisions", ["decision_type"])

    # ------------------------------------------------------------------
    # 6. risk_events
    # ------------------------------------------------------------------

    op.create_table(
        "risk_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("event_type", sa.String(50), nullable=False),
        sa.Column("circuit_breaker_id", sa.String(10), nullable=True),
        sa.Column("market", sa.String(30), nullable=True),
        sa.Column("symbol", sa.String(30), nullable=True),
        sa.Column("severity", sa.Enum(
            "info", "warning", "critical", "emergency",
            name="risk_event_severity", create_type=False,
        ), nullable=False),
        sa.Column("trigger_value", sa.Float, nullable=True),
        sa.Column("threshold_value", sa.Float, nullable=True),
        sa.Column("action_taken", sa.String(100), nullable=True),
        sa.Column("positions_affected", sa.Integer, nullable=False, server_default="0"),
        sa.Column("details", postgresql.JSONB, nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_index("ix_risk_events_event_type", "risk_events", ["event_type"])
    op.create_index("ix_risk_events_created_at", "risk_events", ["created_at"])
    op.create_index("ix_risk_events_severity", "risk_events", ["severity"])
    op.create_index("ix_risk_events_resolved", "risk_events", ["resolved"])

    # ------------------------------------------------------------------
    # 7. portfolio_snapshots
    # ------------------------------------------------------------------

    op.create_table(
        "portfolio_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("snapshot_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
        sa.Column("total_equity", sa.Numeric(20, 4), nullable=False),
        sa.Column("cash_balance", sa.Numeric(20, 4), nullable=False),
        sa.Column("invested_value", sa.Numeric(20, 4), nullable=False),
        sa.Column("unrealized_pnl", sa.Numeric(20, 4), nullable=False),
        sa.Column("realized_pnl_today", sa.Numeric(20, 4), nullable=False),
        sa.Column("realized_pnl_total", sa.Numeric(20, 4), nullable=False),
        sa.Column("daily_return_pct", sa.Float, nullable=True),
        sa.Column("positions_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("market_exposure", postgresql.JSONB, nullable=True),
        sa.Column("drawdown_pct", sa.Float, nullable=True),
        sa.Column("peak_equity", sa.Numeric(20, 4), nullable=True),
        sa.Column("sharpe_ratio", sa.Float, nullable=True),
        sa.Column("sortino_ratio", sa.Float, nullable=True),
        sa.Column("paper_mode", sa.Boolean, nullable=False, server_default="true"),
    )

    op.create_index(
        "ix_portfolio_snapshots_snapshot_at", "portfolio_snapshots", ["snapshot_at"]
    )
    op.create_index(
        "ix_portfolio_snapshots_paper_mode", "portfolio_snapshots", ["paper_mode"]
    )

    # ------------------------------------------------------------------
    # 8. model_performance
    # ------------------------------------------------------------------

    op.create_table(
        "model_performance",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("model_id", sa.String(100), nullable=False),
        sa.Column("agent_name", sa.String(50), nullable=False),
        sa.Column("market", sa.String(30), nullable=True),
        sa.Column("period", sa.String(20), nullable=False),
        sa.Column("period_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("period_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("brier_score", sa.Float, nullable=True),
        sa.Column("brier_skill_score", sa.Float, nullable=True),
        sa.Column("accuracy", sa.Float, nullable=True),
        sa.Column("precision", sa.Float, nullable=True),
        sa.Column("recall", sa.Float, nullable=True),
        sa.Column("f1_score", sa.Float, nullable=True),
        sa.Column("total_predictions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("correct_predictions", sa.Integer, nullable=False, server_default="0"),
        sa.Column("avg_trade_pnl_pct", sa.Float, nullable=True),
        sa.Column("win_rate", sa.Float, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_index("ix_model_performance_model_id", "model_performance", ["model_id"])
    op.create_index("ix_model_performance_created_at", "model_performance", ["created_at"])
    op.create_index(
        "ix_model_performance_agent_period",
        "model_performance",
        ["agent_name", "period"],
    )

    # ------------------------------------------------------------------
    # 9. memory_entries
    # ------------------------------------------------------------------

    op.create_table(
        "memory_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True,
                  server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_name", sa.String(50), nullable=False),
        sa.Column("memory_tier", sa.Enum(
            "short", "medium", "long",
            name="memory_tier", create_type=False,
        ), nullable=False),
        sa.Column("content", sa.Text, nullable=False),
        sa.Column("summary", sa.Text, nullable=True),
        sa.Column("market", sa.String(30), nullable=True),
        sa.Column("symbol", sa.String(30), nullable=True),
        sa.Column("tags", postgresql.JSONB, nullable=True),
        sa.Column("importance_score", sa.Float, nullable=False, server_default="0.5"),
        sa.Column("access_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("last_accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.text("NOW()"), nullable=False),
    )

    op.create_index("ix_memory_entries_agent_name", "memory_entries", ["agent_name"])
    op.create_index("ix_memory_entries_memory_tier", "memory_entries", ["memory_tier"])
    op.create_index("ix_memory_entries_market", "memory_entries", ["market"])
    op.create_index("ix_memory_entries_created_at", "memory_entries", ["created_at"])
    op.create_index("ix_memory_entries_expires_at", "memory_entries", ["expires_at"])

    # ------------------------------------------------------------------
    # 10. Helpful views
    # ------------------------------------------------------------------

    # Open trades view
    op.execute("""
        CREATE OR REPLACE VIEW open_trades AS
        SELECT
            t.*,
            s.strategy   AS signal_strategy,
            s.strength   AS signal_strength,
            s.confidence AS signal_confidence
        FROM trades t
        LEFT JOIN signals s ON s.id = t.signal_id
        WHERE t.status = 'open'
    """)

    # Daily P&L view
    op.execute("""
        CREATE OR REPLACE VIEW daily_pnl AS
        SELECT
            DATE(closed_at AT TIME ZONE 'UTC') AS trade_date,
            market,
            strategy,
            COUNT(*) FILTER (WHERE net_pnl_usd > 0) AS winners,
            COUNT(*) FILTER (WHERE net_pnl_usd <= 0) AS losers,
            COUNT(*) AS total_trades,
            SUM(net_pnl_usd) AS net_pnl_usd,
            AVG(pnl_pct) AS avg_pnl_pct,
            MAX(pnl_pct) AS best_trade_pct,
            MIN(pnl_pct) AS worst_trade_pct
        FROM trades
        WHERE status = 'closed'
          AND closed_at IS NOT NULL
        GROUP BY 1, 2, 3
        ORDER BY 1 DESC, 4 DESC
    """)


# ---------------------------------------------------------------------------
# Downgrade
# ---------------------------------------------------------------------------


def downgrade() -> None:
    """Drop all tables, views, and enum types in reverse order."""

    # Drop views first
    op.execute("DROP VIEW IF EXISTS daily_pnl CASCADE")
    op.execute("DROP VIEW IF EXISTS open_trades CASCADE")

    # Drop tables in reverse dependency order
    op.drop_table("memory_entries")
    op.drop_table("model_performance")
    op.drop_table("portfolio_snapshots")
    op.drop_table("risk_events")
    op.drop_table("agent_decisions")
    op.drop_table("trades")
    op.drop_table("signals")

    # Drop market_data partitions then parent
    for partition_name in ["crypto", "forex", "commodities", "indian_stocks", "us_stocks"]:
        op.execute(f"DROP TABLE IF EXISTS market_data_{partition_name} CASCADE")
    op.execute("DROP TABLE IF EXISTS market_data CASCADE")

    # Drop enum types
    _enum_names = [
        "agent_decision_type",
        "memory_tier",
        "risk_event_severity",
        "order_type",
        "trade_status",
        "direction_type",
        "signal_type",
        "market_type",
    ]
    for enum_name in _enum_names:
        op.execute(f"DROP TYPE IF EXISTS {enum_name} CASCADE")
