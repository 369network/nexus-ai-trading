# src/agents/risk_manager.py
"""Risk Manager Agent — has VETO power over all trade signals."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base_agent import AgentDecision, AgentOutput, BaseAgent
from ..llm.prompt_templates import AGENT_DECISION_SCHEMA

logger = logging.getLogger(__name__)

RISK_MANAGER_SYSTEM_PROMPT = """You are the Risk Manager for NEXUS ALPHA, a multi-market AI trading system.

You have ABSOLUTE VETO POWER over any trade signal. Your primary obligation is capital preservation.

Core responsibilities:
1. VETO trades that pose unacceptable risk to the portfolio
2. SIZE DOWN positions when risk is elevated but not veto-worthy
3. Block trades that would over-concentrate exposure
4. Prevent trading during high-risk market environments
5. Enforce maximum drawdown limits

VETO conditions (set veto=true):
- Position would bring total portfolio exposure above 80% of capital
- Correlation with existing positions would create hidden concentration risk
- Market regime is HIGH_VOLATILITY without proportional reward
- Circuit breaker triggered in this market in last 24 hours
- Proposed stop loss > 5% from entry (risk per trade too high)
- News event of extreme uncertainty pending (FOMC, NFP, earnings in <30 min)

SIZE REDUCTION conditions (reduce approved_size, do not veto):
- ATR suggests entry is not at an optimal risk/reward point
- Other positions in similar assets already at 50%+ of limit
- Market regime is RANGING but signal expects trending behavior
- Sentiment extreme without technical confirmation

Output format: You MUST respond with valid JSON matching the schema provided.
Additionally, you MUST set:
  - "veto": true/false
  - "veto_reason": explanation if vetoing (null if not vetoing)
  - "approved_size": float 0.0-1.0 (fraction of suggested position size to approve)
  - "decision": your risk assessment (NEUTRAL=approved, SELL/STRONG_SELL=veto)

