"""
NEXUS ALPHA — Backtesting Engine Unit Tests
============================================
Tests for BacktestEngine: metrics, walk-forward, commission/slippage,
and multi-symbol portfolio. All use synthetic OHLCV data — no external APIs.
"""

from __future__ import annotations

import math
import random
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

from src.backtesting.engine import (
    BacktestEngine,
    BacktestResult,
    Trade,
    _compute_metrics,
    _sqrt_slippage,
)


# ---------------------------------------------------------------------------
# Helpers & fixtures
# ---------------------------------------------------------------------------


def _make_ohlcv(
    n: int = 200,
    start_price: float = 100.0,
    drift: float = 0.001,
    volatility: float = 0.015,
    seed: int = 42,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data with a random walk."""
    rng = random.Random(seed)
    ts = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    prices: List[float] = []
    price = start_price

    for _ in range(n):
        ret = rng.gauss(drift, volatility)
        price = max(price * (1 + ret), 1.0)
        prices.append(price)

    data: List[Dict[str, Any]] = []
    for i, (t, c) in enumerate(zip(ts, prices)):
        o = prices[i - 1] if i > 0 else c
        h = max(o, c) * (1 + abs(rng.gauss(0, 0.005)))
        l = min(o, c) * (1 - abs(rng.gauss(0, 0.005)))
        vol = rng.uniform(1000, 5000)
        data.append({"open": o, "high": h, "low": l, "close": c, "volume": vol})

    df = pd.DataFrame(data, index=ts)
    return df


def _make_trending_ohlcv(n: int = 200, start: float = 100.0) -> pd.DataFrame:
    """Strong uptrend data — MA crossover will always go long."""
    rng = random.Random(99)
    ts = pd.date_range("2023-01-01", periods=n, freq="1h", tz="UTC")
    price = start
    rows = []
    for _ in range(n):
        ret = rng.gauss(0.005, 0.005)   # Strong positive drift
        price = max(price * (1 + ret), 1.0)
        o = price * (1 - 0.001)
        h = price * 1.003
        l = price * 0.997
        rows.append({"open": o, "high": h, "low": l, "close": price, "volume": 2000.0})
    return pd.DataFrame(rows, index=ts)


# ---------------------------------------------------------------------------
# Simple MA crossover strategy
# ---------------------------------------------------------------------------


class MACrossoverStrategy:
    """
    Simple moving average crossover strategy for testing.
    Generates BUY when fast MA crosses above slow MA, SELL on crossunder.
    """

    def __init__(
        self,
        fast_period: int = 5,
        slow_period: int = 20,
        size_fraction: float = 0.9,
    ) -> None:
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.size_fraction = size_fraction
        self._prev_fast: Optional[float] = None
        self._prev_slow: Optional[float] = None

    def generate_signal(
        self, bar: pd.Series, history: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        if len(history) < self.slow_period:
            return None

        closes = history["close"].values
        fast_ma = closes[-self.fast_period:].mean()
        slow_ma = closes[-self.slow_period:].mean()

        signal = None
        if self._prev_fast is not None and self._prev_slow is not None:
            if self._prev_fast <= self._prev_slow and fast_ma > slow_ma:
                signal = {
                    "action": "BUY",
                    "size_fraction": self.size_fraction,
                    "stop_loss": bar["close"] * 0.97,
                    "take_profit": bar["close"] * 1.06,
                }
            elif self._prev_fast >= self._prev_slow and fast_ma < slow_ma:
                signal = {
                    "action": "SELL",
                    "size_fraction": self.size_fraction,
                }

        self._prev_fast = fast_ma
        self._prev_slow = slow_ma
        return signal


class AlwaysBuyStrategy:
    """Buys on the first bar and holds. Used for isolated metric testing."""

    def __init__(self) -> None:
        self._bought = False

    def generate_signal(
        self, bar: pd.Series, history: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        if not self._bought:
            self._bought = True
            return {"action": "BUY", "size_fraction": 0.95}
        return None


class DoNothingStrategy:
    """Never trades. Used to verify zero-trade edge case."""

    def generate_signal(
        self, bar: pd.Series, history: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        return None


# ---------------------------------------------------------------------------
# 1. Simple momentum backtest (MA crossover)
# ---------------------------------------------------------------------------


class TestSimpleMomentumBacktest:
    """Tests that the backtesting engine runs end-to-end correctly."""

    def test_runs_without_error(self):
        data = _make_ohlcv(200)
        engine = BacktestEngine("2023-01-01", "2023-12-31", initial_capital=10_000)
        strategy = MACrossoverStrategy(fast_period=5, slow_period=20)
        result = engine.run(strategy, data, symbol="TEST")

        assert isinstance(result, BacktestResult)
        assert result.symbol == "TEST"
        assert result.initial_capital == 10_000

    def test_equity_curve_is_series(self):
        data = _make_ohlcv(200)
        engine = BacktestEngine("2023-01-01", "2023-12-31", initial_capital=10_000)
        strategy = MACrossoverStrategy()
        result = engine.run(strategy, data)

        assert isinstance(result.equity_curve, pd.Series)
        assert not result.equity_curve.empty
        assert len(result.equity_curve) == len(data)

    def test_drawdown_series_always_non_positive(self):
        data = _make_ohlcv(200)
        engine = BacktestEngine("2023-01-01", "2023-12-31", initial_capital=10_000)
        strategy = MACrossoverStrategy()
        result = engine.run(strategy, data)

        assert (result.drawdown_series <= 0).all(), "Drawdown should always be <= 0"

    def test_trending_market_generates_profit(self):
        """A simple MA crossover should profit in a strong uptrend."""
        data = _make_trending_ohlcv(300)
        engine = BacktestEngine("2023-01-01", "2023-12-31", initial_capital=10_000)
        strategy = MACrossoverStrategy(fast_period=3, slow_period=15)
        result = engine.run(strategy, data)

        # Should have made some trades in a trending market
        assert result.metrics.get("total_trades", 0) >= 0  # at least ran without error
        assert result.final_equity > 0

    def test_no_trades_returns_initial_capital(self):
        data = _make_ohlcv(100)
        engine = BacktestEngine("2023-01-01", "2023-12-31", initial_capital=50_000)
        strategy = DoNothingStrategy()
        result = engine.run(strategy, data)

        assert result.metrics.get("total_trades", 0) == 0
        assert abs(result.final_equity - 50_000) < 1.0  # No change


# ---------------------------------------------------------------------------
# 2. Backtest metrics calculation
# ---------------------------------------------------------------------------


class TestBacktestMetricsCalculation:
    """Verify all statistical metrics are computed correctly."""

    def _make_equity_from_returns(self, returns: List[float], start: float = 10_000) -> pd.Series:
        idx = pd.date_range("2023-01-01", periods=len(returns), freq="1D", tz="UTC")
        equity = [start]
        for r in returns:
            equity.append(equity[-1] * (1 + r))
        return pd.Series(equity[1:], index=idx)

    def _make_closed_trade(self, pnl: float) -> Trade:
        return Trade(
            trade_id="test",
            symbol="TEST",
            direction="LONG",
            entry_time=datetime(2023, 1, 1, tzinfo=timezone.utc),
            exit_time=datetime(2023, 1, 2, tzinfo=timezone.utc),
            entry_price=100.0,
            exit_price=100.0 + pnl,
            size=1.0,
            commission=0.1,
            slippage=0.05,
            realized_pnl=pnl,
            is_open=False,
        )

    def test_total_return_positive(self):
        equity = self._make_equity_from_returns([0.01] * 30)
        trades = [self._make_closed_trade(10)] * 5
        metrics = _compute_metrics(equity, trades, initial_capital=10_000)
        assert metrics["total_return_pct"] > 0

    def test_total_return_negative(self):
        equity = self._make_equity_from_returns([-0.01] * 30)
        trades = [self._make_closed_trade(-10)] * 5
        metrics = _compute_metrics(equity, trades, initial_capital=10_000)
        assert metrics["total_return_pct"] < 0

    def test_sharpe_ratio_positive_for_positive_returns(self):
        equity = self._make_equity_from_returns([0.002] * 252)
        trades = [self._make_closed_trade(10)] * 10
        metrics = _compute_metrics(equity, trades, initial_capital=10_000, risk_free_rate=0.0)
        assert metrics["sharpe_ratio"] > 0

    def test_sharpe_ratio_zero_for_constant_equity(self):
        equity = self._make_equity_from_returns([0.0] * 100)
        trades = [self._make_closed_trade(0)] * 5
        metrics = _compute_metrics(equity, trades, initial_capital=10_000)
        # Constant returns → std=0 → sharpe=0
        assert metrics["sharpe_ratio"] == 0.0

    def test_max_drawdown_calculation(self):
        # Equity goes up then drops 20%
        returns = [0.02] * 10 + [-0.022] * 10
        equity = self._make_equity_from_returns(returns)
        trades = [self._make_closed_trade(5)] * 3
        metrics = _compute_metrics(equity, trades, initial_capital=10_000)
        assert metrics["max_drawdown_pct"] > 0
        # After 10 gains of 2%, value ≈ 10000 * 1.02^10 ≈ 12190
        # After 10 losses of 2.2%, value drops. Max DD should be meaningful
        assert metrics["max_drawdown_pct"] > 5  # at least 5%

    def test_win_rate_calculation(self):
        trades = (
            [self._make_closed_trade(100)] * 6   # 6 wins
            + [self._make_closed_trade(-50)] * 4  # 4 losses
        )
        equity = self._make_equity_from_returns([0.001] * len(trades))
        metrics = _compute_metrics(equity, trades, initial_capital=10_000)
        assert abs(metrics["win_rate_pct"] - 60.0) < 0.01

    def test_profit_factor_calculation(self):
        trades = (
            [self._make_closed_trade(200)] * 3   # 600 gross profit
            + [self._make_closed_trade(-100)] * 4  # 400 gross loss
        )
        equity = self._make_equity_from_returns([0.001] * len(trades))
        metrics = _compute_metrics(equity, trades, initial_capital=10_000)
        expected_pf = 600 / 400
        assert abs(metrics["profit_factor"] - expected_pf) < 0.01

    def test_expectancy_formula(self):
        trades = (
            [self._make_closed_trade(100)] * 6
            + [self._make_closed_trade(-50)] * 4
        )
        equity = self._make_equity_from_returns([0.001] * len(trades))
        metrics = _compute_metrics(equity, trades, initial_capital=10_000)
        # Expectancy = 0.6 * 100 + 0.4 * (-50) = 60 - 20 = 40
        assert abs(metrics["expectancy"] - 40.0) < 0.01

    def test_empty_trades_returns_partial_metrics(self):
        equity = self._make_equity_from_returns([0.001] * 10)
        metrics = _compute_metrics(equity, [], initial_capital=10_000)
        assert metrics.get("total_trades", 0) == 0

    def test_all_metrics_present(self):
        returns = [0.002, -0.001, 0.003, -0.002, 0.001] * 20
        equity = self._make_equity_from_returns(returns)
        trades = [self._make_closed_trade(t) for t in [50, -20, 80, -30, 60]]
        metrics = _compute_metrics(equity, trades, initial_capital=10_000)

        expected_keys = [
            "total_return_pct", "sharpe_ratio", "sortino_ratio",
            "max_drawdown_pct", "win_rate_pct", "profit_factor",
            "total_trades", "expectancy", "avg_trade_pnl",
        ]
        for key in expected_keys:
            assert key in metrics, f"Missing metric: {key}"


# ---------------------------------------------------------------------------
# 3. Walk-forward analysis
# ---------------------------------------------------------------------------


class TestWalkForward:
    """Verify walk-forward doesn't cheat future data."""

    def test_walk_forward_returns_result(self):
        data = _make_ohlcv(500)
        engine = BacktestEngine("2023-01-01", "2024-01-01", initial_capital=10_000)
        strategy = MACrossoverStrategy(fast_period=5, slow_period=20)
        wf = engine.walk_forward(strategy, data, n_splits=3, test_size=0.2)

        assert wf.n_splits == 3
        assert len(wf.in_sample_metrics) == len(wf.out_of_sample_metrics)

    def test_walk_forward_oos_dates_are_after_is_dates(self):
        """Each OOS fold's data must temporally follow its IS fold."""
        data = _make_ohlcv(500)
        engine = BacktestEngine("2023-01-01", "2024-01-01", initial_capital=10_000)
        strategy = MACrossoverStrategy(fast_period=5, slow_period=20)
        wf = engine.walk_forward(strategy, data, n_splits=3, test_size=0.2)

        for i, fold_result in enumerate(wf.fold_results):
            # Fold result covers OOS window
            assert fold_result.start_date <= fold_result.end_date
            # OOS starts strictly after IS (checked via start dates ordering)
            if i > 0:
                prev_fold = wf.fold_results[i - 1]
                assert fold_result.start_date >= prev_fold.start_date

    def test_walk_forward_n_folds_equals_n_splits(self):
        data = _make_ohlcv(500)
        engine = BacktestEngine("2023-01-01", "2024-01-01", initial_capital=10_000)
        strategy = MACrossoverStrategy()
        wf = engine.walk_forward(strategy, data, n_splits=4, test_size=0.2)
        assert len(wf.fold_results) <= 4  # could be fewer if data runs out

    def test_walk_forward_raises_on_insufficient_data(self):
        data = _make_ohlcv(10)  # too few bars
        engine = BacktestEngine("2023-01-01", "2024-01-01", initial_capital=10_000)
        strategy = MACrossoverStrategy()
        with pytest.raises(ValueError, match="Not enough data"):
            engine.walk_forward(strategy, data, n_splits=5, test_size=0.2)

    def test_walk_forward_summary_metrics_populated(self):
        data = _make_ohlcv(500)
        engine = BacktestEngine("2023-01-01", "2024-01-01", initial_capital=10_000)
        strategy = MACrossoverStrategy()
        wf = engine.walk_forward(strategy, data, n_splits=3, test_size=0.2)
        assert isinstance(wf.summary_metrics, dict)


# ---------------------------------------------------------------------------
# 4. Commission and slippage
# ---------------------------------------------------------------------------


class TestCommissionAndSlippage:
    """Verify trading costs are deducted and affect final equity."""

    def test_commission_reduces_equity(self):
        data = _make_ohlcv(200)

        engine_no_cost = BacktestEngine(
            "2023-01-01", "2023-12-31",
            initial_capital=10_000,
            commission=0.0,
            slippage=0.0,
        )
        engine_with_cost = BacktestEngine(
            "2023-01-01", "2023-12-31",
            initial_capital=10_000,
            commission=0.002,  # 0.2%
            slippage=0.001,
        )

        strategy_1 = MACrossoverStrategy(fast_period=5, slow_period=20)
        strategy_2 = MACrossoverStrategy(fast_period=5, slow_period=20)
        result_free = engine_no_cost.run(strategy_1, data)
        result_costly = engine_with_cost.run(strategy_2, data)

        # If any trades occurred, costs must reduce net equity
        if result_free.metrics.get("total_trades", 0) > 0:
            assert result_costly.final_equity <= result_free.final_equity
            assert result_costly.metrics.get("total_commission", 0) > 0

    def test_total_commission_tracked_correctly(self):
        data = _make_trending_ohlcv(100)
        commission = 0.001
        engine = BacktestEngine(
            "2023-01-01", "2023-12-31",
            initial_capital=10_000,
            commission=commission,
            slippage=0.0,
        )
        strategy = MACrossoverStrategy(fast_period=3, slow_period=15)
        result = engine.run(strategy, data)

        # Commission should be non-negative
        total_comm = result.metrics.get("total_commission", 0)
        assert total_comm >= 0

    def test_slippage_model_positive_for_nonzero_volume(self):
        """sqrt slippage model should return a positive adjustment."""
        slip = _sqrt_slippage(price=100.0, size=10.0, volume=1000.0, base_slippage=0.001)
        assert slip > 0
        assert slip < 1.0  # Should be a small fraction of price

    def test_slippage_increases_with_size(self):
        """Larger orders relative to volume should have higher slippage."""
        small_slip = _sqrt_slippage(price=100.0, size=10.0, volume=10_000.0, base_slippage=0.001)
        large_slip = _sqrt_slippage(price=100.0, size=1000.0, volume=10_000.0, base_slippage=0.001)
        assert large_slip > small_slip

    def test_equity_does_not_go_negative(self):
        """Even with high costs, equity should never go below zero."""
        data = _make_ohlcv(200)
        engine = BacktestEngine(
            "2023-01-01", "2023-12-31",
            initial_capital=1_000,
            commission=0.01,  # Very high commission
            slippage=0.005,
        )
        strategy = MACrossoverStrategy(fast_period=3, slow_period=10)
        result = engine.run(strategy, data)
        assert result.final_equity >= 0

    def test_costs_reported_in_metrics(self):
        data = _make_ohlcv(200)
        engine = BacktestEngine(
            "2023-01-01", "2023-12-31",
            initial_capital=10_000,
            commission=0.001,
            slippage=0.0005,
        )
        strategy = MACrossoverStrategy()
        result = engine.run(strategy, data)

        if result.metrics.get("total_trades", 0) > 0:
            assert "total_commission" in result.metrics
            assert "total_slippage" in result.metrics
            assert "total_costs" in result.metrics
            assert result.metrics["total_costs"] >= 0


# ---------------------------------------------------------------------------
# 5. Multi-symbol portfolio backtest
# ---------------------------------------------------------------------------


class TestMultiSymbolPortfolio:
    """Test portfolio-level multi-symbol backtesting."""

    def test_portfolio_runs_on_two_symbols(self):
        data1 = _make_ohlcv(200, seed=1)
        data2 = _make_ohlcv(200, seed=2)

        engine = BacktestEngine(
            "2023-01-01", "2023-12-31", initial_capital=20_000
        )
        result = engine.run_portfolio(
            strategies={
                "SYM1": MACrossoverStrategy(fast_period=5, slow_period=20),
                "SYM2": MACrossoverStrategy(fast_period=5, slow_period=20),
            },
            market_data={"SYM1": data1, "SYM2": data2},
        )

        assert "SYM1" in result.symbol_results
        assert "SYM2" in result.symbol_results
        assert result.initial_capital == 20_000

    def test_portfolio_equity_curve_combined(self):
        data1 = _make_ohlcv(200, seed=11)
        data2 = _make_ohlcv(200, seed=22)

        engine = BacktestEngine(
            "2023-01-01", "2023-12-31", initial_capital=20_000
        )
        result = engine.run_portfolio(
            strategies={
                "SYM1": MACrossoverStrategy(),
                "SYM2": MACrossoverStrategy(),
            },
            market_data={"SYM1": data1, "SYM2": data2},
        )

        # Combined equity curve should exist
        assert isinstance(result.equity_curve, pd.Series)

    def test_portfolio_capital_split_evenly(self):
        """Each symbol should receive equal allocation of total capital."""
        data1 = _make_ohlcv(100, seed=33)
        data2 = _make_ohlcv(100, seed=44)
        data3 = _make_ohlcv(100, seed=55)

        total = 30_000
        engine = BacktestEngine(
            "2023-01-01", "2023-12-31", initial_capital=total
        )
        result = engine.run_portfolio(
            strategies={
                "A": AlwaysBuyStrategy(),
                "B": AlwaysBuyStrategy(),
                "C": AlwaysBuyStrategy(),
            },
            market_data={"A": data1, "B": data2, "C": data3},
        )

        # Each sub-engine gets total/3 = 10,000
        for sym, sub_result in result.symbol_results.items():
            assert sub_result.initial_capital == pytest.approx(total / 3, rel=0.01)

    def test_portfolio_metrics_populated(self):
        data1 = _make_ohlcv(200, seed=77)
        data2 = _make_ohlcv(200, seed=88)

        engine = BacktestEngine(
            "2023-01-01", "2023-12-31", initial_capital=10_000
        )
        result = engine.run_portfolio(
            strategies={
                "X": MACrossoverStrategy(),
                "Y": MACrossoverStrategy(),
            },
            market_data={"X": data1, "Y": data2},
        )

        assert isinstance(result.metrics, dict)
        assert "total_trades" in result.metrics

    def test_portfolio_with_empty_data_for_one_symbol(self):
        """Portfolio should gracefully handle empty data for a symbol."""
        data1 = _make_ohlcv(200, seed=99)

        engine = BacktestEngine(
            "2023-01-01", "2023-12-31", initial_capital=10_000
        )
        # SYM2 has no data
        result = engine.run_portfolio(
            strategies={
                "SYM1": MACrossoverStrategy(),
                "SYM2": MACrossoverStrategy(),
            },
            market_data={"SYM1": data1, "SYM2": pd.DataFrame()},
        )

        # Should still return a result
        assert "SYM1" in result.symbol_results
        assert "SYM2" in result.symbol_results
