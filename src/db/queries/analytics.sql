-- NEXUS ALPHA Analytics Queries
-- Each query is delimited by a `-- name:` comment that the SQLQueryLoader parses.


-- name: get_portfolio_summary
-- Returns current portfolio value, today's P&L, total P&L, win rate,
-- trade counts, open positions and per-market exposure.
SELECT
    COALESCE(SUM(CASE WHEN t.status = 'open' THEN t.quantity * t.entry_price ELSE 0 END), 0)
        AS total_value,
    COALESCE(SUM(CASE
        WHEN t.status = 'closed'
         AND t.closed_at::date = CURRENT_DATE
        THEN t.pnl ELSE 0 END), 0) AS daily_pnl,
    CASE
        WHEN SUM(CASE
            WHEN t.status = 'closed'
             AND t.closed_at::date = CURRENT_DATE
            THEN t.quantity * t.entry_price ELSE 0 END) > 0
        THEN COALESCE(SUM(CASE
            WHEN t.status = 'closed'
             AND t.closed_at::date = CURRENT_DATE
            THEN t.pnl ELSE 0 END), 0)
           / SUM(CASE
            WHEN t.status = 'closed'
             AND t.closed_at::date = CURRENT_DATE
            THEN t.quantity * t.entry_price ELSE 0 END) * 100
        ELSE 0
    END AS daily_pnl_pct,
    COALESCE(SUM(CASE WHEN t.status = 'closed' THEN t.pnl ELSE 0 END), 0)
        AS total_pnl,
    CASE
        WHEN COUNT(CASE WHEN t.status = 'closed' THEN 1 END) > 0
        THEN COUNT(CASE WHEN t.status = 'closed' AND t.pnl > 0 THEN 1 END)::float
           / COUNT(CASE WHEN t.status = 'closed' THEN 1 END) * 100
        ELSE 0
    END AS win_rate,
    COUNT(CASE WHEN t.status = 'closed' THEN 1 END) AS total_trades,
    COUNT(CASE WHEN t.status = 'open' THEN 1 END)   AS open_positions,
    json_object_agg(
        market_exposure.market,
        market_exposure.exposure
    ) AS market_exposure
FROM trades t
CROSS JOIN LATERAL (
    SELECT
        m.market,
        COALESCE(SUM(
            CASE WHEN t2.status = 'open' AND t2.market = m.market
            THEN t2.quantity * t2.entry_price ELSE 0 END
        ), 0) AS exposure
    FROM (SELECT DISTINCT market FROM trades) m
    LEFT JOIN trades t2 ON t2.market = m.market
    GROUP BY m.market
) market_exposure;


-- name: get_agent_performance
-- Returns per-agent win rate, average confidence, total P&L and trade count.
SELECT
    t.agent_id,
    a.name                                              AS agent_name,
    a.strategy_type,
    COUNT(*)                                            AS total_trades,
    COUNT(CASE WHEN t.pnl > 0 THEN 1 END)              AS winning_trades,
    CASE
        WHEN COUNT(*) > 0
        THEN COUNT(CASE WHEN t.pnl > 0 THEN 1 END)::float / COUNT(*) * 100
        ELSE 0
    END                                                 AS win_rate_pct,
    AVG(t.agent_confidence)                             AS avg_confidence,
    SUM(t.pnl)                                          AS total_pnl,
    AVG(t.pnl)                                          AS avg_pnl_per_trade,
    STDDEV(t.pnl)                                       AS pnl_std_dev,
    MAX(t.pnl)                                          AS best_trade_pnl,
    MIN(t.pnl)                                          AS worst_trade_pnl
FROM trades t
JOIN agents a ON a.id = t.agent_id
WHERE t.status = 'closed'
GROUP BY t.agent_id, a.name, a.strategy_type
ORDER BY total_pnl DESC;


