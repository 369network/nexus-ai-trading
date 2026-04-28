"""
NEXUS ALPHA - Risk Manager
============================
Combines FiveLayerRisk + CircuitBreakers into a unified risk evaluation
entry point.  Wraps the five-layer engine and circuit breaker manager.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.risk.five_layer_risk import FiveLayerRisk, RiskApproval, RiskLevel

logger = logging.getLogger(__name__)

# Default portfolio state when no live data is available (paper mode)
_DEFAULT_CAPITAL = 100_000.0


@dataclass
class RiskResult:
    """Result of a full risk evaluation."""
    approved: bool
    position_size: float          # Adjusted position size fraction (0–1)
    stop_loss: float              # Suggested stop loss price
    take_profit: float            # Suggested take profit price
    rejection_reason: str = ""
    risk_level: str = "NORMAL"
    layer_failed: int = 0         # Which layer blocked (0 = all passed)
    circuit_breaker_triggered: bool = False
    cb_name: str = ""

    def __bool__(self) -> bool:
        return self.approved


@dataclass
class CircuitBreakerStatus:
    """Status of a single circuit breaker."""
    name: str
    tripped: bool
    reason: str = ""
    reset_at: Optional[datetime] = None


class RiskManager:
    """
    Unified risk management facade.

    Delegates to:
    - FiveLayerRisk for sequential layer evaluation
    - CircuitBreakerManager for event-driven circuit breakers

    Parameters
    ----------
    settings : Settings
        Application settings.
    db : SupabaseClient
        Database client for persisting risk events.
    """

    def __init__(self, settings: Any, db: Any) -> None:
        self._settings = settings
        self._db = db
        self._five_layer = FiveLayerRisk()
        self._circuit_breakers: Optional[Any] = None  # CircuitBreakerManager

        # In-memory portfolio state for paper mode
        self._portfolio: Dict[str, Any] = {
            "capital": _DEFAULT_CAPITAL,
            "initial_capital": _DEFAULT_CAPITAL,
            "peak_equity": _DEFAULT_CAPITAL,
            "current_equity": _DEFAULT_CAPITAL,
            "positions": [],
        }
        self._daily_pnl: float = 0.0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Initialise risk components."""
        logger.info("RiskManager: initialising five-layer risk + circuit breakers")

        # Attempt to load circuit breaker manager
        try:
            from src.risk.circuit_breakers import CircuitBreakerManager  # type: ignore[import]
            self._circuit_breakers = CircuitBreakerManager()
            logger.info("RiskManager: CircuitBreakerManager loaded")
        except Exception as exc:
            logger.warning(
                "RiskManager: could not load CircuitBreakerManager: %s — CB checks disabled",
                exc,
            )

        logger.info("RiskManager: all five layers active")

    # ------------------------------------------------------------------
    # Main evaluation interface
    # ------------------------------------------------------------------

    async def evaluate(
        self,
        signal: Any,
        edge: Optional[Any] = None,
        portfolio: Optional[Dict[str, Any]] = None,
    ) -> RiskResult:
        """
        Run the full risk evaluation pipeline.

        Parameters
        ----------
        signal :
            FusedSignal or TradeSignal with attributes: market, size_pct, symbol.
        edge :
            EdgeFilterResult (optional).
        portfolio :
            Current portfolio state dict.  If None, uses internal paper state.

        Returns
        -------
        RiskResult
        """
        if portfolio is None:
            portfolio = self._portfolio

        # --- Circuit breaker check first ---
        cb_result = self._check_circuit_breakers(signal)
        if cb_result is not None:
            return cb_result

        # --- Five-layer risk evaluation ---
        try:
            approval: RiskApproval = self._five_layer.evaluate_all(
                signal=signal,
                portfolio=portfolio,
                daily_pnl=self._daily_pnl,
            )
        except Exception as exc:
            logger.error("RiskManager: five-layer evaluation failed: %s", exc, exc_info=True)
            return RiskResult(
                approved=False,
                position_size=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                rejection_reason=f"risk_evaluation_error: {exc}",
            )

        if not approval.approved:
            logger.info(
                "RiskManager: REJECTED signal for %s — layer=%d reason=%s",
                getattr(signal, "symbol", "?"),
                approval.layer_failed,
                approval.reason,
            )
            await self._persist_risk_event(signal, approval)
            return RiskResult(
                approved=False,
                position_size=0.0,
                stop_loss=0.0,
                take_profit=0.0,
                rejection_reason=approval.reason,
                risk_level=approval.risk_level.value,
                layer_failed=approval.layer_failed,
            )

        # --- Compute position size with size_reduction applied ---
        base_size = float(getattr(signal, "size_pct", 0.03))
        adjusted_size = base_size * approval.size_reduction

        # Derive stop_loss and take_profit from signal if available
        stop_loss = float(getattr(signal, "stop_loss", 0.0) or 0.0)
        take_profit = float(
            getattr(signal, "take_profit_1", None)
            or getattr(signal, "take_profit", 0.0)
            or 0.0
        )

        return RiskResult(
            approved=True,
            position_size=adjusted_size,
            stop_loss=stop_loss,
            take_profit=take_profit,
            rejection_reason="",
            risk_level=approval.risk_level.value,
            layer_failed=0,
        )

    # ------------------------------------------------------------------
    # Circuit breaker status (used by health check loop)
    # ------------------------------------------------------------------

    async def get_circuit_breaker_status(self) -> Dict[str, CircuitBreakerStatus]:
        """
        Return the current status of all circuit breakers.

        Returns an empty dict if circuit breakers are not initialised.
        """
        if self._circuit_breakers is None:
            return {}

        try:
            raw = self._circuit_breakers.get_status()
            # Normalise to CircuitBreakerStatus objects
            result: Dict[str, CircuitBreakerStatus] = {}
            for name, info in raw.items():
                if isinstance(info, dict):
                    result[name] = CircuitBreakerStatus(
                        name=name,
                        tripped=bool(info.get("tripped", False)),
                        reason=info.get("reason", ""),
                    )
                else:
                    # Already a typed object — wrap it
                    tripped = getattr(info, "tripped", False) or getattr(info, "is_tripped", False)
                    result[name] = CircuitBreakerStatus(
                        name=name,
                        tripped=bool(tripped),
                        reason=str(getattr(info, "reason", "")),
                    )
            return result
        except Exception as exc:
            logger.error("RiskManager.get_circuit_breaker_status failed: %s", exc)
            return {}

    # ------------------------------------------------------------------
    # Portfolio state helpers (used by paper mode)
    # ------------------------------------------------------------------

    def update_portfolio(self, portfolio: Dict[str, Any]) -> None:
        """Update internal portfolio state."""
        self._portfolio.update(portfolio)

    def record_daily_pnl(self, daily_pnl: float) -> None:
        """Update daily P&L for Layer 4 evaluation."""
        self._daily_pnl = daily_pnl

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_circuit_breakers(self, signal: Any) -> Optional[RiskResult]:
        """
        Check all circuit breakers synchronously.
        Returns a rejection RiskResult if any breaker is tripped, else None.
        """
        if self._circuit_breakers is None:
            return None

        try:
            status = self._circuit_breakers.get_status()
            for name, info in status.items():
                tripped = (
                    info.get("tripped", False)
                    if isinstance(info, dict)
                    else (getattr(info, "tripped", False) or getattr(info, "is_tripped", False))
                )
                if tripped:
                    reason = (
                        info.get("reason", "circuit breaker tripped")
                        if isinstance(info, dict)
                        else str(getattr(info, "reason", "circuit breaker tripped"))
                    )
                    logger.warning(
                        "RiskManager: circuit breaker %s tripped: %s", name, reason
                    )
                    return RiskResult(
                        approved=False,
                        position_size=0.0,
                        stop_loss=0.0,
                        take_profit=0.0,
                        rejection_reason=f"circuit_breaker:{name}:{reason}",
                        circuit_breaker_triggered=True,
                        cb_name=name,
                    )
        except Exception as exc:
            logger.error("RiskManager: circuit breaker check error: %s", exc)

        return None

    async def _persist_risk_event(self, signal: Any, approval: RiskApproval) -> None:
        """Persist a risk rejection event to Supabase."""
        if self._db is None:
            return
        try:
            event = {
                "event_type": "risk_rejection",
                "market": getattr(signal, "market", "unknown"),
                "symbol": getattr(signal, "symbol", "unknown"),
                "severity": "WARNING",
                "trigger_value": float(getattr(signal, "size_pct", 0.0)),
                "threshold_value": 0.0,
                "action_taken": f"layer_{approval.layer_failed}_rejection",
                "positions_affected": 0,
                "details": {
                    "reason": approval.reason,
                    "layer_failed": approval.layer_failed,
                    "risk_level": approval.risk_level.value,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            }
            if hasattr(self._db, "insert_risk_event"):
                await self._db.insert_risk_event(event)
        except Exception as exc:
            logger.debug("RiskManager._persist_risk_event failed: %s", exc)
