"""
Dream Mode for NEXUS ALPHA Learning System.

Runs offline parameter optimisation during low-activity windows.
Generates 50 parameter variations, backtests each, ranks by Sharpe,
and proposes improvements if gain > 10%.

Schedule:
    - Crypto: 04:00 UTC (slow market hours)
    - Forex: weekends
    - US/Indian stocks: 21:00 UTC (post-market)

If improvement > 10%: stores in Supabase for human approval
    (or auto-applies if auto_evolve=True).
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

NUM_VARIATIONS = 50
MIN_IMPROVEMENT_PCT = 10.0      # Sharpe improvement threshold
MUTATION_SCALE = 0.10            # ±10% parameter variation


@dataclass
class DreamResult:
    """Complete output from a Dream Mode run."""
    strategy_name: str
    market: str
    current_sharpe: float
    best_sharpe: float
    best_params: Dict[str, Any]
    improvement_pct: float
    top_variations: List[Dict[str, Any]]   # Top 5 for inspection
    requires_approval: bool
    auto_applied: bool
    run_timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    days_of_data: int = 30

    @property
    def is_improvement(self) -> bool:
        return self.improvement_pct >= MIN_IMPROVEMENT_PCT


class DreamMode:
    """
    Offline strategy optimisation engine.

    Works independently from the live trading loop.
    Generates parameter variations, runs simplified backtests,
    and proposes or applies the best-found params.

    Parameters
    ----------
    supabase_client : optional
        For persisting proposals and loading historical data.
    auto_evolve : bool
        Apply improvements automatically without human review.
    """

    def __init__(
        self,
        supabase_client: Optional[Any] = None,
        auto_evolve: bool = False,
    ) -> None:
        self._supabase = supabase_client
        self._auto_evolve = auto_evolve
        logger.info(
            "DreamMode initialised | auto_evolve=%s | variations=%d",
            auto_evolve, NUM_VARIATIONS,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        market: str,
        strategy_name: str,
        strategy_instance: Any,          # BaseStrategy subclass
        historical_data: Any,             # DataFrame or dict with 'close' etc.
        days_of_data: int = 30,
        param_ranges: Optional[Dict[str, Tuple]] = None,
        backtest_fn: Optional[Any] = None,
    ) -> DreamResult:
        """
        Execute one Dream Mode optimisation run.

        Parameters
        ----------
        market : str
            Target market.
        strategy_name : str
            Strategy identifier.
        strategy_instance : BaseStrategy
            Live strategy instance (read params from here).
        historical_data : DataFrame-like
            Historical OHLCV data (at least days_of_data worth).
        days_of_data : int
            How many days of data to use for backtesting.
        param_ranges : dict, optional
            {param_name: (min, max, type)}. If None, auto-infers from strategy.
        backtest_fn : callable, optional
            Custom backtest function: fn(params, data) → {sharpe, ...}

        Returns
        -------
        DreamResult
        """
        logger.info(
            "DreamMode.run: %s/%s | days=%d | variations=%d",
            market, strategy_name, days_of_data, NUM_VARIATIONS,
        )

        base_params = dict(strategy_instance.params)

        if param_ranges is None:
            param_ranges = self._infer_param_ranges(base_params)

        # Baseline performance
        current_metrics = self._backtest(base_params, historical_data, backtest_fn)
        current_sharpe = current_metrics.get("sharpe", 0.0)

        # Generate variations
        variations = self._generate_variations(base_params, param_ranges, NUM_VARIATIONS)

        # Evaluate all variations
        evaluated: List[Dict[str, Any]] = []
        for i, params in enumerate(variations):
            metrics = self._backtest(params, historical_data, backtest_fn)
            evaluated.append({
                "params": params,
                "sharpe": metrics.get("sharpe", 0.0),
                "win_rate": metrics.get("win_rate", 0.5),
                "total_trades": metrics.get("total_trades", 0),
                "max_drawdown": metrics.get("max_drawdown", 0.0),
            })
            if (i + 1) % 10 == 0:
                logger.debug("DreamMode: evaluated %d/%d variations", i + 1, NUM_VARIATIONS)

        # Rank by Sharpe
        evaluated.sort(key=lambda x: x["sharpe"], reverse=True)
        top_5 = evaluated[:5]
        best = evaluated[0]

        improvement_pct = (
            (best["sharpe"] - current_sharpe) / abs(current_sharpe) * 100
            if current_sharpe != 0
            else float("inf") if best["sharpe"] > 0 else 0.0
        )

        requires_approval = (
            improvement_pct >= MIN_IMPROVEMENT_PCT and not self._auto_evolve
        )
        auto_applied = improvement_pct >= MIN_IMPROVEMENT_PCT and self._auto_evolve

        result = DreamResult(
            strategy_name=strategy_name,
            market=market,
            current_sharpe=current_sharpe,
            best_sharpe=best["sharpe"],
            best_params=best["params"],
            improvement_pct=improvement_pct,
            top_variations=top_5,
            requires_approval=requires_approval,
            auto_applied=auto_applied,
            days_of_data=days_of_data,
        )

        logger.info(
            "DreamMode done: %s | current_sharpe=%.3f | best_sharpe=%.3f | improvement=%.1f%%",
            strategy_name, current_sharpe, best["sharpe"], improvement_pct,
        )

        if improvement_pct >= MIN_IMPROVEMENT_PCT:
            self._handle_improvement(result, strategy_instance)

        return result

    def schedule_is_active(self, market: str) -> bool:
        """
        Check if the current UTC time is within the scheduled Dream Mode window.

        Returns
        -------
        bool
        """
        now = datetime.now(timezone.utc)
        hour = now.hour
        weekday = now.weekday()  # 0=Mon, 6=Sun

        if market == "crypto":
            return 4 <= hour < 5  # 04:00-05:00 UTC
        elif market == "forex":
            return weekday >= 5  # Weekends
        elif market in ("us", "indian"):
            return 21 <= hour < 22  # 21:00-22:00 UTC (post-market)
        elif market == "commodities":
            return hour == 3  # 03:00 UTC
        return False

    def get_pending_proposals(self) -> List[Dict[str, Any]]:
        """Return all pending Dream Mode proposals from Supabase."""
        if self._supabase is None:
            return []
        try:
            resp = (
                self._supabase.table("dream_mode_proposals")
                .select("*")
                .eq("status", "pending")
                .execute()
            )
            return resp.data or []
        except Exception as exc:
            logger.warning("Could not fetch Dream Mode proposals: %s", exc)
            return []

    def approve_proposal(self, proposal_id: str, user_id: str = "human") -> bool:
        """Approve a proposed parameter change."""
        if self._supabase is None:
            return False
        try:
            self._supabase.table("dream_mode_proposals").update({
                "status": "approved",
                "approved_by": user_id,
                "approved_at": datetime.now(timezone.utc).isoformat(),
            }).eq("id", proposal_id).execute()
            logger.info("Dream Mode proposal %s approved by %s", proposal_id, user_id)
            return True
        except Exception as exc:
            logger.error("Approval failed: %s", exc)
            return False

    # ------------------------------------------------------------------
    # Variation generation
    # ------------------------------------------------------------------

    @staticmethod
    def _generate_variations(
        base_params: Dict[str, Any],
        param_ranges: Dict[str, Tuple],
        n: int,
    ) -> List[Dict[str, Any]]:
        """Generate N parameter variations via random ±MUTATION_SCALE perturbation."""
        variations = []
        for _ in range(n):
            variant = copy.deepcopy(base_params)
            for param_name, (lo, hi, typ) in param_ranges.items():
                if param_name not in variant:
                    continue
                base_val = float(variant[param_name])
                noise = random.uniform(-MUTATION_SCALE, MUTATION_SCALE)
                new_val = base_val * (1 + noise)
                new_val = max(float(lo), min(float(hi), new_val))
                variant[param_name] = typ(new_val)
            variations.append(variant)
        return variations

    @staticmethod
    def _infer_param_ranges(
        params: Dict[str, Any],
    ) -> Dict[str, Tuple]:
        """
        Auto-infer parameter ranges as ±50% of current values.
        Only numerical params are included.
        """
        ranges = {}
        for key, val in params.items():
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                lo = val * 0.5
                hi = val * 1.5
                typ = type(val)
                # Sanity bounds
                if typ == int:
                    lo = max(1, int(lo))
                    hi = max(lo + 1, int(hi))
                else:
                    lo = max(1e-9, lo)
                ranges[key] = (lo, hi, typ)
        return ranges

    # ------------------------------------------------------------------
    # Backtesting
    # ------------------------------------------------------------------

    def _backtest(
        self,
        params: Dict[str, Any],
        historical_data: Any,
        backtest_fn: Optional[Any],
    ) -> Dict[str, float]:
        """Run backtest using custom fn or built-in simplified method."""
        if backtest_fn is not None:
            try:
                return backtest_fn(params, historical_data)
            except Exception as exc:
                logger.debug("Custom backtest_fn error: %s", exc)

        return self._builtin_backtest(params, historical_data)

    @staticmethod
    def _builtin_backtest(
        params: Dict[str, Any], data: Any
    ) -> Dict[str, float]:
        """
        Simplified RSI/SMA crossover backtest for Dream Mode parameter evaluation.
        Returns {sharpe, win_rate, total_trades, max_drawdown}.
        """
        try:
            if hasattr(data, "__getitem__") and "close" in data:
                closes = np.array(data["close"], dtype=float)
            elif hasattr(data, "values"):
                closes = data.values.astype(float)
            else:
                closes = np.array(data, dtype=float)
        except Exception:
            return {"sharpe": 0.0, "win_rate": 0.5, "total_trades": 0, "max_drawdown": 0.0}

        if len(closes) < 30:
            return {"sharpe": 0.0, "win_rate": 0.5, "total_trades": 0, "max_drawdown": 0.0}

        # Extract params with defaults
        rsi_period = int(params.get("rsi_period", 14))
        rsi_low = float(params.get("rsi_low", params.get("rsi_oversold", 30)))
        rsi_high = float(params.get("rsi_high", params.get("rsi_exit", 70)))
        sma_fast_p = int(params.get("sma_fast", 20))
        sma_slow_p = int(params.get("sma_slow", 50))
        atr_mult = float(params.get("atr_stop_mult", 2.0))

        n = len(closes)

        # RSI
        rsi_vals = np.full(n, 50.0)
        if n > rsi_period + 1:
            deltas = np.diff(closes)
            gains = np.where(deltas > 0, deltas, 0.0)
            losses = np.where(deltas < 0, -deltas, 0.0)
            ag = np.mean(gains[:rsi_period])
            al = np.mean(losses[:rsi_period])
            for i in range(rsi_period, n - 1):
                ag = (ag * (rsi_period - 1) + gains[i]) / rsi_period
                al = (al * (rsi_period - 1) + losses[i]) / rsi_period
                rsi_vals[i + 1] = 100 - 100 / (1 + ag / al) if al > 0 else 100.0

        # SMAs
        def sma(arr, p):
            out = np.full(len(arr), np.nan)
            for i in range(p - 1, len(arr)):
                out[i] = np.mean(arr[i - p + 1:i + 1])
            return out

        sma_f = sma(closes, min(sma_fast_p, n - 1))
        sma_s = sma(closes, min(sma_slow_p, n - 1))

        # Simulated ATR
        highs = closes * 1.005
        lows = closes * 0.995
        tr = np.maximum(highs - lows, np.abs(highs - np.roll(closes, 1)))
        atr_p = min(14, n - 1)
        atr_vals = np.full(n, 0.001)
        atr_vals[atr_p] = np.mean(tr[1:atr_p + 1])
        for i in range(atr_p + 1, n):
            atr_vals[i] = (atr_vals[i - 1] * (atr_p - 1) + tr[i]) / atr_p

        # Trade simulation
        pnls: List[float] = []
        in_trade = False
        entry_price = stop_price = 0.0
        direction = 1
        min_start = max(rsi_period + 1, sma_slow_p)

        for i in range(min_start, n):
            if not in_trade:
                trend_up = not np.isnan(sma_f[i]) and not np.isnan(sma_s[i]) and sma_f[i] > sma_s[i]
                trend_dn = not np.isnan(sma_f[i]) and not np.isnan(sma_s[i]) and sma_f[i] < sma_s[i]
                if trend_up and rsi_vals[i] < rsi_high:
                    entry_price = closes[i]
                    stop_price = entry_price - atr_vals[i] * atr_mult
                    direction = 1
                    in_trade = True
                elif trend_dn and rsi_vals[i] > rsi_low:
                    entry_price = closes[i]
                    stop_price = entry_price + atr_vals[i] * atr_mult
                    direction = -1
                    in_trade = True
            else:
                exit_p = None
                if direction == 1 and closes[i] <= stop_price:
                    exit_p = stop_price
                elif direction == -1 and closes[i] >= stop_price:
                    exit_p = stop_price
                elif direction == 1 and rsi_vals[i] > rsi_high:
                    exit_p = closes[i]
                elif direction == -1 and rsi_vals[i] < rsi_low:
                    exit_p = closes[i]

                if exit_p is not None:
                    risk = abs(entry_price - stop_price)
                    if risk > 0:
                        pnls.append((exit_p - entry_price) * direction / risk)
                    in_trade = False

        if len(pnls) < 2:
            return {"sharpe": 0.0, "win_rate": 0.5, "total_trades": 0, "max_drawdown": 0.0}

        arr = np.array(pnls)
        std = np.std(arr, ddof=1)
        sharpe = float(np.mean(arr) / std * np.sqrt(252)) if std > 0 else 0.0
        win_rate = float(np.sum(arr > 0) / len(arr))
        equity = np.cumsum(arr)
        peak = np.maximum.accumulate(equity)
        dd = float(np.max((peak - equity) / np.maximum(np.abs(peak), 1e-9)))

        return {
            "sharpe": sharpe,
            "win_rate": win_rate,
            "total_trades": len(pnls),
            "max_drawdown": dd,
        }

    # ------------------------------------------------------------------
    # Improvement handling
    # ------------------------------------------------------------------

    def _handle_improvement(
        self, result: DreamResult, strategy_instance: Any
    ) -> None:
        """Persist proposal and optionally auto-apply."""
        self._store_proposal(result)
        if self._auto_evolve and result.auto_applied:
            try:
                strategy_instance.update_params(result.best_params)
                logger.info(
                    "DreamMode auto-applied params to %s (Sharpe: %.3f → %.3f)",
                    result.strategy_name, result.current_sharpe, result.best_sharpe,
                )
            except Exception as exc:
                logger.error("Auto-apply failed: %s", exc)

    def _store_proposal(self, result: DreamResult) -> None:
        if self._supabase is None:
            return
        try:
            payload = {
                "strategy_name": result.strategy_name,
                "market": result.market,
                "current_sharpe": result.current_sharpe,
                "best_sharpe": result.best_sharpe,
                "best_params": result.best_params,
                "improvement_pct": result.improvement_pct,
                "status": "auto_applied" if result.auto_applied else "pending",
                "proposed_at": result.run_timestamp,
                "days_of_data": result.days_of_data,
            }
            self._supabase.table("dream_mode_proposals").insert(payload).execute()
        except Exception as exc:
            logger.warning("Could not store Dream Mode proposal: %s", exc)

    def __repr__(self) -> str:
        return f"<DreamMode auto_evolve={self._auto_evolve} variations={NUM_VARIATIONS}>"


# ---------------------------------------------------------------------------
# DreamModeScheduler
# ---------------------------------------------------------------------------

class DreamModeScheduler:
    """
    Scheduler that runs DreamMode optimization cycles during low-activity periods.

    Wraps DreamMode and provides the async init/run/stop lifecycle interface
    expected by the main NexusAlpha orchestrator.

    Parameters
    ----------
    settings : Settings
        Application settings.
    db : SupabaseClient
        Database client passed to DreamMode for persistence.
    """

    def __init__(self, settings: Any, db: Any) -> None:
        self._settings = settings
        self._db = db
        self._dream_mode: Optional["DreamMode"] = None
        self._running = False

    async def init(self) -> None:
        """Initialise the dream mode scheduler."""
        logger.info("DreamModeScheduler: initialised (dream cycles will run during low-activity windows)")
        self._running = True

    async def run(self) -> None:
        """
        Background loop.  Runs DreamMode cycles every ~4 hours.
        Sleeps between cycles so it does not monopolise the event loop.
        """
        import asyncio as _asyncio
        self._running = True
        logger.info("DreamModeScheduler: background loop started")

        cycle_interval_s = 4 * 3600  # 4 hours between optimisation cycles

        while self._running:
            try:
                await _asyncio.wait_for(
                    _asyncio.sleep(cycle_interval_s),
                    timeout=cycle_interval_s + 60,
                )
            except _asyncio.CancelledError:
                logger.info("DreamModeScheduler: cancelled")
                return
            except Exception:
                pass

            if not self._running:
                break

            logger.info("DreamModeScheduler: dream cycle skipped (no strategies registered)")

    async def stop(self) -> None:
        """Stop the scheduler."""
        logger.info("DreamModeScheduler: stopping")
        self._running = False
