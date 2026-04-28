# NEXUS ALPHA — Agent System Documentation

## Overview

NEXUS ALPHA uses 7 specialised LLM agents to debate every candidate signal before execution. Each agent has a distinct analytical bias, ensuring the system is not homogeneous in its reasoning. Consensus emerges through a weighted voting mechanism in the SignalFusionEngine.

---

## The Debate Process

When a strategy generates a candidate signal:

1. The `AgentCoordinator` dispatches the signal context to all 7 agents in parallel.
2. Each agent receives: indicator snapshot, regime, candle history (last 20 candles), open positions, recent performance.
3. Each agent produces a `vote`, `confidence`, and `reasoning`.
4. The `SignalFusionEngine` aggregates votes using calibrated weights.
5. If the weighted score exceeds the edge threshold, the signal proceeds to risk management.

---

## Agent Profiles

### 1. TrendFollower

**Role:** Identifies and follows the primary market trend.

**Bias:** Will vote LONG in uptrends and SHORT in downtrends; avoids counter-trend trades.

**Primary indicators used:**
- EMA (8, 21, 50, 200)
- MACD (12, 26, 9)
- ADX (14) — minimum ADX 25 to confirm trend
- Higher timeframe alignment

**Prompt structure:**
```
Context: [symbol, timeframe, OHLCV, EMA values, MACD values, ADX, regime]
Task: Evaluate whether the current setup presents a high-probability trend-following entry.
Output: {"vote": "LONG|SHORT|NEUTRAL", "confidence": 0.0-1.0, "reasoning": "...", "key_factors": {...}}
```

**Confidence calibration:**
- ADX > 40 + aligned EMAs + MACD crossover → confidence 0.8–0.9
- ADX 25–40 + mostly aligned → confidence 0.5–0.7
- ADX < 25 or mixed signals → confidence < 0.4 → likely NEUTRAL

---

### 2. MeanReversion

**Role:** Identifies overbought/oversold conditions and trades reversion to mean.

**Bias:** Favours counter-trend entries at extremes; avoids trending markets.

**Primary indicators used:**
- RSI (14) — extremes below 30 or above 70
- Bollinger Bands (20, 2.0) — price at band extremes
- Z-score of price (20-period rolling)
- Stochastic oscillator (14, 3, 3)

**Prompt structure:**
```
Context: [OHLCV, RSI, BB position (% of band width), Z-score, Stochastic, regime]
Task: Assess whether price has reached a statistically significant extreme with high reversion probability.
Output: {"vote": "LONG|SHORT|NEUTRAL", "confidence": 0.0-1.0, "reasoning": "...", "key_factors": {...}}
```

**Confidence calibration:**
- RSI < 25 + price at lower BB + Z-score < -2 → confidence 0.8–0.9
- RSI < 30 + one other confirming factor → confidence 0.5–0.7
- Ranging regime only → add 0.1 to confidence
- If ADX > 35 (trending): max confidence 0.4

---

### 3. BreakoutHunter

**Role:** Identifies high-probability breakouts from consolidation patterns.

**Bias:** Trades momentum; requires volume confirmation; avoids low-volume breakouts.

**Primary indicators used:**
- Volume (vs. 20-period average — must be >2x for confirmation)
- ATR-based range analysis
- Pivot levels (daily and weekly)
- Donchian Channel (20)
- Price structure (consolidation detection)

**Prompt structure:**
```
Context: [OHLCV, volume ratio, ATR, Donchian levels, recent price range, key levels]
Task: Determine if a breakout is occurring with genuine momentum and volume confirmation.
Output: {"vote": "LONG|SHORT|NEUTRAL", "confidence": 0.0-1.0, "reasoning": "...", "key_factors": {...}}
```

**Confidence calibration:**
- Volume > 3x average + clean break of multi-touch level → confidence 0.85
- Volume 2–3x + break of recent range → confidence 0.6–0.75
- Volume < 2x → max confidence 0.4 (high false breakout risk)

---

### 4. RiskSentinel

**Role:** Acts as the devil's advocate. Always assesses downside risk and portfolio context.

**Bias:** Conservative; will vote NEUTRAL or against the signal if risk is elevated. Has a veto weight in extreme scenarios.

**Primary indicators used:**
- Current portfolio heat (total risk as % of equity)
- Correlation with open positions
- VaR estimate (historical)
- Recent drawdown
- Market volatility vs. historical average

**Prompt structure:**
```
Context: [signal, portfolio state, current drawdown, VIX/crypto fear index, correlation matrix]
Task: Evaluate the risk of adding this position. Consider portfolio-level effects, not just this trade in isolation.
Output: {"vote": "LONG|SHORT|NEUTRAL", "confidence": 0.0-1.0, "reasoning": "...", "risk_level": "LOW|MEDIUM|HIGH|EXTREME"}
```

**Special behaviour:**
- If portfolio heat >8%: forces NEUTRAL regardless of other agents
- If correlated with 2+ open positions (correlation >0.7): forces NEUTRAL
- Acts as a veto: if risk_level is EXTREME, overrides any positive consensus

---

### 5. MacroAnalyst

**Role:** Evaluates the broader market context and macro environment.

