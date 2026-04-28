-- =============================================================================
-- NEXUS ALPHA — Trade Queries
-- =============================================================================
-- Named queries using the "-- name: <query_name>" convention.
-- All queries are PostgreSQL-specific.
-- Placeholder format: :param_name
-- =============================================================================


-- name: insert_trade
-- Insert a new trade record.
-- Returns the inserted row's id and opened_at for confirmation.
--
-- Parameters:
--   :id                 — UUID (generate client-side with uuid4)
--   :signal_id          — UUID of the originating signal (nullable)
--   :market             — Market type string
--   :symbol             — Instrument symbol
--   :side               — 'long' or 'short'
--   :order_type         — 'market', 'limit', 'stop', 'stop_limit', 'trailing_stop'
--   :status             — Initial status: 'pending' or 'open'
--   :size               — Position size (units)
--   :entry_price        — Entry price
--   :stop_loss          — Stop loss price (nullable)
--   :take_profit        — Primary take profit price (nullable)
--   :strategy           — Strategy name
--   :exchange           — Exchange identifier
--   :exchange_order_id  — Exchange-assigned order ID (nullable)
--   :paper_trade        — Boolean: true = simulated, false = live
--   :metadata           — JSONB metadata blob (nullable)
INSERT INTO trades (
    id,
    signal_id,
    market,
    symbol,
    side,
    order_type,
    status,
    size,
    entry_price,
    stop_loss,
    take_profit,
    strategy,
    exchange,
    exchange_order_id,
    paper_trade,
    metadata
)
VALUES (
    :id,
    :signal_id,
    :market,
    :symbol,
    :side,
    :order_type,
    :status,
    :size,
    :entry_price,
    :stop_loss,
    :take_profit,
    :strategy,
    :exchange,
    :exchange_order_id,
    :paper_trade,
    :metadata::jsonb
)
RETURNING id, opened_at
;


-- name: get_open_trades
-- Fetch all currently open trades.
-- Includes the originating signal's confidence and strength for context.
-- Results ordered by opened_at ascending (oldest open trade first).
--
-- Parameters: none
SELECT
    t.id,
    t.signal_id,
    t.market,
    t.symbol,
    t.side,
    t.order_type,
    t.status,
    t.size,
    t.entry_price,
    t.exit_price,
    t.stop_loss,
    t.take_profit,
    t.filled_price,
    t.fill_timestamp,
    t.slippage_pct,
    t.pnl_usd,
    t.pnl_pct,
    t.commission_usd,
    t.net_pnl_usd,
    t.strategy,
    t.exchange,
    t.exchange_order_id,
    t.paper_trade,
    t.opened_at,
    t.holding_seconds,
    t.metadata,
    -- Signal context
    s.confidence   AS signal_confidence,
    s.strength     AS signal_strength,
    s.timeframe    AS signal_timeframe,
    -- Current holding duration
    EXTRACT(EPOCH FROM (NOW() - t.opened_at))::INTEGER AS holding_seconds_live
FROM trades t
LEFT JOIN signals s ON s.id = t.signal_id
WHERE t.status = 'open'
ORDER BY t.opened_at ASC
;


-- name: get_open_trades_by_market
-- Fetch open trades filtered to a specific market.
-- Used by the Risk Manager for per-market exposure calculations.
--
-- Parameters:
--   :market — Market type string
SELECT
    t.id,
    t.market,
    t.symbol,
    t.side,
    t.size,
    t.entry_price,
    t.stop_loss,
    t.take_profit,
    t.strategy,
    t.opened_at,
    EXTRACT(EPOCH FROM (NOW() - t.opened_at))::INTEGER AS holding_seconds_live
FROM trades t
WHERE
    t.status = 'open'
    AND t.market = :market
ORDER BY t.opened_at ASC
;


-- name: close_trade
-- Mark a trade as closed with exit price and P&L.
-- Calculates holding_seconds automatically from opened_at.
--
-- Parameters:
--   :id              — Trade UUID
--   :exit_price      — Price at which the trade was closed
--   :pnl_usd         — Gross P&L in USD (positive = profit)
--   :pnl_pct         — P&L as a percentage of entry value
--   :commission_usd  — Commission/fee paid in USD
--   :net_pnl_usd     — Net P&L after commission (pnl_usd - commission_usd)
UPDATE trades
SET
    status          = 'closed',
    exit_price      = :exit_price,
    pnl_usd         = :pnl_usd,
    pnl_pct         = :pnl_pct,
    commission_usd  = :commission_usd,
    net_pnl_usd     = :net_pnl_usd,
    closed_at       = NOW(),
    holding_seconds = EXTRACT(EPOCH FROM (NOW() - opened_at))::BIGINT
