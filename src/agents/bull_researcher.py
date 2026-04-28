# src/agents/bull_researcher.py
"""Bull Researcher Agent — seeks out and argues bullish catalysts."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base_agent import AgentDecision, AgentOutput, BaseAgent
from ..llm.prompt_templates import AGENT_DECISION_SCHEMA, format_candles, format_news

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt (verbatim from PRD)
# ---------------------------------------------------------------------------

BULL_SYSTEM_PROMPT = """You are the Bull Researcher for NEXUS ALPHA, a sophisticated AI trading system.

Your role is to identify and articulate the strongest BULLISH case for the asset under review.
You are NOT balanced — your job is to be the bull advocate in a structured debate.

Core responsibilities:
1. Find every credible bullish catalyst in the data provided
2. Identify hidden strength signals that bears might miss
3. Highlight accumulation patterns, institutional buying, positive divergences
4. Argue for the most favourable interpretation of ambiguous indicators
5. Identify the best risk/reward entry points for long positions

Your analysis framework:
- TECHNICAL: Look for bullish patterns, oversold conditions, support bounces, trend reversals
- FUNDAMENTAL: Positive growth metrics, undervaluation, improving business conditions
- SENTIMENT: Fear-driven selloffs as buying opportunities, contrarian signals
- ON-CHAIN (crypto): Smart money accumulation, exchange outflows, whale buying
- MACRO: Favourable macro tailwinds, sector rotation into this asset

Output format: You MUST respond with valid JSON matching the schema provided.
Confidence should reflect genuine conviction — not artificially inflated.
If there are no credible bullish catalysts, output NEUTRAL with low confidence.

Remember: Your job is to be a rigorous bull, not a reckless one. Intellectual honesty
about the bull case strength is critical for the debate system to work correctly."""


class BullResearcherAgent(BaseAgent):
    """Identifies and argues bullish catalysts in structured debate format."""

    def __init__(self, llm_ensemble, market: str = "crypto") -> None:
        super().__init__(
            role="bull_researcher",
            llm_ensemble=llm_ensemble,
            system_prompt=BULL_SYSTEM_PROMPT,
            market=market,
        )

    async def analyze(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> AgentOutput:
        """Analyse market data and construct the bull case.

        Parameters
        ----------
        market_data:
            Dict containing: symbol, candles, indicators, news, onchain,
            sentiment_score, fear_greed, multi_timeframe, levels.
        context:
            Dict containing: portfolio, config, prior_agent_outputs.
        """
        symbol = market_data.get("symbol", "UNKNOWN")
        candles = market_data.get("candles", [])
        indicators = market_data.get("indicators", {})
        news = market_data.get("news", [])
        onchain = market_data.get("onchain", {})
        mtf = market_data.get("multi_timeframe", {})
        levels = market_data.get("levels", {})
        sentiment_score = market_data.get("sentiment_score", 0.0)
        fear_greed = market_data.get("fear_greed", 50)
        patterns = market_data.get("patterns", [])

        user_prompt = self._build_prompt(
            symbol=symbol,
            candles=candles,
            indicators=indicators,
            news=news,
            onchain=onchain,
            mtf=mtf,
            levels=levels,
            sentiment_score=sentiment_score,
            fear_greed=fear_greed,
            patterns=patterns,
            context=context,
        )

        parsed = await self._query_llm(user_prompt)
        output = self._build_output(
            parsed,
            data_used=self._build_data_used(market_data),
        )

        self._record_prediction(output.decision, output.confidence)
        logger.info(
            "[BullResearcher] %s → %s (%.2f confidence)",
            symbol, output.decision.value, output.confidence,
        )
        return output

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        symbol: str,
        candles: List[Dict[str, Any]],
        indicators: Dict[str, Any],
        news: List[Dict[str, Any]],
        onchain: Dict[str, Any],
        mtf: Dict[str, Any],
        levels: Dict[str, Any],
        sentiment_score: float,
        fear_greed: int,
        patterns: List[Any],
        context: Dict[str, Any],
    ) -> str:
        ind = indicators

        # Fear/greed interpretation
        if fear_greed <= 25:
            fg_note = "Extreme Fear — historically a contrarian bullish opportunity."
        elif fear_greed <= 40:
            fg_note = "Fear — possible accumulation zone."
        elif fear_greed <= 60:
            fg_note = "Neutral."
        else:
            fg_note = "Greed — momentum may continue but watch for exhaustion."

        # RSI oversold check
        rsi14 = ind.get("rsi14", 50)
        rsi_note = ""
        if float(rsi14) < 30:
            rsi_note = f"RSI({rsi14:.1f}) is OVERSOLD — historically bullish reversal zone."
        elif float(rsi14) < 45:
            rsi_note = f"RSI({rsi14:.1f}) approaching oversold — watch for bounce."

        # On-chain bullish signals
        onchain_bull = []
        if onchain:
            flow = onchain.get("exchange_flow", 0)
            if isinstance(flow, (int, float)) and flow < 0:
                onchain_bull.append(f"Exchange outflow: {flow:.2f} (coins leaving exchanges = bullish)")
            whale = onchain.get("whale_activity", "")
            if "buy" in str(whale).lower() or "accum" in str(whale).lower():
                onchain_bull.append(f"Whale activity: {whale}")
            funding = onchain.get("funding_rate", 0)
            if isinstance(funding, (int, float)) and funding < -0.01:
                onchain_bull.append(f"Negative funding rate ({funding:.4f}%) — shorts paying longs (contrarian bullish)")

        onchain_str = "\n".join(onchain_bull) if onchain_bull else "No notable on-chain bullish signals."

        # Support proximity
        nearest_support = levels.get("nearest_support", 0)
        dist_support = levels.get("dist_support_pct", 0)
        support_note = (
            f"Price is {dist_support:.2f}% from nearest support at {nearest_support:.6g}"
            if nearest_support else "No nearby support identified."
        )

        # Pattern summary
        bull_patterns = [p for p in patterns if getattr(p, "direction", "") == "BULLISH"]
        pattern_str = (
            ", ".join(p.name for p in bull_patterns)
            if bull_patterns else "No confirmed bullish chart patterns."
        )

        # MTF bias
        overall_bias = mtf.get("overall_bias", "N/A") if isinstance(mtf, dict) else "N/A"
        mtf_str = (
            f"Weekly: {mtf.get('trend_weekly', 'N/A')} | "
            f"Daily: {mtf.get('trend_daily', 'N/A')} | "
            f"4H: {mtf.get('trend_4h', 'N/A')} | "
            f"Overall: {overall_bias}"
        ) if isinstance(mtf, dict) else str(mtf)

        portfolio = context.get("portfolio", {})
        performance_summary = self._format_recent_performance()

        prompt = f"""BULL RESEARCHER ANALYSIS REQUEST