**Bias:** Incorporates news sentiment, economic calendar events, and macro indicators.

**Primary indicators used:**
- Recent news sentiment (from memory store)
- Economic calendar (FOMC, CPI, NFP for US; RBI for India)
- BTC dominance (for crypto)
- Dollar Index (DXY) correlation
- Sector rotation signals

**Prompt structure:**
```
Context: [signal, recent macro events, scheduled announcements within 24h, sentiment from memory]
Task: Does the macro environment support or contradict this trade? Flag any scheduled events that could cause adverse moves.
Output: {"vote": "LONG|SHORT|NEUTRAL", "confidence": 0.0-1.0, "reasoning": "...", "macro_risk": "LOW|MEDIUM|HIGH"}
```

**Confidence calibration:**
- Near major news event (within 4h): confidence capped at 0.5
- Strong macro tailwind: add 0.15
- Strong macro headwind: subtract 0.2 and shift toward NEUTRAL

---

### 6. PatternRecognizer

**Role:** Identifies classical chart patterns and price action setups.

**Bias:** Trades repeatable visual patterns with historical reliability.

**Primary patterns detected:**
- Head and shoulders / inverse H&S
- Double tops / double bottoms
- Bull/bear flags and pennants
- Ascending/descending triangles
- Inside bars / NR7 (Narrow Range 7)
- Pin bars / hammer / shooting star
- Engulfing candles

**Prompt structure:**
```
Context: [last 20 candles OHLCV, key support/resistance levels, identified pattern if any]
Task: Identify any significant chart patterns and assess their reliability for the current signal direction.
Output: {"vote": "LONG|SHORT|NEUTRAL", "confidence": 0.0-1.0, "pattern_found": "...", "reasoning": "..."}
```

**Confidence calibration:**
- Clean textbook pattern + volume confirmation → confidence 0.75–0.85
- Partial pattern or low-volume → confidence 0.4–0.6
- No pattern found → confidence 0.3 (slight bias toward NEUTRAL)

---

### 7. VolumeProfiler

**Role:** Analyses volume at price levels to identify high-probability zones.

**Bias:** Focuses on VWAP, Point of Control (POC), Value Area High/Low.

**Primary indicators used:**
- VWAP (intraday)
- Volume Profile (POC, VAH, VAL)
- On-Balance Volume (OBV)
- Volume Weighted Average Price deviation
- Buy/sell pressure ratio (from tick data where available)

**Prompt structure:**
```
Context: [signal, VWAP, POC, VAH, VAL, OBV trend, current price relative to value area]
Task: Does the volume profile support this trade? Is price in a high-volume acceptance zone or a low-volume rejection zone?
Output: {"vote": "LONG|SHORT|NEUTRAL", "confidence": 0.0-1.0, "reasoning": "...", "vwap_position": "above|below|at"}
```

**Confidence calibration:**
- Price bouncing from POC/VAH with volume → confidence 0.8
- Price at VWAP with volume expansion → confidence 0.65
- Price in low-volume node (rejection zone) → confidence 0.5 (unreliable)

---

## Vote Aggregation (SignalFusionEngine)

```python
AGENT_WEIGHTS = {
    "TrendFollower":     0.20,
    "MeanReversion":     0.15,
    "BreakoutHunter":    0.15,
    "RiskSentinel":      0.20,   # Higher weight: risk gatekeeper
    "MacroAnalyst":      0.10,
    "PatternRecognizer": 0.10,
    "VolumeProfiler":    0.10,
}

# Score per agent: +confidence if LONG, -confidence if SHORT, 0 if NEUTRAL
weighted_score = sum(
    weight * (confidence if vote == "LONG" else -confidence if vote == "SHORT" else 0)
    for agent, (vote, confidence) in votes.items()
    for weight in [AGENT_WEIGHTS[agent]]
)

# Thresholds
LONG_THRESHOLD   = +0.25   # Weighted score > 0.25 → LONG
SHORT_THRESHOLD  = -0.25   # Weighted score < -0.25 → SHORT
# Between -0.25 and +0.25 → NEUTRAL (no trade)
```

---

## Output Format

Each agent returns a structured JSON response:

```json
{
  "vote": "LONG",
  "confidence": 0.72,
  "reasoning": "EMA 8 crossed above EMA 21, ADX at 32 confirms trend strength. Volume is 1.8x average, supporting the move. RSI at 58 has room to run before overbought.",
  "key_factors": {
    "ema_alignment": "bullish",
    "adx": 32.4,
    "volume_ratio": 1.8,
    "rsi": 58.3,
    "regime": "TRENDING_UP"
  },
  "model_used": "gpt-4o",
  "latency_ms": 342
}
```

---

## Brier Score Calibration

Agent confidence is tracked against actual trade outcomes (1=correct, 0=incorrect). Brier score (lower = better):

```
Brier = (predicted_probability - actual_outcome)²
```

Scores are stored in `model_performance` table. A well-calibrated agent at 70% confidence should be right ~70% of the time. Poorly calibrated agents are penalised by reducing their weight in the fusion engine.

Calibration runs nightly as part of Dream Mode and updates weights in `strategy_params`.
