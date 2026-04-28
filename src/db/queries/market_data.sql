-- =============================================================================
-- NEXUS ALPHA — Market Data SQL Queries
-- =============================================================================
-- Named queries using the "-- name: <query_name>" convention.
-- These queries are loaded by the repository layer at startup.
--
-- All queries use PostgreSQL-specific syntax (CTEs, PARTITION BY, etc.)
-- Placeholder format: :param_name (compatible with asyncpg and aiopg)
-- =============================================================================


-- name: get_latest_candles
-- Fetch the N most recent candles for a given symbol and market.
-- Ordered ascending by timestamp so the caller receives them in
-- chronological order (most useful for technical indicator calculation).
--
-- Parameters:
--   :market   — Market type string (e.g. 'crypto', 'forex')
--   :symbol   — Instrument symbol (e.g. 'BTC/USDT', 'EUR_USD')
--   :interval — Candle interval string (e.g. '1m', '5m', '1h')
--   :limit    — Number of candles to return (e.g. 200)
SELECT
    id,
    market,
    symbol,
    interval,
    timestamp,
    open,
    high,
    low,
    close,
    volume,
    vwap,
    trades_count,
    source_exchange,
    created_at
FROM market_data
WHERE
    market   = :market
    AND symbol   = :symbol
    AND interval = :interval
ORDER BY timestamp DESC
LIMIT :limit
;


-- name: get_latest_candles_ordered
-- Same as get_latest_candles but guarantees ascending timestamp order.
-- Use this when feeding data to indicator libraries (TA-Lib, pandas-ta)
-- that expect chronological order.
--
-- Parameters: same as get_latest_candles
SELECT *
FROM (
    SELECT
        id,
        market,
        symbol,
        interval,
        timestamp,
        open,
        high,
        low,
        close,
        volume,
        vwap,
        trades_count,
        source_exchange,
        created_at
    FROM market_data
    WHERE
        market   = :market
        AND symbol   = :symbol
        AND interval = :interval
    ORDER BY timestamp DESC
    LIMIT :limit
) sub
ORDER BY timestamp ASC
;


-- name: get_candles_range
-- Fetch all candles within an explicit time range [start_ts, end_ts].
-- Returns results in ascending chronological order.
-- Use for backtesting, charting, or feeding a fixed historical window.
--
-- Parameters:
--   :market    — Market type string
--   :symbol    — Instrument symbol
--   :interval  — Candle interval string
--   :start_ts  — Start timestamp (inclusive), TIMESTAMPTZ
--   :end_ts    — End timestamp (inclusive), TIMESTAMPTZ
--   :limit     — Safety cap on rows returned (default 10000)
SELECT
    id,
    market,
    symbol,
    interval,
    timestamp,
    open,
    high,
    low,
    close,
    volume,
    vwap,
    trades_count,
    source_exchange,
    created_at
FROM market_data
WHERE
    market    = :market
    AND symbol    = :symbol
    AND interval  = :interval
    AND timestamp >= :start_ts
    AND timestamp <= :end_ts
ORDER BY timestamp ASC
LIMIT :limit
;


-- name: get_multi_symbol_latest
-- Fetch the single most recent candle for each symbol in a list.
-- Useful for building a market overview or portfolio snapshot.
--
-- Parameters:
--   :market   — Market type string
--   :symbols  — Array of symbol strings (pass as a PostgreSQL array)
--   :interval — Candle interval string
--
-- Note: Pass :symbols as a native PostgreSQL text array, e.g.:
--   ARRAY['BTC/USDT', 'ETH/USDT', 'SOL/USDT']
SELECT DISTINCT ON (symbol)
    id,
    market,
    symbol,
    interval,
    timestamp,
    open,
    high,
    low,
    close,
    volume,
    vwap,
    trades_count,
    source_exchange,
    created_at
FROM market_data
WHERE
    market   = :market
    AND symbol   = ANY(:symbols)
    AND interval = :interval
ORDER BY
    symbol,
    timestamp DESC
;


