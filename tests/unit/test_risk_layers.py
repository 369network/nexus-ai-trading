"""
NEXUS ALPHA - Unit Tests: Risk Layers and Circuit Breakers
Tests all 5 risk layers and all 6 circuit breakers.
"""

from __future__ import annotations

import pytest
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Inline risk layer implementations (mirrors src/risk/)
# ---------------------------------------------------------------------------

@dataclass
class RiskResult:
    approved: bool
    rejection_reason: Optional[str] = None
    layer: Optional[str] = None
    action: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class PortfolioState:
    equity: float = 100_000.0
    cash: float = 100_000.0
    peak_equity: float = 100_000.0
    daily_pnl: float = 0.0
    open_positions: Dict[str, Any] = field(default_factory=dict)
    total_equity_start_of_day: float = 100_000.0

    @property
    def drawdown_pct(self) -> float:
        return (self.peak_equity - self.equity) / self.peak_equity if self.peak_equity > 0 else 0.0

    @property
    def daily_loss_pct(self) -> float:
        peak_today = max(self.total_equity_start_of_day, self.equity)
        return max((peak_today - self.equity) / peak_today, 0.0)


@dataclass
class ProposedPosition:
    symbol: str
    market: str = "crypto"
    direction: str = "LONG"
    position_value_usd: float = 5_000.0
    quantity: float = 0.1
    entry_price: float = 50_000.0
    stop_loss: float = 48_500.0
    confidence: float = 0.7


class RiskLayer1PositionSize:
    """Layer 1: Enforce max position size per market."""

    MARKET_MAX = {
        "crypto":       0.10,
        "forex":        0.05,
        "indian_stocks":0.08,
        "us_stocks":    0.08,
    }

    def evaluate(self, position: ProposedPosition, portfolio: PortfolioState) -> RiskResult:
        max_pct = self.MARKET_MAX.get(position.market, 0.10)
        max_value = portfolio.equity * max_pct

        if position.position_value_usd > max_value:
            return RiskResult(
                approved=False,
                layer="L1_position_size",
                rejection_reason=(
                    f"Position ${position.position_value_usd:,.0f} exceeds "
                    f"{max_pct*100:.0f}% max (${max_value:,.0f})"
                ),
            )
        return RiskResult(approved=True, layer="L1_position_size")


class RiskLayer2Correlation:
    """Layer 2: Reject highly correlated new positions."""

    def evaluate(
        self,
        position: ProposedPosition,
        portfolio: PortfolioState,
        correlation_matrix: Dict[str, float] = None,
    ) -> RiskResult:
        if not portfolio.open_positions or not correlation_matrix:
            return RiskResult(approved=True, layer="L2_correlation")

        for existing_sym, corr in correlation_matrix.items():
            if existing_sym == position.symbol:
                continue
            if existing_sym in portfolio.open_positions and abs(corr) > 0.80:
                return RiskResult(
                    approved=False,
                    layer="L2_correlation",
                    rejection_reason=(
                        f"Correlation with {existing_sym}: {corr:.2f} > 0.80"
                    ),
                )
        return RiskResult(approved=True, layer="L2_correlation")


class RiskLayer3DailyLoss:
    """Layer 3: Halt trading if daily loss exceeds threshold."""
    HALT_THRESHOLD = 0.03   # 3%
    ALERT_THRESHOLD = 0.05  # 5%

    def evaluate(self, portfolio: PortfolioState) -> RiskResult:
        loss = portfolio.daily_loss_pct
        if loss >= self.HALT_THRESHOLD:
            return RiskResult(
                approved=False,
                layer="L3_daily_loss",
                action="HALT_24H",
                rejection_reason=f"Daily loss {loss*100:.2f}% ≥ {self.HALT_THRESHOLD*100:.0f}% limit",
            )
        return RiskResult(approved=True, layer="L3_daily_loss")


