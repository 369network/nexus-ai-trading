#!/usr/bin/env python3
"""
NEXUS ALPHA - Dream Mode Runner
Runs parameter optimisation for all enabled strategies and markets.
Proposals can be auto-applied or saved for manual review.

Usage:
    python scripts/run_dream_mode.py
    python scripts/run_dream_mode.py --strategy TrendMomentum --market crypto
    python scripts/run_dream_mode.py --auto-evolve
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from datetime import datetime, timezone
from itertools import product
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("nexus_alpha.dream_mode")

# ---------------------------------------------------------------------------
# Strategy parameter search spaces
# ---------------------------------------------------------------------------

PARAM_SPACES: Dict[str, Dict[str, List[Any]]] = {
    "TrendMomentum": {
        "ema_fast":       [5, 8, 10, 13],
        "ema_slow":       [21, 34, 50],
        "rsi_period":     [10, 14, 21],
        "rsi_threshold":  [50, 55, 60],
        "atr_period":     [10, 14],
        "atr_stop_mult":  [1.0, 1.5, 2.0],
        "atr_tp_mult":    [2.0, 3.0, 4.0],
    },
    "MeanReversionBB": {
        "bb_period":      [14, 20, 25],
        "bb_std":         [1.5, 2.0, 2.5],
        "rsi_period":     [10, 14, 21],
        "rsi_oversold":   [25, 30, 35],
        "rsi_overbought": [65, 70, 75],
        "atr_stop_mult":  [0.5, 1.0, 1.5],
        "atr_tp_mult":    [1.5, 2.0, 3.0],
    },
}

MARKET_SYMBOLS = {
    "crypto":          [("BTCUSDT", "1h"), ("ETHUSDT", "1h"), ("BTCUSDT", "4h")],
    "forex":           [("EUR_USD", "1h"), ("GBP_USD", "1h")],
    "indian_stocks":   [("RELIANCE", "1d"), ("TCS", "1d")],
    "us_stocks":       [("AAPL", "1h"), ("NVDA", "1h")],
}


# ---------------------------------------------------------------------------
# Mini backtest runner (reuses logic from run_backtest.py)
# ---------------------------------------------------------------------------

def _run_mini_backtest(
    candles: List[Dict],
    strategy_name: str,
    params: Dict[str, Any],
    initial_capital: float = 100_000.0,
) -> Optional[Dict[str, Any]]:
    """Run a fast in-memory backtest with given params. Returns metrics dict or None."""
    try:
        from scripts.run_backtest import (
            TrendMomentumStrategy,
            MeanReversionBBStrategy,
            BacktestSimulator,
            BacktestConfig,
        )
        from datetime import timedelta

        STRAT_MAP = {
            "TrendMomentum":   TrendMomentumStrategy,
            "MeanReversionBB": MeanReversionBBStrategy,
        }
        strat_class = STRAT_MAP.get(strategy_name)
        if strat_class is None:
            return None

        # Patch strategy params
        strat = strat_class()
        for k, v in params.items():
            if hasattr(strat, k):
                setattr(strat, k, v)

        if not candles:
            return None

        cfg = BacktestConfig(
            strategy_name     = strategy_name,
            market            = "crypto",
            symbol            = "SIM",
            timeframe         = "1h",
            start_date        = candles[0]["timestamp"],
            end_date          = candles[-1]["timestamp"],
            initial_capital   = initial_capital,
            commission_pct    = 0.075,
            slippage_pct      = 0.05,
            position_size_pct = 2.0,
        )

        sim = BacktestSimulator(config=cfg, candles=candles)
        sim.strategy = strat
        result = sim.run()
        return result.metrics

    except Exception as exc:
        logger.debug("Mini backtest failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Dream Mode optimiser
# ---------------------------------------------------------------------------

class DreamModeOptimiser:
    """
    Runs grid search over parameter space, identifies top performers,
    and returns proposals sorted by Sharpe ratio.
    """

    def __init__(
        self,
        strategy_name: str,
        candles: List[Dict],
        current_params: Dict[str, Any],
        max_combinations: int = 200,
    ) -> None:
        self.strategy_name    = strategy_name
        self.candles          = candles
        self.current_params   = current_params
        self.max_combinations = max_combinations
        self.param_space      = PARAM_SPACES.get(strategy_name, {})

    async def run(self) -> List[Dict[str, Any]]:
        """Return list of {params, metrics, improvement} sorted by Sharpe."""
        if not self.param_space:
            logger.warning("No param space defined for %s", self.strategy_name)
            return []

        # Baseline with current params
        baseline = _run_mini_backtest(self.candles, self.strategy_name, self.current_params)
        baseline_sharpe = (baseline or {}).get("sharpe_ratio", 0)
        logger.info(
            "[%s] Baseline Sharpe=%.3f, WinRate=%.1f%%",
            self.strategy_name,
            baseline_sharpe,
            (baseline or {}).get("win_rate", 0),
        )

        # Build parameter combinations
        keys   = list(self.param_space.keys())
        values = [self.param_space[k] for k in keys]
        all_combos = list(product(*values))

        # Sample if too many
        if len(all_combos) > self.max_combinations:
            import random
            random.shuffle(all_combos)
            all_combos = all_combos[: self.max_combinations]

        logger.info(
            "[%s] Testing %d parameter combinations…", self.strategy_name, len(all_combos)
        )

        results: List[Dict[str, Any]] = []
        loop = asyncio.get_event_loop()

        for i, combo in enumerate(all_combos):
            params = dict(zip(keys, combo))
            # Skip invalid combos (e.g., ema_fast >= ema_slow)
            if "ema_fast" in params and "ema_slow" in params:
                if params["ema_fast"] >= params["ema_slow"]:
                    continue
            if "rsi_oversold" in params and "rsi_overbought" in params:
                if params["rsi_oversold"] >= params["rsi_overbought"]:
                    continue

            # Run in executor to avoid blocking event loop
            metrics = await loop.run_in_executor(
                None, _run_mini_backtest, self.candles, self.strategy_name, params
            )

            if metrics and "sharpe_ratio" in metrics and metrics.get("total_trades", 0) >= 10:
                improvement = (
                    (metrics["sharpe_ratio"] - baseline_sharpe) / abs(baseline_sharpe) * 100
                    if baseline_sharpe != 0
                    else float("inf")
                )
                results.append({
                    "params":          params,
                    "metrics":         metrics,
                    "improvement_pct": round(improvement, 2),
                })

        # Sort by Sharpe ratio
        results.sort(key=lambda x: x["metrics"].get("sharpe_ratio", 0), reverse=True)

        top_10 = results[:10]
        if top_10:
            logger.info(
                "[%s] Top Sharpe=%.3f (improvement: +%.1f%%), Win=%.1f%%",
                self.strategy_name,
                top_10[0]["metrics"]["sharpe_ratio"],
                top_10[0]["improvement_pct"],
                top_10[0]["metrics"]["win_rate"],
            )

        return top_10


# ---------------------------------------------------------------------------
# Proposal storage
# ---------------------------------------------------------------------------

async def save_proposal(
    sb: Any,
    strategy_name: str,
    market: str,
    symbol: str,
    params: Dict[str, Any],
    metrics: Dict[str, Any],
    auto_evolve: bool,
) -> None:
    """Save parameter proposal to Supabase strategy_params table."""
    approval_status = "auto_applied" if auto_evolve else "pending"

    row = {
        "strategy_name":    strategy_name,
        "market":           market,
        "symbol":           symbol,
        "is_current":       auto_evolve,
        "params_json":      json.dumps(params),
        "proposed_by":      "dream_mode",
        "approval_status":  approval_status,
        "backtest_sharpe":  metrics.get("sharpe_ratio"),
        "backtest_win_rate": metrics.get("win_rate", 0) / 100,
        "backtest_drawdown": metrics.get("max_drawdown_pct"),
        "backtest_trades":  metrics.get("total_trades"),
        "notes":            f"Dream Mode optimisation {datetime.now(timezone.utc).isoformat()}",
    }

    try:
        sb.table("strategy_params").insert(row).execute()
        logger.info(
            "[SAVED] %s/%s/%s — status=%s",
            strategy_name, market, symbol, approval_status,
        )
    except Exception as exc:
        logger.error("Failed to save proposal: %s", exc)


async def get_current_params(
    sb: Any, strategy_name: str, market: str
) -> Dict[str, Any]:
    """Fetch current params from DB, or return defaults."""
    try:
        result = (
            sb.table("strategy_params")
            .select("params_json")
            .eq("strategy_name", strategy_name)
            .eq("market", market)
            .eq("is_current", True)
            .limit(1)
            .execute()
        )
        if result.data:
            return json.loads(result.data[0]["params_json"])
    except Exception as exc:
        logger.warning("Could not fetch current params: %s", exc)

    # Return default param space midpoints
    defaults = {}
    for k, vals in PARAM_SPACES.get(strategy_name, {}).items():
        defaults[k] = vals[len(vals) // 2]
    return defaults


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    from dotenv import load_dotenv
    load_dotenv()

    from supabase import create_client  # type: ignore
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")

    sb = create_client(url, key)

    auto_evolve = args.auto_evolve
    if auto_evolve:
        logger.warning("AUTO-EVOLVE mode: improvements will be applied without review!")

    strategies_to_run = [args.strategy] if args.strategy else list(PARAM_SPACES.keys())
    markets_to_run    = [args.market]   if args.market   else list(MARKET_SYMBOLS.keys())

    from scripts.run_backtest import load_candles
    from datetime import timedelta

    end_date   = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    summary: List[Dict] = []

    for strategy_name in strategies_to_run:
        for market in markets_to_run:
            symbol_tf_pairs = MARKET_SYMBOLS.get(market, [])
            if args.symbol:
                symbol_tf_pairs = [(s, t) for s, t in symbol_tf_pairs if s == args.symbol]

            for symbol, timeframe in symbol_tf_pairs:
                logger.info(
                    "\n=== Dream Mode: %s | %s/%s/%s ===",
                    strategy_name, market, symbol, timeframe,
                )

                candles = await load_candles(
                    sb, market, symbol, timeframe, start_date, end_date
                )

                if len(candles) < 100:
                    logger.warning(
                        "Insufficient data (%d candles) for %s/%s/%s — skipping.",
                        len(candles), market, symbol, timeframe,
                    )
                    continue

                current_params = await get_current_params(sb, strategy_name, market)

                optimiser = DreamModeOptimiser(
                    strategy_name    = strategy_name,
                    candles          = candles,
                    current_params   = current_params,
                    max_combinations = args.max_combos,
                )

                proposals = await optimiser.run()

                if not proposals:
                    logger.info("No improvements found for %s/%s/%s", strategy_name, market, symbol)
                    continue

                best = proposals[0]
                improvement = best["improvement_pct"]

                logger.info(
                    "Best proposal: Sharpe=%.3f, WinRate=%.1f%%, Improvement=+%.1f%%",
                    best["metrics"]["sharpe_ratio"],
                    best["metrics"]["win_rate"],
                    improvement,
                )

                if improvement > 0 or auto_evolve:
                    await save_proposal(
                        sb=sb,
                        strategy_name=strategy_name,
                        market=market,
                        symbol=symbol,
                        params=best["params"],
                        metrics=best["metrics"],
                        auto_evolve=auto_evolve and improvement > 5,  # Only auto-apply if >5% better
                    )

                summary.append({
                    "strategy":       strategy_name,
                    "market":         market,
                    "symbol":         symbol,
                    "improvement_pct": improvement,
                    "best_sharpe":    best["metrics"]["sharpe_ratio"],
                    "best_win_rate":  best["metrics"]["win_rate"],
                    "proposals_found": len(proposals),
                })

    # Print summary
    if summary:
        print("\n" + "=" * 70)
        print("  DREAM MODE SUMMARY")
        print("=" * 70)
        print(f"  {'Strategy':<22} {'Market':<14} {'Symbol':<12} {'Improvement':>12} {'Sharpe':>7}")
        print("  " + "-" * 67)
        for r in sorted(summary, key=lambda x: x["improvement_pct"], reverse=True):
            print(
                f"  {r['strategy']:<22} {r['market']:<14} {r['symbol']:<12} "
                f"{r['improvement_pct']:>+11.1f}% {r['best_sharpe']:>7.3f}"
            )
        print("=" * 70)

        if not auto_evolve:
            print(
                "\n  Proposals saved to strategy_params table with status='pending'."
                "\n  Review and approve at: Supabase Dashboard → strategy_params\n"
            )
    else:
        print("\nNo proposals generated.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXUS ALPHA Dream Mode Optimiser")
    parser.add_argument("--strategy",   type=str, help="Optimise specific strategy (default: all)")
    parser.add_argument("--market",     type=str, help="Optimise for specific market (default: all)")
    parser.add_argument("--symbol",     type=str, help="Optimise for specific symbol")
    parser.add_argument("--days",       type=int, default=90, help="Lookback days for backtest data")
    parser.add_argument("--max-combos", type=int, default=200, dest="max_combos",
                        help="Max parameter combinations to test (default: 200)")
    parser.add_argument("--auto-evolve", action="store_true", dest="auto_evolve",
                        help="Auto-apply improvements >5%% without manual review")

    args = parser.parse_args()
    asyncio.run(main(args))
