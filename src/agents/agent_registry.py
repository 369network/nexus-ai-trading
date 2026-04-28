# src/agents/agent_registry.py
"""Agent registry for managing and accessing NEXUS ALPHA agents."""

from __future__ import annotations

import logging
from typing import Dict, List, Optional

from .base_agent import BaseAgent

logger = logging.getLogger(__name__)


class AgentRegistry:
    """Central registry for all NEXUS ALPHA agent instances.

    Provides lookup by role, health monitoring, and lifecycle management.
    """

    def __init__(self) -> None:
        self._agents: Dict[str, BaseAgent] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, agent: BaseAgent) -> None:
        """Register an agent under its role name.

        Parameters
        ----------
        agent:
            Any :class:`BaseAgent` subclass.  The role is read from
            ``agent.role``.  Overwrites any existing agent with the same role.
        """
        self._agents[agent.role] = agent
        logger.info("AgentRegistry: registered %r as %r", type(agent).__name__, agent.role)

    def register_all(self, agents: List[BaseAgent]) -> None:
        """Register multiple agents at once."""
        for agent in agents:
            self.register(agent)

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get(self, role: str) -> Optional[BaseAgent]:
        """Return the agent for *role*, or None if not registered."""
        return self._agents.get(role)

    def get_all(self) -> Dict[str, BaseAgent]:
        """Return all registered agents as a ``{role: agent}`` dict."""
        return dict(self._agents)

    def get_all_list(self) -> List[BaseAgent]:
        """Return all registered agents as a list."""
        return list(self._agents.values())

    def roles(self) -> List[str]:
        """Return the list of registered role names."""
        return list(self._agents.keys())

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, role: str) -> bool:
        return role in self._agents

    # ------------------------------------------------------------------
    # Health monitoring
    # ------------------------------------------------------------------

    async def health_check(self) -> Dict[str, bool]:
        """Check the health of all registered agents.

        A simple liveness check — returns True for each agent that has
        the ``analyze`` method callable.  Can be extended to call a
        lightweight test query per agent.

        Returns
        -------
        dict
            ``{role: is_healthy}``
        """
        health: Dict[str, bool] = {}
        for role, agent in self._agents.items():
            try:
                # Basic check: agent object is usable
                is_healthy = (
                    callable(getattr(agent, "analyze", None))
                    and agent._ensemble is not None
                )
                health[role] = is_healthy
            except Exception as exc:
                logger.warning("Health check failed for %r: %s", role, exc)
                health[role] = False

        all_healthy = all(health.values())
        status = "OK" if all_healthy else "DEGRADED"
        logger.info(
            "AgentRegistry health: %s (%d/%d agents healthy)",
            status, sum(health.values()), len(health),
        )
        return health

    # ------------------------------------------------------------------
    # Performance management
    # ------------------------------------------------------------------

    def reset_performance_history(self) -> None:
        """Clear performance history for all agents (useful for backtesting)."""
        for agent in self._agents.values():
            if hasattr(agent, "reset_performance_history"):
                agent.reset_performance_history()
        logger.info("AgentRegistry: reset performance history for all agents")

    # ------------------------------------------------------------------
    # Repr
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        roles = ", ".join(self._agents.keys()) or "none"
        return f"AgentRegistry(agents=[{roles}])"
