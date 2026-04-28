# src/agents/technical_analyst.py
"""Technical Analyst Agent — multi-timeframe technical analysis."""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .base_agent import AgentDecision, AgentOutput, BaseAgent
from ..llm.prompt_templates import AGENT_DECISION_SCHEMA, format_candles

logger = logging.getLogger(__name__)

TECHNICAL_SYSTEM_PROMPT = """You are the Technical Analyst for NEXUS ALPHA, a sophisticated AI trading system.

Your role is to perform rigorous technical analysis using price action, indicators, and market structure.
You are objective and data-driven — neither biased bull nor bear.

Your analysis must cover:

1. TREND ANALYSIS
   - Primary trend (weekly/daily): direction and strength
   - Intermediate trend (4H): current phase
   - Short-term trend (1H/15M): momentum
   - Moving average stack (20/50/200 EMA/SMA alignment)

2. MOMENTUM INDICATORS
   - RSI: overbought/oversold, divergences, trend confirmation
   - MACD: crossovers, histogram momentum, divergences
   - Stochastic: overbought/oversold, K/D crossovers
   - Volume: above/below average, volume-price confirmation

3. KEY LEVELS
   - Support/resistance: fractal highs/lows, previous swing points
   - Fibonacci retracements: 0.382, 0.5, 0.618 zones
   - Volume Profile: POC, VAH, VAL
   - Round numbers: psychological levels

4. CHART PATTERNS
   - Classical patterns (H&S, triangles, flags, wedges)
   - Candlestick patterns (hammer, doji, engulfing)
   - Volume confirmation of patterns

5. VOLATILITY
   - ATR for stop placement
   - Bollinger Band squeeze/expansion
   - Regime (trending vs ranging)

6. TRADE PLAN
   - Entry price: specific price or trigger
   - Stop loss: invalidation level (ATR-based)
   - Take profit levels: T1 (1:1), T2 (1:2), T3 (1:3+)
   - Risk/reward ratio

Output format: You MUST respond with valid JSON matching the schema provided.
Entry, stop, and take profit levels should be specific price levels, not 'N/A' when you have enough data."""