-- name: get_volume_anomalies
-- Identify candles where volume is significantly above the recent average.
-- A volume surge (>3x average) often signals institutional activity,
-- news catalysts, or technical breakouts worth investigating.
--
-- Parameters:
--   :market         — Market type string
--   :symbol         — Instrument symbol
--   :interval       — Candle interval string
--   :lookback_limit — Number of candles to analyse (e.g. 500)
--   :multiplier     — Volume threshold multiplier (default 3.0 = 3x average)
--   :since          — Only return anomalies after this timestamp, TIMESTAMPTZ
WITH candle_window AS (
    SELECT
        id,
        market,
        symbol,
        interval,
        timestamp,
        open,
        high,
        low,
        close,
        volume,
        vwap,
        trades_count,
        source_exchange,
        -- Rolling 20-period average volume using window function
        AVG(volume) OVER (
            PARTITION BY market, symbol, interval
            ORDER BY timestamp
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS avg_volume_20,
        -- Standard deviation for z-score calculation
        STDDEV(volume) OVER (
            PARTITION BY market, symbol, interval
            ORDER BY timestamp
            ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING
        ) AS stddev_volume_20
    FROM market_data
    WHERE
        market   = :market
        AND symbol   = :symbol
        AND interval = :interval
    ORDER BY timestamp DESC
    LIMIT :lookback_limit
)
SELECT
    id,
    market,
    symbol,
    interval,
    timestamp,
    open,
    high,
    low,
    close,
    volume,
    vwap,
    avg_volume_20,
    -- Volume ratio relative to rolling average
    ROUND(
        (volume / NULLIF(avg_volume_20, 0))::NUMERIC, 2
    ) AS volume_ratio,
    -- Z-score for statistical significance
    CASE
        WHEN NULLIF(stddev_volume_20, 0) IS NULL THEN NULL
        ELSE ROUND(
            ((volume - avg_volume_20) / NULLIF(stddev_volume_20, 0))::NUMERIC, 2
        )
    END AS volume_zscore
FROM candle_window
WHERE
    avg_volume_20 IS NOT NULL
    AND volume >= avg_volume_20 * :multiplier
    AND timestamp >= :since
ORDER BY
    volume_ratio DESC,
    timestamp DESC
;


-- name: get_price_levels
-- Identify significant support and resistance levels for a symbol.
-- Levels are detected by counting how many times price has touched
-- a given price zone (within a tolerance band).
--
-- Algorithm:
--   1. Collect all highs and lows from recent candles
--   2. Round each price to the nearest 'bucket' (0.1% of current price)
--   3. Count touches per bucket
--   4. Return buckets with >= :min_touches touches
--
-- Parameters:
--   :market         — Market type string
--   :symbol         — Instrument symbol
--   :interval       — Candle interval string
--   :lookback_limit — Number of candles to scan (e.g. 500)
--   :min_touches    — Minimum number of touches to qualify as a level (default 3)
--   :bucket_pct     — Price bucket width as a fraction (default 0.001 = 0.1%)
WITH price_data AS (
    SELECT
        high,
        low,
        close,
        timestamp
    FROM market_data
    WHERE
        market   = :market
        AND symbol   = :symbol
        AND interval = :interval
    ORDER BY timestamp DESC
    LIMIT :lookback_limit
),
current_price AS (
    SELECT close AS current_close
    FROM price_data
    LIMIT 1
),
-- Extract both highs and lows as candidate levels
raw_levels AS (
    SELECT high AS price_level, 'resistance' AS level_type, timestamp
    FROM price_data
    UNION ALL
    SELECT low AS price_level, 'support' AS level_type, timestamp
    FROM price_data
),
-- Round prices to nearest bucket for clustering
bucketed AS (
    SELECT
        level_type,
        -- Round to nearest bucket (0.1% of current price by default)
        ROUND(
            price_level / (
                SELECT current_close * :bucket_pct FROM current_price
            )
        ) * (
            SELECT current_close * :bucket_pct FROM current_price
        ) AS price_bucket,
        MIN(timestamp) AS first_touch,
        MAX(timestamp) AS last_touch,
        COUNT(*) AS touch_count
    FROM raw_levels
    GROUP BY
        level_type,
        ROUND(
            price_level / (
                SELECT current_close * :bucket_pct FROM current_price
            )
        )
    HAVING COUNT(*) >= :min_touches
)
SELECT
    level_type,
    ROUND(price_bucket::NUMERIC, 4) AS price_level,
    touch_count,
    first_touch,
    last_touch,
    -- Recency score: more recent touches score higher
    EXTRACT(EPOCH FROM (NOW() - last_touch)) / 86400 AS days_since_last_touch,
    -- Strength score combining touch count and recency
    ROUND(
        (touch_count * 1.0) /
        (1 + EXTRACT(EPOCH FROM (NOW() - last_touch)) / 86400 / 30)
        , 3
    ) AS strength_score
FROM bucketed
ORDER BY
    level_type,
    strength_score DESC
;


-- name: get_candle_count
-- Quick count of available candles for a symbol/interval combination.
-- Used for data availability checks before running analysis.
--
-- Parameters:
--   :market   — Market type string
--   :symbol   — Instrument symbol
--   :interval — Candle interval string
SELECT
    COUNT(*) AS candle_count,
    MIN(timestamp) AS earliest_candle,
    MAX(timestamp) AS latest_candle,
    MAX(timestamp) - MIN(timestamp) AS data_span
FROM market_data
WHERE
    market   = :market
    AND symbol   = :symbol
    AND interval = :interval
;


-- name: get_missing_candles
-- Identify gaps in the candle series for a symbol.
-- Critical for detecting data feed issues and backfill requirements.
--
-- Parameters:
--   :market   — Market type string
--   :symbol   — Instrument symbol
--   :interval — Candle interval string
--   :interval_seconds — Expected seconds between candles (e.g. 300 for 5m)
--   :since    — Only look for gaps after this timestamp, TIMESTAMPTZ
WITH ordered_candles AS (
    SELECT
        timestamp,
        LEAD(timestamp) OVER (ORDER BY timestamp) AS next_timestamp
    FROM market_data
    WHERE
        market   = :market
        AND symbol   = :symbol
        AND interval = :interval
        AND timestamp >= :since
),
gaps AS (
    SELECT
        timestamp AS gap_start,
        next_timestamp AS gap_end,
        EXTRACT(EPOCH FROM (next_timestamp - timestamp)) AS actual_gap_seconds,
        :interval_seconds AS expected_gap_seconds,
        ROUND(
            EXTRACT(EPOCH FROM (next_timestamp - timestamp)) / :interval_seconds
        ) - 1 AS missing_candles
    FROM ordered_candles
    WHERE
        next_timestamp IS NOT NULL
        AND EXTRACT(EPOCH FROM (next_timestamp - timestamp)) > :interval_seconds * 1.5
)
SELECT
    gap_start,
    gap_end,
    actual_gap_seconds,
    missing_candles,
    -- Classify gap severity
    CASE
        WHEN missing_candles <= 5  THEN 'minor'
        WHEN missing_candles <= 24 THEN 'moderate'
        ELSE 'major'
    END AS gap_severity
FROM gaps
ORDER BY gap_start ASC
;
