# src/agents/debate_engine.py
"""Debate Engine — orchestrates the multi-agent decision debate."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .base_agent import AgentDecision, AgentOutput, BaseAgent

logger = logging.getLogger(__name__)

# Voting weights for each agent role
DEFAULT_VOTE_WEIGHTS: Dict[str, float] = {
    "bull_researcher": 1.0,
    "bear_researcher": 1.0,
    "fundamental_analyst": 1.5,
    "technical_analyst": 1.5,
    "sentiment_analyst": 1.0,
}


@dataclass
class DebateResult:
    """The outcome of a multi-agent debate."""

    final_signal: AgentOutput
    agent_outputs: Dict[str, AgentOutput]
    consensus_score: float          # 0.0–1.0 (1.0 = unanimous)
    conflict_areas: List[str]       # Descriptions of where agents disagree
    winning_argument: str           # The key reasoning that swung the decision
    debate_duration_ms: float
    timestamp: datetime = field(default_factory=lambda: datetime.now(tz=timezone.utc))
    was_vetoed: bool = False
    veto_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "final_signal": self.final_signal.to_dict(),
            "agent_outputs": {k: v.to_dict() for k, v in self.agent_outputs.items()},
            "consensus_score": self.consensus_score,
            "conflict_areas": self.conflict_areas,
            "winning_argument": self.winning_argument,
            "debate_duration_ms": self.debate_duration_ms,
            "timestamp": self.timestamp.isoformat(),
            "was_vetoed": self.was_vetoed,
            "veto_reason": self.veto_reason,
        }


class DebateEngine:
    """Orchestrates the multi-agent debate and produces a unified DebateResult.

    Execution order:
    1. Run Bull + Bear researchers concurrently (parallel)
    2. Run Fundamental + Technical + Sentiment concurrently (parallel)
    3. Risk Manager evaluates the emerging consensus
    4. Portfolio Manager makes the final decision
    5. Build and log DebateResult
    """

    def __init__(
        self,
        agents: Dict[str, BaseAgent],
        vote_weights: Optional[Dict[str, float]] = None,
        supabase_client=None,
    ) -> None:
        """
        Parameters
        ----------
        agents:
            Dict of role_name → BaseAgent instance.
            Expected keys: bull_researcher, bear_researcher,
            fundamental_analyst, technical_analyst, sentiment_analyst,
            risk_manager, portfolio_manager.
        vote_weights:
            Override default voting weights.
        supabase_client:
            Optional Supabase client for persisting debate logs.
        """
        self._agents = agents
        self._weights = vote_weights or dict(DEFAULT_VOTE_WEIGHTS)
        self._supabase = supabase_client

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def run_debate(
        self,
        market_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> DebateResult:
        """Execute the full multi-agent debate and return a :class:`DebateResult`.

        Parameters
        ----------
        market_data:
            Unified market data dict passed to all agents.
        context:
            Shared context including portfolio state, config, etc.
        """
        import time
        t0 = time.monotonic()

        agent_outputs: Dict[str, AgentOutput] = {}

        # Phase 1: Research phase (bull + bear in parallel)
        phase1_outputs = await self._run_phase_parallel(
            agents=["bull_researcher", "bear_researcher"],
            market_data=market_data,
            context={**context, "agent_outputs": {}},
        )
        agent_outputs.update(phase1_outputs)

        # Phase 2: Analysis phase (fundamental + technical + sentiment in parallel)
        phase2_context = {**context, "agent_outputs": dict(agent_outputs)}
        phase2_outputs = await self._run_phase_parallel(
            agents=["fundamental_analyst", "technical_analyst", "sentiment_analyst"],
            market_data=market_data,
            context=phase2_context,
        )
        agent_outputs.update(phase2_outputs)

        # Phase 3: Compute preliminary signal for risk manager
        preliminary_signal = self._compute_preliminary_signal(agent_outputs)

        # Phase 4: Risk Manager evaluates
        rm_context = {
            **context,
            "agent_outputs": dict(agent_outputs),
            "proposed_signal": preliminary_signal,
        }
        rm_agent = self._agents.get("risk_manager")
        rm_output: Optional[AgentOutput] = None
        if rm_agent:
            try:
                rm_output = await rm_agent.analyze(market_data, rm_context)
                agent_outputs["risk_manager"] = rm_output
            except Exception as exc:
                logger.error("[DebateEngine] Risk Manager failed: %s", exc)

        # Phase 5: Portfolio Manager makes final decision
        pm_context = {
            **context,
            "agent_outputs": dict(agent_outputs),
            "proposed_signal": preliminary_signal,
        }
        pm_agent = self._agents.get("portfolio_manager")
        final_output: Optional[AgentOutput] = None
        if pm_agent:
            try:
                final_output = await pm_agent.analyze(market_data, pm_context)
                agent_outputs["portfolio_manager"] = final_output
            except Exception as exc:
                logger.error("[DebateEngine] Portfolio Manager failed: %s", exc)

        if final_output is None:
            final_output = self._build_fallback_output(agent_outputs)

        duration_ms = (time.monotonic() - t0) * 1000

        result = self._build_result(
            final_output=final_output,
            agent_outputs=agent_outputs,
            rm_output=rm_output,
            duration_ms=duration_ms,
        )

        # Persist to Supabase
        await self._persist_debate(market_data.get("symbol", ""), result)

        logger.info(
            "[DebateEngine] Debate complete for %s in %.0fms | "
            "Decision: %s (conf=%.2f) | Consensus: %.2f | Vetoed: %s",
            market_data.get("symbol", ""),
            duration_ms,
            final_output.decision.value,
            final_output.confidence,
            result.consensus_score,
            result.was_vetoed,
        )

        return result

    # ------------------------------------------------------------------
    # Phase runners
    # ------------------------------------------------------------------

    async def _run_phase_parallel(
        self,
        agents: List[str],
        market_data: Dict[str, Any],
        context: Dict[str, Any],
    ) -> Dict[str, AgentOutput]:
        """Run multiple agents concurrently and collect their outputs."""
        tasks = {}
        for role in agents:
            agent = self._agents.get(role)
            if agent:
                tasks[role] = agent.analyze(market_data, context)
            else:
                logger.warning("[DebateEngine] Agent %r not found — skipping", role)

        if not tasks:
            return {}

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)
        outputs: Dict[str, AgentOutput] = {}

        for role, result in zip(tasks.keys(), results):
            if isinstance(result, AgentOutput):
                outputs[role] = result
            else:
                logger.error(
                    "[DebateEngine] Agent %r raised exception: %s", role, result
                )

        return outputs

    # ------------------------------------------------------------------
    # Signal aggregation
    # ------------------------------------------------------------------

    def _compute_preliminary_signal(
        self, agent_outputs: Dict[str, AgentOutput]
    ) -> Dict[str, Any]:
        """Compute a weighted preliminary signal from research + analysis agents."""
        total_weight = 0.0
        weighted_num = 0.0

        for role, output in agent_outputs.items():
            if role in ("risk_manager", "portfolio_manager"):
                continue
            w = self._weights.get(role, 1.0)
            weighted_num += output.decision.numeric * w * output.confidence
            total_weight += w

        net_signal = weighted_num / total_weight if total_weight > 0 else 0.0

        # Extract best trade plan from technical analyst
        ta = agent_outputs.get("technical_analyst")
        return {
            "direction": "LONG" if net_signal > 0.1 else "SHORT" if net_signal < -0.1 else "NEUTRAL",
            "net_signal": net_signal,
            "entry": ta.entry_price if ta else None,
            "stop_loss": ta.stop_loss if ta else None,
            "take_profit_1": ta.take_profit_1 if ta else None,
            "take_profit_2": ta.take_profit_2 if ta else None,
            "take_profit_3": ta.take_profit_3 if ta else None,
            "size_pct": 3.0,  # default base size %
            "market": list(self._agents.values())[0].market if self._agents else "crypto",
        }

    # ------------------------------------------------------------------
    # Result construction
    # ------------------------------------------------------------------

    def _build_result(
        self,
        final_output: AgentOutput,
        agent_outputs: Dict[str, AgentOutput],
        rm_output: Optional[AgentOutput],
        duration_ms: float,
    ) -> DebateResult:
        """Build a :class:`DebateResult` from all outputs."""
        consensus_score, conflict_areas = self._compute_consensus(agent_outputs)
        winning_argument = self._identify_winning_argument(agent_outputs, final_output)

        was_vetoed = rm_output.veto if rm_output else False
        veto_reason = rm_output.veto_reason if rm_output else None

        return DebateResult(
            final_signal=final_output,
            agent_outputs=agent_outputs,
            consensus_score=consensus_score,
            conflict_areas=conflict_areas,
            winning_argument=winning_argument,
            debate_duration_ms=duration_ms,
            was_vetoed=was_vetoed,
            veto_reason=veto_reason,
        )

    def _compute_consensus(
        self, agent_outputs: Dict[str, AgentOutput]
    ) -> tuple[float, List[str]]:
        """Compute consensus score and identify conflict areas."""
        research_agents = {
            k: v for k, v in agent_outputs.items()
            if k not in ("risk_manager", "portfolio_manager")
        }

        if not research_agents:
            return 0.5, []

        numerics = [v.decision.numeric for v in research_agents.values()]
        if not numerics:
            return 0.5, []

        # Consensus = 1 - normalised standard deviation of numeric signals
        import statistics
        if len(numerics) < 2:
            return 1.0, []

        std = statistics.stdev(numerics)
        # Max possible stdev is 1.0 (signals ranging from -1 to +1)
        consensus = max(0.0, 1.0 - std)

        # Identify conflicts: agents on opposite sides of 0 with high confidence
        conflict_areas: List[str] = []
        roles = list(research_agents.keys())
        for i in range(len(roles)):
            for j in range(i + 1, len(roles)):
                a = research_agents[roles[i]]
                b = research_agents[roles[j]]
                if (
                    a.decision.numeric * b.decision.numeric < 0  # opposite signs
                    and a.confidence > 0.6
                    and b.confidence > 0.6
                ):
                    conflict_areas.append(
                        f"{roles[i]}({a.decision.value}) vs {roles[j]}({b.decision.value})"
                    )

        return consensus, conflict_areas

    def _identify_winning_argument(
        self,
        agent_outputs: Dict[str, AgentOutput],
        final_output: AgentOutput,
    ) -> str:
        """Find the agent whose reasoning most closely matches the final decision."""
        final_numeric = final_output.decision.numeric

        best_role = "portfolio_manager"
        best_alignment = -1.0

        for role, output in agent_outputs.items():
            if role in ("portfolio_manager",):
                continue
            alignment = 1.0 - abs(output.decision.numeric - final_numeric)
            confidence_weighted = alignment * output.confidence
            if confidence_weighted > best_alignment:
                best_alignment = confidence_weighted
                best_role = role

        winning_agent = agent_outputs.get(best_role)
        if winning_agent:
            return f"[{best_role}] {winning_agent.reasoning[:300]}"
        return final_output.reasoning[:300]

    def _build_fallback_output(
        self, agent_outputs: Dict[str, AgentOutput]
    ) -> AgentOutput:
        """Build a simple majority-vote fallback when Portfolio Manager is unavailable."""
        signal = self._compute_preliminary_signal(agent_outputs)
        direction_str = signal.get("direction", "NEUTRAL")

        try:
            if direction_str == "LONG":
                decision = AgentDecision.BUY
            elif direction_str == "SHORT":
                decision = AgentDecision.SELL
            else:
                decision = AgentDecision.NEUTRAL
        except Exception:
            decision = AgentDecision.NEUTRAL

        return AgentOutput(
            agent_role="debate_engine_fallback",
            decision=decision,
            confidence=0.4,
            reasoning="Portfolio Manager unavailable — using weighted vote fallback",
            key_factors=["fallback_mode"],
            data_used=list(agent_outputs.keys()),
            model_used="rules_engine",
            latency_ms=0.0,
            timestamp=datetime.now(tz=timezone.utc),
        )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist_debate(self, symbol: str, result: DebateResult) -> None:
        if self._supabase is None:
            return
        try:
            self._supabase.table("agent_decisions").insert({
                "symbol": symbol,
                "timestamp": result.timestamp.isoformat(),
                "final_decision": result.final_signal.decision.value,
                "final_confidence": result.final_signal.confidence,
                "consensus_score": result.consensus_score,
                "was_vetoed": result.was_vetoed,
                "veto_reason": result.veto_reason,
                "conflict_areas": result.conflict_areas,
                "winning_argument": result.winning_argument[:500],
                "debate_duration_ms": result.debate_duration_ms,
                "agent_outputs": {k: v.to_dict() for k, v in result.agent_outputs.items()},
            }).execute()
        except Exception as exc:
            logger.error("[DebateEngine] Failed to persist debate to Supabase: %s", exc)
