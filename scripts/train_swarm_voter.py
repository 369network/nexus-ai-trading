#!/usr/bin/env python3
"""
scripts/train_swarm_voter.py
----------------------------
One-shot SwarmVoter training script.

Fetches 1 000 × 1h candles for BTC, ETH and SOL from Binance (public API,
no key required), computes the 19 technical indicators used by the
SwarmVoter, and trains the 100-model ensemble.  Models are saved to
./models/swarm/ so the live bot loads them on next restart.

Usage (from project root):
    python scripts/train_swarm_voter.py

Safe to run while the bot is live; model files are written atomically.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
import pandas as pd

# Ensure project root is in sys.path when run as a script
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)
logger = logging.getLogger("train_swarm")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SYMBOLS      = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]   # Binance spot format
INTERVAL     = "1h"
N_CANDLES    = 1000          # ~41 days of 1h data per symbol
MARKET       = "crypto"
FORWARD_BARS = 5             # Predict direction 5 bars (5h) ahead
N_MODELS     = 100
MODEL_DIR    = str(ROOT / "models" / "swarm")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def fetch_ohlcv(symbol: str, interval: str, limit: int) -> pd.DataFrame:
    """Fetch historical klines from Binance public REST API → DataFrame."""
    from src.data.providers.binance_rest import BinanceRESTProvider
    client = BinanceRESTProvider(market="spot")
    try:
        klines = await client.get_klines(symbol=symbol, interval=interval, limit=limit)
        logger.info("  %s: fetched %d candles", symbol, len(klines))
        if not klines:
            return pd.DataFrame()

        rows = []
        for k in klines:
            rows.append({
                "open":   k.open,
                "high":   k.high,
                "low":    k.low,
                "close":  k.close,
                "volume": k.volume,
            })
        return pd.DataFrame(rows).astype(float)
    finally:
        try:
            await client.close()
        except Exception:
            pass


def enrich_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute all 19 SwarmVoter features from a raw OHLCV DataFrame.

    Uses src/analysis/indicators.py for the core TA indicators, then
    derives the remaining features with simple pandas operations.
    """
    from src.analysis.indicators import compute_indicators
    from src.learning.swarm_voter import ALL_FEATURES

    # Core indicators
    df = compute_indicators(df)

    # -----------------------------------------------------------------------
    # Column name normalisation
    # compute_indicators() uses period-suffixed names (rsi14, atr14, …) but
    # SwarmVoter expects the bare names used by strategy code conventions.
    # -----------------------------------------------------------------------
    if "rsi14" in df.columns and "rsi" not in df.columns:
        df["rsi"] = df["rsi14"]
    if "atr14" in df.columns and "atr_pct" not in df.columns:
        df["atr_pct"] = df["atr14"] / df["close"].replace(0, np.nan)

    # Derived features missing from compute_indicators output
    if "rsi" in df.columns and "rsi_slope" not in df.columns:
        df["rsi_slope"] = df["rsi"].diff().fillna(0.0)

    if "sma20" in df.columns and "sma20_slope" not in df.columns:
        df["sma20_slope"] = df["sma20"].diff().fillna(0.0)

    if "sma50" in df.columns and "sma50_slope" not in df.columns:
        df["sma50_slope"] = df["sma50"].diff().fillna(0.0)

    if "close_vs_sma20" not in df.columns and "sma20" in df.columns:
        df["close_vs_sma20"] = (df["close"] - df["sma20"]) / df["sma20"].replace(0, np.nan)

    if "close_vs_sma50" not in df.columns and "sma50" in df.columns:
        df["close_vs_sma50"] = (df["close"] - df["sma50"]) / df["sma50"].replace(0, np.nan)

    # Candle shape features
    hl_range = (df["high"] - df["low"]).replace(0, np.nan)
    if "high_low_range" not in df.columns:
        df["high_low_range"] = hl_range / df["close"]
    if "body_pct" not in df.columns:
        df["body_pct"] = (df["close"] - df["open"]).abs() / hl_range
    if "upper_shadow" not in df.columns:
        df["upper_shadow"] = (df["high"] - df[["open", "close"]].max(axis=1)) / hl_range
    if "lower_shadow" not in df.columns:
        df["lower_shadow"] = (df[["open", "close"]].min(axis=1) - df["low"]) / hl_range

    # Sentiment proxies — not available from spot data; use neutral constants
    for col in ("fear_greed", "funding_rate", "open_interest_change"):
        if col not in df.columns:
            df[col] = 0.0

    # Ensure all required features exist
    for col in ALL_FEATURES:
        if col not in df.columns:
            logger.warning("Feature '%s' not computed — filling with 0.0", col)
            df[col] = 0.0

    # Fill NaNs
    df = df.fillna(0.0)
    return df


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main() -> None:
    logger.info("=== SwarmVoter Training ===")
    logger.info(
        "Symbols: %s | Interval: %s | Candles/symbol: %d",
        SYMBOLS, INTERVAL, N_CANDLES,
    )

    from src.learning.swarm_voter import SwarmVoter, ALL_FEATURES

    all_frames: List[pd.DataFrame] = []

    for sym in SYMBOLS:
        logger.info("Fetching %s …", sym)
        raw = await fetch_ohlcv(sym, INTERVAL, N_CANDLES)
        if raw.empty:
            logger.warning("No candles for %s — skipping", sym)
            continue

        logger.info("Computing indicators for %s (%d bars) …", sym, len(raw))
        enriched = enrich_features(raw)
        # Discard first 60 bars (indicator warm-up artefacts)
        enriched = enriched.iloc[60:].reset_index(drop=True)
        logger.info("  → %d usable bars after warm-up", len(enriched))
        all_frames.append(enriched)

    if not all_frames:
        logger.error("No training data collected — exiting.")
        sys.exit(1)

    combined = pd.concat(all_frames, ignore_index=True)
    logger.info("Combined dataset: %d rows × %d cols", *combined.shape)

    # Sanity-check: at least one row must have non-zero RSI
    nonzero_rsi = (combined["rsi"] != 0).sum()
    logger.info(
        "Non-zero RSI rows: %d / %d (%.0f%%)",
        nonzero_rsi, len(combined), nonzero_rsi / len(combined) * 100,
    )

    logger.info("Training SwarmVoter (%d models) …", N_MODELS)
    voter = SwarmVoter(model_dir=MODEL_DIR, market=MARKET)
    summary = voter.train_swarm(
        df=combined,
        market=MARKET,
        n_models=N_MODELS,
        forward_bars=FORWARD_BARS,
    )

    logger.info(
        "Training complete: %d/%d models | avg_accuracy=%.3f | trained_at=%s",
        summary.get("n_trained", 0),
        N_MODELS,
        summary.get("avg_train_accuracy", 0.0),
        summary.get("trained_at", "?"),
    )

    if summary.get("n_trained", 0) > 0:
        saved_path = voter.save_models()
        logger.info("Models saved → %s", saved_path)
        logger.info("Restart the bot to load the new ensemble (it will activate SwarmVoter).")
    else:
        logger.error("No models trained — check data quality.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