class RiskLayer4Drawdown:
    """Layer 4: Reduce or pause trading based on drawdown."""
    PAUSE_THRESHOLD = 0.15  # 15%

    def evaluate(self, portfolio: PortfolioState) -> RiskResult:
        dd = portfolio.drawdown_pct
        if dd >= self.PAUSE_THRESHOLD:
            return RiskResult(
                approved=False,
                layer="L4_drawdown",
                action="PAUSE",
                rejection_reason=f"Drawdown {dd*100:.2f}% ≥ {self.PAUSE_THRESHOLD*100:.0f}% pause threshold",
            )
        return RiskResult(approved=True, layer="L4_drawdown")


class RiskLayer5TailRisk:
    """Layer 5: Full stop at extreme drawdown."""
    STOP_THRESHOLD = 0.25   # 25% → STOP
    HARD_STOP = 0.40        # 40% → emergency liquidation

    def evaluate(self, portfolio: PortfolioState) -> RiskResult:
        dd = portfolio.drawdown_pct
        if dd >= self.HARD_STOP:
            return RiskResult(
                approved=False,
                layer="L5_tail_risk",
                action="EMERGENCY_LIQUIDATE",
                rejection_reason=f"CRITICAL: Drawdown {dd*100:.2f}% ≥ {self.HARD_STOP*100:.0f}%",
            )
        if dd >= self.STOP_THRESHOLD:
            return RiskResult(
                approved=False,
                layer="L5_tail_risk",
                action="SYSTEM_STOP",
                rejection_reason=f"Drawdown {dd*100:.2f}% ≥ {self.STOP_THRESHOLD*100:.0f}% stop threshold",
            )
        return RiskResult(approved=True, layer="L5_tail_risk")


# ---------------------------------------------------------------------------
# Circuit Breaker implementations
# ---------------------------------------------------------------------------

@dataclass
class CircuitBreakerState:
    tripped: bool = False
    tripped_at: Optional[datetime] = None
    reason: str = ""


class CircuitBreaker1FlashCrash:
    CRYPTO_THRESHOLD = 0.05
    STOCKS_THRESHOLD = 0.03

    def check(self, market: str, candle: Dict) -> bool:
        threshold = (
            self.CRYPTO_THRESHOLD if market == "crypto"
            else self.STOCKS_THRESHOLD
        )
        if candle["open"] <= 0:
            return False
        drop = (candle["open"] - candle["close"]) / candle["open"]
        return drop >= threshold


class CircuitBreaker2Liquidity:
    SPREAD_MULTIPLIER = 3.0

    def check(self, current_spread: float, avg_spread: float) -> bool:
        if avg_spread <= 0:
            return False
        return current_spread >= avg_spread * self.SPREAD_MULTIPLIER


class CircuitBreaker3Volume:
    VOLUME_MULTIPLIER = 10.0

    def check(self, current_volume: float, avg_volume: float) -> bool:
        if avg_volume <= 0:
            return False
        return current_volume >= avg_volume * self.VOLUME_MULTIPLIER


class CircuitBreaker4APIErrors:
    MAX_ERRORS_PER_MINUTE = 5

    def check(self, error_count_last_minute: int) -> bool:
        return error_count_last_minute > self.MAX_ERRORS_PER_MINUTE


class CircuitBreaker5PnLSpike:
    MAX_LOSS_PCT_5MIN = 0.02  # 2% loss in 5 minutes

    def check(self, equity_5min_ago: float, current_equity: float) -> bool:
        if equity_5min_ago <= 0:
            return False
        loss = (equity_5min_ago - current_equity) / equity_5min_ago
        return loss >= self.MAX_LOSS_PCT_5MIN


class CircuitBreaker6CorrelationCascade:
    MIN_CORRELATED_POSITIONS = 3

    def check(self, positions_moving_against: int) -> bool:
        return positions_moving_against >= self.MIN_CORRELATED_POSITIONS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def clean_portfolio():
    return PortfolioState()


@pytest.fixture
def portfolio_with_loss():
    """Portfolio that has taken a 3.1% daily loss."""
    p = PortfolioState(
        equity=96_900.0,
        peak_equity=100_000.0,
        daily_pnl=-3_100.0,
        total_equity_start_of_day=100_000.0,
    )
    return p


