# src/agents/portfolio_manager.py
"""Portfolio Manager Agent — makes final trade decisions."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base_agent import AgentDecision, AgentOutput, BaseAgent
from ..llm.prompt_templates import AGENT_DECISION_SCHEMA

logger = logging.getLogger(__name__)

PORTFOLIO_MANAGER_SYSTEM_PROMPT = """You are the Portfolio Manager for NEXUS ALPHA, a multi-market AI trading system.

You make the FINAL trade decision after reviewing all agent outputs.
You cannot override the Risk Manager's veto.

Your role:
1. Synthesise all agent outputs (bull, bear, fundamental, technical, sentiment)
2. Resolve conflicts using weighted confidence
3. Determine final direction, size, entry, and exit levels
4. Consider portfolio-level constraints (correlation, concentration, drawdown)
5. Apply Kelly Criterion or fixed fractional position sizing

Decision framework:
- If Risk Manager has VETOED: return NEUTRAL with 0 position size (never override veto)
- If 4/5 agents agree with high confidence (>0.65): follow consensus with full size
- If agents are split (bull vs bear both >0.7 confidence): return NEUTRAL — conflicting signals
- If technical + fundamental align: increase confidence weight by 20%
- If sentiment is at extreme (contrarian): can override directional agents with 40% weight cap

Weighting system (default):
  Bull Researcher    : 1.0x
  Bear Researcher    : 1.0x
  Fundamental Analyst: 1.5x
  Technical Analyst  : 1.5x
  Sentiment Analyst  : 1.0x
  (weights adjust based on Brier scores)

Position sizing:
  - Base size: 2-5% of portfolio
  - Scale by conviction: confidence × base_size
  - Cap at risk manager's approved_size
  - Reduce if multiple open positions in correlated assets