-- name: get_signal_accuracy_by_source
-- Compares signal source weights against eventual trade outcomes.
SELECT
    s.market,
    -- Bucketed confidence deciles for calibration curves
    (FLOOR(s.fused_score * 10) / 10)::numeric(3,1)  AS confidence_bucket,
    COUNT(*)                                          AS signal_count,
    COUNT(CASE WHEN t.pnl > 0 THEN 1 END)            AS profitable_signals,
    CASE
        WHEN COUNT(*) > 0
        THEN COUNT(CASE WHEN t.pnl > 0 THEN 1 END)::float / COUNT(*) * 100
        ELSE 0
    END                                               AS accuracy_pct,
    AVG(s.llm_weight)                                 AS avg_llm_weight,
    AVG(s.technical_weight)                           AS avg_technical_weight,
    AVG(s.sentiment_weight)                           AS avg_sentiment_weight,
    AVG(s.onchain_weight)                             AS avg_onchain_weight,
    AVG(t.pnl_pct)                                    AS avg_pnl_pct,
    -- Brier score (lower = better calibrated)
    AVG(POWER(
        (CASE WHEN t.pnl > 0 THEN 1.0 ELSE 0.0 END) - s.fused_score, 2
    ))                                                AS brier_score
FROM signals s
JOIN trades t ON t.signal_id = s.id
WHERE s.is_executed = true
  AND t.status = 'closed'
GROUP BY s.market, confidence_bucket
ORDER BY s.market, confidence_bucket;


-- name: get_market_correlation_matrix
-- Computes rolling 30-day Pearson correlation of daily returns between markets.
WITH daily_returns AS (
    SELECT
        market,
        closed_at::date AS trade_date,
        SUM(pnl_pct)    AS daily_return
    FROM trades
    WHERE status = 'closed'
      AND closed_at >= CURRENT_DATE - INTERVAL '30 days'
    GROUP BY market, trade_date
),
market_pairs AS (
    SELECT
        a.market  AS market_a,
        b.market  AS market_b,
        a.trade_date,
        a.daily_return AS return_a,
        b.daily_return AS return_b
    FROM daily_returns a
    JOIN daily_returns b ON a.trade_date = b.trade_date
                        AND a.market < b.market
)
SELECT
    market_a,
    market_b,
    COUNT(*)    AS observation_days,
    CORR(return_a, return_b)::numeric(5, 4) AS pearson_correlation
FROM market_pairs
GROUP BY market_a, market_b
HAVING COUNT(*) >= 5
ORDER BY ABS(CORR(return_a, return_b)) DESC;