WHERE
    id     = :id
    AND status = 'open'
RETURNING
    id,
    symbol,
    net_pnl_usd,
    pnl_pct,
    closed_at,
    holding_seconds
;


-- name: update_trade_stop
-- Update the stop loss price for an open trade (trailing stop management).
--
-- Parameters:
--   :id        — Trade UUID
--   :stop_loss — New stop loss price
UPDATE trades
SET
    stop_loss = :stop_loss
WHERE
    id     = :id
    AND status = 'open'
RETURNING id, symbol, stop_loss
;


-- name: cancel_trade
-- Cancel a pending trade (never filled).
--
-- Parameters:
--   :id — Trade UUID
UPDATE trades
SET
    status    = 'cancelled',
    closed_at = NOW()
WHERE
    id     = :id
    AND status = 'pending'
RETURNING id, symbol, closed_at
;


-- name: get_performance_by_market
-- Aggregate performance statistics grouped by market.
-- Returns win rate, average P&L, total trades, and Sharpe-related metrics
-- for each market across all closed trades in the specified date range.
--
-- Parameters:
--   :since       — Start of the analysis period, TIMESTAMPTZ
--   :until       — End of the analysis period, TIMESTAMPTZ
--   :paper_trade — Filter: true = paper trades only, false = live only, null = all
SELECT
    market,
    COUNT(*) AS total_trades,
    COUNT(*) FILTER (WHERE net_pnl_usd > 0) AS winning_trades,
    COUNT(*) FILTER (WHERE net_pnl_usd <= 0) AS losing_trades,
    -- Win rate as a ratio [0, 1]
    ROUND(
        COUNT(*) FILTER (WHERE net_pnl_usd > 0)::NUMERIC / NULLIF(COUNT(*), 0),
        4
    ) AS win_rate,
    -- P&L aggregates (USD)
    ROUND(SUM(net_pnl_usd)::NUMERIC, 2) AS total_net_pnl_usd,
    ROUND(AVG(net_pnl_usd)::NUMERIC, 2) AS avg_net_pnl_usd,
    ROUND(AVG(net_pnl_usd) FILTER (WHERE net_pnl_usd > 0)::NUMERIC, 2) AS avg_winner_usd,
    ROUND(AVG(net_pnl_usd) FILTER (WHERE net_pnl_usd <= 0)::NUMERIC, 2) AS avg_loser_usd,
    -- P&L as percentage
    ROUND(AVG(pnl_pct)::NUMERIC, 4) AS avg_pnl_pct,
    ROUND(STDDEV(pnl_pct)::NUMERIC, 4) AS stddev_pnl_pct,
    -- Profit factor: gross profit / gross loss
    ROUND(
        SUM(net_pnl_usd) FILTER (WHERE net_pnl_usd > 0)::NUMERIC /
        NULLIF(ABS(SUM(net_pnl_usd) FILTER (WHERE net_pnl_usd <= 0)), 0)
        , 3
    ) AS profit_factor,
    -- Holding time statistics
    ROUND(AVG(holding_seconds) / 3600.0, 2) AS avg_holding_hours,
    -- Commission drag
    ROUND(SUM(commission_usd)::NUMERIC, 2) AS total_commission_usd
FROM trades
WHERE
    status = 'closed'
    AND closed_at IS NOT NULL
    AND closed_at BETWEEN :since AND :until
    AND (:paper_trade IS NULL OR paper_trade = :paper_trade)
GROUP BY market
ORDER BY total_net_pnl_usd DESC
;