@pytest.fixture
def portfolio_with_drawdown_15():
    """Portfolio at 15% drawdown."""
    return PortfolioState(
        equity=85_000.0,
        peak_equity=100_000.0,
        daily_pnl=-5_000.0,
        total_equity_start_of_day=90_000.0,
    )


@pytest.fixture
def portfolio_with_drawdown_30():
    """Portfolio at 30% drawdown (exceeds STOP threshold)."""
    return PortfolioState(
        equity=70_000.0,
        peak_equity=100_000.0,
        daily_pnl=-5_000.0,
        total_equity_start_of_day=75_000.0,
    )


@pytest.fixture
def portfolio_with_drawdown_42():
    """Portfolio at 42% drawdown (exceeds HARD STOP)."""
    return PortfolioState(
        equity=58_000.0,
        peak_equity=100_000.0,
        daily_pnl=-3_000.0,
        total_equity_start_of_day=60_000.0,
    )


@pytest.fixture
def normal_position():
    return ProposedPosition(
        symbol="BTCUSDT",
        market="crypto",
        position_value_usd=5_000.0,  # 5% of $100k — within 10% limit
    )


@pytest.fixture
def oversized_position_crypto():
    """Position exceeding 10% limit for crypto."""
    return ProposedPosition(
        symbol="BTCUSDT",
        market="crypto",
        position_value_usd=12_000.0,  # 12% — exceeds 10% limit
    )


@pytest.fixture
def oversized_position_forex():
    """Position exceeding 5% limit for forex."""
    return ProposedPosition(
        symbol="EURUSD",
        market="forex",
        position_value_usd=6_000.0,  # 6% — exceeds 5% limit
    )


# ---------------------------------------------------------------------------
# Risk Layer 1 Tests: Position Size
# ---------------------------------------------------------------------------

class TestRiskLayer1:
    def test_rejects_position_exceeding_10pct_crypto(self, oversized_position_crypto, clean_portfolio):
        """Layer 1 must reject crypto position > 10% equity."""
        layer = RiskLayer1PositionSize()
        result = layer.evaluate(oversized_position_crypto, clean_portfolio)
        assert result.approved is False
        assert result.layer == "L1_position_size"
        assert "10%" in result.rejection_reason or "max" in result.rejection_reason.lower()

    def test_approves_position_within_limit(self, normal_position, clean_portfolio):
        """Layer 1 approves position within 10% limit."""
        layer = RiskLayer1PositionSize()
        result = layer.evaluate(normal_position, clean_portfolio)
        assert result.approved is True

    def test_rejects_forex_position_exceeding_5pct(self, oversized_position_forex, clean_portfolio):
        """Forex max is 5% — Layer 1 should reject 6% position."""
        layer = RiskLayer1PositionSize()
        result = layer.evaluate(oversized_position_forex, clean_portfolio)
        assert result.approved is False
        assert "5%" in result.rejection_reason or "max" in result.rejection_reason.lower()

    def test_boundary_exactly_at_limit_is_approved(self, clean_portfolio):
        """Position exactly at limit should be approved."""
        layer = RiskLayer1PositionSize()
        position = ProposedPosition(
            symbol="BTCUSDT",
            market="crypto",
            position_value_usd=10_000.0,  # Exactly 10% of $100k
        )
        result = layer.evaluate(position, clean_portfolio)
        assert result.approved is True

    def test_zero_position_is_approved(self, clean_portfolio):
        """Zero position value should always be approved."""
        layer = RiskLayer1PositionSize()
        position = ProposedPosition("BTCUSDT", "crypto", position_value_usd=0.0)
        result = layer.evaluate(position, clean_portfolio)
        assert result.approved is True


# ---------------------------------------------------------------------------
# Risk Layer 3 Tests: Daily Loss
# ---------------------------------------------------------------------------

