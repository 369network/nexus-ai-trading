# src/signals/edge_filter.py
"""Edge filter — determines which fused signals are actionable."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .signal_types import FusedSignal, SignalDirection

logger = logging.getLogger(__name__)

DEFAULT_EDGE_CONFIG: Dict[str, Any] = {
    "min_ev": 0.0,              # minimum expected value (EV > 0 means positive edge)
    "min_confidence": 0.30,     # minimum confidence score
    "require_mtf_confirm": False, # require higher-TF confirmation
    "max_open_positions": 10,   # veto if already holding this symbol
    "min_risk_reward": 1.5,     # minimum R:R ratio
}


class EdgeFilter:
    """Filter fused signals to only pass those with a statistical edge."""

    def __init__(self, config: Optional[Dict[str, Any]] = None) -> None:
        self._config = {**DEFAULT_EDGE_CONFIG, **(config or {})}
        self._rejection_log: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def passes(
        self,
        signal: FusedSignal,
        market: str,
        open_positions: Optional[List[str]] = None,
    ) -> bool:
        """Return True if *signal* has sufficient edge to be acted upon.

        Parameters
        ----------
        signal:
            The fused signal to evaluate.
        market:
            Market context (some thresholds are market-specific).
        open_positions:
            List of currently held symbols. If signal.symbol is in this
            list, the signal is rejected as already-in-position.
        """
        open_positions = open_positions or []
        failures: List[str] = []

        # 1. Already in this position
        if signal.symbol in open_positions and signal.direction != SignalDirection.NEUTRAL:
            failures.append(f"already_in_position:{signal.symbol}")

        # 2. Confidence check
        min_conf = float(self._config.get("min_confidence", 0.30))
        if signal.confidence < min_conf:
            failures.append(
                f"low_confidence:{signal.confidence:.2f} < {min_conf:.2f}"
            )

        # 3. Expected value check
        min_ev = float(self._config.get("min_ev", 0.0))
        if signal.expected_value <= min_ev:
            failures.append(
                f"insufficient_ev:{signal.expected_value:.3f} <= {min_ev:.3f}"
            )

        # 4. MTF confirmation (if required)
        if self._config.get("require_mtf_confirm", False) and not signal.mtf_confirmed:
            failures.append("mtf_not_confirmed")

        # 5. Risk/reward check
        min_rr = float(self._config.get("min_risk_reward", 1.5))
        if signal.risk_reward < min_rr and signal.direction != SignalDirection.NEUTRAL:
            failures.append(
                f"low_risk_reward:{signal.risk_reward:.2f} < {min_rr:.2f}"
            )

        # 6. Neutral signal
        if signal.direction == SignalDirection.NEUTRAL:
            failures.append("neutral_direction")

        # Log and return
        if failures:
            rejection = {
                "symbol": signal.symbol,
                "market": market,
                "timeframe": signal.timeframe,
                "direction": signal.direction.value,
                "confidence": signal.confidence,
                "expected_value": signal.expected_value,
                "risk_reward": signal.risk_reward,
                "reasons": failures,
                "timestamp": signal.timestamp.isoformat(),
            }
            self._rejection_log.append(rejection)
            logger.info(
                "EdgeFilter REJECTED %s/%s: %s",
                signal.symbol, signal.timeframe, "; ".join(failures),
            )
            return False

        logger.info(
            "EdgeFilter PASSED %s/%s %s (conf=%.2f, EV=%.3f, RR=%.2f)",
            signal.symbol, signal.timeframe, signal.direction.value,
            signal.confidence, signal.expected_value, signal.risk_reward,
        )
        return True

    def get_rejection_log(self) -> List[Dict[str, Any]]:
        """Return all rejection records (useful for analytics)."""
        return list(self._rejection_log)

    def clear_rejection_log(self) -> None:
        self._rejection_log.clear()

    def update_config(self, **kwargs) -> None:
        """Update edge filter thresholds at runtime."""
        self._config.update(kwargs)
        logger.info("EdgeFilter config updated: %s", kwargs)
