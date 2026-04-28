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

SYMBOLS    = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]  # Binance spot format (no slash)
INTERVAL   = "1h"
N_CANDLES  = 1000          # ~41 days of 1h data per symbol
MARKET     = "crypto"
FORWARD_BARS = 5           # Predict direction 5 bars (5h) ahead
N_MODELS   = 100
MODEL_DIR  = str(ROOT / "models" / "swarm")

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def fetch_candles(symbol: str, interval: str, limit: int) -> List[Dict[str, Any]]:
    """Fetch historical klines from Binance public REST API."""
    from src.data.providers.binance_rest import BinanceRESTClient
    client = BinanceRESTClient(market="spot")
    try:
        await client.init()
        klines = await client.get_klines(symbol=symbol, interval=interval, limit=limit)
        logger.info("  %s: fetched %d candles", symbol, len(klines))
        return klines
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def compute_indicators_for_series(
    klines: List[Any],
    symbol: str,
    timeframe: str,
) -> List[Dict[str, Any]]:
    """
    Feed klines sequentially through IndicatorEngine and collect
    the per-bar indicator dictionaries.  The first 60 bars are discarded
    as warm-up for indicators that need a long lookback (e.g. SMA 50).
    """
    from src.indicators.engine import IndicatorEngine

    engine = IndicatorEngine(symbol=symbol, timeframe=timeframe)
    results: List[Dict[str, Any]] = []

    for kline in klines:
        # Convert kline to candle dict expected by IndicatorEngine
        if hasattr(kline, "_asdict"):
            candle = kline._asdict()
        elif hasattr(kline, "__dict__"):
            candle = kline.__dict__
        elif isinstance(kline, (list, tuple)) and len(kline) >= 6:
            # Raw Binance list format: [ts, open, high, low, close, volume, ...]
            candle = {
                "open":   float(kline[1]),
                "high":   float(kline[2]),
                "low":    float(kline[3]),
                "close":  float(kline[4]),
                "volume": float(kline[5]),
            }
        else:
            candle = dict(kline) if hasattr(kline, "items") else {}

        try:
            indicators = await engine.compute(candle, timeframe)
            indicators["close"] = float(candle.get("close", 0))
            results.append(indicators)
        except Exception as exc:
            logger.debug("Indicator compute error (skipping bar): %s", exc)

    # Discard first 60 bars (warm-up artefacts)
    return results[60:]


def build_dataframe(rows: List[Dict[str, Any]]):
    """Build a pandas DataFrame with all SwarmVoter feature columns."""
    import pandas as pd

    from src.learning.swarm_voter import ALL_FEATURES

    df = pd.DataFrame(rows)

    # Ensure all required feature columns are present (fill missing with 0)
    for col in ALL_FEATURES + ["close"]:
        if col not in df.columns:
            df[col] = 0.0
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)

    return df


async def main() -> None:
    logger.info("=== SwarmVoter Training ===")
    logger.info("Symbols: %s | Interval: %s | Candles/symbol: %d", SYMBOLS, INTERVAL, N_CANDLES)

    from src.learning.swarm_voter import SwarmVoter

    all_rows: List[Dict[str, Any]] = []

    for sym in SYMBOLS:
        logger.info("Fetching %s …", sym)
        klines = await fetch_candles(sym, INTERVAL, N_CANDLES)
        if not klines:
            logger.warning("No candles returned for %s — skipping", sym)
            continue

        # Strip /USDT suffix for IndicatorEngine (it uses "BTC/USDT" format)
        display_sym = sym[:-4] + "/USDT"  # "BTCUSDT" → "BTC/USDT"
        logger.info("Computing indicators for %s …", display_sym)
        rows = await compute_indicators_for_series(klines, display_sym, INTERVAL)
        logger.info("  → %d usable bars after warm-up", len(rows))
        all_rows.extend(rows)

    if len(all_rows) < 100:
        logger.error("Insufficient training data (%d bars); exiting.", len(all_rows))
        sys.exit(1)

    logger.info("Building feature matrix from %d total bars …", len(all_rows))
    df = build_dataframe(all_rows)
    logger.info("DataFrame shape: %s", df.shape)

    logger.info("Training SwarmVoter (%d models) …", N_MODELS)
    voter = SwarmVoter(model_dir=MODEL_DIR, market=MARKET)
    summary = voter.train_swarm(
        df=df,
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

    logger.info("Saving models to %s …", MODEL_DIR)
    voter.save_models()
    logger.info("Done — restart the bot to load the new ensemble.")


if __name__ == "__main__":
    asyncio.run(main())