-- name: get_drawdown_periods
-- Identifies drawdown periods: start date, end date, depth, duration (days).
WITH equity_curve AS (
    SELECT
        closed_at::date                  AS trade_date,
        SUM(pnl) OVER (
            ORDER BY closed_at::date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        )                                AS cumulative_pnl
    FROM trades
    WHERE status = 'closed'
    GROUP BY closed_at::date, pnl
    ORDER BY trade_date
),
running_peak AS (
    SELECT
        trade_date,
        cumulative_pnl,
        MAX(cumulative_pnl) OVER (
            ORDER BY trade_date
            ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
        ) AS peak_pnl
    FROM equity_curve
),
drawdown_calc AS (
    SELECT
        trade_date,
        cumulative_pnl,
        peak_pnl,
        cumulative_pnl - peak_pnl           AS drawdown_abs,
        CASE
            WHEN peak_pnl != 0
            THEN (cumulative_pnl - peak_pnl) / ABS(peak_pnl) * 100
            ELSE 0
        END                                  AS drawdown_pct,
        LAG(peak_pnl) OVER (ORDER BY trade_date) AS prev_peak
    FROM running_peak
),
dd_periods AS (
    SELECT
        trade_date,
        drawdown_abs,
        drawdown_pct,
        -- Mark start of a new drawdown when peak changes or drawdown goes negative
        SUM(CASE WHEN drawdown_abs >= 0 THEN 1 ELSE 0 END)
            OVER (ORDER BY trade_date ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
            AS period_group
    FROM drawdown_calc
    WHERE drawdown_abs < 0
)
SELECT
    period_group,
    MIN(trade_date)                         AS drawdown_start,
    MAX(trade_date)                         AS drawdown_end,
    (MAX(trade_date) - MIN(trade_date))     AS duration_days,
    MIN(drawdown_abs)                       AS max_drawdown_abs,
    MIN(drawdown_pct)                       AS max_drawdown_pct
FROM dd_periods
GROUP BY period_group
ORDER BY max_drawdown_pct ASC
LIMIT 20;


-- name: get_best_performing_strategies
-- Ranks strategies by Sharpe ratio, win rate and total P&L.
WITH strategy_daily AS (
    SELECT
        strategy,
        closed_at::date       AS trade_date,
        SUM(pnl)              AS daily_pnl,
        COUNT(*)              AS trades_per_day
    FROM trades
    WHERE status = 'closed'
      AND closed_at >= CURRENT_DATE - INTERVAL '90 days'
    GROUP BY strategy, trade_date
),
strategy_stats AS (
    SELECT
        strategy,
        COUNT(DISTINCT trade_date)              AS active_days,
        SUM(trades_per_day)                     AS total_trades,
        SUM(daily_pnl)                          AS total_pnl,
        AVG(daily_pnl)                          AS avg_daily_pnl,
        STDDEV(daily_pnl)                       AS stddev_daily_pnl,
        MAX(daily_pnl)                          AS best_day,
        MIN(daily_pnl)                          AS worst_day
    FROM strategy_daily
    GROUP BY strategy
),
trade_level AS (
    SELECT
        strategy,
        COUNT(*)                                                        AS closed_trades,
        COUNT(CASE WHEN pnl > 0 THEN 1 END)                            AS wins,
        AVG(pnl_pct)                                                   AS avg_pnl_pct,
        AVG(CASE WHEN pnl > 0 THEN pnl END)                            AS avg_win,
        ABS(AVG(CASE WHEN pnl < 0 THEN pnl END))                      AS avg_loss,
        AVG(agent_confidence)                                          AS avg_confidence
    FROM trades
    WHERE status = 'closed'
      AND closed_at >= CURRENT_DATE - INTERVAL '90 days'
    GROUP BY strategy
)
SELECT
    ss.strategy,
    ss.total_trades,
    ss.total_pnl,
    ss.avg_daily_pnl,
    CASE
        WHEN ss.stddev_daily_pnl > 0
        THEN (ss.avg_daily_pnl / ss.stddev_daily_pnl) * SQRT(252)
        ELSE 0
    END                                                                 AS annualized_sharpe,
    tl.wins::float / NULLIF(tl.closed_trades, 0) * 100                 AS win_rate_pct,
    tl.avg_pnl_pct,
    CASE
        WHEN tl.avg_loss > 0
        THEN tl.avg_win / tl.avg_loss
        ELSE NULL
    END                                                                 AS profit_factor,
    tl.avg_confidence,
    ss.best_day,
    ss.worst_day
FROM strategy_stats ss
JOIN trade_level tl ON tl.strategy = ss.strategy
ORDER BY annualized_sharpe DESC;


-- name: get_hourly_pnl_heatmap
-- Returns average P&L by hour-of-day × day-of-week for heatmap rendering.
SELECT
    EXTRACT(DOW FROM closed_at)::int    AS day_of_week,   -- 0=Sunday … 6=Saturday
    TO_CHAR(closed_at, 'Dy')            AS day_name,
    EXTRACT(HOUR FROM closed_at)::int   AS hour_of_day,
    market,
    COUNT(*)                            AS trade_count,
    SUM(pnl)                            AS total_pnl,
    AVG(pnl)                            AS avg_pnl,
    AVG(pnl_pct)                        AS avg_pnl_pct,
    COUNT(CASE WHEN pnl > 0 THEN 1 END) AS wins,
    COUNT(CASE WHEN pnl < 0 THEN 1 END) AS losses
FROM trades
WHERE status = 'closed'
  AND closed_at >= CURRENT_DATE - INTERVAL '180 days'
GROUP BY day_of_week, day_name, hour_of_day, market
ORDER BY day_of_week, hour_of_day, market;


-- name: get_win_loss_by_hour
-- Aggregates win rate and average P&L for each UTC hour across all markets.
SELECT
    EXTRACT(HOUR FROM closed_at)::int   AS hour_utc,
    market,
    COUNT(*)                            AS total_trades,
    COUNT(CASE WHEN pnl > 0 THEN 1 END) AS wins,
    COUNT(CASE WHEN pnl < 0 THEN 1 END) AS losses,
    COUNT(CASE WHEN pnl = 0 THEN 1 END) AS breakeven,
    CASE
        WHEN COUNT(*) > 0
        THEN COUNT(CASE WHEN pnl > 0 THEN 1 END)::float / COUNT(*) * 100
        ELSE 0
    END                                 AS win_rate_pct,
    AVG(pnl)                            AS avg_pnl,
    AVG(pnl_pct)                        AS avg_pnl_pct,
    PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY pnl_pct)
                                        AS median_pnl_pct,
    SUM(pnl)                            AS cumulative_pnl
FROM trades
WHERE status = 'closed'
  AND closed_at >= CURRENT_DATE - INTERVAL '90 days'
GROUP BY hour_utc, market
ORDER BY market, hour_utc;
