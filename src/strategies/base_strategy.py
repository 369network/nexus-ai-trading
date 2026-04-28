"""
Base Strategy Abstract Class for NEXUS ALPHA Trading System.

All trading strategies inherit from this class. It enforces a consistent
interface for signal generation, entry/exit logic, parameter management,
and backtest metric reporting used by Dream Mode for ranking.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)


class SignalDirection(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"


class SignalStrength(str, Enum):
    STRONG = "STRONG"
    MODERATE = "MODERATE"
    SLIGHT = "SLIGHT"


@dataclass
class TradeSignal:
    """Represents a trading signal emitted by a strategy."""

    strategy_name: str
    market: str
    symbol: str
    direction: SignalDirection
    strength: SignalStrength
    entry_price: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    take_profit_3: float
    size_pct: float  # 0.0–1.0 fraction of risk budget
    timeframe: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    confidence: float = 0.5  # 0.0–1.0
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def risk_reward(self) -> float:
        """Return risk/reward ratio to TP1."""
        risk = abs(self.entry_price - self.stop_loss)
        reward = abs(self.take_profit_1 - self.entry_price)
        if risk == 0:
            return 0.0
        return reward / risk

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy_name": self.strategy_name,
            "market": self.market,
            "symbol": self.symbol,
            "direction": self.direction.value,
            "strength": self.strength.value,
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "take_profit_3": self.take_profit_3,
            "size_pct": self.size_pct,
            "timeframe": self.timeframe,
            "timestamp": self.timestamp.isoformat(),
            "confidence": self.confidence,
            "risk_reward": self.risk_reward,
            "metadata": self.metadata,
        }


@dataclass
class BacktestMetric:
    """Summary statistics returned by a strategy for Dream Mode ranking."""

    total_trades: int
    win_rate: float          # 0.0–1.0
    sharpe_ratio: float
    sortino_ratio: float
    max_drawdown: float      # 0.0–1.0 (fraction)
    profit_factor: float
    avg_rr: float            # average risk/reward
    expectancy: float        # avg $ profit per trade (normalised)
    calmar_ratio: float
    period_days: int

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_trades": self.total_trades,
            "win_rate": self.win_rate,
            "sharpe_ratio": self.sharpe_ratio,
            "sortino_ratio": self.sortino_ratio,
            "max_drawdown": self.max_drawdown,
            "profit_factor": self.profit_factor,
            "avg_rr": self.avg_rr,
            "expectancy": self.expectancy,
            "calmar_ratio": self.calmar_ratio,
            "period_days": self.period_days,
        }


class BaseStrategy(ABC):
    """
    Abstract base class for all NEXUS ALPHA trading strategies.

    Subclasses must implement:
        - generate_signal
        - check_entry_conditions
        - check_exit_conditions
        - validate_params
        - backtest_metric

    Parameters
    ----------
    name : str
        Unique human-readable strategy identifier.
    market : str
        Market category: 'crypto', 'forex', 'commodities', 'indian', 'us'.
    primary_timeframe : str
        Main chart timeframe used for signal generation (e.g. '4h', '1h').
    confirmation_timeframe : str
        Higher timeframe used to confirm trend direction (e.g. '1d', '1w').
    params : dict
        Tunable parameters — Dream Mode adjusts these during optimisation.
    """

    def __init__(
        self,
        name: str,
        market: str,
        primary_timeframe: str,
        confirmation_timeframe: str,
        params: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.name = name
        self.market = market
        self.primary_timeframe = primary_timeframe
        self.confirmation_timeframe = confirmation_timeframe
        self.params: Dict[str, Any] = params or {}

        # Internal state
        self._trade_history: List[Dict[str, Any]] = []
        self._signal_history: List[TradeSignal] = []
        self._is_enabled: bool = True
        self._last_signal: Optional[TradeSignal] = None
        self._created_at: datetime = datetime.utcnow()

        # Validate params on construction
        if self.params and not self.validate_params(self.params):
            raise ValueError(
                f"Strategy '{self.name}' received invalid params: {self.params}"
            )

        logger.info(
            "Strategy initialised: %s | market=%s | tf=%s/%s",
            self.name, self.market, self.primary_timeframe, self.confirmation_timeframe,
        )

    # ------------------------------------------------------------------
    # Abstract interface — must be implemented by subclasses
    # ------------------------------------------------------------------

    @abstractmethod
    def generate_signal(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> Optional[TradeSignal]:
        """
        Evaluate market_data and context; produce a TradeSignal or None.

        Parameters
        ----------
        market_data : dict
            Must include at minimum:
                - 'ohlcv': pd.DataFrame with columns [open, high, low, close, volume]
                - 'symbol': str
                - 'indicators': dict of pre-computed indicator values
                - 'sentiment': dict (fear_greed, news_score, llm_consensus)
                - 'regime': str ('trending_up', 'trending_down', 'ranging',
                                 'high_volatility')
        context : dict
            System-level context:
                - 'portfolio': current portfolio state
                - 'risk_budget': remaining risk budget (0.0–1.0)
                - 'active_positions': list of open positions
                - 'market_session': current trading session

        Returns
        -------
        TradeSignal or None
            None means no actionable signal.
        """

    @abstractmethod
    def check_entry_conditions(self, market_data: Dict[str, Any]) -> bool:
        """
        Return True only when ALL entry conditions are satisfied.

        This is called by generate_signal after high-level filtering.
        Implementations should log which specific condition failed to
        aid in debugging and back-testing attribution.
        """

    @abstractmethod
    def check_exit_conditions(
        self, position: Dict[str, Any], market_data: Dict[str, Any]
    ) -> Optional[str]:
        """
        Inspect an open position and current market data.

        Returns
        -------
        str or None
            Human-readable exit reason if the position should be closed,
            e.g. 'stop_loss_hit', 'take_profit_1', 'time_exit_72h'.
            None means keep the position open.
        """

    @abstractmethod
    def validate_params(self, params: Dict[str, Any]) -> bool:
        """
        Validate a parameter dictionary.

        Called on construction and before every Dream Mode update.
        Should check types, ranges, and cross-parameter constraints.

        Returns
        -------
        bool
            True if all params are valid; False otherwise.
        """

    @abstractmethod
    def backtest_metric(self) -> Dict[str, Any]:
        """
        Compute and return summary statistics from internal trade history.

        Used by Dream Mode to rank parameter sets.  Must always return
        a dict compatible with BacktestMetric.to_dict().
        """

    # ------------------------------------------------------------------
    # Concrete helpers — shared by all strategies
    # ------------------------------------------------------------------

    def update_params(self, new_params: Dict[str, Any]) -> None:
        """
        Apply Dream Mode parameter updates after validation.

        Parameters
        ----------
        new_params : dict
            New parameter values to merge into self.params.

        Raises
        ------
        ValueError
            If new_params fail validation.
        """
        if not self.validate_params(new_params):
            raise ValueError(
                f"Strategy '{self.name}': invalid params rejected: {new_params}"
            )
        old_params = dict(self.params)
        self.params.update(new_params)
        logger.info(
            "Strategy '%s' params updated: %s -> %s",
            self.name, old_params, self.params,
        )

    def record_trade(self, trade: Dict[str, Any]) -> None:
        """Append a completed trade to internal history for metric calculation."""
        trade.setdefault("recorded_at", datetime.utcnow().isoformat())
        self._trade_history.append(trade)

    def record_signal(self, signal: TradeSignal) -> None:
        """Track emitted signals (useful for signal quality analysis)."""
        self._signal_history.append(signal)
        self._last_signal = signal

    def enable(self) -> None:
        """Enable the strategy (default state)."""
        self._is_enabled = True
        logger.info("Strategy '%s' enabled.", self.name)

    def disable(self) -> None:
        """Disable the strategy; generate_signal will return None immediately."""
        self._is_enabled = False
        logger.info("Strategy '%s' disabled.", self.name)

    @property
    def is_enabled(self) -> bool:
        return self._is_enabled

    def get_param(self, key: str, default: Any = None) -> Any:
        """Safely retrieve a parameter value."""
        return self.params.get(key, default)

    # ------------------------------------------------------------------
    # MarketOrchestrator adapter interface
    # ------------------------------------------------------------------

    def is_applicable(
        self,
        symbol: str,
        timeframe: str,
        regime: Any = None,
    ) -> bool:
        """
        Return True if this strategy should be evaluated for the given
        symbol/timeframe/regime combination.

        Default: True when the strategy is enabled and the timeframe matches
        the strategy's primary_timeframe. Subclasses may override for
        more granular filtering.
        """
        if not self._is_enabled:
            return False
        return timeframe == self.primary_timeframe

    async def evaluate(
        self,
        symbol: str,
        timeframe: str,
        candle: Dict[str, Any],
        candle_history: List[Dict[str, Any]],
        indicators: Dict[str, Any],
        regime: Any = None,
    ) -> Optional[TradeSignal]:
        """
        Async adapter called by MarketOrchestrator.check_strategies.

        Bridges the orchestrator's per-candle interface to the strategy's
        generate_signal(market_data, context) interface.
        """
        # Strategies expect ohlcv as dict-of-lists ({"close": [...], ...})
        # so they can do ohlcv["close"] without iterating by integer index.
        if candle_history and isinstance(candle_history[0], dict):
            keys = ("timestamp", "open", "high", "low", "close", "volume")
            ohlcv: Any = {k: [c.get(k, 0) for c in candle_history] for k in keys}
        else:
            ohlcv = candle_history  # already in expected format

        # Provide paper-mode sentiment defaults so that sentiment-gated
        # strategy conditions (LLM consensus, Fear & Greed) don't silently
        # block all signals when live sentiment feeds aren't connected.
        # Real feeds (news, social, on-chain) will override these when wired in.
        paper_sentiment: Dict[str, Any] = {
            "fear_greed":    indicators.get("fear_greed", 50),
            "llm_consensus": "SLIGHT_BUY",   # neutral-bullish default for paper
            "funding_rate":  indicators.get("funding_rate", 0.01),
        }

        market_data: Dict[str, Any] = {
            "symbol":      symbol,
            "timeframe":   timeframe,
            "candle":      candle,
            "ohlcv":       ohlcv,
            "indicators":  indicators,
            "sentiment":   paper_sentiment,
        }
        context: Dict[str, Any] = {
            "regime":     regime,
            "symbol":     symbol,
            "paper_mode": True,
        }
        try:
            return self.generate_signal(market_data, context)
        except Exception as exc:
            logger.debug(
                "Strategy '%s' generate_signal error for %s/%s: %s",
                self.name, symbol, timeframe, exc,
            )
            return None

    # ------------------------------------------------------------------
    # Shared technical indicator helpers (keep strategies DRY)
    # ------------------------------------------------------------------

    @staticmethod
    def compute_atr(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray, period: int = 14) -> np.ndarray:
        """Average True Range."""
        n = len(closes)
        tr = np.zeros(n)
        tr[0] = highs[0] - lows[0]
        for i in range(1, n):
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        atr = np.zeros(n)
        atr[period - 1] = np.mean(tr[:period])
        for i in range(period, n):
            atr[i] = (atr[i - 1] * (period - 1) + tr[i]) / period
        return atr

    @staticmethod
    def compute_rsi(closes: np.ndarray, period: int = 14) -> np.ndarray:
        """Relative Strength Index."""
        n = len(closes)
        rsi = np.full(n, np.nan)
        if n < period + 1:
            return rsi
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = np.mean(gains[:period])
        avg_loss = np.mean(losses[:period])
        for i in range(period, n - 1):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
            if avg_loss == 0:
                rsi[i + 1] = 100.0
            else:
                rs = avg_gain / avg_loss
                rsi[i + 1] = 100.0 - (100.0 / (1.0 + rs))
        return rsi

    @staticmethod
    def compute_ema(closes: np.ndarray, period: int) -> np.ndarray:
        """Exponential Moving Average."""
        ema = np.full(len(closes), np.nan)
        if len(closes) < period:
            return ema
        k = 2.0 / (period + 1)
        ema[period - 1] = np.mean(closes[:period])
        for i in range(period, len(closes)):
            ema[i] = closes[i] * k + ema[i - 1] * (1 - k)
        return ema

    @staticmethod
    def compute_sma(closes: np.ndarray, period: int) -> np.ndarray:
        """Simple Moving Average."""
        sma = np.full(len(closes), np.nan)
        for i in range(period - 1, len(closes)):
            sma[i] = np.mean(closes[i - period + 1 : i + 1])
        return sma

    @staticmethod
    def compute_bollinger_bands(
        closes: np.ndarray, period: int = 20, std_dev: float = 2.0
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Bollinger Bands — returns (upper, mid, lower)."""
        mid = BaseStrategy.compute_sma(closes, period)
        upper = np.full(len(closes), np.nan)
        lower = np.full(len(closes), np.nan)
        for i in range(period - 1, len(closes)):
            std = np.std(closes[i - period + 1 : i + 1], ddof=1)
            upper[i] = mid[i] + std_dev * std
            lower[i] = mid[i] - std_dev * std
        return upper, mid, lower

    @staticmethod
    def compute_macd(
        closes: np.ndarray, fast: int = 12, slow: int = 26, signal: int = 9
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """MACD — returns (macd_line, signal_line, histogram)."""
        ema_fast = BaseStrategy.compute_ema(closes, fast)
        ema_slow = BaseStrategy.compute_ema(closes, slow)
        macd_line = ema_fast - ema_slow
        signal_line = BaseStrategy.compute_ema(
            np.where(np.isnan(macd_line), 0, macd_line), signal
        )
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def compute_donchian(
        highs: np.ndarray, lows: np.ndarray, period: int = 20
    ) -> tuple[np.ndarray, np.ndarray]:
        """Donchian Channel — returns (upper, lower)."""
        n = len(highs)
        upper = np.full(n, np.nan)
        lower = np.full(n, np.nan)
        for i in range(period - 1, n):
            upper[i] = np.max(highs[i - period + 1 : i + 1])
            lower[i] = np.min(lows[i - period + 1 : i + 1])
        return upper, lower

    @staticmethod
    def compute_adx(
        highs: np.ndarray,
        lows: np.ndarray,
        closes: np.ndarray,
        period: int = 14,
    ) -> np.ndarray:
        """Average Directional Index (simplified Wilder smoothing)."""
        n = len(closes)
        adx = np.full(n, np.nan)
        if n < period * 2:
            return adx
        plus_dm = np.zeros(n)
        minus_dm = np.zeros(n)
        tr = np.zeros(n)
        for i in range(1, n):
            up_move = highs[i] - highs[i - 1]
            down_move = lows[i - 1] - lows[i]
            plus_dm[i] = up_move if up_move > down_move and up_move > 0 else 0
            minus_dm[i] = down_move if down_move > up_move and down_move > 0 else 0
            tr[i] = max(
                highs[i] - lows[i],
                abs(highs[i] - closes[i - 1]),
                abs(lows[i] - closes[i - 1]),
            )
        atr_s = np.zeros(n)
        plus_di_s = np.zeros(n)
        minus_di_s = np.zeros(n)
        atr_s[period] = np.sum(tr[1 : period + 1])
        plus_di_s[period] = np.sum(plus_dm[1 : period + 1])
        minus_di_s[period] = np.sum(minus_dm[1 : period + 1])
        for i in range(period + 1, n):
            atr_s[i] = atr_s[i - 1] - atr_s[i - 1] / period + tr[i]
            plus_di_s[i] = plus_di_s[i - 1] - plus_di_s[i - 1] / period + plus_dm[i]
            minus_di_s[i] = minus_di_s[i - 1] - minus_di_s[i - 1] / period + minus_dm[i]
        safe_atr = np.where(atr_s > 0, atr_s, 1.0)
        plus_di = np.where(atr_s > 0, 100 * plus_di_s / safe_atr, 0)
        minus_di = np.where(atr_s > 0, 100 * minus_di_s / safe_atr, 0)
        di_sum = plus_di + minus_di
        safe_di_sum = np.where(di_sum > 0, di_sum, 1.0)
        dx = np.where(
            di_sum > 0,
            100 * np.abs(plus_di - minus_di) / safe_di_sum,
            0,
        )
        adx[period * 2 - 1] = np.mean(dx[period : period * 2])
        for i in range(period * 2, n):
            adx[i] = (adx[i - 1] * (period - 1) + dx[i]) / period
        return adx

    # ------------------------------------------------------------------
    # Shared statistics helpers for backtest_metric implementations
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_sharpe(returns: List[float], risk_free: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        arr = np.array(returns)
        excess = arr - risk_free
        std = np.std(excess, ddof=1)
        if std == 0:
            return 0.0
        return float(np.mean(excess) / std * np.sqrt(252))

    @staticmethod
    def _compute_sortino(returns: List[float], risk_free: float = 0.0) -> float:
        if len(returns) < 2:
            return 0.0
        arr = np.array(returns)
        excess = arr - risk_free
        downside = excess[excess < 0]
        if len(downside) == 0:
            return float("inf")
        downside_std = np.std(downside, ddof=1)
        if downside_std == 0:
            return float("inf")
        return float(np.mean(excess) / downside_std * np.sqrt(252))

    @staticmethod
    def _compute_max_drawdown(equity_curve: List[float]) -> float:
        if len(equity_curve) < 2:
            return 0.0
        peak = equity_curve[0]
        max_dd = 0.0
        for v in equity_curve:
            if v > peak:
                peak = v
            dd = (peak - v) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    def _build_backtest_metric_from_history(self) -> BacktestMetric:
        """
        Build a BacktestMetric from self._trade_history.

        Each trade dict should contain:
            - 'pnl': float (profit/loss in R-multiples or absolute)
            - 'won': bool
            - 'rr': float (actual risk/reward)
        """
        trades = self._trade_history
        if not trades:
            return BacktestMetric(
                total_trades=0,
                win_rate=0.0,
                sharpe_ratio=0.0,
                sortino_ratio=0.0,
                max_drawdown=0.0,
                profit_factor=0.0,
                avg_rr=0.0,
                expectancy=0.0,
                calmar_ratio=0.0,
                period_days=0,
            )

        pnls = [t.get("pnl", 0.0) for t in trades]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls)
        profit_factor = sum(wins) / abs(sum(losses)) if losses else float("inf")
        avg_rr = float(np.mean([t.get("rr", 1.0) for t in trades]))
        expectancy = float(np.mean(pnls))

        # Build equity curve
        equity: List[float] = [1.0]
        for p in pnls:
            equity.append(equity[-1] + p * 0.01)  # assume 1% risk per trade

        sharpe = self._compute_sharpe(pnls)
        sortino = self._compute_sortino(pnls)
        max_dd = self._compute_max_drawdown(equity)
        calmar = (equity[-1] - 1.0) / max_dd if max_dd > 0 else 0.0

        # Period in days (use trade timestamps if available)
        try:
            dates = [t["recorded_at"] for t in trades if "recorded_at" in t]
            if len(dates) >= 2:
                from datetime import datetime as dt_
                d1 = dt_.fromisoformat(dates[0])
                d2 = dt_.fromisoformat(dates[-1])
                period_days = max(1, abs((d2 - d1).days))
            else:
                period_days = 30
        except Exception:
            period_days = 30

        return BacktestMetric(
            total_trades=len(trades),
            win_rate=win_rate,
            sharpe_ratio=sharpe,
            sortino_ratio=sortino,
            max_drawdown=max_dd,
            profit_factor=profit_factor,
            avg_rr=avg_rr,
            expectancy=expectancy,
            calmar_ratio=calmar,
            period_days=period_days,
        )

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} name={self.name!r} "
            f"market={self.market!r} enabled={self._is_enabled}>"
        )