If you veto: set decision=STRONG_SELL (or SELL), veto=true, approved_size=0.0
If you approve: set decision=NEUTRAL, veto=false, approved_size=1.0 (or reduced fraction)
If you partially approve: decision=SLIGHT_SELL, veto=false, approved_size=0.3-0.8"""


class RiskManagerAgent(BaseAgent):
    """Risk management layer with veto power over all trade signals."""

    def __init__(
        self,
        llm_ensemble,
        market: str = "crypto",
        max_exposure_pct: float = 80.0,
        max_risk_per_trade_pct: float = 2.0,
        max_corr_concentration: float = 0.5,
    ) -> None:
        super().__init__(
            role="risk_manager",
            llm_ensemble=llm_ensemble,
            system_prompt=RISK_MANAGER_SYSTEM_PROMPT,
            market=market,
        )
        self._max_exposure_pct = max_exposure_pct
        self._max_risk_per_trade_pct = max_risk_per_trade_pct
        self._max_corr_concentration = max_corr_concentration

    async def analyze(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> AgentOutput:
        """Evaluate risk for the proposed trade signal.

        Parameters
        ----------
        market_data:
            Standard market data dict.
        context:
            Must include: portfolio, proposed_signal (from PortfolioManager),
            agent_outputs (all other agents), circuit_breaker_history.
        """
        symbol = market_data.get("symbol", "UNKNOWN")
        portfolio = context.get("portfolio", {})
        proposed_signal = context.get("proposed_signal", {})
        circuit_breakers = context.get("circuit_breaker_history", [])
        agent_outputs = context.get("agent_outputs", {})
        indicators = market_data.get("indicators", {})
        onchain = market_data.get("onchain", {})

        # Pre-compute hard rule checks (deterministic, before LLM call)
        hard_veto, hard_reason = self._check_hard_rules(
            portfolio=portfolio,
            proposed_signal=proposed_signal,
            circuit_breakers=circuit_breakers,
            indicators=indicators,
            symbol=symbol,
        )

        if hard_veto:
            logger.warning("[RiskManager] HARD VETO for %s: %s", symbol, hard_reason)
            return AgentOutput(
                agent_role=self.role,
                decision=AgentDecision.STRONG_SELL,
                confidence=0.95,
                reasoning=f"HARD RULE VETO: {hard_reason}",
                key_factors=[hard_reason],
                data_used=["portfolio", "rules"],
                model_used="rules_engine",
                latency_ms=0.0,
                timestamp=__import__("datetime").datetime.now(
                    tz=__import__("datetime").timezone.utc
                ),
                veto=True,
                veto_reason=hard_reason,
                approved_size=0.0,
            )

        # LLM-based risk assessment
        user_prompt = self._build_prompt(
            symbol=symbol,
            portfolio=portfolio,
            proposed_signal=proposed_signal,
            agent_outputs=agent_outputs,
            circuit_breakers=circuit_breakers,
            indicators=indicators,
            onchain=onchain,
        )

        parsed = await self._query_llm(user_prompt)
        output = self._build_risk_output(parsed)

        logger.info(
            "[RiskManager] %s → veto=%s, approved_size=%.2f, reason=%s",
            symbol, output.veto, output.approved_size or 1.0, output.veto_reason or "OK",
        )
        return output

    # ------------------------------------------------------------------
    # Hard rule engine (runs before LLM to save cost on obvious vetoes)
    # ------------------------------------------------------------------

    def _check_hard_rules(
        self,
        portfolio: Dict[str, Any],
        proposed_signal: Dict[str, Any],
        circuit_breakers: List[Dict[str, Any]],
        indicators: Dict[str, Any],
        symbol: str,
    ) -> tuple[bool, Optional[str]]:
        """Return (should_veto, reason) based on deterministic hard rules."""

        # Rule 1: Portfolio exposure limit
        current_exposure = float(portfolio.get("exposure_pct", 0))
        proposed_size_pct = float(proposed_signal.get("size_pct", 5))
        if current_exposure + proposed_size_pct > self._max_exposure_pct:
            return True, (
                f"Exposure limit breach: current {current_exposure:.1f}% + "
                f"proposed {proposed_size_pct:.1f}% = {current_exposure + proposed_size_pct:.1f}% "
                f"(limit: {self._max_exposure_pct:.1f}%)"
            )

        # Rule 2: Circuit breaker in last 24h
        import datetime
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        recent_cbs = [
            cb for cb in circuit_breakers
            if abs((now - _parse_ts(cb.get("timestamp", ""))).total_seconds()) < 86400
            and cb.get("market") == proposed_signal.get("market")
        ]
        if recent_cbs:
            return True, f"Circuit breaker active in this market (last 24h): {recent_cbs[-1].get('reason', 'unknown')}"

        # Rule 3: Stop too far from entry
        entry = float(proposed_signal.get("entry", 0))
        stop = float(proposed_signal.get("stop_loss", 0))
        if entry > 0 and stop > 0:
            risk_pct = abs(entry - stop) / entry * 100
            if risk_pct > self._max_risk_per_trade_pct * 2.5:
                return True, (
                    f"Stop too far from entry: {risk_pct:.1f}% risk "
                    f"(max allowed: {self._max_risk_per_trade_pct * 2.5:.1f}%)"
                )

        return False, None

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        symbol: str,
        portfolio: Dict[str, Any],
        proposed_signal: Dict[str, Any],
        agent_outputs: Dict[str, Any],
        circuit_breakers: List[Dict[str, Any]],
        indicators: Dict[str, Any],
        onchain: Dict[str, Any],
    ) -> str:
        # Agent consensus summary
        decisions = []
        for role, output in agent_outputs.items():
            if hasattr(output, "decision"):
                decisions.append(
                    f"  {role}: {output.decision.value} (conf={output.confidence:.2f})"
                )
        agent_summary = "\n".join(decisions) if decisions else "  No prior agent outputs"

        # Volatility assessment
        atr14 = indicators.get("atr14", 0)
        vol_ratio = indicators.get("volume_ratio", 1.0)
        regime = indicators.get("market_regime", "UNKNOWN")

        # On-chain risk for crypto
        onchain_risk = ""
        if self.market == "crypto" and onchain:
            funding = onchain.get("funding_rate", 0)
            oi_change = onchain.get("oi_change", 0)
            if isinstance(funding, (int, float)) and abs(float(funding)) > 0.05:
                onchain_risk = f"  WARNING: Extreme funding rate {funding}% — high liquidation risk\n"
            if isinstance(oi_change, (int, float)) and abs(float(oi_change)) > 30:
                onchain_risk += f"  WARNING: OI changed {oi_change}% — high leverage risk"

        prompt = f"""RISK MANAGER ASSESSMENT