class TechnicalAnalystAgent(BaseAgent):
    """Performs comprehensive multi-timeframe technical analysis."""

    def __init__(self, llm_ensemble, market: str = "crypto") -> None:
        super().__init__(
            role="technical_analyst",
            llm_ensemble=llm_ensemble,
            system_prompt=TECHNICAL_SYSTEM_PROMPT,
            market=market,
        )

    async def analyze(
        self, market_data: Dict[str, Any], context: Dict[str, Any]
    ) -> AgentOutput:
        """Analyse technical data and return a trade plan."""
        symbol = market_data.get("symbol", "UNKNOWN")
        candles = market_data.get("candles", [])
        indicators = market_data.get("indicators", {})
        patterns = market_data.get("patterns", [])
        mtf = market_data.get("multi_timeframe", {})
        levels = market_data.get("levels", {})
        candles_by_tf = market_data.get("candles_by_tf", {})

        user_prompt = self._build_prompt(
            symbol=symbol,
            candles=candles,
            indicators=indicators,
            patterns=patterns,
            mtf=mtf,
            levels=levels,
            candles_by_tf=candles_by_tf,
            context=context,
        )

        parsed = await self._query_llm(user_prompt)
        output = self._build_output(parsed, data_used=["candles", "indicators", "patterns", "levels", "mtf"])

        self._record_prediction(output.decision, output.confidence)
        logger.info(
            "[TechnicalAnalyst] %s → %s (%.2f confidence, entry=%s, stop=%s)",
            symbol, output.decision.value, output.confidence,
            output.entry_price, output.stop_loss,
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
        patterns: List[Any],
        mtf: Dict[str, Any],
        levels: Dict[str, Any],
        candles_by_tf: Dict[str, Any],
        context: Dict[str, Any],
    ) -> str:
        ind = indicators

        # Format detected patterns
        all_patterns = "\n".join(
            f"  - {p.name} | {p.direction} | conf={p.confidence:.2f} | "
            f"target={p.target_price:.6g if p.target_price else 'N/A'}"
            for p in patterns
        ) if patterns else "  No significant patterns detected."

        # MTF trend summary
        if isinstance(mtf, dict):
            mtf_str = (
                f"  Weekly : {mtf.get('trend_weekly', 'N/A')}\n"
                f"  Daily  : {mtf.get('trend_daily', 'N/A')}\n"
                f"  4H     : {mtf.get('trend_4h', 'N/A')}\n"
                f"  1H     : {mtf.get('trend_1h', 'N/A')}\n"
                f"  15M    : {mtf.get('trend_15m', 'N/A')}\n"
                f"  Alignment: {mtf.get('alignment_score', 'N/A')}/1.0\n"
                f"  Overall: {mtf.get('overall_bias', 'N/A')}"
            )
        else:
            mtf_str = str(mtf)

        # Key price levels
        resistance = levels.get("resistance", [])[:5]
        support = levels.get("support", [])[:5]
        fib = levels.get("fibonacci", {})

        resist_str = ", ".join(f"{r:.6g}" for r in resistance) if resistance else "N/A"
        support_str = ", ".join(f"{s:.6g}" for s in support) if support else "N/A"
        fib_str = " | ".join(f"{k}={v:.6g}" for k, v in fib.items()) if fib else "N/A"

        # Current price and ATR for stop guidance
        current_price = candles[-1].get("close", 0) if candles else 0
        atr14 = ind.get("atr14", 0)
        stop_guidance = ""
        if current_price and atr14:
            bull_stop = current_price - 2.0 * float(atr14)
            bear_stop = current_price + 2.0 * float(atr14)
            stop_guidance = (
                f"ATR-based stops: LONG stop = {bull_stop:.6g} "
                f"(2x ATR below), SHORT stop = {bear_stop:.6g} (2x ATR above)"
            )

        prompt = f"""TECHNICAL ANALYST ASSESSMENT
Symbol: {symbol} | Market: {self.market.upper()}

PRICE ACTION (current: {current_price:.6g}):
{format_candles(candles, limit=20)}

TREND INDICATORS:
  SMA Stack : 20={ind.get('sma20', 'N/A')} | 50={ind.get('sma50', 'N/A')} | 200={ind.get('sma200', 'N/A')}
  EMA Stack : 9={ind.get('ema9', 'N/A')} | 21={ind.get('ema21', 'N/A')} | 55={ind.get('ema55', 'N/A')}
  Price vs SMA20: {ind.get('price_vs_sma20', 'N/A')}%  |  Price vs SMA200: {ind.get('price_vs_sma200', 'N/A')}%
  ADX={ind.get('adx', 'N/A')} | DI+={ind.get('di_plus', 'N/A')} | DI-={ind.get('di_minus', 'N/A')}
  Supertrend: {ind.get('supertrend', 'N/A')} ({ind.get('supertrend_direction', 'N/A')})

MOMENTUM:
  RSI(14)={ind.get('rsi14', 'N/A')} | RSI(7)={ind.get('rsi7', 'N/A')} | RSI(21)={ind.get('rsi21', 'N/A')}
  MACD: Line={ind.get('macd_line', 'N/A')} Signal={ind.get('macd_signal', 'N/A')} Hist={ind.get('macd_hist', 'N/A')}
  Stoch K={ind.get('stoch_k', 'N/A')} D={ind.get('stoch_d', 'N/A')}
  Williams %R={ind.get('williams_r', 'N/A')} | CCI={ind.get('cci', 'N/A')} | MFI={ind.get('mfi', 'N/A')}

VOLATILITY:
  ATR(14)={ind.get('atr14', 'N/A')} | ATR(7)={ind.get('atr7', 'N/A')}
  BB: Upper={ind.get('bb_upper', 'N/A')} Mid={ind.get('bb_mid', 'N/A')} Lower={ind.get('bb_lower', 'N/A')}
  BB Width={ind.get('bb_width', 'N/A')} | BB %B={ind.get('bb_pct', 'N/A')}
  Squeeze: {'ON' if ind.get('squeeze_on') else 'OFF' if ind.get('squeeze_off') else 'N/A'}
  Market Regime: {ind.get('market_regime', 'N/A')}

VOLUME:
  OBV={ind.get('obv', 'N/A')} | VWAP={ind.get('vwap', 'N/A')}
  Volume Ratio={ind.get('volume_ratio', 'N/A')}x (vs 20-bar avg)

MULTI-TIMEFRAME ANALYSIS:
{mtf_str}

KEY LEVELS:
  Resistance: {resist_str}
  Support   : {support_str}
  Fibonacci : {fib_str}

CHART PATTERNS:
{all_patterns}

STOP PLACEMENT GUIDANCE:
{stop_guidance}

YOUR PERFORMANCE:
{self._format_recent_performance()}

TASK: Provide a complete technical analysis for {symbol}.
Include specific entry, stop loss, and take profit levels.
Weight your analysis toward the primary timeframe trend.
Identify the highest-probability setup currently visible in the data.

{AGENT_DECISION_SCHEMA}"""

        return prompt