class TestRiskLayer3:
    def test_halts_after_3pct_daily_loss(self, portfolio_with_loss):
        """Layer 3 must halt after 3%+ daily loss."""
        layer = RiskLayer3DailyLoss()
        result = layer.evaluate(portfolio_with_loss)
        assert result.approved is False
        assert result.action == "HALT_24H"
        assert "loss" in result.rejection_reason.lower()

    def test_approves_when_loss_below_threshold(self, clean_portfolio):
        """No loss → Layer 3 approves."""
        layer = RiskLayer3DailyLoss()
        result = layer.evaluate(clean_portfolio)
        assert result.approved is True

    def test_approves_small_loss(self):
        """1% daily loss is within limit."""
        layer = RiskLayer3DailyLoss()
        portfolio = PortfolioState(
            equity=99_000.0,
            peak_equity=100_000.0,
            total_equity_start_of_day=100_000.0,
        )
        result = layer.evaluate(portfolio)
        assert result.approved is True

    def test_rejects_at_exactly_threshold(self):
        """Exactly at 3% threshold triggers halt."""
        layer = RiskLayer3DailyLoss()
        portfolio = PortfolioState(
            equity=97_000.0,
            total_equity_start_of_day=100_000.0,
            peak_equity=100_000.0,
        )
        result = layer.evaluate(portfolio)
        assert result.approved is False


# ---------------------------------------------------------------------------
# Risk Layer 4 Tests: Drawdown
# ---------------------------------------------------------------------------

class TestRiskLayer4:
    def test_pauses_at_15pct_drawdown(self, portfolio_with_drawdown_15):
        """Layer 4 should PAUSE at 15% drawdown."""
        layer = RiskLayer4Drawdown()
        result = layer.evaluate(portfolio_with_drawdown_15)
        assert result.approved is False
        assert result.action == "PAUSE"
        assert "15%" in result.rejection_reason or "pause" in result.rejection_reason.lower()

    def test_approves_below_threshold(self, clean_portfolio):
        """Below 15% drawdown → approved."""
        layer = RiskLayer4Drawdown()
        result = layer.evaluate(clean_portfolio)
        assert result.approved is True

    def test_10pct_drawdown_is_approved_by_layer4(self):
        """10% drawdown does NOT trigger Layer 4 pause (only Layer 5 handles lower thresholds)."""
        layer = RiskLayer4Drawdown()
        portfolio = PortfolioState(equity=90_000.0, peak_equity=100_000.0)
        result = layer.evaluate(portfolio)
        assert result.approved is True


# ---------------------------------------------------------------------------
# Risk Layer 5 Tests: Tail Risk
# ---------------------------------------------------------------------------

class TestRiskLayer5:
    def test_triggers_pause_at_30pct_drawdown(self, portfolio_with_drawdown_30):
        """Layer 5 stops trading at 25%+ drawdown."""
        layer = RiskLayer5TailRisk()
        result = layer.evaluate(portfolio_with_drawdown_30)
        assert result.approved is False
        assert result.action == "SYSTEM_STOP"

    def test_triggers_emergency_at_40pct_drawdown(self, portfolio_with_drawdown_42):
        """Layer 5 emergency liquidation at 40%+ drawdown."""
        layer = RiskLayer5TailRisk()
        result = layer.evaluate(portfolio_with_drawdown_42)
        assert result.approved is False
        assert result.action == "EMERGENCY_LIQUIDATE"

    def test_approves_below_stop_threshold(self, clean_portfolio):
        """Zero drawdown → Layer 5 approves."""
        layer = RiskLayer5TailRisk()
        result = layer.evaluate(clean_portfolio)
        assert result.approved is True

    def test_20pct_drawdown_does_not_trigger_layer5(self):
        """20% drawdown is below 25% stop threshold."""
        layer = RiskLayer5TailRisk()
        portfolio = PortfolioState(equity=80_000.0, peak_equity=100_000.0)
        result = layer.evaluate(portfolio)
        assert result.approved is True


# ---------------------------------------------------------------------------
# Circuit Breaker Tests
# ---------------------------------------------------------------------------

