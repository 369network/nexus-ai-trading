"""
Strategy Evolver for NEXUS ALPHA — Genetic Algorithm.

Evolves trading strategy parameters to maximise Sharpe ratio.

Algorithm:
    1. Generate population of 20 parameter sets (initial = random perturbations)
    2. Backtest each set on last 30 days of data
    3. Select top 5 by Sharpe ratio (elitist selection)
    4. Crossover: create offspring by randomly mixing two parents
    5. Mutation: ±10% random adjustment on one parameter
    6. Repeat for N generations
    7. Return best params if Sharpe improvement > 10%

Human approval: store proposed params in Supabase for approval unless
auto_evolve=True is set.
"""

from __future__ import annotations

import copy
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

POPULATION_SIZE = 20
ELITE_SIZE = 5
MUTATION_RATE = 0.3     # Probability of mutating a given offspring
MUTATION_SCALE = 0.10   # ±10% perturbation
MIN_IMPROVEMENT_RATIO = 0.10  # 10% Sharpe improvement threshold


@dataclass
class Individual:
    """A single parameter set candidate in the genetic population."""
    params: Dict[str, Any]
    sharpe: float = 0.0
    win_rate: float = 0.0
    total_trades: int = 0
    max_drawdown: float = 0.0

    def fitness(self) -> float:
        return self.sharpe


