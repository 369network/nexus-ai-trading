# src/agents/sentiment_analyst.py
"""Sentiment Analyst Agent — news, social, on-chain, and positioning sentiment."""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from .base_agent import AgentDecision, AgentOutput, BaseAgent
from ..llm.prompt_templates import AGENT_DECISION_SCHEMA, format_news

logger = logging.getLogger(__name__)

SENTIMENT_SYSTEM_PROMPT = """You are the Sentiment Analyst for NEXUS ALPHA, a multi-market AI trading system.

Your role is to assess market sentiment from multiple sources and identify:
1. Crowd psychology and positioning
2. News flow impact and catalysts
3. Social media sentiment and trends
4. On-chain sentiment (for crypto)
5. Contrarian signals (extreme sentiment as fade opportunity)

Key principles:
- Extreme fear = potential bullish contrarian signal
- Extreme greed = potential bearish contrarian signal
- But sustained sentiment can reinforce trends (don't fade early in a trend)
- Weight recent high-impact news more heavily than older low-impact news
- Distinguish between price-relevant and noise news
- Social sentiment leads price action by 1-4 hours in crypto
- For stocks, earnings/guidance surprise is the key driver

Your output should specify:
- The dominant sentiment (bullish/bearish/neutral)
- Confidence in the sentiment reading
- Whether the sentiment is extreme enough to be contrarian
- Key events/news that are driving sentiment

Output format: You MUST respond with valid JSON matching the schema provided."""


