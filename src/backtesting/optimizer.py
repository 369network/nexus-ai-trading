"""
NEXUS ALPHA — Strategy Parameter Optimizer
===========================================
Grid search and random search optimizers with out-of-sample validation,
overfitting warnings, and Supabase result persistence for Dream Mode.
"""

from __future__ import annotations

import asyncio
import itertools
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Type

import numpy as np
import pandas as pd
import structlog

from src.backtesting.engine import BacktestEngine, BacktestResult

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass
class OptimizationResult:
    """Result of a full optimizer run."""

    best_params: Dict[str, Any]
    best_score: float
    metric: str
    results_df: pd.DataFrame       # One row per parameter combination
    in_sample_score: float         # Best in-sample score
    out_of_sample_score: float     # Score on held-out validation set
    overfit_warning: bool          # True if IS >> OOS score
    elapsed_seconds: float
    total_combinations: int
    evaluated_combinations: int


# ---------------------------------------------------------------------------
# Metric extractor
# ---------------------------------------------------------------------------


def _extract_metric(result: BacktestResult, metric: str) -> float:
    """Extract a named metric from BacktestResult, defaulting to -inf on error."""
    val = result.metrics.get(metric, float("-inf"))
    if val is None or (isinstance(val, float) and (np.isnan(val) or np.isinf(val))):
        return float("-inf")
    # Invert drawdown metrics so higher = better
    if metric in ("max_drawdown_pct",):
        return -abs(val)
    return float(val)


# ---------------------------------------------------------------------------
# Base optimizer
# ---------------------------------------------------------------------------