@dataclass
class EvolutionResult:
    """Result returned by StrategyEvolver.evolve()."""
    strategy_name: str
    current_sharpe: float
    best_sharpe: float
    best_params: Dict[str, Any]
    improvement_pct: float
    generations: int
    population_evaluated: int
    requires_approval: bool
    proposed_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class StrategyEvolver:
    """
    Genetic algorithm for trading strategy parameter optimisation.

    Parameters
    ----------
    supabase_client : optional
        Supabase client for storing proposed params and approval workflow.
    auto_evolve : bool
        If True, apply improvements automatically without human approval.
    """

    def __init__(
        self,
        supabase_client: Optional[Any] = None,
        auto_evolve: bool = False,
        random_seed: Optional[int] = None,
    ) -> None:
        self._supabase = supabase_client
        self._auto_evolve = auto_evolve
        if random_seed is not None:
            random.seed(random_seed)
            np.random.seed(random_seed)
        logger.info(
            "StrategyEvolver initialised | auto_evolve=%s | pop=%d | elite=%d",
            auto_evolve, POPULATION_SIZE, ELITE_SIZE,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def evolve(
        self,
        strategy_name: str,
        base_params: Dict[str, Any],
        param_ranges: Dict[str, Tuple[Any, Any, type]],  # {name: (min, max, type)}
        historical_data: Any,  # pd.DataFrame or similar
        current_sharpe: float = 0.0,
        generations: int = 10,
        backtest_fn: Optional[Any] = None,
    ) -> EvolutionResult:
        """
        Run genetic algorithm to find better parameters.

        Parameters
        ----------
        strategy_name : str
            Strategy identifier.
        base_params : dict
            Current parameter set (starting point).
        param_ranges : dict
            Specifies valid range per param: {name: (min_val, max_val, type)}.
            Only params listed here will be evolved.
        historical_data : DataFrame-like
            30 days of OHLCV data for backtesting.
        current_sharpe : float
            Sharpe of the current production params (baseline).
        generations : int
            Number of GA generations.
        backtest_fn : callable, optional
            Custom backtest function: fn(params, data) → {sharpe, win_rate, ...}
            If None, uses built-in simplified backtester.

        Returns
        -------
        EvolutionResult
        """
        logger.info("Evolving %s for %d generations", strategy_name, generations)

        # Initialise population
        population = self._initialise_population(base_params, param_ranges)

        # Evaluate initial population
        for ind in population:
            metrics = self._backtest(ind.params, historical_data, backtest_fn)
            ind.sharpe = metrics.get("sharpe", 0.0)
            ind.win_rate = metrics.get("win_rate", 0.0)
            ind.total_trades = metrics.get("total_trades", 0)
            ind.max_drawdown = metrics.get("max_drawdown", 0.0)

        # Evolution loop
        for gen in range(generations):
            # Select elite
            elite = sorted(population, key=lambda x: x.fitness(), reverse=True)[:ELITE_SIZE]

            # Generate offspring
            offspring = self._generate_offspring(
                elite, POPULATION_SIZE - ELITE_SIZE, param_ranges
            )

            # Evaluate offspring
            for ind in offspring:
                metrics = self._backtest(ind.params, historical_data, backtest_fn)
                ind.sharpe = metrics.get("sharpe", 0.0)
                ind.win_rate = metrics.get("win_rate", 0.0)
                ind.total_trades = metrics.get("total_trades", 0)
                ind.max_drawdown = metrics.get("max_drawdown", 0.0)

            # New population = elite + offspring
            population = elite + offspring

            best = max(population, key=lambda x: x.fitness())
            logger.debug(
                "Gen %d/%d | best_sharpe=%.3f | win_rate=%.2f%%",
                gen + 1, generations, best.sharpe, best.win_rate * 100,
            )

        # Final best
        best = max(population, key=lambda x: x.fitness())
        improvement_pct = (
            (best.sharpe - current_sharpe) / abs(current_sharpe) * 100
            if current_sharpe != 0 else float("inf")
        )

        requires_approval = (
            improvement_pct >= MIN_IMPROVEMENT_RATIO * 100
            and not self._auto_evolve
        )

        result = EvolutionResult(
            strategy_name=strategy_name,
            current_sharpe=current_sharpe,
            best_sharpe=best.sharpe,
            best_params=best.params,
            improvement_pct=improvement_pct,
            generations=generations,
            population_evaluated=POPULATION_SIZE * (generations + 1),
            requires_approval=requires_approval,
        )

        logger.info(
            "%s evolution done | improvement=%.1f%% | requires_approval=%s",
            strategy_name, improvement_pct, requires_approval,
        )

        if improvement_pct >= MIN_IMPROVEMENT_RATIO * 100:
            self._store_proposed_params(result)
            if self._auto_evolve:
                logger.info("Auto-evolve: params applied for %s", strategy_name)

        return result

    def get_pending_approvals(self) -> List[Dict[str, Any]]:
        """Return proposed parameter updates awaiting human approval."""
        if self._supabase is None:
            return []
        try:
            resp = (
                self._supabase.table("strategy_param_proposals")
                .select("*")
                .eq("status", "pending")
                .execute()
            )
            return resp.data or []
        except Exception as exc:
            logger.warning("Could not fetch pending approvals: %s", exc)
            return []

    def approve_proposal(self, proposal_id: str, approved_by: str = "human") -> bool:
        """Mark a proposal as approved in Supabase."""
        if self._supabase is None:
            return False
        try:
            self._supabase.table("strategy_param_proposals").update({
                "status": "approved",
                "approved_by": approved_by,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", proposal_id).execute()
            return True
        except Exception as exc:
            logger.error("Approval failed: %s", exc)
            return False

    def reject_proposal(self, proposal_id: str) -> bool:
        """Mark a proposal as rejected."""
        if self._supabase is None:
            return False
        try:
            self._supabase.table("strategy_param_proposals").update({
                "status": "rejected",
            }).eq("id", proposal_id).execute()
            return True
        except Exception as exc:
            logger.error("Rejection failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Genetic operations
    # ------------------------------------------------------------------

    def _initialise_population(
        self,
        base_params: Dict[str, Any],
        param_ranges: Dict[str, Tuple],
    ) -> List[Individual]:
        """Create initial population via random perturbations of base_params."""
        population = [Individual(params=copy.deepcopy(base_params))]  # Include original

        for _ in range(POPULATION_SIZE - 1):
            ind_params = copy.deepcopy(base_params)
            # Randomly perturb each evolvable param
            for param_name, (lo, hi, typ) in param_ranges.items():
                if param_name not in ind_params:
                    continue
                base_val = float(ind_params[param_name])
                noise = random.uniform(-MUTATION_SCALE * 3, MUTATION_SCALE * 3)
                new_val = base_val * (1 + noise)
                new_val = max(float(lo), min(float(hi), new_val))
                ind_params[param_name] = typ(new_val)
            population.append(Individual(params=ind_params))

        return population

    def _generate_offspring(
        self,
        elite: List[Individual],
        n: int,
        param_ranges: Dict[str, Tuple],
    ) -> List[Individual]:
        """Generate n offspring from elite via crossover + mutation."""
        offspring = []
        for _ in range(n):
            if len(elite) >= 2:
                parent_a, parent_b = random.sample(elite, 2)
                child_params = self._crossover(parent_a.params, parent_b.params, param_ranges)
            else:
                child_params = copy.deepcopy(elite[0].params)

            if random.random() < MUTATION_RATE:
                child_params = self._mutate(child_params, param_ranges)

            offspring.append(Individual(params=child_params))
        return offspring

    @staticmethod
    def _crossover(
        params_a: Dict[str, Any],
        params_b: Dict[str, Any],
        param_ranges: Dict[str, Tuple],
    ) -> Dict[str, Any]:
        """Uniform crossover: randomly pick each evolvable param from parent A or B."""
        child = copy.deepcopy(params_a)
        for param_name in param_ranges:
            if param_name in params_b and random.random() > 0.5:
                child[param_name] = params_b[param_name]
        return child

    @staticmethod
    def _mutate(
        params: Dict[str, Any],
        param_ranges: Dict[str, Tuple],
    ) -> Dict[str, Any]:
        """Randomly adjust one evolvable parameter by ±MUTATION_SCALE."""
        mutable = [k for k in param_ranges if k in params]
        if not mutable:
            return params
        param_name = random.choice(mutable)
        lo, hi, typ = param_ranges[param_name]
        base_val = float(params[param_name])
        noise = random.uniform(-MUTATION_SCALE, MUTATION_SCALE)
        new_val = base_val * (1 + noise)
        new_val = max(float(lo), min(float(hi), new_val))
        params[param_name] = typ(new_val)
        return params

    # ------------------------------------------------------------------
    # Backtesting
    # ------------------------------------------------------------------

    def _backtest(
        self,
        params: Dict[str, Any],
        historical_data: Any,
        backtest_fn: Optional[Any],
    ) -> Dict[str, float]:
        """
        Run backtest for given params on historical data.

        Uses custom backtest_fn if provided, otherwise runs a simplified
        built-in backtest based on RSI and SMA crossover signals.
        """
        if backtest_fn is not None:
            try:
                return backtest_fn(params, historical_data)
            except Exception as exc:
                logger.warning("Custom backtest_fn failed: %s", exc)
                return {"sharpe": 0.0, "win_rate": 0.5, "total_trades": 0, "max_drawdown": 0.0}

        return self._simple_backtest(params, historical_data)

    @staticmethod
    def _simple_backtest(
        params: Dict[str, Any], data: Any
    ) -> Dict[str, float]:
        """
        Simplified backtest using RSI-based signals on close prices.
        Only used when no custom backtest_fn is provided.
        """
        try:
            closes = np.array(data["close"] if hasattr(data, "__getitem__") else data, dtype=float)
        except Exception:
            return {"sharpe": 0.0, "win_rate": 0.5, "total_trades": 0, "max_drawdown": 0.0}

        if len(closes) < 30:
            return {"sharpe": 0.0, "win_rate": 0.5, "total_trades": 0, "max_drawdown": 0.0}

        # RSI-based signals
        rsi_period = int(params.get("rsi_period", 14))
        rsi_low = float(params.get("rsi_low", params.get("rsi_oversold", 30)))
        rsi_high = float(params.get("rsi_high", params.get("rsi_exit", 70)))
        atr_mult = float(params.get("atr_stop_mult", 2.0))

        # Compute RSI manually (same as BaseStrategy)
        n = len(closes)
        rsi = np.full(n, 50.0)
        if n > rsi_period + 1:
            deltas = np.diff(closes)
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)
            avg_gain = np.mean(gains[:rsi_period])
            avg_loss = np.mean(losses[:rsi_period])
            for i in range(rsi_period, n - 1):
                avg_gain = (avg_gain * (rsi_period - 1) + gains[i]) / rsi_period
                avg_loss = (avg_loss * (rsi_period - 1) + losses[i]) / rsi_period
                if avg_loss == 0:
                    rsi[i + 1] = 100.0
                else:
                    rsi[i + 1] = 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)

        # Simple ATR for stops
        highs = closes * 1.005
        lows = closes * 0.995
        tr = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)))
        atr_period = min(14, n - 1)
        atr = np.full(n, 0.0)
        atr[atr_period] = np.mean(tr[1:atr_period + 1])
        for i in range(atr_period + 1, n):
            atr[i] = (atr[i - 1] * (atr_period - 1) + tr[i]) / atr_period

        # Simulate trades
        pnls: List[float] = []
        in_trade = False
        entry_price = 0.0
        stop_price = 0.0
        direction = 1

        for i in range(rsi_period + 1, n):
            if not in_trade:
                if rsi[i] < rsi_low:
                    # Long entry
                    entry_price = closes[i]
                    stop_price = entry_price - atr[i] * atr_mult
                    direction = 1
                    in_trade = True
                elif rsi[i] > rsi_high:
                    # Short entry
                    entry_price = closes[i]
                    stop_price = entry_price + atr[i] * atr_mult
                    direction = -1
                    in_trade = True
            else:
                # Exit conditions
                exit_price = None
                if direction == 1 and closes[i] <= stop_price:
                    exit_price = stop_price
                elif direction == -1 and closes[i] >= stop_price:
                    exit_price = stop_price
                elif direction == 1 and rsi[i] > rsi_high:
                    exit_price = closes[i]
                elif direction == -1 and rsi[i] < rsi_low:
                    exit_price = closes[i]

                if exit_price is not None:
                    risk = abs(entry_price - stop_price)
                    if risk > 0:
                        pnl_r = (exit_price - entry_price) * direction / risk
                        pnls.append(pnl_r)
                    in_trade = False

        if len(pnls) < 2:
            return {"sharpe": 0.0, "win_rate": 0.5, "total_trades": 0, "max_drawdown": 0.0}

        arr = np.array(pnls)
        sharpe = float(np.mean(arr) / np.std(arr, ddof=1) * np.sqrt(252)) if np.std(arr, ddof=1) > 0 else 0.0
        win_rate = float(np.sum(arr > 0) / len(arr))
        equity = np.cumsum(arr)
        peak = np.maximum.accumulate(equity)
        drawdown = np.max((peak - equity) / np.maximum(peak, 1e-9))

        return {
            "sharpe": sharpe,
            "win_rate": win_rate,
            "total_trades": len(pnls),
            "max_drawdown": float(drawdown),
        }

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _store_proposed_params(self, result: EvolutionResult) -> None:
        """Store evolution result in Supabase for approval workflow."""
        if self._supabase is None:
            return
        try:
            payload = {
                "strategy_name": result.strategy_name,
                "current_sharpe": result.current_sharpe,
                "best_sharpe": result.best_sharpe,
                "best_params": result.best_params,
                "improvement_pct": result.improvement_pct,
                "status": "auto_applied" if self._auto_evolve else "pending",
                "proposed_at": result.proposed_at,
                "requires_approval": result.requires_approval,
            }
            self._supabase.table("strategy_param_proposals").insert(payload).execute()
            logger.info("Stored proposed params for %s in Supabase", result.strategy_name)
        except Exception as exc:
            logger.warning("Could not store proposed params: %s", exc)