-- name: get_performance_by_strategy
-- Aggregate performance statistics grouped by strategy.
-- Returns the same metrics as get_performance_by_market but by strategy name.
--
-- Parameters:
--   :since       — Start of the analysis period, TIMESTAMPTZ
--   :until       — End of the analysis period, TIMESTAMPTZ
--   :paper_trade — Filter: true = paper, false = live, null = all
SELECT
    strategy,
    market,
    COUNT(*) AS total_trades,
    COUNT(*) FILTER (WHERE net_pnl_usd > 0) AS winning_trades,
    ROUND(
        COUNT(*) FILTER (WHERE net_pnl_usd > 0)::NUMERIC / NULLIF(COUNT(*), 0),
        4
    ) AS win_rate,
    ROUND(SUM(net_pnl_usd)::NUMERIC, 2) AS total_net_pnl_usd,
    ROUND(AVG(net_pnl_usd)::NUMERIC, 2) AS avg_net_pnl_usd,
    ROUND(AVG(pnl_pct)::NUMERIC, 4) AS avg_pnl_pct,
    ROUND(STDDEV(pnl_pct)::NUMERIC, 4) AS stddev_pnl_pct,
    ROUND(
        SUM(net_pnl_usd) FILTER (WHERE net_pnl_usd > 0)::NUMERIC /
        NULLIF(ABS(SUM(net_pnl_usd) FILTER (WHERE net_pnl_usd <= 0)), 0)
        , 3
    ) AS profit_factor,
    ROUND(AVG(holding_seconds) / 3600.0, 2) AS avg_holding_hours,
    -- Best and worst trade
    ROUND(MAX(pnl_pct)::NUMERIC, 4) AS best_trade_pct,
    ROUND(MIN(pnl_pct)::NUMERIC, 4) AS worst_trade_pct
FROM trades
WHERE
    status = 'closed'
    AND closed_at IS NOT NULL
    AND closed_at BETWEEN :since AND :until
    AND (:paper_trade IS NULL OR paper_trade = :paper_trade)
GROUP BY strategy, market
ORDER BY total_net_pnl_usd DESC
;


-- name: get_daily_pnl
-- Daily P&L summary for charting equity curves and performance dashboards.
-- Returns one row per calendar day, aggregated across all closed trades.
--
-- Parameters:
--   :since       — Start date (TIMESTAMPTZ)
--   :until       — End date (TIMESTAMPTZ)
--   :paper_trade — Filter: true = paper, false = live, null = all
SELECT
    DATE(closed_at AT TIME ZONE 'UTC')     AS trade_date,
    COUNT(*)                               AS trades_closed,
    COUNT(*) FILTER (WHERE net_pnl_usd > 0) AS winners,
    COUNT(*) FILTER (WHERE net_pnl_usd <= 0) AS losers,
    ROUND(SUM(net_pnl_usd)::NUMERIC, 2)   AS daily_net_pnl_usd,
    ROUND(AVG(pnl_pct)::NUMERIC, 4)       AS avg_pnl_pct,
    ROUND(SUM(commission_usd)::NUMERIC, 2) AS daily_commission_usd,
    -- Running total P&L (cumulative)
    ROUND(
        SUM(SUM(net_pnl_usd)) OVER (ORDER BY DATE(closed_at AT TIME ZONE 'UTC'))::NUMERIC,
        2
    ) AS cumulative_net_pnl_usd,
    -- Rolling 5-day average for smoothing
    ROUND(
        AVG(SUM(net_pnl_usd)) OVER (
            ORDER BY DATE(closed_at AT TIME ZONE 'UTC')
            ROWS BETWEEN 4 PRECEDING AND CURRENT ROW
        )::NUMERIC,
        2
    ) AS rolling_5d_avg_pnl_usd,
    -- Markets traded on this day
    ARRAY_AGG(DISTINCT market) AS markets_traded,
    -- Strategies used
    ARRAY_AGG(DISTINCT strategy) AS strategies_used
FROM trades
WHERE
    status = 'closed'
    AND closed_at IS NOT NULL
    AND closed_at BETWEEN :since AND :until
    AND (:paper_trade IS NULL OR paper_trade = :paper_trade)
GROUP BY
    DATE(closed_at AT TIME ZONE 'UTC')
ORDER BY
    trade_date ASC
;


