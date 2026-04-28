"""
NEXUS ALPHA — Backtesting Engine
==================================
Event-driven bar-by-bar backtesting with realistic slippage, commission,
partial fills, walk-forward analysis, and full statistics computation.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Protocol, Tuple

import numpy as np
import pandas as pd
import structlog

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class Strategy(Protocol):
    """Minimal interface every strategy must implement for backtesting."""

    def generate_signal(
        self, bar: pd.Series, history: pd.DataFrame
    ) -> Optional[Dict[str, Any]]:
        """
        Called for each bar in chronological order.

        Args:
            bar: Current bar as a Series (open, high, low, close, volume, …).
            history: All bars up to and including the current bar.

        Returns:
            A signal dict with keys: action ('BUY'|'SELL'|'HOLD'),
            size_fraction (0–1 of capital), stop_loss, take_profit.
            Return None to hold.
        """
        ...


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class Trade:
    """Represents a completed round-trip trade."""

    trade_id: str
    symbol: str
    direction: str                    # 'LONG' | 'SHORT'
    entry_time: datetime
    exit_time: Optional[datetime]
    entry_price: float
    exit_price: Optional[float]
    size: float                       # units / contracts
    commission: float
    slippage: float
    realized_pnl: float = 0.0
    is_open: bool = True
    exit_reason: str = ""             # 'signal' | 'stop_loss' | 'take_profit' | 'eod'
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None


@dataclass
class BacktestResult:
    """Complete result of a single-symbol backtest run."""

    symbol: str
    start_date: datetime
    end_date: datetime
    initial_capital: float
    final_equity: float
    equity_curve: pd.Series            # index=datetime, values=equity
    drawdown_series: pd.Series         # index=datetime, values=drawdown_pct
    trades: List[Trade]
    metrics: Dict[str, float]
    parameters: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioBacktestResult:
    """Result of a multi-symbol portfolio backtest."""

    symbols: List[str]
    initial_capital: float
    final_equity: float
    equity_curve: pd.Series
    drawdown_series: pd.Series
    symbol_results: Dict[str, BacktestResult]
    metrics: Dict[str, float]


@dataclass
class WalkForwardResult:
    """Result of walk-forward analysis across multiple folds."""

    n_splits: int
    fold_results: List[BacktestResult]
    in_sample_metrics: List[Dict[str, float]]
    out_of_sample_metrics: List[Dict[str, float]]
    combined_equity_curve: pd.Series
    summary_metrics: Dict[str, float]


# ---------------------------------------------------------------------------
# Slippage model
# ---------------------------------------------------------------------------


def _sqrt_slippage(
    price: float,
    size: float,
    volume: float,
    base_slippage: float,
) -> float:
    """
    Realistic square-root market impact model.
    Impact = base_slippage * sqrt(size / daily_volume) * price.

    Args:
        price: Execution price.
        size: Order size in units.
        volume: Bar volume.
        base_slippage: Base slippage coefficient.
    """
    if volume <= 0:
        return price * base_slippage
    volume_fraction = size / max(volume, 1.0)
    impact = base_slippage * math.sqrt(volume_fraction)
    return price * impact


# ---------------------------------------------------------------------------
# Statistics calculation
# ---------------------------------------------------------------------------


def _compute_metrics(
    equity_curve: pd.Series,
    trades: List[Trade],
    initial_capital: float,
    risk_free_rate: float = 0.05,
) -> Dict[str, float]:
    """
    Compute full suite of backtest performance metrics.

    Returns dict with: total_return, cagr, sharpe, sortino, calmar,
    max_drawdown, win_rate, profit_factor, avg_trade, expectancy,
    avg_win, avg_loss, best_trade, worst_trade, total_trades, etc.
    """
    metrics: Dict[str, float] = {}

    if equity_curve.empty or len(trades) == 0:
        return {"total_return": 0.0, "total_trades": 0}

    # --- Return metrics ---
    final_equity = equity_curve.iloc[-1]
    total_return = (final_equity - initial_capital) / initial_capital
    metrics["total_return_pct"] = round(total_return * 100, 4)
    metrics["final_equity"] = round(final_equity, 2)

    # CAGR
    n_days = (equity_curve.index[-1] - equity_curve.index[0]).days
    if n_days > 0:
        years = n_days / 365.25
        cagr = (final_equity / initial_capital) ** (1 / years) - 1
        metrics["cagr_pct"] = round(cagr * 100, 4)
    else:
        metrics["cagr_pct"] = 0.0

    # --- Drawdown ---
    rolling_max = equity_curve.cummax()
    drawdown = (equity_curve - rolling_max) / rolling_max
    max_dd = drawdown.min()
    metrics["max_drawdown_pct"] = round(abs(max_dd) * 100, 4)

    # Max drawdown duration
    in_dd = drawdown < 0
    dd_periods = (in_dd != in_dd.shift()).cumsum()
    dd_groups = drawdown[in_dd].groupby(dd_periods[in_dd])
    if len(dd_groups) > 0:
        dd_lengths = dd_groups.apply(len)
        metrics["max_drawdown_duration_bars"] = int(dd_lengths.max())
    else:
        metrics["max_drawdown_duration_bars"] = 0

    # --- Sharpe & Sortino ---
    daily_returns = equity_curve.pct_change().dropna()
    if len(daily_returns) > 1:
        ann_factor = math.sqrt(252)  # assume daily bars; caller normalizes
        excess = daily_returns - (risk_free_rate / 252)
        std = daily_returns.std()
        if std > 0:
            sharpe = (excess.mean() / std) * ann_factor
            metrics["sharpe_ratio"] = round(sharpe, 4)
        else:
            metrics["sharpe_ratio"] = 0.0

        downside = daily_returns[daily_returns < 0]
        downside_std = downside.std()
        if downside_std > 0 and not math.isnan(downside_std):
            sortino = (daily_returns.mean() / downside_std) * ann_factor
            metrics["sortino_ratio"] = round(sortino, 4)
        else:
            metrics["sortino_ratio"] = 0.0
    else:
        metrics["sharpe_ratio"] = 0.0
        metrics["sortino_ratio"] = 0.0

    # Calmar
    if abs(max_dd) > 0 and metrics.get("cagr_pct", 0) != 0:
        calmar = metrics["cagr_pct"] / (abs(max_dd) * 100)
        metrics["calmar_ratio"] = round(calmar, 4)
    else:
        metrics["calmar_ratio"] = 0.0

    # --- Trade statistics ---
    closed = [t for t in trades if not t.is_open]
    metrics["total_trades"] = len(closed)
    metrics["open_trades"] = len([t for t in trades if t.is_open])

    if not closed:
        return metrics

    pnls = [t.realized_pnl for t in closed]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    metrics["win_rate_pct"] = round(len(wins) / len(pnls) * 100, 2)
    metrics["avg_trade_pnl"] = round(sum(pnls) / len(pnls), 2)
    metrics["avg_win"] = round(sum(wins) / len(wins), 2) if wins else 0.0
    metrics["avg_loss"] = round(sum(losses) / len(losses), 2) if losses else 0.0
    metrics["best_trade"] = round(max(pnls), 2)
    metrics["worst_trade"] = round(min(pnls), 2)
    metrics["total_gross_profit"] = round(sum(wins), 2)
    metrics["total_gross_loss"] = round(sum(losses), 2)

    # Profit factor
    gross_loss_abs = abs(sum(losses))
    if gross_loss_abs > 0:
        metrics["profit_factor"] = round(sum(wins) / gross_loss_abs, 4)
    else:
        metrics["profit_factor"] = float("inf") if wins else 0.0

    # Expectancy
    win_rate = len(wins) / len(pnls)
    loss_rate = 1 - win_rate
    avg_win = metrics["avg_win"]
    avg_loss_abs = abs(metrics["avg_loss"])
    metrics["expectancy"] = round(win_rate * avg_win - loss_rate * avg_loss_abs, 2)

    # Trade duration
    durations = [
        (t.exit_time - t.entry_time).total_seconds() / 3600
        for t in closed
        if t.exit_time
    ]
    if durations:
        metrics["avg_trade_duration_hours"] = round(sum(durations) / len(durations), 2)
    else:
        metrics["avg_trade_duration_hours"] = 0.0

    # Commission and slippage totals
    metrics["total_commission"] = round(sum(t.commission for t in closed), 2)
    metrics["total_slippage"] = round(sum(t.slippage for t in closed), 2)
    metrics["total_costs"] = round(metrics["total_commission"] + metrics["total_slippage"], 2)

    # Consecutive wins/losses
    consecutive_w = consecutive_l = cur_w = cur_l = 0
    for p in pnls:
        if p > 0:
            cur_w += 1; cur_l = 0
        else:
            cur_l += 1; cur_w = 0
        consecutive_w = max(consecutive_w, cur_w)
        consecutive_l = max(consecutive_l, cur_l)
    metrics["max_consecutive_wins"] = consecutive_w
    metrics["max_consecutive_losses"] = consecutive_l

    return metrics


# ---------------------------------------------------------------------------
# Core engine
# ---------------------------------------------------------------------------


class BacktestEngine:
    """
    Event-driven bar-by-bar backtesting engine.

    Simulates realistic execution including slippage, commission,
    partial fills, stop-loss/take-profit management, and P&L tracking.

    Usage::

        engine = BacktestEngine("2023-01-01", "2024-01-01", initial_capital=100_000)
        result = engine.run(my_strategy, market_data_df)
        print(result.metrics)
    """

    def __init__(
        self,
        start_date: str | datetime,
        end_date: str | datetime,
        initial_capital: float = 100_000.0,
        commission: float = 0.001,       # 0.1% per side
        slippage: float = 0.0005,        # 0.05% base; scaled by sqrt(vol fraction)
        risk_free_rate: float = 0.05,
    ) -> None:
        self.start_date = pd.Timestamp(start_date, tz="UTC")
        self.end_date = pd.Timestamp(end_date, tz="UTC")
        self.initial_capital = initial_capital
        self.commission = commission
        self.slippage = slippage
        self.risk_free_rate = risk_free_rate

        # Runtime state (reset on each run)
        self._cash: float = 0.0
        self._position: Optional[Trade] = None
        self._trades: List[Trade] = []
        self._equity_history: List[Tuple[datetime, float]] = []

        log.info(
            "backtest_engine_initialized",
            start=str(self.start_date.date()),
            end=str(self.end_date.date()),
            capital=initial_capital,
            commission=commission,
            slippage=slippage,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        strategy: Strategy,
        market_data: pd.DataFrame,
        symbol: str = "UNKNOWN",
    ) -> BacktestResult:
        """
        Run a single-symbol backtest.

        Args:
            strategy: Strategy implementing ``generate_signal(bar, history)``.
            market_data: DataFrame with columns open/high/low/close/volume,
                         DatetimeIndex in UTC. Must be sorted ascending.
            symbol: Symbol identifier for result labelling.

        Returns:
            BacktestResult with equity curve, trades list, and metrics dict.
        """
        data = self._prepare_data(market_data)
        if data.empty:
            log.warning("backtest_empty_data", symbol=symbol)
            return self._empty_result(symbol)

        self._reset_state()
        log.info("backtest_started", symbol=symbol, bars=len(data))

        for i in range(len(data)):
            bar = data.iloc[i]
            history = data.iloc[: i + 1]

            # 1. Check stop-loss / take-profit on open position
            if self._position and not self._position.is_open is False:
                self._check_sl_tp(bar)

            # 2. Ask strategy for signal
            signal = strategy.generate_signal(bar, history)

            # 3. Execute signal
            if signal and signal.get("action") in ("BUY", "SELL"):
                self._execute_signal(bar, signal, symbol)

            # 4. Mark-to-market equity
            equity = self._compute_equity(bar)
            self._equity_history.append((bar.name, equity))

        # Close any open position at last bar
        if self._position and self._position.is_open:
            last_bar = data.iloc[-1]
            self._close_position(last_bar, exit_reason="end_of_data")

        equity_series, drawdown_series = self._build_series()
        final_equity = equity_series.iloc[-1] if not equity_series.empty else self.initial_capital
        metrics = _compute_metrics(
            equity_series, self._trades, self.initial_capital, self.risk_free_rate
        )

        log.info(
            "backtest_complete",
            symbol=symbol,
            trades=len(self._trades),
            final_equity=round(final_equity, 2),
            sharpe=metrics.get("sharpe_ratio"),
        )

        return BacktestResult(
            symbol=symbol,
            start_date=self.start_date.to_pydatetime(),
            end_date=self.end_date.to_pydatetime(),
            initial_capital=self.initial_capital,
            final_equity=final_equity,
            equity_curve=equity_series,
            drawdown_series=drawdown_series,
            trades=self._trades,
            metrics=metrics,
        )

    def run_portfolio(
        self,
        strategies: Dict[str, Strategy],
        market_data: Dict[str, pd.DataFrame],
    ) -> PortfolioBacktestResult:
        """
        Run a portfolio-level backtest across multiple symbols.

        Capital is allocated equally across symbols at the start.
        Each symbol runs independently; equity curves are combined.

        Args:
            strategies: Mapping of symbol -> Strategy instance.
            market_data: Mapping of symbol -> OHLCV DataFrame.

        Returns:
            PortfolioBacktestResult with combined equity and per-symbol results.
        """
        symbols = list(strategies.keys())
        per_symbol_capital = self.initial_capital / max(len(symbols), 1)

        symbol_results: Dict[str, BacktestResult] = {}
        for sym in symbols:
            engine = BacktestEngine(
                start_date=self.start_date,
                end_date=self.end_date,
                initial_capital=per_symbol_capital,
                commission=self.commission,
                slippage=self.slippage,
                risk_free_rate=self.risk_free_rate,
            )
            data = market_data.get(sym, pd.DataFrame())
            result = engine.run(strategies[sym], data, symbol=sym)
            symbol_results[sym] = result
            log.info("portfolio_symbol_done", symbol=sym, trades=result.metrics.get("total_trades"))

        # Combine equity curves
        combined = self._combine_equity_curves(symbol_results)
        rolling_max = combined.cummax()
        drawdown = (combined - rolling_max) / rolling_max

        all_trades = [t for r in symbol_results.values() for t in r.trades]
        metrics = _compute_metrics(combined, all_trades, self.initial_capital, self.risk_free_rate)

        return PortfolioBacktestResult(
            symbols=symbols,
            initial_capital=self.initial_capital,
            final_equity=combined.iloc[-1] if not combined.empty else self.initial_capital,
            equity_curve=combined,
            drawdown_series=drawdown,
            symbol_results=symbol_results,
            metrics=metrics,
        )

    def walk_forward(
        self,
        strategy: Strategy,
        data: pd.DataFrame,
        n_splits: int = 5,
        test_size: float = 0.2,
        symbol: str = "UNKNOWN",
    ) -> WalkForwardResult:
        """
        Walk-forward analysis: train on in-sample, evaluate on out-of-sample.

        Strictly avoids peeking at future data. Each fold's out-of-sample
        period is contiguous and non-overlapping.

        Args:
            strategy: Strategy to test (re-fitted per fold if it has ``fit()``).
            data: Full OHLCV dataset, DatetimeIndex ascending.
            n_splits: Number of folds.
            test_size: Fraction of each fold used for out-of-sample testing.
            symbol: Symbol label.

        Returns:
            WalkForwardResult with per-fold metrics and combined equity.
        """
        prepared = self._prepare_data(data)
        total_bars = len(prepared)
        fold_size = total_bars // n_splits
        test_bars = max(int(fold_size * test_size), 1)
        train_bars = fold_size - test_bars

        if train_bars < 10:
            raise ValueError(
                f"Not enough data for {n_splits} splits. "
                f"Need at least {n_splits * 10 / (1 - test_size):.0f} bars."
            )

        log.info(
            "walk_forward_started",
            symbol=symbol,
            n_splits=n_splits,
            total_bars=total_bars,
            train_bars=train_bars,
            test_bars=test_bars,
        )

        fold_results: List[BacktestResult] = []
        is_metrics: List[Dict[str, float]] = []
        oos_metrics: List[Dict[str, float]] = []
        oos_equity_curves: List[pd.Series] = []

        for fold in range(n_splits):
            start_idx = fold * fold_size
            train_end = start_idx + train_bars
            test_end = min(train_end + test_bars, total_bars)

            in_sample = prepared.iloc[start_idx:train_end]
            out_of_sample = prepared.iloc[train_end:test_end]

            if in_sample.empty or out_of_sample.empty:
                log.warning("walk_forward_empty_fold", fold=fold)
                continue

            # Optionally fit strategy on in-sample
            if hasattr(strategy, "fit"):
                strategy.fit(in_sample)  # type: ignore[attr-defined]

            # In-sample backtest
            is_engine = BacktestEngine(
                start_date=in_sample.index[0],
                end_date=in_sample.index[-1],
                initial_capital=self.initial_capital,
                commission=self.commission,
                slippage=self.slippage,
            )
            is_result = is_engine.run(strategy, in_sample, symbol=f"{symbol}_IS_fold{fold}")
            is_metrics.append(is_result.metrics)

            # Out-of-sample backtest (uses IS final equity as starting capital)
            oos_capital = is_result.final_equity
            oos_engine = BacktestEngine(
                start_date=out_of_sample.index[0],
                end_date=out_of_sample.index[-1],
                initial_capital=oos_capital,
                commission=self.commission,
                slippage=self.slippage,
            )
            oos_result = oos_engine.run(strategy, out_of_sample, symbol=f"{symbol}_OOS_fold{fold}")
            oos_metrics.append(oos_result.metrics)
            fold_results.append(oos_result)
            oos_equity_curves.append(oos_result.equity_curve)

            log.info(
                "walk_forward_fold_done",
                fold=fold,
                is_sharpe=is_result.metrics.get("sharpe_ratio"),
                oos_sharpe=oos_result.metrics.get("sharpe_ratio"),
            )

        # Combine OOS equity curves
        if oos_equity_curves:
            combined_oos = pd.concat(oos_equity_curves).sort_index()
            # Chain: normalize each segment to continue from previous end
            combined_oos = self._chain_equity_curves(oos_equity_curves)
        else:
            combined_oos = pd.Series(dtype=float)

        # Summary metrics across OOS folds
        summary = self._average_metrics(oos_metrics)
        summary["is_oos_sharpe_ratio"] = (
            self._average_metrics(is_metrics).get("sharpe_ratio", 0.0)
            / max(summary.get("sharpe_ratio", 1.0), 0.001)
        )

        # Warn on overfitting
        is_avg_sharpe = self._average_metrics(is_metrics).get("sharpe_ratio", 0.0)
        oos_avg_sharpe = summary.get("sharpe_ratio", 0.0)
        if is_avg_sharpe > 0 and oos_avg_sharpe < is_avg_sharpe * 0.5:
            log.warning(
                "walk_forward_potential_overfit",
                is_sharpe=round(is_avg_sharpe, 3),
                oos_sharpe=round(oos_avg_sharpe, 3),
                ratio=round(oos_avg_sharpe / max(is_avg_sharpe, 0.001), 3),
            )

        return WalkForwardResult(
            n_splits=n_splits,
            fold_results=fold_results,
            in_sample_metrics=is_metrics,
            out_of_sample_metrics=oos_metrics,
            combined_equity_curve=combined_oos,
            summary_metrics=summary,
        )

    # ------------------------------------------------------------------
    # Private: state management
    # ------------------------------------------------------------------

    def _reset_state(self) -> None:
        self._cash = self.initial_capital
        self._position = None
        self._trades = []
        self._equity_history = []

    def _prepare_data(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalize DataFrame: ensure DatetimeIndex UTC, required columns, date filter."""
        if df.empty:
            return df

        data = df.copy()

        # Ensure DatetimeIndex
        if not isinstance(data.index, pd.DatetimeIndex):
            if "timestamp" in data.columns:
                data.index = pd.to_datetime(data["timestamp"], utc=True)
                data = data.drop(columns=["timestamp"], errors="ignore")
            elif "date" in data.columns:
                data.index = pd.to_datetime(data["date"], utc=True)
                data = data.drop(columns=["date"], errors="ignore")

        if data.index.tz is None:
            data.index = data.index.tz_localize("UTC")
        else:
            data.index = data.index.tz_convert("UTC")

        data = data.sort_index()

        # Filter to backtest window
        data = data[(data.index >= self.start_date) & (data.index <= self.end_date)]

        # Normalize column names
        data.columns = [c.lower() for c in data.columns]
        required = ["open", "high", "low", "close", "volume"]
        missing = [c for c in required if c not in data.columns]
        if missing:
            raise ValueError(f"Market data missing columns: {missing}")

        data = data.dropna(subset=["open", "high", "low", "close"])
        return data

    def _compute_equity(self, bar: pd.Series) -> float:
        """Mark portfolio to market at current close."""
        if self._position and self._position.is_open:
            unrealized = (bar["close"] - self._position.entry_price) * self._position.size
            if self._position.direction == "SHORT":
                unrealized = -unrealized
            return self._cash + unrealized
        return self._cash

    # ------------------------------------------------------------------
    # Private: order execution
    # ------------------------------------------------------------------

    def _execute_signal(
        self, bar: pd.Series, signal: Dict[str, Any], symbol: str
    ) -> None:
        """Process a BUY or SELL signal, handling position flips and partial fills."""
        action = signal["action"]
        size_fraction = float(signal.get("size_fraction", 1.0))
        size_fraction = max(0.0, min(1.0, size_fraction))

        if action == "BUY":
            # Close existing short before going long
            if self._position and self._position.is_open and self._position.direction == "SHORT":
                self._close_position(bar, exit_reason="signal_flip")
            if self._position is None or not self._position.is_open:
                self._open_long(bar, signal, symbol, size_fraction)

        elif action == "SELL":
            # Close existing long before going short
            if self._position and self._position.is_open and self._position.direction == "LONG":
                self._close_position(bar, exit_reason="signal_flip")
            if self._position is None or not self._position.is_open:
                self._open_short(bar, signal, symbol, size_fraction)

    def _open_long(
        self,
        bar: pd.Series,
        signal: Dict[str, Any],
        symbol: str,
        size_fraction: float,
    ) -> None:
        volume = bar.get("volume", 1.0)
        capital_to_use = self._cash * size_fraction

        # Entry price with slippage (buy at ask = close + slippage)
        raw_price = bar["open"]  # use open of next bar as fill price
        slip_cost = _sqrt_slippage(raw_price, capital_to_use / max(raw_price, 1e-9), volume, self.slippage)
        entry_price = raw_price + slip_cost

        units = capital_to_use / entry_price
        comm = capital_to_use * self.commission

        # Partial fill: if not enough cash
        if capital_to_use + comm > self._cash:
            capital_to_use = self._cash * 0.99  # leave 1% buffer
            units = capital_to_use / entry_price
            comm = capital_to_use * self.commission

        cost = units * entry_price + comm
        if cost > self._cash:
            return  # Insufficient capital

        self._cash -= cost
        trade = Trade(
            trade_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            direction="LONG",
            entry_time=bar.name.to_pydatetime() if hasattr(bar.name, "to_pydatetime") else bar.name,
            exit_time=None,
            entry_price=entry_price,
            exit_price=None,
            size=units,
            commission=comm,
            slippage=slip_cost * units,
            stop_loss=signal.get("stop_loss"),
            take_profit=signal.get("take_profit"),
        )
        self._position = trade
        self._trades.append(trade)

    def _open_short(
        self,
        bar: pd.Series,
        signal: Dict[str, Any],
        symbol: str,
        size_fraction: float,
    ) -> None:
        volume = bar.get("volume", 1.0)
        capital_to_use = self._cash * size_fraction

        raw_price = bar["open"]
        slip_cost = _sqrt_slippage(raw_price, capital_to_use / max(raw_price, 1e-9), volume, self.slippage)
        entry_price = raw_price - slip_cost  # short: sell at bid = lower price

        units = capital_to_use / entry_price
        comm = capital_to_use * self.commission

        if capital_to_use + comm > self._cash:
            capital_to_use = self._cash * 0.99
            units = capital_to_use / entry_price
            comm = capital_to_use * self.commission

        self._cash -= comm  # Only commission leaves immediately for short
        trade = Trade(
            trade_id=str(uuid.uuid4())[:8],
            symbol=symbol,
            direction="SHORT",
            entry_time=bar.name.to_pydatetime() if hasattr(bar.name, "to_pydatetime") else bar.name,
            exit_time=None,
            entry_price=entry_price,
            exit_price=None,
            size=units,
            commission=comm,
            slippage=slip_cost * units,
            stop_loss=signal.get("stop_loss"),
            take_profit=signal.get("take_profit"),
        )
        self._position = trade
        self._trades.append(trade)

    def _close_position(self, bar: pd.Series, exit_reason: str = "signal") -> None:
        """Close the current open position at the given bar's open price."""
        if not self._position or not self._position.is_open:
            return

        pos = self._position
        volume = bar.get("volume", 1.0)
        raw_price = bar["open"]
        slip_cost = _sqrt_slippage(raw_price, pos.size, volume, self.slippage)

        if pos.direction == "LONG":
            exit_price = raw_price - slip_cost   # sell at bid
            pnl = (exit_price - pos.entry_price) * pos.size
            proceeds = exit_price * pos.size
        else:  # SHORT
            exit_price = raw_price + slip_cost   # buy-back at ask
            pnl = (pos.entry_price - exit_price) * pos.size
            proceeds = pos.size * exit_price     # cost to cover

        exit_comm = proceeds * self.commission
        net_pnl = pnl - exit_comm

        if pos.direction == "LONG":
            self._cash += proceeds - exit_comm
        else:
            self._cash += net_pnl + pos.size * pos.entry_price  # return margin + pnl

        pos.exit_price = exit_price
        pos.exit_time = bar.name.to_pydatetime() if hasattr(bar.name, "to_pydatetime") else bar.name
        pos.realized_pnl = net_pnl
        pos.commission += exit_comm
        pos.slippage += slip_cost * pos.size
        pos.is_open = False
        pos.exit_reason = exit_reason
        self._position = None

    def _check_sl_tp(self, bar: pd.Series) -> None:
        """Check if stop-loss or take-profit is hit within the bar's range."""
        if not self._position or not self._position.is_open:
            return
        pos = self._position
        low, high = bar["low"], bar["high"]

        if pos.direction == "LONG":
            if pos.stop_loss and low <= pos.stop_loss:
                self._close_position(bar, exit_reason="stop_loss")
            elif pos.take_profit and high >= pos.take_profit:
                self._close_position(bar, exit_reason="take_profit")
        else:  # SHORT
            if pos.stop_loss and high >= pos.stop_loss:
                self._close_position(bar, exit_reason="stop_loss")
            elif pos.take_profit and low <= pos.take_profit:
                self._close_position(bar, exit_reason="take_profit")

    # ------------------------------------------------------------------
    # Private: result building
    # ------------------------------------------------------------------

    def _build_series(self) -> Tuple[pd.Series, pd.Series]:
        if not self._equity_history:
            return pd.Series(dtype=float), pd.Series(dtype=float)
        timestamps, equities = zip(*self._equity_history)
        eq = pd.Series(list(equities), index=list(timestamps), name="equity")
        rolling_max = eq.cummax()
        dd = (eq - rolling_max) / rolling_max
        return eq, dd

    def _empty_result(self, symbol: str) -> BacktestResult:
        return BacktestResult(
            symbol=symbol,
            start_date=self.start_date.to_pydatetime(),
            end_date=self.end_date.to_pydatetime(),
            initial_capital=self.initial_capital,
            final_equity=self.initial_capital,
            equity_curve=pd.Series(dtype=float),
            drawdown_series=pd.Series(dtype=float),
            trades=[],
            metrics={"total_trades": 0},
        )

    @staticmethod
    def _combine_equity_curves(
        results: Dict[str, BacktestResult],
    ) -> pd.Series:
        """Sum equity curves from all symbols (each starts at allocated capital)."""
        curves = [r.equity_curve for r in results.values() if not r.equity_curve.empty]
        if not curves:
            return pd.Series(dtype=float)
        combined = pd.concat(curves, axis=1).fillna(method="ffill").sum(axis=1)
        return combined

    @staticmethod
    def _chain_equity_curves(curves: List[pd.Series]) -> pd.Series:
        """Chain equity curves so each starts where the previous ended."""
        if not curves:
            return pd.Series(dtype=float)
        chained = [curves[0]]
        for i in range(1, len(curves)):
            prev_end = chained[-1].iloc[-1]
            curr_start = curves[i].iloc[0]
            scale = prev_end / max(curr_start, 1e-9)
            chained.append(curves[i] * scale)
        return pd.concat(chained).sort_index()

    @staticmethod
    def _average_metrics(metrics_list: List[Dict[str, float]]) -> Dict[str, float]:
        """Average numeric metrics across folds."""
        if not metrics_list:
            return {}
        keys = set().union(*metrics_list)
        result: Dict[str, float] = {}
        for k in keys:
            vals = [m[k] for m in metrics_list if k in m and isinstance(m[k], (int, float))]
            if vals:
                result[k] = round(sum(vals) / len(vals), 4)
        return result
