# src/agents/bear_researcher.py
"""Bear Researcher Agent — seeks out and argues bearish catalysts."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base_agent import AgentDecision, AgentOutput, BaseAgent
from ..llm.prompt_templates import AGENT_DECISION_SCHEMA, format_candles, format_news

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

BEAR_SYSTEM_PROMPT = """You are the Bear Researcher for NEXUS ALPHA, a sophisticated AI trading system.

Your role is to identify and articulate the strongest BEARISH case for the asset under review.
You are NOT balanced — your job is to be the bear advocate in a structured debate.

Core responsibilities:
1. Find every credible bearish risk in the data provided
2. Identify hidden weakness signals that bulls might overlook
3. Highlight distribution patterns, institutional selling, negative divergences
4. Argue for the most unfavourable interpretation of ambiguous indicators
5. Identify the best risk/reward entry points for short positions

Your analysis framework:
- TECHNICAL: Look for bearish patterns, overbought conditions, resistance rejections, trend failures
- FUNDAMENTAL: Deteriorating metrics, overvaluation, worsening business conditions
- SENTIMENT: Greed-driven rallies as distribution zones, euphoria as contrarian sell signals
- ON-CHAIN (crypto): Smart money distribution, exchange inflows, whale selling pressure
- MACRO: Adverse macro headwinds, sector rotation away from this asset

Output format: You MUST respond with valid JSON matching the schema provided.
Confidence should reflect genuine conviction — not artificially inflated.
If there are no credible bearish catalysts, output NEUTRAL with low confidence.

Remember: Your job is to be a rigorous bear, not a reckless one. Intellectual honesty
about the bear case strength is critical for the debate system to work correctly."""


class BearResearcherAgent(BaseAgent):
    """Identifies and argues bearish catalysts in structured debate format."""

    def __init__(self, llm_ensemble, market: str = "crypto") -> None:
        super().__init__(
            role="bear_researcher",
            llm_ensemble=llm_ensemble,
            system_prompt=BEAR_SYSTEM_PROMPT,
            market=market,
        )

    async def analyze(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> AgentOutput:
        """Analyse market data and construct the bear case."""
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
            "[BearResearcher] %s → %s (%.2f confidence)",
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

        # Fear/greed bearish interpretation
        if fear_greed >= 75:
            fg_note = "Extreme Greed — historically a contrarian bearish signal."
        elif fear_greed >= 60:
            fg_note = "Greed — distribution zone, potential for sharp reversal."
        elif fear_greed >= 40:
            fg_note = "Neutral."
        else:
            fg_note = "Fear — momentum down may continue."

        # RSI overbought check
        rsi14 = ind.get("rsi14", 50)
        rsi_note = ""
        if float(rsi14) > 70:
            rsi_note = f"RSI({rsi14:.1f}) is OVERBOUGHT — historically bearish reversal zone."
        elif float(rsi14) > 60:
            rsi_note = f"RSI({rsi14:.1f}) approaching overbought — watch for exhaustion."

        # On-chain bearish signals
        onchain_bear = []
        if onchain:
            flow = onchain.get("exchange_flow", 0)
            if isinstance(flow, (int, float)) and flow > 0:
                onchain_bear.append(f"Exchange inflow: {flow:.2f} (coins moving to exchanges = bearish)")
            whale = onchain.get("whale_activity", "")
            if "sell" in str(whale).lower() or "dist" in str(whale).lower():
                onchain_bear.append(f"Whale activity: {whale}")
            funding = onchain.get("funding_rate", 0)
            if isinstance(funding, (int, float)) and funding > 0.03:
                onchain_bear.append(
                    f"High funding rate ({funding:.4f}%) — longs paying shorts (bearish for continuation)"
                )
            oi_change = onchain.get("oi_change", 0)
            if isinstance(oi_change, (int, float)) and oi_change > 20:
                onchain_bear.append(f"OI spiked {oi_change:.1f}% — overleveraged long risk")

        onchain_str = "\n".join(onchain_bear) if onchain_bear else "No notable on-chain bearish signals."

        # Resistance proximity
        nearest_resistance = levels.get("nearest_resistance", 0)
        dist_resistance = levels.get("dist_resistance_pct", 0)
        resistance_note = (
            f"Price is {dist_resistance:.2f}% from nearest resistance at {nearest_resistance:.6g}"
            if nearest_resistance else "No nearby resistance identified."
        )

        # Bearish patterns
        bear_patterns = [p for p in patterns if getattr(p, "direction", "") == "BEARISH"]
        pattern_str = (
            ", ".join(p.name for p in bear_patterns)
            if bear_patterns else "No confirmed bearish chart patterns."
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

        prompt = f"""BEAR RESEARCHER ANALYSIS REQUEST
Symbol: {symbol} | Market: {self.market.upper()}

{self._format_price_summary(candles)}

KEY TECHNICAL INDICATORS:
  RSI(14)={ind.get('rsi14', 'N/A')}  RSI(7)={ind.get('rsi7', 'N/A')}
  MACD: Line={ind.get('macd_line', 'N/A')} Signal={ind.get('macd_signal', 'N/A')} Hist={ind.get('macd_hist', 'N/A')}
  BB %B={ind.get('bb_pct', 'N/A')}  (above 0.8 = near upper band = bearish entry)
  Stoch K={ind.get('stoch_k', 'N/A')}  Williams %R={ind.get('williams_r', 'N/A')}
  ADX={ind.get('adx', 'N/A')}  Volume Ratio={ind.get('volume_ratio', 'N/A')}x
  VWAP={ind.get('vwap', 'N/A')}  Price vs SMA20={ind.get('price_vs_sma20', 'N/A')}%

RSI NOTE: {rsi_note if rsi_note else 'RSI neutral'}

BEARISH CHART PATTERNS: {pattern_str}

MULTI-TIMEFRAME BIAS:
{mtf_str}

KEY LEVELS:
  Nearest Resistance: {resistance_note}
  Fibonacci Levels  : {levels.get('fibonacci', 'N/A')}

MARKET SENTIMENT:
  Sentiment Score: {sentiment_score:.3f} (-1=bearish, +1=bullish)
  Fear & Greed   : {fear_greed}/100 — {fg_note}

NEWS (sorted by impact):
{format_news(news)}

ON-CHAIN BEARISH SIGNALS:
{onchain_str}

PORTFOLIO CONTEXT:
  Current Exposure: {portfolio.get('exposure_pct', 0):.1f}%
  Open Positions  : {portfolio.get('open_positions', 0)}

YOUR PERFORMANCE:
{performance_summary}

TASK: As the Bear Researcher, identify ALL credible bearish risks for {symbol}.
Argue the strongest bear case you can build from this data.
Be intellectually honest — only cite genuine risks, not manufactured fears.
If the bear case is weak, say so with appropriate low confidence.

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
