"""
NEXUS ALPHA - Five-Layer Risk Engine
======================================
Validates every signal through five sequential risk gates before
allowing order placement.  Any layer failure halts the trade.

Layer 1: Position-level risk (single position cap)
Layer 2: Market-segment exposure (e.g. total crypto ≤ 40%)
Layer 3: Portfolio-wide gross exposure (≤ 80%)
Layer 4: Daily P&L circuit breaker (halt if daily loss > 15%)
Layer 5: Drawdown gate (reduce or stop at 30% / 40% drawdown)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Layer 1: maximum single-position size as fraction of capital
POSITION_LIMITS: Dict[str, float] = {
    "crypto":      0.10,
    "forex":       0.05,
    "commodities": 0.08,
    "stocks_in":   0.15,
    "stocks_us":   0.15,
    "stocks":      0.15,
}

# Layer 2: maximum total exposure per market segment as fraction of capital
MARKET_EXPOSURE_LIMITS: Dict[str, float] = {
    "crypto":      0.40,
    "forex":       0.30,
    "commodities": 0.20,
    "stocks_in":   0.30,
    "stocks_us":   0.30,
    "stocks":      0.30,
}

# Layer 3: maximum total gross exposure across ALL markets
MAX_TOTAL_EXPOSURE = 0.80

# Layer 4: daily loss limit as fraction of capital
MAX_DAILY_LOSS_FRACTION = 0.15

# Layer 5: drawdown thresholds
DRAWDOWN_PAUSE_THRESHOLD = 0.30   # 30% → PAUSE (reduce size 75%)
DRAWDOWN_STOP_THRESHOLD  = 0.40   # 40% → STOP (emergency shutdown)


# ---------------------------------------------------------------------------
# Enumerations and dataclasses
# ---------------------------------------------------------------------------

class RiskLevel(str, Enum):
    """Operational risk level from drawdown monitoring."""
    NORMAL  = "NORMAL"
    WARNING = "WARNING"
    PAUSE   = "PAUSE"
    STOP    = "STOP"


@dataclass
class RiskApproval:
    """Result of the five-layer risk evaluation."""
    approved: bool
    layer_failed: int          # 0 = all passed; 1–5 = which layer failed
    reason: str
    risk_level: RiskLevel = RiskLevel.NORMAL
    size_reduction: float = 1.0  # multiplier to apply to position size (0–1)

    def __bool__(self) -> bool:
        return self.approved


# ---------------------------------------------------------------------------
# FiveLayerRisk
# ---------------------------------------------------------------------------

class FiveLayerRisk:
    """
    Sequential five-layer risk evaluation engine.

    All layers must pass for a signal to receive approval.  Layers are
    evaluated in order; the first failure short-circuits the remainder.

    Usage
    -----
    risk = FiveLayerRisk()
    approval = risk.evaluate_all(signal, portfolio, daily_pnl)
    if approval:
        executor.place_order(...)
    """

    # ------------------------------------------------------------------
    # Layer 1 – Position Risk
    # ------------------------------------------------------------------

    def check_position_limit(self, market: str, size_pct: float) -> bool:
        """
        Layer 1: Validate that a single position does not exceed the
        per-market position cap.

        Parameters
        ----------
        market : str
            Market segment, e.g. "crypto", "forex".
        size_pct : float
            Proposed position size as a fraction of capital (0–1).

        Returns
        -------
        bool
            True if within limit.
        """
        limit = POSITION_LIMITS.get(market, POSITION_LIMITS["stocks"])
        ok = size_pct <= limit
        if not ok:
            logger.warning(
                "Layer1 FAIL: %s position %.2f%% exceeds limit %.2f%%",
                market, size_pct * 100, limit * 100,
            )
        return ok

    # ------------------------------------------------------------------
    # Layer 2 – Market Exposure
    # ------------------------------------------------------------------

    def check_market_exposure(self, market: str, portfolio: Dict[str, Any]) -> bool:
        """
        Layer 2: Validate that the portfolio's total exposure to a market
        segment stays within the allowed maximum.

        Parameters
        ----------
        market : str
            Market segment key.
        portfolio : dict
            Must contain:
              - capital (float): total portfolio capital in USD
              - positions (list[dict]): each dict with 'market' and 'notional_usd' keys

        Returns
        -------
        bool
            True if the current market exposure is within limits.
        """
        capital: float = portfolio.get("capital", 1.0)
        positions: List[Dict[str, Any]] = portfolio.get("positions", [])

        market_notional = sum(
            p.get("notional_usd", 0.0)
            for p in positions
            if p.get("market", "") == market
        )
        exposure_pct = market_notional / capital if capital > 0 else 0.0
        limit = MARKET_EXPOSURE_LIMITS.get(market, 0.30)

        ok = exposure_pct <= limit
        if not ok:
            logger.warning(
                "Layer2 FAIL: %s exposure %.2f%% exceeds limit %.2f%%",
                market, exposure_pct * 100, limit * 100,
            )
        return ok

    # ------------------------------------------------------------------
    # Layer 3 – Portfolio Exposure
    # ------------------------------------------------------------------

    def check_total_exposure(self, portfolio: Dict[str, Any]) -> bool:
        """
        Layer 3: Validate that total gross exposure across ALL markets
        does not exceed 80% of capital.

        Parameters
        ----------
        portfolio : dict
            Must contain:
              - capital (float)
              - positions (list[dict]): each with 'notional_usd'

        Returns
        -------
        bool
            True if total exposure is within limit.
        """
        capital: float = portfolio.get("capital", 1.0)
        positions: List[Dict[str, Any]] = portfolio.get("positions", [])

        total_notional = sum(p.get("notional_usd", 0.0) for p in positions)
        total_exposure_pct = total_notional / capital if capital > 0 else 0.0

        ok = total_exposure_pct <= MAX_TOTAL_EXPOSURE
        if not ok:
            logger.warning(
                "Layer3 FAIL: total exposure %.2f%% exceeds limit %.2f%%",
                total_exposure_pct * 100, MAX_TOTAL_EXPOSURE * 100,
            )
        return ok

    # ------------------------------------------------------------------
    # Layer 4 – Daily P&L
    # ------------------------------------------------------------------

    def check_daily_loss(self, daily_pnl: float, capital: float) -> bool:
        """
        Layer 4: Halt new positions if the daily loss exceeds 15% of capital.

        Parameters
        ----------
        daily_pnl : float
            Realised + unrealised P&L for the current trading day (negative = loss).
        capital : float
            Portfolio capital in USD.

        Returns
        -------
        bool
            True if daily loss is within acceptable limits.
        """
        if capital <= 0:
            return True  # no capital to protect

        daily_loss_pct = -daily_pnl / capital  # positive means loss
        ok = daily_loss_pct < MAX_DAILY_LOSS_FRACTION

        if not ok:
            logger.warning(
                "Layer4 FAIL: daily loss %.2f%% exceeds limit %.2f%%",
                daily_loss_pct * 100, MAX_DAILY_LOSS_FRACTION * 100,
            )
        return ok

    # ------------------------------------------------------------------
    # Layer 5 – Drawdown
    # ------------------------------------------------------------------

    def check_drawdown(self, peak_equity: float, current_equity: float) -> RiskLevel:
        """
        Layer 5: Assess current drawdown and return the appropriate risk level.

        Parameters
        ----------
        peak_equity : float
            All-time (or rolling window) peak portfolio equity in USD.
        current_equity : float
            Current portfolio equity in USD.

        Returns
        -------
        RiskLevel
            NORMAL | WARNING | PAUSE | STOP
        """
        if peak_equity <= 0:
            return RiskLevel.NORMAL

        drawdown = (peak_equity - current_equity) / peak_equity

        if drawdown >= DRAWDOWN_STOP_THRESHOLD:
            logger.critical(
                "Layer5: STOP – drawdown %.2f%% ≥ %.2f%% threshold",
                drawdown * 100, DRAWDOWN_STOP_THRESHOLD * 100,
            )
            return RiskLevel.STOP

        if drawdown >= DRAWDOWN_PAUSE_THRESHOLD:
            logger.error(
                "Layer5: PAUSE – drawdown %.2f%% ≥ %.2f%% threshold",
                drawdown * 100, DRAWDOWN_PAUSE_THRESHOLD * 100,
            )
            return RiskLevel.PAUSE

        if drawdown >= 0.15:
            return RiskLevel.WARNING

        return RiskLevel.NORMAL

    # ------------------------------------------------------------------
    # Master evaluator
    # ------------------------------------------------------------------

    def evaluate_all(
        self,
        signal: Any,
        portfolio: Dict[str, Any],
        daily_pnl: float,
    ) -> RiskApproval:
        """
        Run all five layers sequentially and return a consolidated approval.

        Parameters
        ----------
        signal : Any
            Signal object with attributes: market, size_pct (as fraction).
        portfolio : dict
            Portfolio state dict (see individual layer methods for keys).
        daily_pnl : float
            Current day's net P&L in USD (negative = loss).

        Returns
        -------
        RiskApproval
            Approval result with layer that failed (0 = all passed) and reason.
        """
        market: str = getattr(signal, "market", "crypto")
        size_pct: float = getattr(signal, "size_pct", 0.0)
        capital: float = portfolio.get("capital", 0.0)
        peak_equity: float = portfolio.get("peak_equity", capital)
        current_equity: float = portfolio.get("current_equity", capital)

        # ---- Layer 1 ----
        if not self.check_position_limit(market, size_pct):
            return RiskApproval(
                approved=False,
                layer_failed=1,
                reason=f"Position size {size_pct:.2%} exceeds {market} limit "
                       f"{POSITION_LIMITS.get(market, 0.10):.2%}",
            )

        # ---- Layer 2 ----
        if not self.check_market_exposure(market, portfolio):
            limit = MARKET_EXPOSURE_LIMITS.get(market, 0.30)
            return RiskApproval(
                approved=False,
                layer_failed=2,
                reason=f"{market} market exposure exceeds {limit:.0%} limit",
            )

        # ---- Layer 3 ----
        if not self.check_total_exposure(portfolio):
            return RiskApproval(
                approved=False,
                layer_failed=3,
                reason=f"Total portfolio exposure exceeds {MAX_TOTAL_EXPOSURE:.0%} limit",
            )

        # ---- Layer 4 ----
        if not self.check_daily_loss(daily_pnl, capital):
            return RiskApproval(
                approved=False,
                layer_failed=4,
                reason=f"Daily loss exceeds {MAX_DAILY_LOSS_FRACTION:.0%} of capital – trading halted",
            )

        # ---- Layer 5 ----
        risk_level = self.check_drawdown(peak_equity, current_equity)
        size_reduction = 1.0

        if risk_level == RiskLevel.STOP:
            return RiskApproval(
                approved=False,
                layer_failed=5,
                reason="Drawdown ≥ 40% – emergency shutdown triggered",
                risk_level=risk_level,
                size_reduction=0.0,
            )

        if risk_level == RiskLevel.PAUSE:
            # Allow trading but reduce size by 75%
            size_reduction = 0.25
            logger.warning("Layer5 PAUSE: size reduced to 25%% of normal")

        logger.debug(
            "evaluate_all: APPROVED market=%s size_pct=%.2f%% risk_level=%s size_reduction=%.2f",
            market, size_pct * 100, risk_level.value, size_reduction,
        )

        return RiskApproval(
            approved=True,
            layer_failed=0,
            reason="All five risk layers passed",
            risk_level=risk_level,
            size_reduction=size_reduction,
        )