Symbol: {symbol} | Market: {self.market.upper()}

{self._format_price_summary(candles)}

KEY TECHNICAL INDICATORS:
  RSI(14)={ind.get('rsi14', 'N/A')}  RSI(7)={ind.get('rsi7', 'N/A')}
  MACD: Line={ind.get('macd_line', 'N/A')} Signal={ind.get('macd_signal', 'N/A')} Hist={ind.get('macd_hist', 'N/A')}
  BB %B={ind.get('bb_pct', 'N/A')}  (below 0.2 = near lower band = bullish entry)
  Stoch K={ind.get('stoch_k', 'N/A')}  Williams %R={ind.get('williams_r', 'N/A')}
  ADX={ind.get('adx', 'N/A')}  Volume Ratio={ind.get('volume_ratio', 'N/A')}x
  VWAP={ind.get('vwap', 'N/A')}  Price vs SMA20={ind.get('price_vs_sma20', 'N/A')}%

RSI NOTE: {rsi_note if rsi_note else 'RSI neutral'}

BULLISH CHART PATTERNS: {pattern_str}

MULTI-TIMEFRAME BIAS:
{mtf_str}

KEY LEVELS:
  Nearest Support : {support_note}
  Fibonacci Levels: {levels.get('fibonacci', 'N/A')}

MARKET SENTIMENT:
  Sentiment Score: {sentiment_score:.3f} (-1=bearish, +1=bullish)
  Fear & Greed   : {fear_greed}/100 — {fg_note}

NEWS (sorted by impact):
{format_news(news)}

ON-CHAIN BULLISH SIGNALS:
{onchain_str}

PORTFOLIO CONTEXT:
  Current Exposure: {portfolio.get('exposure_pct', 0):.1f}%
  Open Positions  : {portfolio.get('open_positions', 0)}

YOUR PERFORMANCE:
{performance_summary}

TASK: As the Bull Researcher, identify ALL credible bullish catalysts for {symbol}.
Argue the strongest bull case you can build from this data.
Be intellectually honest — only cite genuine signals, not wishful thinking.
If the bull case is weak, say so with appropriate low confidence.

{AGENT_DECISION_SCHEMA}"""

        return prompt

    @staticmethod
    def _build_data_used(market_data: Dict[str, Any]) -> List[str]:
        used = ["candles", "indicators"]
        if market_data.get("news"):
            used.append("news")
        if market_data.get("onchain"):
            used.append("onchain")
        if market_data.get("sentiment_score") is not None:
            used.append("sentiment")
        if market_data.get("multi_timeframe"):
            used.append("multi_timeframe")
        return used