Output format: You MUST respond with valid JSON matching the schema.
Provide specific entry, stop, and TP levels — do not leave these as null when you have the data."""


class PortfolioManagerAgent(BaseAgent):
    """Synthesises all agent outputs and makes the final trade decision."""

    def __init__(
        self,
        llm_ensemble,
        market: str = "crypto",
        agent_weights: Optional[Dict[str, float]] = None,
    ) -> None:
        super().__init__(
            role="portfolio_manager",
            llm_ensemble=llm_ensemble,
            system_prompt=PORTFOLIO_MANAGER_SYSTEM_PROMPT,
            market=market,
        )
        self._agent_weights = agent_weights or {
            "bull_researcher": 1.0,
            "bear_researcher": 1.0,
            "fundamental_analyst": 1.5,
            "technical_analyst": 1.5,
            "sentiment_analyst": 1.0,
        }

    async def analyze(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> AgentOutput:
        """Synthesise agent outputs and make the final trade decision."""
        symbol = market_data.get("symbol", "UNKNOWN")
        agent_outputs: Dict[str, AgentOutput] = context.get("agent_outputs", {})
        portfolio = context.get("portfolio", {})
        risk_output: Optional[AgentOutput] = agent_outputs.get("risk_manager")

        # Hard constraint: respect Risk Manager veto
        if risk_output and risk_output.veto:
            logger.info(
                "[PortfolioManager] Respecting Risk Manager veto for %s: %s",
                symbol, risk_output.veto_reason,
            )
            return AgentOutput(
                agent_role=self.role,
                decision=AgentDecision.NEUTRAL,
                confidence=1.0,
                reasoning=f"Risk Manager veto respected: {risk_output.veto_reason}",
                key_factors=["risk_manager_veto"],
                data_used=["risk_manager"],
                model_used="rules_engine",
                latency_ms=0.0,
                timestamp=__import__("datetime").datetime.now(
                    tz=__import__("datetime").timezone.utc
                ),
                approved_size=0.0,
                veto=False,  # PM doesn't veto — it just follows RM
            )

        # Check for conflicting signals (bull and bear both high confidence)
        conflict = self._check_conflict(agent_outputs)
        if conflict:
            logger.info(
                "[PortfolioManager] Conflicting signals for %s — returning NEUTRAL",
                symbol,
            )

        # Compute weighted consensus
        weighted_direction = self._compute_weighted_direction(agent_outputs)

        user_prompt = self._build_prompt(
            symbol=symbol,
            agent_outputs=agent_outputs,
            portfolio=portfolio,
            risk_output=risk_output,
            weighted_direction=weighted_direction,
            conflict=conflict,
            context=context,
        )

        parsed = await self._query_llm(user_prompt)
        output = self._build_output(
            parsed,
            data_used=list(agent_outputs.keys()),
        )

        # Apply risk manager's approved size
        if risk_output and risk_output.approved_size is not None:
            output.approved_size = risk_output.approved_size

        # Force NEUTRAL if conflict detected
        if conflict:
            output.decision = AgentDecision.NEUTRAL
            output.confidence = max(0.3, output.confidence * 0.5)
            output.reasoning = f"[CONFLICT DETECTED] {output.reasoning}"

        self._record_prediction(output.decision, output.confidence)
        logger.info(
            "[PortfolioManager] %s → %s (%.2f confidence, approved_size=%.2f)",
            symbol, output.decision.value, output.confidence,
            output.approved_size or 1.0,
        )
        return output

    # ------------------------------------------------------------------
    # Conflict detection
    # ------------------------------------------------------------------

    def _check_conflict(self, agent_outputs: Dict[str, AgentOutput]) -> bool:
        """Return True if bull and bear researchers both have high confidence."""
        bull = agent_outputs.get("bull_researcher")
        bear = agent_outputs.get("bear_researcher")
        if bull and bear:
            bull_bullish = bull.decision in (
                AgentDecision.STRONG_BUY, AgentDecision.BUY
            )
            bear_bearish = bear.decision in (
                AgentDecision.STRONG_SELL, AgentDecision.SELL
            )
            if bull_bullish and bull.confidence > 0.7 and bear_bearish and bear.confidence > 0.7:
                return True
        return False

    # ------------------------------------------------------------------
    # Weighted direction calculation
    # ------------------------------------------------------------------

    def _compute_weighted_direction(
        self, agent_outputs: Dict[str, AgentOutput]
    ) -> Dict[str, Any]:
        """Compute a weighted directional signal from all agent outputs."""
        total_weight = 0.0
        weighted_sum = 0.0
        weighted_confidence = 0.0

        for role, output in agent_outputs.items():
            if role == "risk_manager":
                continue
            weight = self._agent_weights.get(role, 1.0)
            signal = output.decision.numeric  # -1.0 to +1.0
            weighted_sum += signal * weight * output.confidence
            weighted_confidence += output.confidence * weight
            total_weight += weight

        if total_weight == 0:
            return {"signal": 0.0, "confidence": 0.3, "direction": "NEUTRAL"}

        norm_signal = weighted_sum / total_weight
        avg_confidence = weighted_confidence / total_weight

        if norm_signal > 0.5:
            direction = "STRONG_BUY"
        elif norm_signal > 0.2:
            direction = "BUY"
        elif norm_signal > 0.05:
            direction = "SLIGHT_BUY"
        elif norm_signal < -0.5:
            direction = "STRONG_SELL"
        elif norm_signal < -0.2:
            direction = "SELL"
        elif norm_signal < -0.05:
            direction = "SLIGHT_SELL"
        else:
            direction = "NEUTRAL"

        return {
            "signal": norm_signal,
            "confidence": avg_confidence,
            "direction": direction,
        }

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        symbol: str,
        agent_outputs: Dict[str, AgentOutput],
        portfolio: Dict[str, Any],
        risk_output: Optional[AgentOutput],
        weighted_direction: Dict[str, Any],
        conflict: bool,
        context: Dict[str, Any],
    ) -> str:
        # Agent output summary
        agent_lines = []
        for role, output in agent_outputs.items():
            if role == "risk_manager":
                continue
            weight = self._agent_weights.get(role, 1.0)
            agent_lines.append(
                f"  {role} (weight {weight}x): {output.decision.value} "
                f"(conf={output.confidence:.2f}) — {output.reasoning[:100]}..."
            )
        agent_summary = "\n".join(agent_lines) if agent_lines else "  No agent outputs"

        # Risk manager summary
        rm_summary = "  No Risk Manager output"
        if risk_output:
            rm_summary = (
                f"  Veto: {risk_output.veto} | "
                f"Approved Size: {risk_output.approved_size:.0%} | "
                f"Reason: {risk_output.veto_reason or 'OK'}"
            )

        # Technical analyst's trade plan (most specific levels)
        ta_output = agent_outputs.get("technical_analyst")
        ta_plan = ""
        if ta_output:
            ta_plan = (
                f"  TA Entry: {ta_output.entry_price} | "
                f"Stop: {ta_output.stop_loss} | "
                f"TP1: {ta_output.take_profit_1} | "
                f"TP2: {ta_output.take_profit_2} | "
                f"TP3: {ta_output.take_profit_3}"
            )

        conflict_note = (
            "\nWARNING: Bull and Bear researchers have conflicting high-confidence signals. "
            "Output NEUTRAL unless other agents provide clear resolution."
            if conflict else ""
        )

        prompt = f"""PORTFOLIO MANAGER FINAL DECISION
Symbol: {symbol} | Market: {self.market.upper()}
{conflict_note}

AGENT OUTPUTS:
{agent_summary}

WEIGHTED CONSENSUS SIGNAL:
  Direction  : {weighted_direction['direction']}
  Net Signal : {weighted_direction['signal']:.3f} (-1=full short, +1=full long)
  Avg Confidence: {weighted_direction['confidence']:.2f}

RISK MANAGER ASSESSMENT:
{rm_summary}

TECHNICAL ANALYST'S TRADE PLAN:
{ta_plan if ta_plan else '  No specific levels provided'}

PORTFOLIO STATE:
  Total Exposure  : {portfolio.get('exposure_pct', 0):.1f}%
  Open Positions  : {portfolio.get('open_positions', 0)}
  Capital Available: {portfolio.get('available_capital_pct', 100):.1f}%
  Daily P&L       : {portfolio.get('daily_pnl_pct', 0):.2f}%

POSITION SIZING GUIDANCE:
  Base Size: 2-5% of portfolio
  Scale by conviction (confidence × base)
  Never exceed Risk Manager's approved_size: {risk_output.approved_size if risk_output else 1.0:.0%}

YOUR PERFORMANCE:
{self._format_recent_performance()}

TASK: Make the FINAL trade decision for {symbol}.
- Review all agent outputs and resolve any conflicts
- Provide specific entry, stop loss, and take profit levels
- Set position size within approved limits
- If agents disagree significantly, reduce confidence and size
- NEVER override a Risk Manager veto

{AGENT_DECISION_SCHEMA}"""

        return prompt
