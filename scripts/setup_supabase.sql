-- =============================================================================
-- NEXUS ALPHA - Complete Supabase Schema
-- Run as: psql $DATABASE_URL -f scripts/setup_supabase.sql
-- =============================================================================

-- =============================================================================
-- 1. EXTENSIONS
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS "pg_trgm";
CREATE EXTENSION IF NOT EXISTS "btree_gin";
CREATE EXTENSION IF NOT EXISTS "vector";           -- pgvector for similarity search
CREATE EXTENSION IF NOT EXISTS "pg_cron";          -- scheduled jobs
CREATE EXTENSION IF NOT EXISTS "pg_stat_statements";
CREATE EXTENSION IF NOT EXISTS "timescaledb" CASCADE;  -- time-series optimisations

-- =============================================================================
-- 2. ENUMS
-- =============================================================================
DO $$ BEGIN
  CREATE TYPE signal_direction AS ENUM ('LONG', 'SHORT', 'NEUTRAL');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE trade_status AS ENUM (
    'PENDING', 'OPEN', 'PARTIALLY_FILLED', 'FILLED',
    'CLOSING', 'CLOSED', 'CANCELLED', 'REJECTED', 'ERROR'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE market_class AS ENUM ('crypto', 'forex', 'indian_stocks', 'us_stocks');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE timeframe_enum AS ENUM ('1m', '3m', '5m', '15m', '30m', '1h', '4h', '1d', '1w');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE regime_type AS ENUM ('TRENDING_UP', 'TRENDING_DOWN', 'RANGING', 'VOLATILE', 'UNKNOWN');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE risk_event_type AS ENUM (
    'CIRCUIT_BREAKER_TRIPPED', 'DRAWDOWN_ALERT', 'POSITION_LIMIT_HIT',
    'DAILY_LOSS_LIMIT', 'VOLATILITY_SPIKE', 'CORRELATION_BREACH',
    'MARGIN_CALL', 'SYSTEM_HALT'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE agent_name AS ENUM (
    'TrendFollower', 'MeanReversion', 'BreakoutHunter',
    'RiskSentinel', 'MacroAnalyst', 'PatternRecognizer', 'VolumeProfiler'
  );
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE memory_tier AS ENUM ('hot', 'warm', 'cold', 'archive');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE approval_status AS ENUM ('pending', 'approved', 'rejected', 'auto_applied');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

DO $$ BEGIN
  CREATE TYPE execution_mode AS ENUM ('paper', 'live');
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

-- =============================================================================
-- 3. MARKET DATA TABLE (partitioned by market_class)
-- =============================================================================
CREATE TABLE IF NOT EXISTS market_data (
    id              BIGSERIAL,
    market          market_class            NOT NULL,
    symbol          VARCHAR(32)             NOT NULL,
    timeframe       timeframe_enum          NOT NULL,
    timestamp       TIMESTAMPTZ             NOT NULL,
    open            NUMERIC(20, 8)          NOT NULL,
    high            NUMERIC(20, 8)          NOT NULL,
    low             NUMERIC(20, 8)          NOT NULL,
    close           NUMERIC(20, 8)          NOT NULL,
    volume          NUMERIC(28, 8)          NOT NULL DEFAULT 0,
    quote_volume    NUMERIC(28, 8),
    trades_count    INTEGER,
    is_closed       BOOLEAN                 NOT NULL DEFAULT TRUE,
    ingested_at     TIMESTAMPTZ             NOT NULL DEFAULT NOW(),
    PRIMARY KEY (id, market)
) PARTITION BY LIST (market);

-- Partition tables
CREATE TABLE IF NOT EXISTS market_data_crypto
    PARTITION OF market_data FOR VALUES IN ('crypto');

CREATE TABLE IF NOT EXISTS market_data_forex
    PARTITION OF market_data FOR VALUES IN ('forex');

CREATE TABLE IF NOT EXISTS market_data_indian_stocks
    PARTITION OF market_data FOR VALUES IN ('indian_stocks');

CREATE TABLE IF NOT EXISTS market_data_us_stocks
    PARTITION OF market_data FOR VALUES IN ('us_stocks');

-- Unique constraint per partition (on base table via trigger or per-partition)
DO $$ BEGIN
  ALTER TABLE market_data_crypto
    ADD CONSTRAINT uq_market_data_crypto UNIQUE (symbol, timeframe, timestamp);
EXCEPTION WHEN duplicate_table THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE market_data_forex
    ADD CONSTRAINT uq_market_data_forex UNIQUE (symbol, timeframe, timestamp);
EXCEPTION WHEN duplicate_table THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE market_data_indian_stocks
    ADD CONSTRAINT uq_market_data_indian UNIQUE (symbol, timeframe, timestamp);
EXCEPTION WHEN duplicate_table THEN NULL; END $$;

DO $$ BEGIN
  ALTER TABLE market_data_us_stocks
    ADD CONSTRAINT uq_market_data_us UNIQUE (symbol, timeframe, timestamp);
EXCEPTION WHEN duplicate_table THEN NULL; END $$;

-- =============================================================================
-- 4. SIGNALS TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS signals (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Identification
    market              market_class    NOT NULL,
    symbol              VARCHAR(32)     NOT NULL,
    timeframe           timeframe_enum  NOT NULL,
    strategy_name       VARCHAR(64)     NOT NULL,

    -- Signal details
    direction           signal_direction NOT NULL,
    confidence          NUMERIC(5, 4)   NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    expected_value      NUMERIC(10, 6),
    edge_detected       BOOLEAN         NOT NULL DEFAULT FALSE,

    -- Market context at signal time
    entry_price         NUMERIC(20, 8)  NOT NULL,
    stop_loss           NUMERIC(20, 8),
    take_profit_1       NUMERIC(20, 8),
    take_profit_2       NUMERIC(20, 8),
    take_profit_3       NUMERIC(20, 8),
    atr_at_signal       NUMERIC(20, 8),
    regime              regime_type,

    -- Multi-timeframe analysis
    multi_tf_confirmed  BOOLEAN         NOT NULL DEFAULT FALSE,
    higher_tf_bias      signal_direction,

    -- Fusion scores (JSON: {"TrendFollower": 0.7, ...})
    agent_scores        JSONB,
    fusion_score        NUMERIC(5, 4),

    -- Risk assessment
    risk_approved       BOOLEAN         NOT NULL DEFAULT FALSE,
    risk_rejection_reason VARCHAR(256),
    position_size_units NUMERIC(20, 8),
    position_size_usd   NUMERIC(20, 4),
    risk_pct_of_equity  NUMERIC(6, 4),

    -- Execution mode
    execution_mode      execution_mode  NOT NULL DEFAULT 'paper',

    -- Metadata
    raw_signal_json     JSONB,
    notes               TEXT
);

-- =============================================================================
-- 5. TRADES TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS trades (
    id                  UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    signal_id           UUID            REFERENCES signals(id) ON DELETE SET NULL,
    created_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    -- Identification
    market              market_class    NOT NULL,
    symbol              VARCHAR(32)     NOT NULL,
    exchange_order_id   VARCHAR(128),
    client_order_id     VARCHAR(128)    UNIQUE,

    -- Trade details
    direction           signal_direction NOT NULL,
    status              trade_status    NOT NULL DEFAULT 'PENDING',
    execution_mode      execution_mode  NOT NULL DEFAULT 'paper',

    -- Entry
    entry_price         NUMERIC(20, 8),
    entry_time          TIMESTAMPTZ,
    quantity            NUMERIC(20, 8)  NOT NULL,
    quantity_filled     NUMERIC(20, 8)  NOT NULL DEFAULT 0,

    -- Exit
    exit_price          NUMERIC(20, 8),
    exit_time           TIMESTAMPTZ,
    exit_reason         VARCHAR(128),   -- 'take_profit_1', 'stop_loss', 'manual', 'timeout'

    -- Risk parameters
    stop_loss           NUMERIC(20, 8),
    take_profit_1       NUMERIC(20, 8),
    take_profit_2       NUMERIC(20, 8),
    take_profit_3       NUMERIC(20, 8),
    trailing_stop_pct   NUMERIC(6, 4),

    -- P&L
    realized_pnl        NUMERIC(20, 8),
    realized_pnl_usd    NUMERIC(20, 4),
    unrealized_pnl      NUMERIC(20, 8),
    fees_paid           NUMERIC(20, 8)  NOT NULL DEFAULT 0,
    net_pnl_usd         NUMERIC(20, 4),

    -- Metrics
    mae                 NUMERIC(20, 8),  -- Maximum Adverse Excursion
    mfe                 NUMERIC(20, 8),  -- Maximum Favorable Excursion
    duration_seconds    INTEGER,
    slippage_pct        NUMERIC(8, 6),

    -- Metadata
    strategy_name       VARCHAR(64),
    trade_metadata      JSONB
);

-- Auto-update updated_at
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trades_updated_at ON trades;
CREATE TRIGGER trades_updated_at
    BEFORE UPDATE ON trades
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 6. AGENT DECISIONS TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS agent_decisions (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    signal_id       UUID            NOT NULL REFERENCES signals(id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    agent           agent_name      NOT NULL,
    vote            signal_direction NOT NULL,
    confidence      NUMERIC(5, 4)   NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    reasoning       TEXT,
    key_factors     JSONB,          -- {"rsi": 72.3, "trend": "up", ...}
    model_used      VARCHAR(64),    -- Which LLM was used
    tokens_used     INTEGER,
    latency_ms      INTEGER,

    UNIQUE (signal_id, agent)
);

-- =============================================================================
-- 7. RISK EVENTS TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS risk_events (
    id              UUID            PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),

    event_type      risk_event_type NOT NULL,
    severity        SMALLINT        NOT NULL CHECK (severity BETWEEN 1 AND 5),
    market          market_class,
    symbol          VARCHAR(32),

    -- Context
    trigger_value   NUMERIC(20, 8),     -- The value that triggered the event
    threshold_value NUMERIC(20, 8),     -- The threshold that was breached
    circuit_breaker VARCHAR(64),        -- Which circuit breaker

    -- Impact
    action_taken    VARCHAR(256),       -- What was done (HALT, REDUCE, ALERT)
    positions_affected INTEGER,
    equity_at_event NUMERIC(20, 4),

    resolved_at     TIMESTAMPTZ,
    notes           TEXT,
    raw_data        JSONB
);

-- =============================================================================
-- 8. PORTFOLIO SNAPSHOTS TABLE (hourly)
-- =============================================================================
CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id                  UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    snapshot_at         TIMESTAMPTZ NOT NULL,
    execution_mode      execution_mode NOT NULL DEFAULT 'paper',

    -- Equity
    total_equity_usd    NUMERIC(20, 4) NOT NULL,
    cash_usd            NUMERIC(20, 4) NOT NULL,
    open_positions_usd  NUMERIC(20, 4) NOT NULL DEFAULT 0,
    unrealized_pnl_usd  NUMERIC(20, 4) NOT NULL DEFAULT 0,

    -- P&L
    daily_pnl_usd       NUMERIC(20, 4) NOT NULL DEFAULT 0,
    daily_pnl_pct       NUMERIC(8, 6)  NOT NULL DEFAULT 0,
    weekly_pnl_usd      NUMERIC(20, 4),
    monthly_pnl_usd     NUMERIC(20, 4),

    -- Drawdown
    peak_equity_usd     NUMERIC(20, 4),
    current_drawdown_pct NUMERIC(8, 6)  NOT NULL DEFAULT 0,
    max_drawdown_pct    NUMERIC(8, 6),

    -- Risk metrics
    open_positions_count INTEGER       NOT NULL DEFAULT 0,
    largest_position_pct NUMERIC(8, 6),
    portfolio_heat      NUMERIC(8, 6),   -- Sum of all position risk %

    -- Trade stats (cumulative)
    total_trades        INTEGER         NOT NULL DEFAULT 0,
    winning_trades      INTEGER         NOT NULL DEFAULT 0,
    losing_trades       INTEGER         NOT NULL DEFAULT 0,
    win_rate            NUMERIC(5, 4),

    -- Position breakdown (JSON: {"BTC/USDT": {"size": ..., "pnl": ...}})
    positions_json      JSONB,

    UNIQUE (snapshot_at, execution_mode)
);

-- =============================================================================
-- 9. MODEL PERFORMANCE TABLE (for Brier scores and calibration)
-- =============================================================================
CREATE TABLE IF NOT EXISTS model_performance (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    model_name      VARCHAR(64) NOT NULL,
    agent           agent_name,
    strategy_name   VARCHAR(64),
    market          market_class,
    symbol          VARCHAR(32),

    -- Prediction tracking (for Brier score calculation)
    signal_id       UUID        REFERENCES signals(id) ON DELETE SET NULL,
    predicted_prob  NUMERIC(5, 4) NOT NULL,  -- Probability predicted
    actual_outcome  SMALLINT,                 -- 1=correct, 0=incorrect (filled after trade closes)
    brier_score     NUMERIC(10, 8),           -- (prob - outcome)^2

    -- Aggregated metrics (rolling)
    rolling_accuracy     NUMERIC(5, 4),
    rolling_brier        NUMERIC(10, 8),
    rolling_sharpe       NUMERIC(8, 4),
    sample_count         INTEGER,
    window_days          INTEGER,

    raw_data        JSONB
);

-- =============================================================================
-- 10. MEMORY ENTRIES TABLE (tiered memory system)
-- =============================================================================
CREATE TABLE IF NOT EXISTS memory_entries (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at      TIMESTAMPTZ,

    tier            memory_tier NOT NULL DEFAULT 'warm',
    category        VARCHAR(64) NOT NULL,   -- 'market_truth', 'pattern', 'lesson', 'context'
    market          market_class,
    symbol          VARCHAR(32),

    -- Content
    key             VARCHAR(256) NOT NULL,
    value           TEXT         NOT NULL,
    confidence      NUMERIC(5, 4) DEFAULT 0.5,
    source          VARCHAR(128),           -- 'agent:TrendFollower', 'backtest', 'human'

    -- Access tracking (for tier promotion/demotion)
    access_count    INTEGER      NOT NULL DEFAULT 0,
    last_accessed   TIMESTAMPTZ,
    reinforcement_count INTEGER  NOT NULL DEFAULT 0,

    metadata        JSONB
);

DROP TRIGGER IF EXISTS memory_entries_updated_at ON memory_entries;
CREATE TRIGGER memory_entries_updated_at
    BEFORE UPDATE ON memory_entries
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 11. PATTERN VECTORS TABLE (cosine similarity search)
-- =============================================================================
CREATE TABLE IF NOT EXISTS pattern_vectors (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    market          market_class NOT NULL,
    symbol          VARCHAR(32)  NOT NULL,
    pattern_name    VARCHAR(128) NOT NULL,

    -- The 25-dimensional feature vector
    embedding       vector(25)   NOT NULL,

    -- Pattern outcome tracking
    signal_direction signal_direction NOT NULL,
    outcome_pnl_pct  NUMERIC(10, 6),     -- Actual % P&L after pattern
    outcome_correct  BOOLEAN,
    sample_count     INTEGER      NOT NULL DEFAULT 1,
    avg_pnl_pct      NUMERIC(10, 6),
    win_rate         NUMERIC(5, 4),

    -- Context at pattern formation
    timeframe        timeframe_enum,
    regime           regime_type,
    features_json    JSONB,              -- Human-readable feature values

    UNIQUE (market, symbol, pattern_name)
);

-- =============================================================================
-- 12. STRATEGY PARAMS TABLE
-- =============================================================================
CREATE TABLE IF NOT EXISTS strategy_params (
    id              UUID        PRIMARY KEY DEFAULT uuid_generate_v4(),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    strategy_name   VARCHAR(64)     NOT NULL,
    market          market_class,
    symbol          VARCHAR(32),

    -- Current vs proposed
    is_current      BOOLEAN         NOT NULL DEFAULT TRUE,
    params_json     JSONB           NOT NULL,   -- {"rsi_period": 14, "bb_std": 2.0, ...}

    -- Dream Mode proposals
    proposed_by     VARCHAR(64),               -- 'dream_mode', 'manual', 'backtest'
    approval_status approval_status NOT NULL DEFAULT 'pending',
    approved_by     VARCHAR(128),
    approved_at     TIMESTAMPTZ,

    -- Performance of this param set
    backtest_sharpe    NUMERIC(8, 4),
    backtest_win_rate  NUMERIC(5, 4),
    backtest_drawdown  NUMERIC(6, 4),
    backtest_trades    INTEGER,
    live_sharpe        NUMERIC(8, 4),
    live_win_rate      NUMERIC(5, 4),

    notes           TEXT
);

DROP TRIGGER IF EXISTS strategy_params_updated_at ON strategy_params;
CREATE TRIGGER strategy_params_updated_at
    BEFORE UPDATE ON strategy_params
    FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();

-- =============================================================================
-- 13. SYSTEM CONFIG TABLE (feature flag overrides)
-- =============================================================================
CREATE TABLE IF NOT EXISTS system_config (
    key             VARCHAR(128)    PRIMARY KEY,
    value           TEXT            NOT NULL,
    value_type      VARCHAR(16)     NOT NULL DEFAULT 'string',  -- string/bool/int/float/json
    description     TEXT,
    updated_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_by      VARCHAR(128)
);

-- =============================================================================
-- 14. INDEXES
-- =============================================================================

-- market_data indexes
CREATE INDEX IF NOT EXISTS idx_market_data_crypto_sym_tf_ts
    ON market_data_crypto (symbol, timeframe, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_market_data_forex_sym_tf_ts
    ON market_data_forex (symbol, timeframe, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_market_data_indian_sym_tf_ts
    ON market_data_indian_stocks (symbol, timeframe, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_market_data_us_sym_tf_ts
    ON market_data_us_stocks (symbol, timeframe, timestamp DESC);

-- signals indexes
CREATE INDEX IF NOT EXISTS idx_signals_market_symbol ON signals (market, symbol);
CREATE INDEX IF NOT EXISTS idx_signals_created_at ON signals (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_signals_strategy ON signals (strategy_name);
CREATE INDEX IF NOT EXISTS idx_signals_direction ON signals (direction);
CREATE INDEX IF NOT EXISTS idx_signals_edge ON signals (edge_detected) WHERE edge_detected = TRUE;

-- trades indexes
CREATE INDEX IF NOT EXISTS idx_trades_signal_id ON trades (signal_id);
CREATE INDEX IF NOT EXISTS idx_trades_status ON trades (status);
CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades (symbol);
CREATE INDEX IF NOT EXISTS idx_trades_created_at ON trades (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_trades_open ON trades (status) WHERE status = 'OPEN';
CREATE INDEX IF NOT EXISTS idx_trades_entry_time ON trades (entry_time DESC);

-- agent_decisions indexes
CREATE INDEX IF NOT EXISTS idx_agent_decisions_signal ON agent_decisions (signal_id);
CREATE INDEX IF NOT EXISTS idx_agent_decisions_agent ON agent_decisions (agent);

-- risk_events indexes
CREATE INDEX IF NOT EXISTS idx_risk_events_type ON risk_events (event_type);
CREATE INDEX IF NOT EXISTS idx_risk_events_created_at ON risk_events (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_risk_events_unresolved ON risk_events (resolved_at)
    WHERE resolved_at IS NULL;

-- portfolio_snapshots indexes
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_at ON portfolio_snapshots (snapshot_at DESC);
CREATE INDEX IF NOT EXISTS idx_portfolio_snapshots_mode ON portfolio_snapshots (execution_mode, snapshot_at DESC);

-- model_performance indexes
CREATE INDEX IF NOT EXISTS idx_model_perf_model ON model_performance (model_name, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_perf_agent ON model_performance (agent, recorded_at DESC);
CREATE INDEX IF NOT EXISTS idx_model_perf_signal ON model_performance (signal_id);

-- memory_entries indexes
CREATE INDEX IF NOT EXISTS idx_memory_tier ON memory_entries (tier);
CREATE INDEX IF NOT EXISTS idx_memory_category ON memory_entries (category);
CREATE INDEX IF NOT EXISTS idx_memory_key ON memory_entries (key);
CREATE INDEX IF NOT EXISTS idx_memory_market_symbol ON memory_entries (market, symbol);
CREATE INDEX IF NOT EXISTS idx_memory_expires ON memory_entries (expires_at)
    WHERE expires_at IS NOT NULL;

-- pattern_vectors indexes (IVFFlat for approximate nearest neighbour)
CREATE INDEX IF NOT EXISTS idx_pattern_vectors_embedding
    ON pattern_vectors USING ivfflat (embedding vector_cosine_ops)
    WITH (lists = 100);
CREATE INDEX IF NOT EXISTS idx_pattern_vectors_market ON pattern_vectors (market, symbol);

-- strategy_params indexes
CREATE INDEX IF NOT EXISTS idx_strategy_params_name ON strategy_params (strategy_name);
CREATE INDEX IF NOT EXISTS idx_strategy_params_current ON strategy_params (strategy_name)
    WHERE is_current = TRUE;
CREATE INDEX IF NOT EXISTS idx_strategy_params_pending ON strategy_params (approval_status)
    WHERE approval_status = 'pending';

-- =============================================================================
-- 15. SUPABASE REALTIME PUBLICATION
-- =============================================================================
DO $$ BEGIN
  -- Create publication if it doesn't exist
  IF NOT EXISTS (SELECT 1 FROM pg_publication WHERE pubname = 'nexus_realtime') THEN
    CREATE PUBLICATION nexus_realtime FOR TABLE
      signals,
      trades,
      risk_events,
      portfolio_snapshots,
      system_config;
  END IF;
END $$;

-- =============================================================================
-- 16. ROW LEVEL SECURITY
-- =============================================================================

ALTER TABLE market_data         ENABLE ROW LEVEL SECURITY;
ALTER TABLE signals             ENABLE ROW LEVEL SECURITY;
ALTER TABLE trades              ENABLE ROW LEVEL SECURITY;
ALTER TABLE agent_decisions     ENABLE ROW LEVEL SECURITY;
ALTER TABLE risk_events         ENABLE ROW LEVEL SECURITY;
ALTER TABLE portfolio_snapshots ENABLE ROW LEVEL SECURITY;
ALTER TABLE model_performance   ENABLE ROW LEVEL SECURITY;
ALTER TABLE memory_entries      ENABLE ROW LEVEL SECURITY;
ALTER TABLE pattern_vectors     ENABLE ROW LEVEL SECURITY;
ALTER TABLE strategy_params     ENABLE ROW LEVEL SECURITY;
ALTER TABLE system_config       ENABLE ROW LEVEL SECURITY;

-- Service role has full access to all tables
CREATE POLICY "service_role_all_market_data"
    ON market_data FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_signals"
    ON signals FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_trades"
    ON trades FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_agent_decisions"
    ON agent_decisions FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_risk_events"
    ON risk_events FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_portfolio"
    ON portfolio_snapshots FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_model_perf"
    ON model_performance FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_memory"
    ON memory_entries FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_patterns"
    ON pattern_vectors FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_strategy_params"
    ON strategy_params FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

CREATE POLICY "service_role_all_system_config"
    ON system_config FOR ALL TO service_role USING (TRUE) WITH CHECK (TRUE);

-- Anon/authenticated: read-only on non-sensitive tables
CREATE POLICY "anon_read_signals"
    ON signals FOR SELECT TO anon, authenticated USING (TRUE);

CREATE POLICY "anon_read_trades"
    ON trades FOR SELECT TO anon, authenticated USING (TRUE);

CREATE POLICY "anon_read_portfolio"
    ON portfolio_snapshots FOR SELECT TO anon, authenticated USING (TRUE);

CREATE POLICY "anon_read_system_config"
    ON system_config FOR SELECT TO anon, authenticated USING (TRUE);

-- =============================================================================
-- 17. TRIGGER: on new trade → update portfolio_snapshot
-- =============================================================================
CREATE OR REPLACE FUNCTION trigger_portfolio_snapshot_on_trade()
RETURNS TRIGGER AS $$
DECLARE
    v_mode      execution_mode;
    v_equity    NUMERIC(20,4);
    v_cash      NUMERIC(20,4);
    v_open_pnl  NUMERIC(20,4);
    v_open_cnt  INTEGER;
    v_daily_pnl NUMERIC(20,4);
BEGIN
    v_mode := NEW.execution_mode;

    -- Calculate current open positions value
    SELECT
        COALESCE(SUM(
            CASE WHEN direction = 'LONG'
                THEN quantity_filled * entry_price
                ELSE 0
            END
        ), 0),
        COUNT(*),
        COALESCE(SUM(COALESCE(unrealized_pnl, 0)), 0)
    INTO v_open_pnl, v_open_cnt, v_open_pnl
    FROM trades
    WHERE execution_mode = v_mode
      AND status IN ('OPEN', 'PARTIALLY_FILLED');

    -- Daily realised P&L
    SELECT COALESCE(SUM(COALESCE(net_pnl_usd, 0)), 0)
    INTO v_daily_pnl
    FROM trades
    WHERE execution_mode = v_mode
      AND status = 'CLOSED'
      AND exit_time >= DATE_TRUNC('day', NOW());

    -- Upsert hourly snapshot
    INSERT INTO portfolio_snapshots (
        snapshot_at,
        execution_mode,
        total_equity_usd,
        cash_usd,
        open_positions_usd,
        unrealized_pnl_usd,
        daily_pnl_usd,
        open_positions_count
    )
    VALUES (
        DATE_TRUNC('hour', NOW()),
        v_mode,
        1000000,            -- Placeholder: replace with actual equity lookup
        1000000 - v_open_pnl,
        v_open_pnl,
        v_open_pnl,
        v_daily_pnl,
        v_open_cnt
    )
    ON CONFLICT (snapshot_at, execution_mode) DO UPDATE SET
        open_positions_usd   = EXCLUDED.open_positions_usd,
        unrealized_pnl_usd   = EXCLUDED.unrealized_pnl_usd,
        daily_pnl_usd        = EXCLUDED.daily_pnl_usd,
        open_positions_count = EXCLUDED.open_positions_count;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trades_portfolio_snapshot ON trades;
CREATE TRIGGER trades_portfolio_snapshot
    AFTER INSERT OR UPDATE ON trades
    FOR EACH ROW EXECUTE FUNCTION trigger_portfolio_snapshot_on_trade();

-- =============================================================================
-- 18. TRIGGER: on new signal → pg_notify
-- =============================================================================
CREATE OR REPLACE FUNCTION notify_new_signal()
RETURNS TRIGGER AS $$
BEGIN
    PERFORM pg_notify(
        'nexus_new_signal',
        json_build_object(
            'id',        NEW.id,
            'market',    NEW.market,
            'symbol',    NEW.symbol,
            'direction', NEW.direction,
            'confidence',NEW.confidence,
            'edge',      NEW.edge_detected,
            'approved',  NEW.risk_approved,
            'ts',        NEW.created_at
        )::text
    );
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS signals_pg_notify ON signals;
CREATE TRIGGER signals_pg_notify
    AFTER INSERT ON signals
    FOR EACH ROW EXECUTE FUNCTION notify_new_signal();

-- =============================================================================
-- 19. SEED DATA: pre-populate memory_entries with market truths
-- =============================================================================
INSERT INTO memory_entries (tier, category, key, value, confidence, source)
VALUES
  ('hot', 'market_truth', 'trend_is_friend',
   'The trend is your friend until it ends. Trading with the primary trend has historically higher win rates.',
   0.90, 'backtest'),

  ('hot', 'market_truth', 'volume_confirms_trend',
   'Price moves accompanied by above-average volume are more likely to sustain. Low-volume breakouts often fail.',
   0.85, 'backtest'),

  ('hot', 'market_truth', 'mean_reversion_ranges',
   'In ranging markets, prices tend to revert to the mean. RSI extremes (>70 or <30) in ranging regimes signal reversals.',
   0.80, 'backtest'),

  ('hot', 'market_truth', 'false_breakout_risk',
   'Breakouts from key levels fail ~60% of the time without volume confirmation and multiple timeframe alignment.',
   0.85, 'backtest'),

  ('hot', 'market_truth', 'crypto_weekend_effect',
   'Crypto markets often exhibit lower liquidity on weekends, leading to exaggerated moves and wider spreads.',
   0.75, 'observation'),

  ('warm', 'market_truth', 'btc_alt_correlation',
   'BTC dominance changes correlate inversely with alt-coin performance. Rising BTC dominance often precedes alt selloffs.',
   0.78, 'observation'),

  ('warm', 'market_truth', 'forex_session_bias',
   'EUR/USD tends to trend during London session overlap with New York (13:00-17:00 UTC). Asian session is typically ranging.',
   0.80, 'backtest'),

  ('warm', 'market_truth', 'earnings_vol_crush',
   'Options implied volatility for US stocks typically collapses after earnings announcements (IV crush). Avoid long vega plays.',
   0.88, 'observation'),

  ('warm', 'market_truth', 'support_resistance_flips',
   'Broken support levels frequently become resistance and vice versa. This flip often provides high-probability trade setups.',
   0.85, 'backtest'),

  ('warm', 'market_truth', 'atr_position_sizing',
   'Position sizing based on ATR (1-2% risk per ATR unit) produces more consistent drawdown profiles than fixed-size approaches.',
   0.90, 'backtest'),

  ('warm', 'market_truth', 'india_nifty_global_correlation',
   'Indian markets (NIFTY50) show ~0.6 correlation with US markets overnight. Gap-ups/downs often partially fill within the session.',
   0.72, 'observation'),

  ('cold', 'market_truth', 'news_fade_strategy',
   'Sharp moves on news events (>2 ATR in one candle) often partially retrace within 2-4 candles as initial panic fades.',
   0.65, 'backtest'),

  ('cold', 'market_truth', 'quarter_end_rebalancing',
   'Large institutional rebalancing at quarter end (last 3 trading days) can cause unusual price patterns not supported by fundamentals.',
   0.70, 'observation')

ON CONFLICT DO NOTHING;

-- =============================================================================
-- 20. SEED: Default strategy params
-- =============================================================================
INSERT INTO strategy_params (strategy_name, market, is_current, params_json, proposed_by, approval_status)
VALUES
  ('TrendMomentum', 'crypto', TRUE,
   '{"ema_fast": 8, "ema_slow": 21, "rsi_period": 14, "rsi_threshold": 55, "atr_period": 14, "min_atr_pct": 0.5, "volume_factor": 1.5}',
   'manual', 'approved'),

  ('MeanReversionBB', 'crypto', TRUE,
   '{"bb_period": 20, "bb_std": 2.0, "rsi_period": 14, "rsi_oversold": 30, "rsi_overbought": 70, "volume_confirm": true}',
   'manual', 'approved'),

  ('BreakoutVolume', 'crypto', TRUE,
   '{"lookback_bars": 20, "volume_multiplier": 2.0, "atr_buffer_pct": 0.2, "confirmation_bars": 1}',
   'manual', 'approved'),

  ('ScalpEMA', 'forex', TRUE,
   '{"ema_fast": 5, "ema_slow": 13, "rsi_period": 7, "session": "london_ny_overlap", "pip_target": 10, "pip_stop": 7}',
   'manual', 'approved'),

  ('SwingSupRess', 'indian_stocks', TRUE,
   '{"lookback_bars": 50, "touch_tolerance_pct": 0.3, "min_touches": 2, "volume_confirm": true, "atr_stop_multiplier": 1.5}',
   'manual', 'approved')

ON CONFLICT DO NOTHING;

-- =============================================================================
-- 21. SEED: Default system config / feature flags
-- =============================================================================
INSERT INTO system_config (key, value, value_type, description)
VALUES
  ('paper_mode',                   'true',  'bool',   'Force paper trading mode'),
  ('max_open_positions',           '5',     'int',    'Maximum concurrent open positions'),
  ('max_position_size_pct',        '10.0',  'float',  'Maximum single position as % of equity'),
  ('daily_loss_limit_pct',         '3.0',   'float',  'Daily loss limit % before trading halts'),
  ('weekly_loss_limit_pct',        '8.0',   'float',  'Weekly loss limit %'),
  ('drawdown_pause_pct',           '15.0',  'float',  'Drawdown % that triggers PAUSE mode'),
  ('drawdown_stop_pct',            '25.0',  'float',  'Drawdown % that triggers full STOP'),
  ('dream_mode_enabled',           'true',  'bool',   'Enable Dream Mode overnight optimisation'),
  ('dream_mode_auto_evolve',       'false', 'bool',   'Auto-apply Dream Mode improvements without review'),
  ('llm_cost_guard_daily_usd',     '10.0',  'float',  'Maximum daily LLM API spend in USD'),
  ('alert_telegram_enabled',       'true',  'bool',   'Send alerts via Telegram'),
  ('alert_on_every_signal',        'false', 'bool',   'Alert on every signal (not just executed trades)'),
  ('crypto_enabled',               'true',  'bool',   'Enable crypto market'),
  ('forex_enabled',                'false', 'bool',   'Enable forex market'),
  ('indian_stocks_enabled',        'false', 'bool',   'Enable Indian stocks market'),
  ('us_stocks_enabled',            'false', 'bool',   'Enable US stocks market')

ON CONFLICT (key) DO NOTHING;

-- =============================================================================
-- DONE
-- =============================================================================
\echo 'NEXUS ALPHA schema setup complete.'