class SentimentAnalystAgent(BaseAgent):
    """Analyses multi-source sentiment data for trading signals."""

    def __init__(self, llm_ensemble, market: str = "crypto") -> None:
        super().__init__(
            role="sentiment_analyst",
            llm_ensemble=llm_ensemble,
            system_prompt=SENTIMENT_SYSTEM_PROMPT,
            market=market,
        )

    async def analyze(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> AgentOutput:
        """Analyse sentiment signals from all available sources."""
        symbol = market_data.get("symbol", "UNKNOWN")
        news = market_data.get("news", [])
        sentiment_score = market_data.get("sentiment_score", 0.0)
        fear_greed = market_data.get("fear_greed", 50)
        social = market_data.get("social", {})
        onchain = market_data.get("onchain", {})
        positioning = market_data.get("positioning", {})

        user_prompt = self._build_prompt(
            symbol=symbol,
            news=news,
            sentiment_score=sentiment_score,
            fear_greed=fear_greed,
            social=social,
            onchain=onchain,
            positioning=positioning,
            context=context,
        )

        parsed = await self._query_llm(user_prompt)
        output = self._build_output(
            parsed,
            data_used=self._build_data_used(market_data),
        )

        self._record_prediction(output.decision, output.confidence)
        logger.info(
            "[SentimentAnalyst] %s → %s (%.2f confidence, fg=%s)",
            symbol, output.decision.value, output.confidence, fear_greed,
        )
        return output

    # ------------------------------------------------------------------
    # Prompt construction
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        symbol: str,
        news: List[Dict[str, Any]],
        sentiment_score: float,
        fear_greed: int,
        social: Dict[str, Any],
        onchain: Dict[str, Any],
        positioning: Dict[str, Any],
        context: Dict[str, Any],
    ) -> str:
        # Fear/greed interpretation
        if fear_greed <= 15:
            fg_label = "EXTREME FEAR"
            fg_signal = "CONTRARIAN BULLISH — historically excellent buying opportunity"
        elif fear_greed <= 30:
            fg_label = "FEAR"
            fg_signal = "Moderate bullish contrarian signal"
        elif fear_greed <= 45:
            fg_label = "SLIGHT FEAR"
            fg_signal = "Neutral to mildly bullish"
        elif fear_greed <= 55:
            fg_label = "NEUTRAL"
            fg_signal = "No strong contrarian signal"
        elif fear_greed <= 70:
            fg_label = "GREED"
            fg_signal = "Moderate bearish contrarian signal"
        elif fear_greed <= 85:
            fg_label = "HIGH GREED"
            fg_signal = "CONTRARIAN BEARISH — watch for reversal"
        else:
            fg_label = "EXTREME GREED"
            fg_signal = "CONTRARIAN BEARISH — historically dangerous to buy here"

        # Sentiment score interpretation
        if sentiment_score > 0.5:
            sent_label = "STRONGLY BULLISH"
        elif sentiment_score > 0.2:
            sent_label = "BULLISH"
        elif sentiment_score > -0.2:
            sent_label = "NEUTRAL"
        elif sentiment_score > -0.5:
            sent_label = "BEARISH"
        else:
            sent_label = "STRONGLY BEARISH"

        # Social media metrics
        social_str = ""
        if social:
            social_str = f"""
SOCIAL MEDIA SENTIMENT:
  Twitter/X Mentions: {social.get('twitter_mentions', 'N/A')}
  Twitter Sentiment : {social.get('twitter_sentiment', 'N/A')}
  Reddit Sentiment  : {social.get('reddit_sentiment', 'N/A')}
  Google Trends     : {social.get('google_trends', 'N/A')}
  Telegram Activity : {social.get('telegram_activity', 'N/A')}"""

        # On-chain sentiment (crypto)
        onchain_str = ""
        if self.market == "crypto" and onchain:
            funding = onchain.get("funding_rate", 0)
            ls_ratio = onchain.get("ls_ratio", 1.0)
            funding_signal = ""
            if isinstance(funding, (int, float)):
                if funding > 0.05:
                    funding_signal = "OVERCROWDED LONGS — bearish contrarian"
                elif funding < -0.02:
                    funding_signal = "OVERCROWDED SHORTS — bullish contrarian"
            onchain_str = f"""
ON-CHAIN SENTIMENT:
  Funding Rate   : {funding}% {funding_signal}
  Long/Short Ratio: {ls_ratio} ({'longs dominant' if float(ls_ratio) > 1 else 'shorts dominant'})
  Exchange Inflow : {onchain.get('exchange_flow', 'N/A')}
  Whale Activity  : {onchain.get('whale_activity', 'N/A')}
  OI Change (24h) : {onchain.get('oi_change', 'N/A')}%"""

        # Positioning data (futures/options)
        pos_str = ""
        if positioning:
            pos_str = f"""
POSITIONING DATA:
  Net Speculative Position: {positioning.get('cot_net', 'N/A')}
  Put/Call Ratio          : {positioning.get('pcr', 'N/A')}
  Options Skew            : {positioning.get('skew', 'N/A')}
  Max Pain                : {positioning.get('max_pain', 'N/A')}
  Gamma Exposure          : {positioning.get('gamma_exposure', 'N/A')}"""

        prompt = f"""SENTIMENT ANALYST ASSESSMENT
Symbol: {symbol} | Market: {self.market.upper()}

AGGREGATE SENTIMENT:
  Composite Score: {sentiment_score:.3f} → {sent_label}
  Fear & Greed   : {fear_greed}/100 → {fg_label}
  Contrarian Signal: {fg_signal}
{social_str}
{onchain_str}
{pos_str}

NEWS FLOW (sorted by impact):
{format_news(news, max_items=10)}

SENTIMENT DIVERGENCE CHECK:
  News Sentiment vs Price: {self._check_divergence(sentiment_score, context)}

YOUR PERFORMANCE:
{self._format_recent_performance()}

TASK: Analyse all sentiment signals for {symbol}.
Key questions to answer:
1. Is current sentiment extreme enough to be a contrarian signal?
2. Does news flow confirm or contradict the technical trend?
3. What is the smart money positioning vs retail crowd?
4. What specific events are driving sentiment and how long will they last?

Provide your decision: if sentiment is strongly bullish (not contrarian), return BUY-type.
If sentiment is at extreme greed (contrarian), consider SELL-type.
Neutral/conflicting sentiment → NEUTRAL.

{AGENT_DECISION_SCHEMA}"""

        return prompt

    def _check_divergence(
        self, sentiment_score: float, context: Dict[str, Any]
    ) -> str:
        """Check if sentiment diverges from recent price action."""
        candles = context.get("candles", [])
        if not candles or len(candles) < 5:
            return "Insufficient data"

        recent_closes = [c.get("close", 0) for c in candles[-5:]]
        if all(c > 0 for c in recent_closes):
            price_trend = "UP" if recent_closes[-1] > recent_closes[0] else "DOWN"
            sentiment_trend = "UP" if sentiment_score > 0.1 else "DOWN" if sentiment_score < -0.1 else "NEUTRAL"

            if price_trend != sentiment_trend and sentiment_trend != "NEUTRAL":
                return f"DIVERGENCE DETECTED: Price trending {price_trend} but sentiment is {sentiment_trend}"
            else:
                return f"Aligned: Price {price_trend}, Sentiment {sentiment_trend}"
        return "N/A"

    @staticmethod
    def _build_data_used(market_data: Dict[str, Any]) -> List[str]:
        used = ["sentiment_score", "fear_greed"]
        if market_data.get("news"):
            used.append("news")
        if market_data.get("social"):
            used.append("social_media")
        if market_data.get("onchain"):
            used.append("onchain")
        if market_data.get("positioning"):
            used.append("positioning")
        return used
