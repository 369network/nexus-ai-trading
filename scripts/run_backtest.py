#!/usr/bin/env python3
"""
NEXUS ALPHA - Backtesting Engine
Loads historical data from Supabase, simulates strategy execution with realistic
slippage and fees, and produces a comprehensive performance report.

Usage:
    python scripts/run_backtest.py --strategy TrendMomentum --market crypto --symbol BTCUSDT
    python scripts/run_backtest.py --strategy all --market crypto --days 90
    python scripts/run_backtest.py --compare TrendMomentum,MeanReversionBB --market crypto
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("nexus_alpha.backtest")


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BacktestConfig:
    strategy_name: str
    market: str
    symbol: str
    timeframe: str
    start_date: datetime
    end_date: datetime
    initial_capital: float = 100_000.0
    commission_pct: float = 0.075       # 0.075% taker fee (Binance)
    slippage_pct: float   = 0.05        # 0.05% estimated slippage
    position_size_pct: float = 2.0      # Risk 2% per trade
    max_positions: int   = 1


@dataclass
class SimulatedTrade:
    entry_time: datetime
    exit_time: Optional[datetime]
    direction: str
    entry_price: float
    exit_price: Optional[float]
    quantity: float
    stop_loss: float
    take_profit: float
    exit_reason: str
    gross_pnl: float = 0.0
    fees: float = 0.0
    slippage: float = 0.0
    net_pnl: float = 0.0
    net_pnl_pct: float = 0.0
    mae: float = 0.0    # Maximum Adverse Excursion
    mfe: float = 0.0    # Maximum Favorable Excursion


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: List[SimulatedTrade] = field(default_factory=list)
    equity_curve: List[Tuple[datetime, float]] = field(default_factory=list)
    metrics: Dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

async def load_candles(
    sb: Any,
    market: str,
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime,
) -> List[Dict[str, Any]]:
    """Load historical candles from Supabase."""
    try:
        result = (
            sb.table("market_data")
            .select("timestamp,open,high,low,close,volume")
            .eq("market", market)
            .eq("symbol", symbol)
            .eq("timeframe", timeframe)
            .gte("timestamp", since.isoformat())
            .lte("timestamp", until.isoformat())
            .order("timestamp", desc=False)
            .execute()
        )
        candles = []
        for row in result.data:
            candles.append({
                "timestamp": datetime.fromisoformat(
                    row["timestamp"].replace("Z", "+00:00")
                ),
                "open":   float(row["open"]),
                "high":   float(row["high"]),
                "low":    float(row["low"]),
                "close":  float(row["close"]),
                "volume": float(row["volume"]),
            })
        return candles
    except Exception as exc:
        logger.error("Failed to load candles: %s", exc)
        return []


# ---------------------------------------------------------------------------
# Strategy signal generator (simplified version for backtesting)
# ---------------------------------------------------------------------------

def compute_ema(closes: List[float], period: int) -> List[float]:
    if len(closes) < period:
        return []
    ema = []
    k = 2 / (period + 1)
    ema.append(sum(closes[:period]) / period)
    for price in closes[period:]:
        ema.append(price * k + ema[-1] * (1 - k))
    return ema


def compute_rsi(closes: List[float], period: int = 14) -> List[float]:
    if len(closes) < period + 1:
        return []
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas]
    losses = [-min(d, 0) for d in deltas]

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    rsi_vals = []
    for i in range(period, len(deltas)):
        if avg_loss == 0:
            rsi_vals.append(100.0)
        else:
            rs = avg_gain / avg_loss
            rsi_vals.append(100 - 100 / (1 + rs))
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    return rsi_vals


def compute_atr(candles: List[Dict], period: int = 14) -> List[float]:
    if len(candles) < 2:
        return []
    trs = []
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i - 1]["close"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))

    atrs = []
    if len(trs) < period:
        return atrs
    atrs.append(sum(trs[:period]) / period)
    for tr in trs[period:]:
        atrs.append((atrs[-1] * (period - 1) + tr) / period)
    return atrs


class TrendMomentumStrategy:
    """EMA crossover + RSI filter backtest strategy."""
    name = "TrendMomentum"
    ema_fast   = 8
    ema_slow   = 21
    rsi_period = 14
    rsi_threshold = 55
    atr_period = 14
    atr_stop_mult = 1.5
    atr_tp_mult   = 3.0

    def generate_signal(
        self, candles: List[Dict], idx: int
    ) -> Optional[Dict[str, Any]]:
        if idx < self.ema_slow + self.rsi_period + 2:
            return None

        closes = [c["close"] for c in candles[: idx + 1]]
        fast_emas = compute_ema(closes, self.ema_fast)
        slow_emas = compute_ema(closes, self.ema_slow)
        rsi_vals  = compute_rsi(closes, self.rsi_period)
        atrs      = compute_atr(candles[: idx + 1], self.atr_period)

        if not fast_emas or not slow_emas or not rsi_vals or not atrs:
            return None

        fast = fast_emas[-1]
        slow = slow_emas[-1]
        fast_prev = fast_emas[-2] if len(fast_emas) >= 2 else fast
        slow_prev = slow_emas[-2] if len(slow_emas) >= 2 else slow
        rsi  = rsi_vals[-1]
        atr  = atrs[-1]
        price = candles[idx]["close"]

        # Bullish crossover + RSI confirms trend
        if fast_prev <= slow_prev and fast > slow and rsi > self.rsi_threshold:
            return {
                "direction":   "LONG",
                "entry_price": price,
                "stop_loss":   price - atr * self.atr_stop_mult,
                "take_profit": price + atr * self.atr_tp_mult,
                "confidence":  min((rsi - 50) / 50, 1.0),
            }
        # Bearish crossover + RSI confirms downtrend
        if fast_prev >= slow_prev and fast < slow and rsi < (100 - self.rsi_threshold):
            return {
                "direction":   "SHORT",
                "entry_price": price,
                "stop_loss":   price + atr * self.atr_stop_mult,
                "take_profit": price - atr * self.atr_tp_mult,
                "confidence":  min((50 - rsi) / 50, 1.0),
            }
        return None


class MeanReversionBBStrategy:
    """Bollinger Band mean reversion backtest strategy."""
    name = "MeanReversionBB"
    bb_period   = 20
    bb_std      = 2.0
    rsi_period  = 14
    rsi_oversold   = 30
    rsi_overbought = 70
    atr_period     = 14
    atr_stop_mult  = 1.0
    atr_tp_mult    = 2.0

    def generate_signal(
        self, candles: List[Dict], idx: int
    ) -> Optional[Dict[str, Any]]:
        if idx < self.bb_period + self.rsi_period + 2:
            return None

        closes = [c["close"] for c in candles[: idx + 1]]
        if len(closes) < self.bb_period:
            return None

        # Bollinger Bands
        window = closes[-self.bb_period:]
        mean = sum(window) / self.bb_period
        variance = sum((x - mean) ** 2 for x in window) / self.bb_period
        std = variance ** 0.5
        upper = mean + self.bb_std * std
        lower = mean - self.bb_std * std
        price = closes[-1]

        rsi_vals = compute_rsi(closes, self.rsi_period)
        atrs     = compute_atr(candles[: idx + 1], self.atr_period)
        if not rsi_vals or not atrs:
            return None

        rsi = rsi_vals[-1]
        atr = atrs[-1]

        # Price below lower band + RSI oversold → mean reversion LONG
        if price < lower and rsi < self.rsi_oversold:
            return {
                "direction":   "LONG",
                "entry_price": price,
                "stop_loss":   price - atr * self.atr_stop_mult,
                "take_profit": mean,
                "confidence":  min((self.rsi_oversold - rsi) / self.rsi_oversold, 1.0),
            }
        # Price above upper band + RSI overbought → mean reversion SHORT
        if price > upper and rsi > self.rsi_overbought:
            return {
                "direction":   "SHORT",
                "entry_price": price,
                "stop_loss":   price + atr * self.atr_stop_mult,
                "take_profit": mean,
                "confidence":  min((rsi - self.rsi_overbought) / (100 - self.rsi_overbought), 1.0),
            }
        return None


STRATEGIES = {
    "TrendMomentum":    TrendMomentumStrategy,
    "MeanReversionBB":  MeanReversionBBStrategy,
}


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------

class BacktestSimulator:
    def __init__(self, config: BacktestConfig, candles: List[Dict]) -> None:
        self.config  = config
        self.candles = candles
        self.strategy = STRATEGIES[config.strategy_name]()

    def run(self) -> BacktestResult:
        cfg    = self.config
        result = BacktestResult(config=cfg)
        equity = cfg.initial_capital

        result.equity_curve.append((self.candles[0]["timestamp"], equity))
        open_trade: Optional[SimulatedTrade] = None

        for idx in range(len(self.candles)):
            candle = self.candles[idx]

            # Manage open trade
            if open_trade is not None:
                open_trade, closed = self._manage_trade(open_trade, candle, equity)
                if closed:
                    equity += closed.net_pnl
                    result.trades.append(closed)
                    result.equity_curve.append((candle["timestamp"], equity))
                    open_trade = None

            # Check for new signal (only if no open trade)
            if open_trade is None:
                signal = self.strategy.generate_signal(self.candles, idx)
                if signal:
                    # Apply slippage to entry
                    direction = signal["direction"]
                    slip = signal["entry_price"] * cfg.slippage_pct / 100
                    entry = signal["entry_price"] + (slip if direction == "LONG" else -slip)

                    # Position sizing (ATR-based risk)
                    risk_amount = equity * cfg.position_size_pct / 100
                    stop_dist   = abs(entry - signal["stop_loss"])
                    if stop_dist == 0:
                        continue
                    qty = risk_amount / stop_dist

                    # Entry fee
                    entry_fee = entry * qty * cfg.commission_pct / 100

                    open_trade = SimulatedTrade(
                        entry_time  = candle["timestamp"],
                        exit_time   = None,
                        direction   = direction,
                        entry_price = entry,
                        exit_price  = None,
                        quantity    = qty,
                        stop_loss   = signal["stop_loss"],
                        take_profit = signal["take_profit"],
                        exit_reason = "",
                        fees        = entry_fee,
                        slippage    = slip * qty,
                    )

        # Force-close any open trade at end of backtest
        if open_trade is not None:
            last = self.candles[-1]
            open_trade.exit_time  = last["timestamp"]
            open_trade.exit_price = last["close"]
            open_trade.exit_reason = "end_of_data"
            self._finalise_trade(open_trade, cfg)
            result.trades.append(open_trade)
            equity += open_trade.net_pnl
            result.equity_curve.append((last["timestamp"], equity))

        result.metrics = self._compute_metrics(result.trades, cfg.initial_capital, equity)
        return result

    def _manage_trade(
        self, trade: SimulatedTrade, candle: Dict, equity: float
    ) -> Tuple[Optional[SimulatedTrade], Optional[SimulatedTrade]]:
        """Check stop loss / take profit against this candle. Returns (open, closed)."""
        cfg = self.config
        low  = candle["low"]
        high = candle["high"]

        hit_sl = hit_tp = False

        if trade.direction == "LONG":
            if low <= trade.stop_loss:
                hit_sl = True
            elif high >= trade.take_profit:
                hit_tp = True

            # MAE / MFE tracking
            trade.mae = max(trade.mae, (trade.entry_price - low) / trade.entry_price)
            trade.mfe = max(trade.mfe, (high - trade.entry_price) / trade.entry_price)
        else:
            if high >= trade.stop_loss:
                hit_sl = True
            elif low <= trade.take_profit:
                hit_tp = True

            trade.mae = max(trade.mae, (high - trade.entry_price) / trade.entry_price)
            trade.mfe = max(trade.mfe, (trade.entry_price - low) / trade.entry_price)

        if hit_sl:
            exit_price = trade.stop_loss
            slip = exit_price * cfg.slippage_pct / 100
            trade.exit_price  = exit_price - (slip if trade.direction == "LONG" else -slip)
            trade.exit_time   = candle["timestamp"]
            trade.exit_reason = "stop_loss"
            self._finalise_trade(trade, cfg)
            return None, trade

        if hit_tp:
            exit_price = trade.take_profit
            slip = exit_price * cfg.slippage_pct / 100
            trade.exit_price  = exit_price - (slip if trade.direction == "LONG" else -slip)
            trade.exit_time   = candle["timestamp"]
            trade.exit_reason = "take_profit"
            self._finalise_trade(trade, cfg)
            return None, trade

        return trade, None

    def _finalise_trade(self, trade: SimulatedTrade, cfg: BacktestConfig) -> None:
        if trade.exit_price is None:
            return
        exit_fee = trade.exit_price * trade.quantity * cfg.commission_pct / 100
        trade.fees += exit_fee

        if trade.direction == "LONG":
            gross = (trade.exit_price - trade.entry_price) * trade.quantity
        else:
            gross = (trade.entry_price - trade.exit_price) * trade.quantity

        trade.gross_pnl  = gross
        trade.net_pnl    = gross - trade.fees - trade.slippage
        trade.net_pnl_pct = trade.net_pnl / (trade.entry_price * trade.quantity) * 100

    @staticmethod
    def _compute_metrics(
        trades: List[SimulatedTrade],
        initial_capital: float,
        final_equity: float,
    ) -> Dict[str, Any]:
        if not trades:
            return {"error": "No trades generated"}

        closed = [t for t in trades if t.exit_price is not None]
        winners = [t for t in closed if t.net_pnl > 0]
        losers  = [t for t in closed if t.net_pnl <= 0]

        total_pnl = sum(t.net_pnl for t in closed)
        win_pnl   = sum(t.net_pnl for t in winners)
        loss_pnl  = abs(sum(t.net_pnl for t in losers))

        win_rate  = len(winners) / len(closed) if closed else 0
        profit_factor = win_pnl / loss_pnl if loss_pnl > 0 else float("inf")
        avg_win   = win_pnl / len(winners) if winners else 0
        avg_loss  = loss_pnl / len(losers) if losers else 0
        avg_rr    = avg_win / avg_loss if avg_loss > 0 else 0
        total_return_pct = (final_equity - initial_capital) / initial_capital * 100

        # Compute Sharpe ratio from daily returns
        pnls = [t.net_pnl_pct for t in closed]
        if len(pnls) > 1:
            import statistics
            mean_ret = statistics.mean(pnls)
            std_ret  = statistics.stdev(pnls)
            sharpe   = (mean_ret / std_ret) * (252 ** 0.5) if std_ret > 0 else 0
        else:
            sharpe = 0

        # Max drawdown from equity curve (approximated from trades)
        running = initial_capital
        peak    = initial_capital
        max_dd  = 0.0
        for t in closed:
            running += t.net_pnl
            peak     = max(peak, running)
            dd       = (peak - running) / peak
            max_dd   = max(max_dd, dd)

        durations = [
            (t.exit_time - t.entry_time).total_seconds() / 3600
            for t in closed
            if t.exit_time
        ]
        avg_duration_h = sum(durations) / len(durations) if durations else 0

        return {
            "total_trades":       len(closed),
            "winning_trades":     len(winners),
            "losing_trades":      len(losers),
            "win_rate":           round(win_rate * 100, 2),
            "profit_factor":      round(profit_factor, 3),
            "avg_win_usd":        round(avg_win, 2),
            "avg_loss_usd":       round(avg_loss, 2),
            "avg_risk_reward":    round(avg_rr, 2),
            "total_pnl_usd":      round(total_pnl, 2),
            "total_return_pct":   round(total_return_pct, 2),
            "final_equity_usd":   round(final_equity, 2),
            "sharpe_ratio":       round(sharpe, 3),
            "max_drawdown_pct":   round(max_dd * 100, 2),
            "avg_trade_duration_hours": round(avg_duration_h, 1),
            "total_fees_usd":     round(sum(t.fees for t in closed), 2),
        }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def save_report(result: BacktestResult, output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    fname = f"{result.config.strategy_name}_{result.config.market}_{result.config.symbol}_{ts}.json"
    path  = output_dir / fname

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "strategy":         result.config.strategy_name,
            "market":           result.config.market,
            "symbol":           result.config.symbol,
            "timeframe":        result.config.timeframe,
            "start_date":       result.config.start_date.isoformat(),
            "end_date":         result.config.end_date.isoformat(),
            "initial_capital":  result.config.initial_capital,
            "commission_pct":   result.config.commission_pct,
            "slippage_pct":     result.config.slippage_pct,
        },
        "metrics": result.metrics,
        "equity_curve": [
            {"ts": ts.isoformat(), "equity": eq}
            for ts, eq in result.equity_curve
        ],
        "trades": [
            {
                "entry_time":   t.entry_time.isoformat(),
                "exit_time":    t.exit_time.isoformat() if t.exit_time else None,
                "direction":    t.direction,
                "entry_price":  t.entry_price,
                "exit_price":   t.exit_price,
                "quantity":     t.quantity,
                "stop_loss":    t.stop_loss,
                "take_profit":  t.take_profit,
                "exit_reason":  t.exit_reason,
                "net_pnl":      t.net_pnl,
                "net_pnl_pct":  t.net_pnl_pct,
                "fees":         t.fees,
                "mae":          t.mae,
                "mfe":          t.mfe,
            }
            for t in result.trades
        ],
    }

    with open(path, "w") as f:
        json.dump(report, f, indent=2)

    return path


def print_metrics(result: BacktestResult) -> None:
    cfg = result.config
    m   = result.metrics
    print(f"\n{'=' * 65}")
    print(f"  BACKTEST RESULTS: {cfg.strategy_name} | {cfg.market}/{cfg.symbol} | {cfg.timeframe}")
    print(f"  Period: {cfg.start_date.date()} → {cfg.end_date.date()}")
    print(f"{'=' * 65}")

    if "error" in m:
        print(f"  ERROR: {m['error']}")
        return

    print(f"  {'Total trades:':<35} {m['total_trades']}")
    print(f"  {'Win rate:':<35} {m['win_rate']}%")
    print(f"  {'Profit factor:':<35} {m['profit_factor']}")
    print(f"  {'Avg win / avg loss:':<35} ${m['avg_win_usd']:,.2f} / ${m['avg_loss_usd']:,.2f}")
    print(f"  {'Avg R:R:':<35} {m['avg_risk_reward']}")
    print(f"  {'Total P&L:':<35} ${m['total_pnl_usd']:,.2f}")
    print(f"  {'Total return:':<35} {m['total_return_pct']}%")
    print(f"  {'Final equity:':<35} ${m['final_equity_usd']:,.2f}")
    print(f"  {'Sharpe ratio:':<35} {m['sharpe_ratio']}")
    print(f"  {'Max drawdown:':<35} {m['max_drawdown_pct']}%")
    print(f"  {'Avg trade duration:':<35} {m['avg_trade_duration_hours']}h")
    print(f"  {'Total fees paid:':<35} ${m['total_fees_usd']:,.2f}")
    print(f"{'=' * 65}\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def main(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    from dotenv import load_dotenv
    load_dotenv()

    from supabase import create_client  # type: ignore
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set")

    sb = create_client(url, key)

    end_date   = datetime.now(timezone.utc)
    start_date = end_date - timedelta(days=args.days)

    strategies_to_run = list(STRATEGIES.keys()) if args.strategy == "all" else [args.strategy]
    if args.compare:
        strategies_to_run = [s.strip() for s in args.compare.split(",")]

    output_dir = Path("tests/backtests/reports")
    all_results: List[BacktestResult] = []

    for strat_name in strategies_to_run:
        if strat_name not in STRATEGIES:
            logger.error("Unknown strategy: %s. Available: %s", strat_name, list(STRATEGIES.keys()))
            continue

        logger.info("Loading candles for %s/%s/%s…", args.market, args.symbol, args.timeframe)
        candles = await load_candles(
            sb, args.market, args.symbol, args.timeframe, start_date, end_date
        )

        if len(candles) < 50:
            logger.error(
                "Insufficient candle data (%d candles). Run seed_historical.py first.", len(candles)
            )
            continue

        logger.info("Running backtest: %s on %d candles…", strat_name, len(candles))
        cfg = BacktestConfig(
            strategy_name    = strat_name,
            market           = args.market,
            symbol           = args.symbol,
            timeframe        = args.timeframe,
            start_date       = start_date,
            end_date         = end_date,
            initial_capital  = args.capital,
            commission_pct   = args.commission,
            slippage_pct     = args.slippage,
            position_size_pct= args.position_size,
        )

        simulator = BacktestSimulator(config=cfg, candles=candles)
        result    = simulator.run()
        all_results.append(result)

        print_metrics(result)

        report_path = save_report(result, output_dir)
        logger.info("Report saved: %s", report_path)

    # Comparison table
    if len(all_results) > 1:
        print("\n" + "=" * 65)
        print("  STRATEGY COMPARISON")
        print("=" * 65)
        header = f"  {'Strategy':<22} {'WinRate':>8} {'PF':>6} {'Sharpe':>7} {'Return':>8} {'MaxDD':>7}"
        print(header)
        print("  " + "-" * 60)
        for r in all_results:
            m = r.metrics
            if "error" in m:
                continue
            print(
                f"  {r.config.strategy_name:<22} "
                f"{m['win_rate']:>7.1f}% "
                f"{m['profit_factor']:>6.2f} "
                f"{m['sharpe_ratio']:>7.3f} "
                f"{m['total_return_pct']:>7.1f}% "
                f"{m['max_drawdown_pct']:>6.1f}%"
            )
        print("=" * 65 + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="NEXUS ALPHA Backtesting Engine")
    parser.add_argument("--strategy",  default="TrendMomentum",
                        help="Strategy name or 'all'")
    parser.add_argument("--compare",   type=str,
                        help="Comma-separated strategies to compare")
    parser.add_argument("--market",    default="crypto")
    parser.add_argument("--symbol",    default="BTCUSDT")
    parser.add_argument("--timeframe", default="1h")
    parser.add_argument("--days",      type=int, default=90)
    parser.add_argument("--capital",   type=float, default=100_000.0)
    parser.add_argument("--commission",type=float, default=0.075)
    parser.add_argument("--slippage",  type=float, default=0.05)
    parser.add_argument("--position-size", type=float, default=2.0, dest="position_size")

    args = parser.parse_args()
    asyncio.run(main(args))
