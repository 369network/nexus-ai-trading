# src/agents/base_agent.py
"""Abstract base agent for NEXUS ALPHA multi-agent debate system."""

from __future__ import annotations

import json
import logging
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# Maximum number of recent calls tracked for Brier calibration
PERFORMANCE_HISTORY_SIZE = 50


class AgentDecision(Enum):
    """Directional decision outcomes for an agent."""

    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    SLIGHT_BUY = "SLIGHT_BUY"
    NEUTRAL = "NEUTRAL"
    SLIGHT_SELL = "SLIGHT_SELL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"

    @property
    def numeric(self) -> float:
        """Map decision to numeric signal (-1.0 to +1.0)."""
        mapping = {
            "STRONG_BUY": 1.0,
            "BUY": 0.6,
            "SLIGHT_BUY": 0.3,
            "NEUTRAL": 0.0,
            "SLIGHT_SELL": -0.3,
            "SELL": -0.6,
            "STRONG_SELL": -1.0,
        }
        return mapping[self.value]

    @property
    def is_long(self) -> bool:
        return self in (AgentDecision.STRONG_BUY, AgentDecision.BUY, AgentDecision.SLIGHT_BUY)

    @property
    def is_short(self) -> bool:
        return self in (AgentDecision.STRONG_SELL, AgentDecision.SELL, AgentDecision.SLIGHT_SELL)


