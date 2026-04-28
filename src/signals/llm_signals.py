# src/signals/llm_signals.py
"""LLM signal generator — converts debate results to normalised signal scores."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from ..agents.base_agent import AgentDecision, AgentOutput
from ..agents.debate_engine import DebateResult

logger = logging.getLogger(__name__)

# Mapping from AgentDecision to numeric signal
_DECISION_TO_NUMERIC: Dict[str, float] = {
    AgentDecision.STRONG_BUY.value: 1.0,
    AgentDecision.BUY.value: 0.6,
    AgentDecision.SLIGHT_BUY.value: 0.3,
    AgentDecision.NEUTRAL.value: 0.0,
    AgentDecision.SLIGHT_SELL.value: -0.3,
    AgentDecision.SELL.value: -0.6,
    AgentDecision.STRONG_SELL.value: -1.0,
}


class LLMSignalGenerator:
    """Convert debate engine results to a normalised [-1, +1] signal score.

    The score is computed as a weighted average of agent decisions, scaled
    by their individual confidence levels.
    """

    # Vote weights for each agent role (same as DebateEngine defaults)
    DEFAULT_WEIGHTS: Dict[str, float] = {
        "bull_researcher": 1.0,
        "bear_researcher": 1.0,
        "fundamental_analyst": 1.5,
        "technical_analyst": 1.5,
        "sentiment_analyst": 1.0,
        # portfolio_manager and risk_manager outputs are handled separately
    }

    def generate(
        self,
        market_data: Dict[str, Any],
        context: Dict[str, Any],
        market: str,
        debate_result: Optional[DebateResult] = None,
        agent_outputs: Optional[Dict[str, AgentOutput]] = None,
    ) -> float:
        """Generate an LLM signal from a DebateResult or raw agent outputs.

        Parameters
        ----------
        market_data:
            Passed through for context (not used directly here).
        context:
            Shared context dict.
        market:
            Market type for any market-specific adjustments.
        debate_result:
            If provided, extract signal from ``debate_result.agent_outputs``.
        agent_outputs:
            Alternative: pass agent outputs dict directly.

        Returns
        -------
        float
            -1.0 (strong bearish) to +1.0 (strong bullish).
        """
        outputs = (
            debate_result.agent_outputs
            if debate_result is not None
            else (agent_outputs or {})
        )

        if not outputs:
            logger.warning("LLMSignalGenerator: no agent outputs provided, returning 0")
            return 0.0

        return self._weighted_average(outputs)

    def from_debate_result(self, result: DebateResult) -> float:
        """Convenience method: extract signal from a DebateResult."""
        # Primary: use portfolio manager's final decision
        pm_output = result.agent_outputs.get("portfolio_manager")
        if pm_output and not result.was_vetoed:
            pm_numeric = _DECISION_TO_NUMERIC.get(pm_output.decision.value, 0.0)
            # Weight by confidence
            return pm_numeric * pm_output.confidence

        if result.was_vetoed:
            return 0.0

        return self._weighted_average(result.agent_outputs)

    def from_single_output(self, output: AgentOutput) -> float:
        """Convert a single AgentOutput to a numeric signal."""
        numeric = _DECISION_TO_NUMERIC.get(output.decision.value, 0.0)
        return numeric * output.confidence

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _weighted_average(
        self, agent_outputs: Dict[str, AgentOutput]
    ) -> float:
        """Compute confidence-weighted, role-weighted average signal."""
        total_weight = 0.0
        weighted_sum = 0.0

        for role, output in agent_outputs.items():
            # Skip risk manager (veto role) and portfolio manager (final decision)
            if role in ("risk_manager", "portfolio_manager"):
                continue

            role_weight = self.DEFAULT_WEIGHTS.get(role, 1.0)
            numeric = _DECISION_TO_NUMERIC.get(output.decision.value, 0.0)

            # Weight by both role importance and model confidence
            effective_weight = role_weight * output.confidence
            weighted_sum += numeric * effective_weight
            total_weight += effective_weight

        if total_weight == 0:
            return 0.0

        raw = weighted_sum / total_weight
        return max(-1.0, min(1.0, raw))

    @staticmethod
    def decision_to_numeric(decision: AgentDecision) -> float:
        """Map an :class:`AgentDecision` to a numeric signal value."""
        return _DECISION_TO_NUMERIC.get(decision.value, 0.0)