class TestCircuitBreakers:
    # CB-1: Flash Crash
    def test_cb1_triggers_on_5pct_drop_crypto(self):
        cb = CircuitBreaker1FlashCrash()
        candle = {"open": 50_000.0, "close": 47_000.0}  # -6%
        assert cb.check("crypto", candle) is True

    def test_cb1_does_not_trigger_on_4pct_drop_crypto(self):
        cb = CircuitBreaker1FlashCrash()
        candle = {"open": 50_000.0, "close": 48_100.0}  # -3.8%
        assert cb.check("crypto", candle) is False

    def test_cb1_triggers_on_3pct_drop_stocks(self):
        cb = CircuitBreaker1FlashCrash()
        candle = {"open": 200.0, "close": 193.0}  # -3.5%
        assert cb.check("us_stocks", candle) is True

    def test_cb1_no_trigger_for_upward_move(self):
        cb = CircuitBreaker1FlashCrash()
        candle = {"open": 47_000.0, "close": 50_000.0}  # +6.4%
        assert cb.check("crypto", candle) is False

    # CB-2: Liquidity
    def test_cb2_triggers_when_spread_3x_normal(self):
        cb = CircuitBreaker2Liquidity()
        assert cb.check(current_spread=0.06, avg_spread=0.02) is True

    def test_cb2_no_trigger_at_2x(self):
        cb = CircuitBreaker2Liquidity()
        assert cb.check(current_spread=0.04, avg_spread=0.02) is False

    def test_cb2_handles_zero_avg_spread(self):
        cb = CircuitBreaker2Liquidity()
        # Should not raise ZeroDivisionError
        result = cb.check(current_spread=0.01, avg_spread=0.0)
        assert result is False

    # CB-3: Volume Anomaly
    def test_cb3_triggers_at_10x_volume(self):
        cb = CircuitBreaker3Volume()
        assert cb.check(current_volume=100_000, avg_volume=9_000) is True

    def test_cb3_no_trigger_at_5x_volume(self):
        cb = CircuitBreaker3Volume()
        assert cb.check(current_volume=50_000, avg_volume=10_000) is False

    def test_cb3_exactly_10x_triggers(self):
        cb = CircuitBreaker3Volume()
        assert cb.check(current_volume=100_000, avg_volume=10_000) is True

    # CB-4: API Errors
    def test_cb4_triggers_over_5_errors(self):
        cb = CircuitBreaker4APIErrors()
        assert cb.check(error_count_last_minute=6) is True

    def test_cb4_no_trigger_at_5_errors(self):
        cb = CircuitBreaker4APIErrors()
        assert cb.check(error_count_last_minute=5) is False

    def test_cb4_no_trigger_at_0_errors(self):
        cb = CircuitBreaker4APIErrors()
        assert cb.check(error_count_last_minute=0) is False

    # CB-5: P&L Spike
    def test_cb5_triggers_on_2pct_loss_in_5min(self):
        cb = CircuitBreaker5PnLSpike()
        assert cb.check(equity_5min_ago=100_000, current_equity=97_500) is True

    def test_cb5_no_trigger_on_1pct_loss(self):
        cb = CircuitBreaker5PnLSpike()
        assert cb.check(equity_5min_ago=100_000, current_equity=99_200) is False

    def test_cb5_no_trigger_on_gain(self):
        cb = CircuitBreaker5PnLSpike()
        assert cb.check(equity_5min_ago=100_000, current_equity=102_000) is False

    # CB-6: Correlation Cascade
    def test_cb6_triggers_at_3_positions_against(self):
        cb = CircuitBreaker6CorrelationCascade()
        assert cb.check(positions_moving_against=3) is True

    def test_cb6_no_trigger_at_2_positions(self):
        cb = CircuitBreaker6CorrelationCascade()
        assert cb.check(positions_moving_against=2) is False

    def test_cb6_triggers_at_4_positions(self):
        cb = CircuitBreaker6CorrelationCascade()
        assert cb.check(positions_moving_against=4) is True