-- name: get_drawdown_series
-- Compute the drawdown series for a portfolio equity curve.
-- Drawdown = (current_equity - peak_equity) / peak_equity
-- Used for the maximum drawdown chart and Calmar ratio calculation.
--
-- Parameters:
--   :since       — Start of the analysis period, TIMESTAMPTZ
--   :paper_trade — Filter: true = paper, false = live, null = all
WITH daily_pnl_raw AS (
    SELECT
        DATE(closed_at AT TIME ZONE 'UTC') AS trade_date,
        SUM(net_pnl_usd) AS daily_pnl
    FROM trades
    WHERE
        status = 'closed'
        AND closed_at IS NOT NULL
        AND closed_at >= :since
        AND (:paper_trade IS NULL OR paper_trade = :paper_trade)
    GROUP BY DATE(closed_at AT TIME ZONE 'UTC')
),
equity_curve AS (
    SELECT
        trade_date,
        daily_pnl,
        -- Cumulative P&L as equity (starting from 0 delta — absolute $ P&L)
        SUM(daily_pnl) OVER (ORDER BY trade_date) AS cumulative_pnl
    FROM daily_pnl_raw
),
peak_tracking AS (
    SELECT
        trade_date,
        daily_pnl,
        cumulative_pnl,
        -- Running maximum equity
        MAX(cumulative_pnl) OVER (
            ORDER BY trade_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS peak_equity
    FROM equity_curve
)
SELECT
    trade_date,
    ROUND(daily_pnl::NUMERIC, 2) AS daily_pnl_usd,
    ROUND(cumulative_pnl::NUMERIC, 2) AS cumulative_pnl_usd,
    ROUND(peak_equity::NUMERIC, 2) AS peak_equity_usd,
    -- Drawdown in USD
    ROUND((cumulative_pnl - peak_equity)::NUMERIC, 2) AS drawdown_usd,
    -- Drawdown as percentage of peak equity
    CASE
        WHEN peak_equity = 0 THEN 0
        ELSE ROUND(
            ((cumulative_pnl - peak_equity) / ABS(peak_equity) * 100)::NUMERIC,
            4
        )
    END AS drawdown_pct
FROM peak_tracking
ORDER BY trade_date ASC
;


-- name: get_symbol_performance
-- Detailed performance breakdown for a specific symbol.
-- Shows how consistently a symbol has been traded profitably.
--
-- Parameters:
--   :symbol      — Instrument symbol
--   :since       — Start date, TIMESTAMPTZ
--   :paper_trade — Filter: true = paper, false = live, null = all
SELECT
    symbol,
    market,
    side,
    strategy,
    COUNT(*) AS total_trades,
    COUNT(*) FILTER (WHERE net_pnl_usd > 0) AS winners,
    ROUND(
        COUNT(*) FILTER (WHERE net_pnl_usd > 0)::NUMERIC / NULLIF(COUNT(*), 0),
        4
    ) AS win_rate,
    ROUND(SUM(net_pnl_usd)::NUMERIC, 2) AS total_net_pnl_usd,
    ROUND(AVG(pnl_pct)::NUMERIC, 4) AS avg_pnl_pct,
    ROUND(MAX(pnl_pct)::NUMERIC, 4) AS best_pct,
    ROUND(MIN(pnl_pct)::NUMERIC, 4) AS worst_pct,
    ROUND(AVG(holding_seconds) / 3600.0, 2) AS avg_hold_hours,
    MIN(opened_at) AS first_trade_at,
    MAX(closed_at) AS last_trade_at
FROM trades
WHERE
    symbol = :symbol
    AND status = 'closed'
    AND closed_at IS NOT NULL
    AND opened_at >= :since
    AND (:paper_trade IS NULL OR paper_trade = :paper_trade)
GROUP BY symbol, market, side, strategy
ORDER BY total_net_pnl_usd DESC
;


-- name: get_recent_trades
-- Fetch the N most recently closed trades for audit/dashboard display.
--
-- Parameters:
--   :limit       — Number of trades to return
--   :paper_trade — Filter: true = paper, false = live, null = all
SELECT
    t.id,
    t.market,
    t.symbol,
    t.side,
    t.strategy,
    t.size,
    t.entry_price,
    t.exit_price,
    t.net_pnl_usd,
    t.pnl_pct,
    t.commission_usd,
    t.paper_trade,
    t.opened_at,
    t.closed_at,
    t.holding_seconds,
    s.confidence AS signal_confidence,
    s.strength AS signal_strength
FROM trades t
LEFT JOIN signals s ON s.id = t.signal_id
WHERE
    t.status = 'closed'
    AND (:paper_trade IS NULL OR t.paper_trade = :paper_trade)
ORDER BY t.closed_at DESC
LIMIT :limit
;