Symbol: {symbol} | Market: {self.market.upper()}

PROPOSED TRADE SIGNAL:
  Direction    : {proposed_signal.get('direction', 'N/A')}
  Entry Price  : {proposed_signal.get('entry', 'N/A')}
  Stop Loss    : {proposed_signal.get('stop_loss', 'N/A')}
  Take Profit 1: {proposed_signal.get('take_profit_1', 'N/A')}
  Position Size: {proposed_signal.get('size_pct', 'N/A')}%
  Risk/Reward  : {proposed_signal.get('risk_reward', 'N/A')}

PORTFOLIO STATE:
  Total Exposure    : {portfolio.get('exposure_pct', 0):.1f}% of capital
  Open Positions    : {portfolio.get('open_positions', 0)}
  Today's P&L       : {portfolio.get('daily_pnl_pct', 0):.2f}%
  Max Drawdown Today: {portfolio.get('max_drawdown_today', 0):.2f}%
  Capital Available : {portfolio.get('available_capital_pct', 100):.1f}%

AGENT CONSENSUS:
{agent_summary}

MARKET ENVIRONMENT:
  Regime     : {regime}
  ATR(14)    : {atr14}  (stop sizing baseline)
  Volume Ratio: {vol_ratio}x
  Volatility : {indicators.get('vol_percentile', 'N/A')} percentile
{onchain_risk}

CIRCUIT BREAKER HISTORY (last 7 days):
  {len(circuit_breakers)} events | Last: {circuit_breakers[-1].get('reason', 'none') if circuit_breakers else 'none'}

RISK LIMITS:
  Max Portfolio Exposure: {self._max_exposure_pct}%
  Max Risk Per Trade    : {self._max_risk_per_trade_pct}%
  Max Correlation Concentration: {self._max_corr_concentration * 100}%

YOUR PERFORMANCE:
{self._format_recent_performance()}

TASK: Evaluate the risk of the proposed trade.
1. Should this trade be VETOED? If yes, set veto=true and explain why.
2. If approved, what size fraction is appropriate? (1.0 = full size, 0.5 = half)
3. Are there any specific risks the other agents may have overlooked?

{AGENT_DECISION_SCHEMA}
Also include in your JSON:
  "veto": bool,
  "veto_reason": string or null,
  "approved_size": float (0.0-1.0)"""

        return prompt

    # ------------------------------------------------------------------
    # Output parsing
    # ------------------------------------------------------------------

    def _build_risk_output(self, parsed: Dict[str, Any]) -> AgentOutput:
        """Convert parsed LLM response to AgentOutput with risk-specific fields."""
        output = self._build_output(parsed, data_used=["portfolio", "indicators", "onchain"])

        # Override with risk-specific fields
        output.veto = bool(parsed.get("veto", False))
        output.veto_reason = parsed.get("veto_reason")
        approved_size = parsed.get("approved_size", 1.0)
        try:
            output.approved_size = max(0.0, min(1.0, float(approved_size)))
        except (TypeError, ValueError):
            output.approved_size = 1.0

        # If veto flagged, ensure decision reflects it
        if output.veto:
            output.decision = AgentDecision.STRONG_SELL
            output.approved_size = 0.0

        return output


def _parse_ts(ts_str: str):
    """Parse ISO timestamp string, return epoch on failure."""
    import datetime
    try:
        return datetime.datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    except Exception:
        return datetime.datetime.fromtimestamp(0, tz=datetime.timezone.utc)
