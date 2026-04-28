"""
NEXUS ALPHA - Agent Coordinator
=================================
Manages the 7-agent debate system and returns consensus decisions.
Wraps DebateEngine and handles agent lifecycle.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from src.agents.debate_engine import DebateEngine
from src.agents.base_agent import AgentOutput

logger = logging.getLogger(__name__)

# Agent roles in the debate
_AGENT_ROLES = [
    "bull_researcher",
    "bear_researcher",
    "fundamental_analyst",
    "technical_analyst",
    "sentiment_analyst",
    "risk_manager",
    "portfolio_manager",
]


class AgentCoordinator:
    """
    Manages the 7-agent debate and returns consensus decisions.

    This coordinator is responsible for:
    1. Instantiating and wiring up all 7 agents
    2. Running the multi-agent debate via DebateEngine
    3. Returning the consolidated DebateResult

    Parameters
    ----------
    settings : Settings
        Application settings.
    llm_ensemble : LLMEnsemble
        The LLM ensemble used by individual agents.
    db : SupabaseClient
        Database client for persisting debate results.
    """

    def __init__(
        self,
        settings: Any,
        llm_ensemble: Any,
        db: Any,
    ) -> None:
        self._settings = settings
        self._llm_ensemble = llm_ensemble
        self._db = db
        self._debate_engine: Optional[DebateEngine] = None
        self._agents: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def init(self) -> None:
        """Initialise all 7 agents and wire them into the DebateEngine."""
        logger.info("AgentCoordinator: initialising %d agents...", len(_AGENT_ROLES))

        self._agents = await self._build_agents()

        self._debate_engine = DebateEngine(
            agents=self._agents,
            supabase_client=self._db,
        )

        logger.info(
            "AgentCoordinator: ready with agents: %s",
            list(self._agents.keys()),
        )

    # ------------------------------------------------------------------
    # Debate interface (called from main.py _on_candle_close)
    # ------------------------------------------------------------------

    async def debate(
        self,
        candidate: Any,
        indicators: Dict[str, Any],
        regime: str,
        candle: Dict[str, Any],
    ) -> Any:
        """
        Run the 7-agent debate for the given candidate signal.

        Parameters
        ----------
        candidate :
            CandidateSignal from the market orchestrator.
        indicators :
            Computed technical indicators for the current candle.
        regime :
            Current market regime string.
        candle :
            Raw OHLCV candle dict.

        Returns
        -------
        DebateResult
        """
        if self._debate_engine is None:
            raise RuntimeError("AgentCoordinator not initialised — call init() first")

        market_data = {
            "symbol": getattr(candidate, "symbol", ""),
            "market": getattr(candidate, "market", "crypto"),
            "timeframe": getattr(candidate, "timeframe", ""),
            "direction": str(getattr(candidate, "direction", "")),
            "confidence": getattr(candidate, "confidence", 0.0),
            "candle": candle,
            "indicators": indicators,
            "regime": regime,
        }

        context = {
            "paper_mode": self._settings.paper_mode,
            "regime": regime,
            "candidate_direction": str(getattr(candidate, "direction", "")),
            "candidate_confidence": getattr(candidate, "confidence", 0.0),
        }

        try:
            result = await self._debate_engine.run_debate(
                market_data=market_data,
                context=context,
            )
            return result
        except Exception as exc:
            logger.error(
                "AgentCoordinator.debate failed for %s: %s",
                market_data.get("symbol"), exc, exc_info=True,
            )
            # Return a neutral stub result rather than crashing the pipeline
            return _NeutralDebateResult(symbol=market_data.get("symbol", ""))

    async def run_debate(self, signal_context: Dict[str, Any]) -> Dict[str, Any]:
        """
        Alternate public interface accepting a plain dict signal_context.
        Returns the debate result as a dict.
        """
        if self._debate_engine is None:
            raise RuntimeError("AgentCoordinator not initialised — call init() first")

        market_data = signal_context.get("market_data", signal_context)
        context = signal_context.get("context", {})

        try:
            result = await self._debate_engine.run_debate(
                market_data=market_data,
                context=context,
            )
            return result.to_dict() if hasattr(result, "to_dict") else {}
        except Exception as exc:
            logger.error("AgentCoordinator.run_debate failed: %s", exc, exc_info=True)
            return {"consensus_score": 0.0, "was_vetoed": True, "veto_reason": str(exc)}

    # ------------------------------------------------------------------
    # Agent construction
    # ------------------------------------------------------------------

    async def _build_agents(self) -> Dict[str, Any]:
        """
        Instantiate all 7 agent roles.

        Agents are built lazily — if a role's module fails to import,
        a stub agent is used so the system can still start.
        """
        agents: Dict[str, Any] = {}

        role_module_map = {
            "bull_researcher":     ("src.agents.bull_researcher",     "BullResearcherAgent"),
            "bear_researcher":     ("src.agents.bear_researcher",     "BearResearcherAgent"),
            "fundamental_analyst": ("src.agents.fundamental_analyst", "FundamentalAnalystAgent"),
            "technical_analyst":   ("src.agents.technical_analyst",   "TechnicalAnalystAgent"),
            "sentiment_analyst":   ("src.agents.sentiment_analyst",   "SentimentAnalystAgent"),
            "risk_manager":        ("src.agents.risk_manager",        "RiskManagerAgent"),
            "portfolio_manager":   ("src.agents.portfolio_manager",   "PortfolioManagerAgent"),
        }

        for role, (module_path, class_name) in role_module_map.items():
            try:
                import importlib
                mod = importlib.import_module(module_path)
                cls = getattr(mod, class_name)
                # Agents only require llm_ensemble; settings/db are not
                # part of the BaseAgent contract.
                agent = cls(llm_ensemble=self._llm_ensemble)
                agents[role] = agent
                logger.debug("AgentCoordinator: built agent %s (%s)", role, class_name)
            except Exception as exc:
                logger.warning(
                    "AgentCoordinator: could not load agent %s (%s.%s): %s — using stub",
                    role, module_path, class_name, exc,
                )
                agents[role] = _StubAgent(role=role)

        return agents


# ---------------------------------------------------------------------------
# Fallback stubs (used when agents cannot be loaded)
# ---------------------------------------------------------------------------

class _StubAgent:
    """Minimal stub agent that returns a NEUTRAL decision."""

    def __init__(self, role: str) -> None:
        self.role = role
        self.market = "crypto"

    async def analyze(self, market_data: Dict[str, Any], context: Dict[str, Any]) -> Any:
        from src.agents.base_agent import AgentDecision, AgentOutput
        from datetime import datetime, timezone
        return AgentOutput(
            agent_role=self.role,
            decision=AgentDecision.NEUTRAL,
            confidence=0.3,
            reasoning=f"Stub agent {self.role} — module unavailable",
            key_factors=["stub"],
            data_used=[],
            model_used="stub",
            latency_ms=0.0,
            timestamp=datetime.now(tz=timezone.utc),
        )


class _NeutralDebateResult:
    """Minimal stub DebateResult returned when debate crashes."""

    def __init__(self, symbol: str) -> None:
        from datetime import datetime, timezone
        self.symbol = symbol
        self.consensus_score = 0.0
        self.was_vetoed = True
        self.veto_reason = "debate_engine_error"
        self.conflict_areas: list = []
        self.winning_argument = ""
        self.debate_duration_ms = 0.0
        self.timestamp = datetime.now(tz=timezone.utc)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "consensus_score": self.consensus_score,
            "was_vetoed": self.was_vetoed,
            "veto_reason": self.veto_reason,
        }