class _BaseOptimizer:
    """Shared infrastructure for grid and random search optimizers."""

    def __init__(
        self,
        engine_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._engine_kwargs: Dict[str, Any] = engine_kwargs or {}

    def _run_single(
        self,
        strategy_class: Type,
        params: Dict[str, Any],
        data: pd.DataFrame,
        symbol: str,
        engine_kwargs: Dict[str, Any],
    ) -> BacktestResult:
        """Instantiate strategy with params and run backtest."""
        strategy = strategy_class(**params)
        engine = BacktestEngine(**engine_kwargs)
        return engine.run(strategy, data, symbol=symbol)

    async def _run_parallel(
        self,
        strategy_class: Type,
        param_sets: List[Dict[str, Any]],
        data: pd.DataFrame,
        symbol: str,
        engine_kwargs: Dict[str, Any],
        n_jobs: int,
    ) -> List[BacktestResult]:
        """Run backtests in parallel using asyncio + thread pool."""
        semaphore = asyncio.Semaphore(n_jobs)

        async def _bounded_run(params: Dict[str, Any]) -> BacktestResult:
            async with semaphore:
                return await asyncio.to_thread(
                    self._run_single, strategy_class, params, data, symbol, engine_kwargs
                )

        tasks = [_bounded_run(p) for p in param_sets]
        return await asyncio.gather(*tasks)

    def _build_results_df(
        self,
        param_sets: List[Dict[str, Any]],
        results: List[BacktestResult],
        metric: str,
    ) -> pd.DataFrame:
        rows = []
        for params, res in zip(param_sets, results):
            row = {**params}
            row["score"] = _extract_metric(res, metric)
            row["total_return_pct"] = res.metrics.get("total_return_pct", 0.0)
            row["sharpe_ratio"] = res.metrics.get("sharpe_ratio", 0.0)
            row["max_drawdown_pct"] = res.metrics.get("max_drawdown_pct", 0.0)
            row["win_rate_pct"] = res.metrics.get("win_rate_pct", 0.0)
            row["total_trades"] = res.metrics.get("total_trades", 0)
            rows.append(row)
        return pd.DataFrame(rows).sort_values("score", ascending=False).reset_index(drop=True)

    @staticmethod
    def _split_data(data: pd.DataFrame, val_size: float) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Split data into in-sample and validation (out-of-sample) sets."""
        n = len(data)
        split_idx = int(n * (1 - val_size))
        return data.iloc[:split_idx], data.iloc[split_idx:]

    @staticmethod
    def _overfit_check(is_score: float, oos_score: float, threshold: float = 2.0) -> bool:
        """Return True if in-sample score is more than threshold× better than OOS."""
        if is_score <= 0 or oos_score <= 0:
            return False
        return (is_score / max(oos_score, 1e-9)) > threshold

    async def _persist_to_supabase(
        self,
        result: OptimizationResult,
        strategy_class_name: str,
        symbol: str,
    ) -> None:
        """Store optimization results to Supabase for Dream Mode consumption."""
        try:
            from src.db.supabase_client import SupabaseClient
            from src.config import get_settings

            settings = get_settings()
            client = await SupabaseClient.get_instance(
                url=settings.supabase_url,
                key=settings.supabase_service_key,
            )

            row = {
                "strategy": strategy_class_name,
                "symbol": symbol,
                "metric": result.metric,
                "best_params": result.best_params,
                "best_score": result.best_score,
                "is_score": result.in_sample_score,
                "oos_score": result.out_of_sample_score,
                "overfit_warning": result.overfit_warning,
                "total_combinations": result.total_combinations,
                "evaluated_combinations": result.evaluated_combinations,
                "elapsed_seconds": result.elapsed_seconds,
                "results_json": result.results_df.head(50).to_json(orient="records"),
            }

            await asyncio.to_thread(
                lambda: client._client.table("optimization_results").upsert(row).execute()  # type: ignore[union-attr]
            )
            log.info("optimizer_persisted_to_supabase", strategy=strategy_class_name, symbol=symbol)

        except Exception as exc:
            log.warning("optimizer_supabase_persist_failed", error=str(exc))


# ---------------------------------------------------------------------------
# Grid Search Optimizer
# ---------------------------------------------------------------------------


class GridSearchOptimizer(_BaseOptimizer):
    """
    Exhaustive grid search over all combinations in param_grid.

    Example::

        opt = GridSearchOptimizer()
        result = await opt.optimize(
            MACrossover,
            param_grid={"fast_period": [5, 10, 20], "slow_period": [50, 100, 200]},
            data=df,
            metric="sharpe_ratio",
            n_jobs=4,
        )
        print(result.best_params)
    """

    async def optimize(
        self,
        strategy_class: Type,
        param_grid: Dict[str, List[Any]],
        data: pd.DataFrame,
        metric: str = "sharpe_ratio",
        n_jobs: int = 4,
        val_size: float = 0.2,
        symbol: str = "UNKNOWN",
        engine_kwargs: Optional[Dict[str, Any]] = None,
        persist_results: bool = True,
    ) -> OptimizationResult:
        """
        Run exhaustive grid search.

        Args:
            strategy_class: Class to instantiate for each parameter combination.
                            Must accept params as kwargs in __init__.
            param_grid: Dict mapping param names to lists of values.
            data: Full OHLCV DataFrame for backtesting.
            metric: Metric to maximize (e.g. "sharpe_ratio", "total_return_pct").
            n_jobs: Max parallel backtest workers.
            val_size: Fraction of data reserved for out-of-sample validation.
            symbol: Symbol label for logging.
            engine_kwargs: Override BacktestEngine constructor arguments.
            persist_results: Store results to Supabase.

        Returns:
            OptimizationResult with best_params, best_score, and full results_df.
        """
        t_start = time.monotonic()
        ek = {**self._engine_kwargs, **(engine_kwargs or {})}

        if not ek:
            # Default engine: use full data date range
            ek = _default_engine_kwargs(data)

        in_sample, val_data = self._split_data(data, val_size)

        # Generate all combinations
        keys = list(param_grid.keys())
        value_lists = [param_grid[k] for k in keys]
        all_combinations = [dict(zip(keys, combo)) for combo in itertools.product(*value_lists)]
        total = len(all_combinations)

        log.info(
            "grid_search_started",
            strategy=strategy_class.__name__,
            combinations=total,
            metric=metric,
            n_jobs=n_jobs,
        )

        # Run all combinations on in-sample data
        is_results = await self._run_parallel(
            strategy_class, all_combinations, in_sample, symbol, ek, n_jobs
        )

        # Score and sort
        scored = [(params, _extract_metric(res, metric), res)
                  for params, res in zip(all_combinations, is_results)]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_params, best_is_score, _ = scored[0]
        results_df = self._build_results_df(all_combinations, is_results, metric)

        # Validate best params on out-of-sample
        oos_result = await asyncio.to_thread(
            self._run_single, strategy_class, best_params, val_data, symbol, ek
        )
        oos_score = _extract_metric(oos_result, metric)

        overfit = self._overfit_check(best_is_score, oos_score)
        if overfit:
            log.warning(
                "grid_search_overfit_warning",
                is_score=round(best_is_score, 4),
                oos_score=round(oos_score, 4),
                best_params=best_params,
            )

        elapsed = time.monotonic() - t_start

        opt_result = OptimizationResult(
            best_params=best_params,
            best_score=oos_score,   # Report OOS as canonical score
            metric=metric,
            results_df=results_df,
            in_sample_score=best_is_score,
            out_of_sample_score=oos_score,
            overfit_warning=overfit,
            elapsed_seconds=round(elapsed, 2),
            total_combinations=total,
            evaluated_combinations=total,
        )

        log.info(
            "grid_search_done",
            best_params=best_params,
            is_score=round(best_is_score, 4),
            oos_score=round(oos_score, 4),
            elapsed=round(elapsed, 1),
        )

        if persist_results:
            await self._persist_to_supabase(opt_result, strategy_class.__name__, symbol)

        return opt_result


# ---------------------------------------------------------------------------
# Random Search Optimizer
# ---------------------------------------------------------------------------


class RandomSearchOptimizer(_BaseOptimizer):
    """
    Random sampling optimizer — much faster than grid search for large spaces.

    Samples param combinations randomly without replacement (or with, if the
    space is smaller than n_iter).

    Example::

        opt = RandomSearchOptimizer(n_iter=100, seed=42)
        result = await opt.optimize(
            MACrossover,
            param_grid={"fast_period": range(5, 50), "slow_period": range(50, 300)},
            data=df,
            metric="sharpe_ratio",
        )
    """

    def __init__(
        self,
        n_iter: int = 50,
        seed: Optional[int] = None,
        engine_kwargs: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(engine_kwargs=engine_kwargs)
        self.n_iter = n_iter
        self.seed = seed

    async def optimize(
        self,
        strategy_class: Type,
        param_grid: Dict[str, Any],
        data: pd.DataFrame,
        metric: str = "sharpe_ratio",
        n_jobs: int = 4,
        val_size: float = 0.2,
        symbol: str = "UNKNOWN",
        engine_kwargs: Optional[Dict[str, Any]] = None,
        persist_results: bool = True,
    ) -> OptimizationResult:
        """
        Run random search optimization.

        ``param_grid`` values can be:
        - A list: sampled uniformly from the list.
        - A ``range``: sampled as int.
        - A tuple ``(low, high)``: sampled as continuous float.
        - A callable ``() -> value``: called each iteration.

        Args:
            strategy_class: Strategy class.
            param_grid: Parameter space definition (see above).
            data: Full OHLCV DataFrame.
            metric: Metric to maximize.
            n_jobs: Parallel workers.
            val_size: Out-of-sample fraction.
            symbol: Symbol label.
            engine_kwargs: Override BacktestEngine kwargs.
            persist_results: Store to Supabase.

        Returns:
            OptimizationResult.
        """
        t_start = time.monotonic()
        ek = {**self._engine_kwargs, **(engine_kwargs or {})}
        if not ek:
            ek = _default_engine_kwargs(data)

        rng = random.Random(self.seed)
        in_sample, val_data = self._split_data(data, val_size)

        # Build candidate set
        keys = list(param_grid.keys())
        # Estimate total space
        total_space = _estimate_space(param_grid)
        n_eval = min(self.n_iter, total_space)

        param_sets: List[Dict[str, Any]] = []
        seen: set[str] = set()
        max_attempts = n_eval * 10

        attempts = 0
        while len(param_sets) < n_eval and attempts < max_attempts:
            attempts += 1
            combo = {k: _sample_param(param_grid[k], rng) for k in keys}
            key = str(sorted(combo.items()))
            if key not in seen:
                seen.add(key)
                param_sets.append(combo)

        log.info(
            "random_search_started",
            strategy=strategy_class.__name__,
            n_eval=len(param_sets),
            total_space=total_space,
            metric=metric,
            n_jobs=n_jobs,
        )

        # Run all samples on in-sample data
        is_results = await self._run_parallel(
            strategy_class, param_sets, in_sample, symbol, ek, n_jobs
        )

        scored = [(params, _extract_metric(res, metric), res)
                  for params, res in zip(param_sets, is_results)]
        scored.sort(key=lambda x: x[1], reverse=True)

        best_params, best_is_score, _ = scored[0]
        results_df = self._build_results_df(param_sets, is_results, metric)

        # Validate on OOS
        oos_result = await asyncio.to_thread(
            self._run_single, strategy_class, best_params, val_data, symbol, ek
        )
        oos_score = _extract_metric(oos_result, metric)

        overfit = self._overfit_check(best_is_score, oos_score)
        if overfit:
            log.warning(
                "random_search_overfit_warning",
                is_score=round(best_is_score, 4),
                oos_score=round(oos_score, 4),
                best_params=best_params,
            )

        elapsed = time.monotonic() - t_start

        opt_result = OptimizationResult(
            best_params=best_params,
            best_score=oos_score,
            metric=metric,
            results_df=results_df,
            in_sample_score=best_is_score,
            out_of_sample_score=oos_score,
            overfit_warning=overfit,
            elapsed_seconds=round(elapsed, 2),
            total_combinations=total_space,
            evaluated_combinations=len(param_sets),
        )

        log.info(
            "random_search_done",
            best_params=best_params,
            is_score=round(best_is_score, 4),
            oos_score=round(oos_score, 4),
            evaluated=len(param_sets),
            elapsed=round(elapsed, 1),
        )

        if persist_results:
            await self._persist_to_supabase(opt_result, strategy_class.__name__, symbol)

        return opt_result


# ---------------------------------------------------------------------------
# Convenience top-level function
# ---------------------------------------------------------------------------


async def optimize(
    strategy_class: Type,
    param_grid: Dict[str, Any],
    data: pd.DataFrame,
    metric: str = "sharpe_ratio",
    n_jobs: int = 4,
    method: str = "grid",
    n_iter: int = 50,
    val_size: float = 0.2,
    symbol: str = "UNKNOWN",
    engine_kwargs: Optional[Dict[str, Any]] = None,
    persist_results: bool = True,
) -> OptimizationResult:
    """
    Unified entry-point for strategy optimization.

    Args:
        strategy_class: Strategy class to optimize.
        param_grid: Parameter grid/space.
        data: Full OHLCV DataFrame.
        metric: Metric to maximize.
        n_jobs: Parallel workers.
        method: "grid" or "random".
        n_iter: Iterations for random search.
        val_size: Out-of-sample fraction.
        symbol: Symbol label.
        engine_kwargs: BacktestEngine overrides.
        persist_results: Store to Supabase.

    Returns:
        OptimizationResult.
    """
    if method == "grid":
        opt = GridSearchOptimizer()
    elif method == "random":
        opt = RandomSearchOptimizer(n_iter=n_iter)
    else:
        raise ValueError(f"Unknown optimization method: {method!r}. Use 'grid' or 'random'.")

    return await opt.optimize(
        strategy_class=strategy_class,
        param_grid=param_grid,
        data=data,
        metric=metric,
        n_jobs=n_jobs,
        val_size=val_size,
        symbol=symbol,
        engine_kwargs=engine_kwargs,
        persist_results=persist_results,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sample_param(spec: Any, rng: random.Random) -> Any:
    """Sample a single parameter value from various spec types."""
    if isinstance(spec, list):
        return rng.choice(spec)
    if isinstance(spec, range):
        return rng.randint(spec.start, spec.stop - 1)
    if isinstance(spec, tuple) and len(spec) == 2:
        lo, hi = spec
        if isinstance(lo, int) and isinstance(hi, int):
            return rng.randint(lo, hi)
        return rng.uniform(lo, hi)
    if callable(spec):
        return spec()
    return spec


def _estimate_space(param_grid: Dict[str, Any]) -> int:
    """Estimate the total parameter space size (for logging)."""
    total = 1
    for spec in param_grid.values():
        if isinstance(spec, list):
            total *= len(spec)
        elif isinstance(spec, range):
            total *= len(spec)
        elif isinstance(spec, tuple) and len(spec) == 2:
            total *= 100  # Treat continuous as ~100 discrete points
        else:
            total *= 1
    return total


def _default_engine_kwargs(data: pd.DataFrame) -> Dict[str, Any]:
    """Build default BacktestEngine kwargs from data date range."""
    if data.empty:
        return {"start_date": "2020-01-01", "end_date": "2024-01-01"}
    idx = pd.to_datetime(data.index, utc=True)
    return {
        "start_date": idx.min().to_pydatetime(),
        "end_date": idx.max().to_pydatetime(),
        "initial_capital": 100_000.0,
    }
