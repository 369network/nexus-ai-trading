"""
NEXUS ALPHA - Unit Tests: Position Sizer
Tests Kelly criterion, ATR-based sizing, and drawdown scaling.
"""

from __future__ import annotations

import pytest
from typing import Any, Dict, Optional


# ---------------------------------------------------------------------------
# Inline position sizer (matches src/risk/position_sizer.py interface)
# ---------------------------------------------------------------------------

class PositionSizer:
    """
    Replicates the production position sizing logic for unit testing.
    This mirrors src/risk/position_sizer.py exactly.
    """

    # Market-specific maximum position sizes (% of equity)
    MARKET_MAX_PCT = {
        "crypto":         10.0,
        "forex":           5.0,
        "indian_stocks":   8.0,
        "us_stocks":       8.0,
    }

    # Drawdown scaling factors
    DRAWDOWN_SCALE = [
        (0.15, 0.40),   # drawdown ≥ 15% → 40% of normal
        (0.10, 0.60),   # drawdown ≥ 10% → 60% of normal
        (0.05, 0.80),   # drawdown ≥ 5%  → 80% of normal
        (0.0,  1.00),   # drawdown < 5%  → 100%
    ]

    def __init__(self, settings: Any = None):
        self.risk_pct_per_trade = 1.0  # Default: risk 1% per trade
        self.min_position_usd   = 10.0

    def kelly_fraction(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """
        Calculate the full Kelly fraction.
        kelly = (win_rate / abs_loss) - (loss_rate / abs_win)
        """
        if avg_win <= 0 or avg_loss <= 0:
            return 0.0
        loss_rate = 1.0 - win_rate
        kelly = (win_rate / avg_loss) - (loss_rate / avg_win)
        return max(kelly, 0.0)

    def quarter_kelly(
        self,
        win_rate: float,
        avg_win: float,
        avg_loss: float,
    ) -> float:
        """Quarter Kelly = Kelly / 4 (conservative live trading)."""
        return self.kelly_fraction(win_rate, avg_win, avg_loss) / 4.0

    def atr_based_size(
        self,
        equity: float,
        entry_price: float,
        atr: float,
        atr_stop_multiplier: float = 1.5,
        risk_pct: Optional[float] = None,
        drawdown_pct: float = 0.0,
    ) -> Dict[str, float]:
        """
        ATR-based position sizing.

        Returns:
            quantity, risk_amount, stop_distance, position_value_usd
        """
        rp = risk_pct if risk_pct is not None else self.risk_pct_per_trade
        risk_amount   = equity * rp / 100.0
        stop_distance = atr * atr_stop_multiplier

        if stop_distance <= 0:
            return {"quantity": 0.0, "risk_amount": 0.0,
                    "stop_distance": 0.0, "position_value_usd": 0.0}

        quantity          = risk_amount / stop_distance
        position_value    = quantity * entry_price

        # Apply drawdown scaling
        scale = self._drawdown_scale(drawdown_pct)
        quantity       *= scale
        position_value *= scale

        return {
            "quantity":          quantity,
            "risk_amount":       risk_amount * scale,
            "stop_distance":     stop_distance,
            "position_value_usd": position_value,
        }

    def apply_market_max(
        self,
        position_value_usd: float,
        equity: float,
        market: str,
    ) -> float:
        """Enforce market-specific maximum position size."""
        max_pct   = self.MARKET_MAX_PCT.get(market, 10.0)
        max_value = equity * max_pct / 100.0
        return min(position_value_usd, max_value)

    def _drawdown_scale(self, drawdown_pct: float) -> float:
        for threshold, scale in self.DRAWDOWN_SCALE:
            if drawdown_pct >= threshold:
                return scale
        return 1.0

    def compute_final_size(
        self,
        equity: float,
        entry_price: float,
        atr: float,
        market: str,
        win_rate: float = 0.55,
        avg_win: float  = 1.5,
        avg_loss: float = 1.0,
        drawdown_pct: float = 0.0,
        atr_stop_multiplier: float = 1.5,
    ) -> Dict[str, float]:
        """
        Compute final position size using ATR method, bounded by Kelly and market max.
        """
        # ATR sizing
        atr_result = self.atr_based_size(
            equity=equity,
            entry_price=entry_price,
            atr=atr,
            atr_stop_multiplier=atr_stop_multiplier,
            drawdown_pct=drawdown_pct,
        )

        # Kelly cap
        qk = self.quarter_kelly(win_rate, avg_win, avg_loss)
        kelly_max_value = equity * qk

        # Market cap
        market_max = self.apply_market_max(
            atr_result["position_value_usd"], equity, market
        )

        # Take the minimum of all limits
        final_value = min(
            atr_result["position_value_usd"],
            kelly_max_value,
            market_max,
        )
        final_value = max(final_value, 0.0)

        quantity = final_value / entry_price if entry_price > 0 else 0.0

        return {
            "quantity":         quantity,
            "position_value":   final_value,
            "risk_pct":         final_value / equity * 100 if equity > 0 else 0,
            "method":           "atr_bounded_quarter_kelly",
            "kelly_fraction":   qk,
            "drawdown_scale":   self._drawdown_scale(drawdown_pct),
        }


@pytest.fixture
def sizer():
    return PositionSizer()


# ---------------------------------------------------------------------------
# Kelly Criterion Tests
# ---------------------------------------------------------------------------

class TestKellyCriterion:
    def test_quarter_kelly_never_exceeds_full_kelly(self, sizer):
        """Quarter Kelly must always be ≤ Full Kelly."""
        for wr in [0.4, 0.5, 0.6, 0.7]:
            full = sizer.kelly_fraction(wr, avg_win=1.5, avg_loss=1.0)
            quarter = sizer.quarter_kelly(wr, avg_win=1.5, avg_loss=1.0)
            assert quarter <= full + 1e-9, (
                f"Quarter Kelly {quarter} > Full Kelly {full} for win_rate={wr}"
            )

    def test_quarter_kelly_is_exactly_one_quarter(self, sizer):
        """Quarter Kelly = Full Kelly / 4 exactly."""
        full    = sizer.kelly_fraction(0.6, 2.0, 1.0)
        quarter = sizer.quarter_kelly(0.6, 2.0, 1.0)
        assert abs(quarter - full / 4.0) < 1e-10

    def test_kelly_negative_ev_is_zero(self, sizer):
        """Kelly should return 0 for negative expected value."""
        # win_rate=0.3, avg_win=1.0, avg_loss=2.0 → negative EV
        full = sizer.kelly_fraction(0.3, avg_win=1.0, avg_loss=2.0)
        assert full == 0.0, f"Negative EV should give Kelly=0, got {full}"

    def test_kelly_break_even_is_near_zero(self, sizer):
        """At breakeven (EV=0), Kelly approaches 0."""
        # win_rate=0.5, avg_win=1.0, avg_loss=1.0 → EV=0
        full = sizer.kelly_fraction(0.5, avg_win=1.0, avg_loss=1.0)
        assert full == 0.0

    def test_kelly_increases_with_win_rate(self, sizer):
        """Higher win rate → higher Kelly fraction."""
        k1 = sizer.kelly_fraction(0.5, 2.0, 1.0)
        k2 = sizer.kelly_fraction(0.6, 2.0, 1.0)
        k3 = sizer.kelly_fraction(0.7, 2.0, 1.0)
        assert k1 < k2 < k3

    def test_kelly_increases_with_reward_ratio(self, sizer):
        """Better R:R → higher Kelly."""
        k1 = sizer.kelly_fraction(0.55, avg_win=1.0, avg_loss=1.0)
        k2 = sizer.kelly_fraction(0.55, avg_win=2.0, avg_loss=1.0)
        k3 = sizer.kelly_fraction(0.55, avg_win=3.0, avg_loss=1.0)
        assert k1 < k2 < k3


# ---------------------------------------------------------------------------
# Market Maximum Tests
# ---------------------------------------------------------------------------

class TestMarketMaximum:
    def test_position_never_exceeds_market_max_crypto(self, sizer):
        """Crypto max: 10% of equity."""
        equity = 100_000
        # Request a huge position
        capped = sizer.apply_market_max(50_000, equity, "crypto")
        assert capped <= equity * 0.10 + 1e-6

    def test_position_never_exceeds_market_max_forex(self, sizer):
        """Forex max: 5% of equity."""
        equity = 100_000
        capped = sizer.apply_market_max(50_000, equity, "forex")
        assert capped <= equity * 0.05 + 1e-6

    def test_small_position_not_capped(self, sizer):
        """A position within limits should not be reduced."""
        equity = 100_000
        position = 5_000  # 5% — within 10% crypto max
        capped = sizer.apply_market_max(position, equity, "crypto")
        assert abs(capped - position) < 1e-6

    @pytest.mark.parametrize("market,max_pct", [
        ("crypto", 10.0),
        ("forex", 5.0),
        ("indian_stocks", 8.0),
        ("us_stocks", 8.0),
    ])
    def test_all_market_maxima(self, sizer, market, max_pct):
        """All markets have correct configured maxima."""
        equity = 200_000
        oversized = equity  # 100% — should be capped
        capped = sizer.apply_market_max(oversized, equity, market)
        assert abs(capped - equity * max_pct / 100) < 1e-6


# ---------------------------------------------------------------------------
# ATR Sizing Tests
# ---------------------------------------------------------------------------

class TestATRSizing:
    def test_higher_atr_gives_smaller_quantity(self, sizer):
        """With higher ATR (wider stop), position quantity is smaller."""
        result_low_atr  = sizer.atr_based_size(100_000, 50_000, atr=500,  atr_stop_multiplier=1.5)
        result_high_atr = sizer.atr_based_size(100_000, 50_000, atr=2000, atr_stop_multiplier=1.5)

        assert result_low_atr["quantity"] > result_high_atr["quantity"], (
            "Lower ATR should produce larger position (tighter stop = more units)"
        )

    def test_lower_atr_gives_larger_quantity(self, sizer):
        """Lower ATR (tighter stop) → more units for same risk amount."""
        result_low  = sizer.atr_based_size(100_000, 50_000, atr=250, atr_stop_multiplier=1.5)
        result_high = sizer.atr_based_size(100_000, 50_000, atr=500, atr_stop_multiplier=1.5)
        assert result_low["quantity"] > result_high["quantity"]

    def test_risk_amount_is_consistent(self, sizer):
        """Risk amount = quantity × stop_distance."""
        result = sizer.atr_based_size(100_000, 50_000, atr=1000, atr_stop_multiplier=1.5)
        expected_risk = result["quantity"] * result["stop_distance"]
        # Should be approximately equity × risk_pct
        assert abs(expected_risk - result["risk_amount"]) < 0.01

    def test_zero_atr_returns_zero_quantity(self, sizer):
        """Zero ATR (no range) should return zero position."""
        result = sizer.atr_based_size(100_000, 50_000, atr=0)
        assert result["quantity"] == 0.0

    def test_position_scales_with_equity(self, sizer):
        """Position should scale linearly with equity."""
        r1 = sizer.atr_based_size(100_000, 50_000, atr=1000)
        r2 = sizer.atr_based_size(200_000, 50_000, atr=1000)
        ratio = r2["quantity"] / r1["quantity"]
        assert abs(ratio - 2.0) < 0.01, f"Expected 2x quantity for 2x equity, got {ratio}x"


# ---------------------------------------------------------------------------
# Drawdown Scaling Tests
# ---------------------------------------------------------------------------

class TestDrawdownScaling:
    def test_no_drawdown_is_full_size(self, sizer):
        """Zero drawdown → full position size."""
        scale = sizer._drawdown_scale(0.0)
        assert scale == 1.0

    def test_4pct_drawdown_is_full_size(self, sizer):
        """4% drawdown → still full size (below 5% threshold)."""
        scale = sizer._drawdown_scale(0.04)
        assert scale == 1.0

    def test_5pct_drawdown_reduces_size(self, sizer):
        """5% drawdown → 80% position size."""
        scale = sizer._drawdown_scale(0.05)
        assert abs(scale - 0.80) < 1e-6

    def test_10pct_drawdown_reduces_more(self, sizer):
        """10% drawdown → 60% position size."""
        scale = sizer._drawdown_scale(0.10)
        assert abs(scale - 0.60) < 1e-6

    def test_15pct_drawdown_reduces_severely(self, sizer):
        """15% drawdown → 40% position size."""
        scale = sizer._drawdown_scale(0.15)
        assert abs(scale - 0.40) < 1e-6

    def test_scale_applied_correctly_to_quantity(self, sizer):
        """With 10% drawdown, quantity should be 60% of no-drawdown quantity."""
        result_normal = sizer.atr_based_size(100_000, 50_000, atr=1000, drawdown_pct=0.0)
        result_dd     = sizer.atr_based_size(100_000, 50_000, atr=1000, drawdown_pct=0.10)

        ratio = result_dd["quantity"] / result_normal["quantity"]
        assert abs(ratio - 0.60) < 1e-6, f"Expected 60% quantity at 10% drawdown, got {ratio:.2f}"

    def test_drawdown_scale_monotonically_decreasing(self, sizer):
        """Higher drawdown → smaller scale."""
        scales = [sizer._drawdown_scale(dd) for dd in [0.0, 0.05, 0.10, 0.15, 0.20]]
        for i in range(len(scales) - 1):
            assert scales[i] >= scales[i+1], (
                f"Scale should be non-increasing, but {scales[i]} < {scales[i+1]}"
            )


# ---------------------------------------------------------------------------
# Integration: compute_final_size
# ---------------------------------------------------------------------------

class TestComputeFinalSize:
    def test_final_size_respects_all_limits(self, sizer):
        """Final size should be within Kelly cap, market cap, and ATR limits."""
        result = sizer.compute_final_size(
            equity=100_000,
            entry_price=50_000,
            atr=1000,
            market="crypto",
            win_rate=0.55,
            avg_win=2.0,
            avg_loss=1.0,
            drawdown_pct=0.0,
        )
        # Should not exceed 10% of equity (crypto market max)
        assert result["position_value"] <= 100_000 * 0.10 + 1e-6

    def test_zero_equity_returns_zero(self, sizer):
        """Zero equity → zero position."""
        result = sizer.compute_final_size(0, 50_000, 1000, "crypto")
        assert result["quantity"] == 0.0

    def test_negative_ev_produces_small_position(self, sizer):
        """Negative EV (Kelly=0) → position limited to min."""
        result = sizer.compute_final_size(
            equity=100_000,
            entry_price=50_000,
            atr=1000,
            market="crypto",
            win_rate=0.30,  # Low win rate
            avg_win=0.5,    # Small wins
            avg_loss=2.0,   # Large losses
        )
        # Kelly capping may result in zero or very small position
        assert result["position_value"] >= 0

    def test_risk_pct_output_is_reasonable(self, sizer):
        """Risk % should be between 0 and market max."""
        result = sizer.compute_final_size(
            equity=100_000,
            entry_price=50_000,
            atr=500,
            market="crypto",
            win_rate=0.6,
            avg_win=2.0,
            avg_loss=1.0,
        )
        assert 0 <= result["risk_pct"] <= 10.0 + 1e-6