@dataclass
class AgentOutput:
    """Standardised output returned by every agent."""

    agent_role: str
    decision: AgentDecision
    confidence: float             # 0.0–1.0
    reasoning: str
    key_factors: List[str]
    data_used: List[str]          # which data sources influenced this call
    model_used: str
    latency_ms: float
    timestamp: datetime

    # Optional structured trade plan
    entry_price: Optional[float] = None
    stop_loss: Optional[float] = None
    take_profit_1: Optional[float] = None
    take_profit_2: Optional[float] = None
    take_profit_3: Optional[float] = None
    risk_reward: Optional[float] = None
    timeframe: Optional[str] = None
    invalidation: Optional[str] = None

    # Risk manager specific
    approved_size: Optional[float] = None   # fraction of suggested size
    veto: bool = False
    veto_reason: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_role": self.agent_role,
            "decision": self.decision.value,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "key_factors": self.key_factors,
            "data_used": self.data_used,
            "model_used": self.model_used,
            "latency_ms": self.latency_ms,
            "timestamp": self.timestamp.isoformat(),
            "entry_price": self.entry_price,
            "stop_loss": self.stop_loss,
            "take_profit_1": self.take_profit_1,
            "take_profit_2": self.take_profit_2,
            "take_profit_3": self.take_profit_3,
            "risk_reward": self.risk_reward,
            "timeframe": self.timeframe,
            "invalidation": self.invalidation,
            "veto": self.veto,
            "veto_reason": self.veto_reason,
            "approved_size": self.approved_size,
        }


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class BaseAgent(ABC):
    """Abstract base for all NEXUS ALPHA agents.

    Subclasses implement :meth:`analyze`.  The base provides:
    - LLM querying with JSON extraction and retry
    - Performance tracking for Brier calibration
    - Common prompt formatting helpers
    """

    def __init__(
        self,
        role: str,
        llm_ensemble,       # LLMEnsemble — avoid circular import with string hint
        system_prompt: str,
        market: str = "crypto",
    ) -> None:
        self.role = role
        self._ensemble = llm_ensemble
        self._system_prompt = system_prompt
        self.market = market

        # Rolling performance history (last PERFORMANCE_HISTORY_SIZE calls)
        self._performance_history: Deque[Dict[str, Any]] = deque(
            maxlen=PERFORMANCE_HISTORY_SIZE
        )

    # ------------------------------------------------------------------
    # Abstract interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def analyze(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> AgentOutput:
        """Analyse market data and return an :class:`AgentOutput`.

        Parameters
        ----------
        market_data:
            Dict containing candles, indicators, news, on-chain data etc.
        context:
            Shared context: portfolio state, prior agent outputs, config.
        """

    # ------------------------------------------------------------------
    # LLM querying
    # ------------------------------------------------------------------

    async def _query_llm(
        self, user_prompt: str, market: Optional[str] = None
    ) -> Dict[str, Any]:
        """Send a query to the LLM ensemble and parse the JSON response.

        Returns the parsed dict, or a safe default if parsing fails.
        """
        t0 = time.monotonic()
        target_market = market or self.market

        try:
            response = await self._ensemble.query(
                system_prompt=self._system_prompt,
                user_prompt=user_prompt,
                market=target_market,
            )
            latency_ms = (time.monotonic() - t0) * 1000

            parsed = self._extract_json(response.text)
            parsed["_model_used"] = response.model
            parsed["_latency_ms"] = latency_ms
            return parsed

        except Exception as exc:
            logger.error("[%s] LLM query failed: %s", self.role, exc)
            return self._safe_default_response()

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extract and parse the first JSON object from *text*.

        Handles code fences, partial text before/after the JSON block, etc.
        """
        # Strip markdown code fences
        text = re.sub(r"```(?:json)?", "", text).strip()

        # Try direct parse first
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Find first { ... } block
        start = text.find("{")
        end = text.rfind("}") + 1
        if start >= 0 and end > start:
            try:
                return json.loads(text[start:end])
            except json.JSONDecodeError:
                pass

        logger.warning("[%s] Could not parse LLM JSON response: %s…", self.role, text[:200])
        return self._safe_default_response()

    @staticmethod
    def _safe_default_response() -> Dict[str, Any]:
        return {
            "decision": "NEUTRAL",
            "confidence": 0.3,
            "reasoning": "Unable to parse LLM response",
            "key_factors": [],
            "entry_price": None,
            "stop_loss": None,
            "take_profit_1": None,
            "take_profit_2": None,
            "take_profit_3": None,
            "risk_reward": None,
            "timeframe": None,
            "invalidation": None,
            "data_quality": 0.5,
        }

    # ------------------------------------------------------------------
    # Output construction helpers
    # ------------------------------------------------------------------

    def _build_output(
        self, parsed: Dict[str, Any], data_used: List[str]
    ) -> AgentOutput:
        """Convert a parsed LLM response dict into an :class:`AgentOutput`."""
        decision_str = str(parsed.get("decision", "NEUTRAL")).upper()
        try:
            decision = AgentDecision[decision_str]
        except KeyError:
            decision = AgentDecision.NEUTRAL

        confidence = float(parsed.get("confidence", 0.3))
        confidence = max(0.0, min(1.0, confidence))

        return AgentOutput(
            agent_role=self.role,
            decision=decision,
            confidence=confidence,
            reasoning=str(parsed.get("reasoning", "")),
            key_factors=list(parsed.get("key_factors", [])),
            data_used=data_used,
            model_used=str(parsed.get("_model_used", "unknown")),
            latency_ms=float(parsed.get("_latency_ms", 0)),
            timestamp=datetime.now(tz=timezone.utc),
            entry_price=parsed.get("entry_price"),
            stop_loss=parsed.get("stop_loss"),
            take_profit_1=parsed.get("take_profit_1"),
            take_profit_2=parsed.get("take_profit_2"),
            take_profit_3=parsed.get("take_profit_3"),
            risk_reward=parsed.get("risk_reward"),
            timeframe=parsed.get("timeframe"),
            invalidation=parsed.get("invalidation"),
        )

    # ------------------------------------------------------------------
    # Prompt formatting helpers
    # ------------------------------------------------------------------

    def _format_price_summary(self, candles: List[Dict[str, Any]]) -> str:
        """Format a concise OHLCV price summary from a list of candles."""
        if not candles:
            return "No price data available."

        recent = candles[-5:]
        lines = ["Recent price action (last 5 candles):"]
        for c in recent:
            ts = str(c.get("timestamp", c.get("time", "")))[:16]
            o = c.get("open", 0)
            h = c.get("high", 0)
            lo = c.get("low", 0)
            cl = c.get("close", 0)
            v = c.get("volume", 0)
            pct = ((cl - o) / o * 100) if o > 0 else 0
            lines.append(
                f"  {ts}  O={o:.4g} H={h:.4g} L={lo:.4g} C={cl:.4g} "
                f"({'+'if pct>=0 else ''}{pct:.2f}%)  V={v:.3g}"
            )

        if len(candles) >= 2:
            first_close = candles[0].get("close", 0)
            last_close = candles[-1].get("close", 0)
            if first_close > 0:
                total_pct = (last_close - first_close) / first_close * 100
                lines.append(f"  Total move over {len(candles)} bars: {total_pct:+.2f}%")

        return "\n".join(lines)

    def _format_recent_performance(self) -> str:
        """Summarise this agent's recent prediction accuracy."""
        history = list(self._performance_history)
        if not history:
            return "No performance history available."

        resolved = [h for h in history if "outcome" in h]
        if not resolved:
            return f"Performance history: {len(history)} predictions, none resolved yet."

        correct = sum(1 for h in resolved if h.get("correct", False))
        accuracy = correct / len(resolved) * 100

        avg_confidence = sum(h.get("confidence", 0.5) for h in resolved) / len(resolved)

        return (
            f"Recent performance: {accuracy:.1f}% accuracy over {len(resolved)} resolved calls "
            f"(avg confidence: {avg_confidence:.2f})"
        )

    # ------------------------------------------------------------------
    # Brier score tracking
    # ------------------------------------------------------------------

    def record_outcome(
        self,
        decision: AgentDecision,
        confidence: float,
        outcome: bool,
    ) -> None:
        """Update the performance history with a resolved outcome.

        Parameters
        ----------
        decision:
            The decision that was made.
        confidence:
            Confidence level at the time of the decision.
        outcome:
            True if the prediction was directionally correct.
        """
        history = list(self._performance_history)

        # Find the most recent unresolved prediction matching this decision
        for entry in reversed(history):
            if "outcome" not in entry and entry.get("decision") == decision.value:
                entry["outcome"] = outcome
                entry["correct"] = outcome
                entry["brier_score"] = (confidence - float(outcome)) ** 2
                break

    def _record_prediction(self, decision: AgentDecision, confidence: float) -> None:
        """Add a new prediction to the rolling history."""
        self._performance_history.append({
            "decision": decision.value,
            "confidence": confidence,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        })

    def reset_performance_history(self) -> None:
        """Clear the performance history (useful for testing)."""
        self._performance_history.clear()

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(role={self.role!r}, market={self.market!r})"
