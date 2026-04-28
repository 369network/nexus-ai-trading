# src/llm/prompt_templates.py
"""Prompt templates and formatting utilities for NEXUS ALPHA LLM agents."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Agent output JSON schema
# ---------------------------------------------------------------------------

AGENT_DECISION_SCHEMA = """
You MUST respond with a single valid JSON object matching this exact schema.
No markdown, no commentary, no code fences — raw JSON only.

{
  "decision": "<one of: STRONG_BUY | BUY | SLIGHT_BUY | NEUTRAL | SLIGHT_SELL | SELL | STRONG_SELL>",
  "confidence": <float 0.0–1.0>,
  "reasoning": "<concise paragraph explaining your decision>",
  "key_factors": ["<factor 1>", "<factor 2>", "<factor 3>"],
  "entry_price": <float or null>,
  "stop_loss": <float or null>,
  "take_profit_1": <float or null>,
  "take_profit_2": <float or null>,
  "take_profit_3": <float or null>,
  "risk_reward": <float or null>,
  "timeframe": "<e.g. 4H, 1D>",
  "invalidation": "<what would make this trade thesis wrong>",
  "data_quality": <float 0.0–1.0, how complete the data is>
}
"""


# ---------------------------------------------------------------------------
# Main market context template
# ---------------------------------------------------------------------------

MARKET_CONTEXT_TEMPLATE = """
=== MARKET CONTEXT: {symbol} | {market} | {timeframe} ===
Generated: {timestamp}

--- PRICE SUMMARY ---
Current Price : {current_price}
24h Change    : {price_change_24h}%
24h High      : {high_24h}
24h Low       : {low_24h}
Volume (24h)  : {volume_24h}
Avg Volume    : {avg_volume}
Volume Ratio  : {volume_ratio}x

--- RECENT CANDLES (newest last) ---
{candles_str}

--- TECHNICAL INDICATORS ---
Trend:
  SMA20={sma20}  SMA50={sma50}  SMA200={sma200}
  EMA9={ema9}    EMA21={ema21}  EMA55={ema55}
  Price vs SMA20: {price_vs_sma20}%  Price vs SMA200: {price_vs_sma200}%

Momentum:
  RSI(14)={rsi14}  RSI(7)={rsi7}  RSI(21)={rsi21}
  MACD Line={macd_line}  Signal={macd_signal}  Hist={macd_hist}
  Stoch K={stoch_k}  Stoch D={stoch_d}
  Williams %R={williams_r}  CCI={cci}  MFI={mfi}

Volatility:
  BB Upper={bb_upper}  BB Mid={bb_mid}  BB Lower={bb_lower}
  BB Width={bb_width}  BB %B={bb_pct}
  ATR(14)={atr14}  ATR(7)={atr7}

Volume / Flow:
  OBV={obv}  VWAP={vwap}
  ADX={adx}  DI+={di_plus}  DI-={di_minus}

Advanced:
  Supertrend={supertrend} ({supertrend_direction})
  Squeeze: {squeeze_status}  Momentum={squeeze_momentum}

--- MULTI-TIMEFRAME BIAS ---
Weekly : {trend_weekly}
Daily  : {trend_daily}
4H     : {trend_4h}
1H     : {trend_1h}
15M    : {trend_15m}
Alignment Score: {alignment_score}/1.0
Overall Bias: {overall_bias}

--- KEY LEVELS ---
Resistance : {resistance_levels}
Support    : {support_levels}
Nearest Resistance: {nearest_resistance} (dist: {dist_resistance}%)
Nearest Support   : {nearest_support} (dist: {dist_support}%)
Fibonacci Levels  : {fib_levels}

--- MARKET REGIME ---
Current Regime: {market_regime}
ADX Value: {adx}  (>25 = trending)
Volatility Percentile: {vol_percentile}%

--- NEWS & SENTIMENT ---
{news_str}

Sentiment Score: {sentiment_score} (-1.0 bearish → +1.0 bullish)
Fear & Greed Index: {fear_greed} ({fear_greed_label})

{onchain_section}

--- PORTFOLIO CONTEXT ---
Current Exposure: {current_exposure}%
Open Positions  : {open_positions}
Correlation Risk: {correlation_risk}
Daily P&L       : {daily_pnl}%
"""


# On-chain section injected for crypto only
_ONCHAIN_SECTION_TEMPLATE = """--- ON-CHAIN METRICS (crypto) ---
Exchange Inflow/Outflow: {exchange_flow}
Whale Activity (24h): {whale_activity}
Funding Rate: {funding_rate}%
Open Interest Change: {oi_change}%
Long/Short Ratio: {ls_ratio}
"""

_ONCHAIN_SECTION_EMPTY = "--- ON-CHAIN METRICS ---\nN/A (non-crypto market)\n"


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def format_candles(candles: List[Dict[str, Any]], limit: int = 20) -> str:
    """Convert a list of OHLCV dicts to a compact tabular string.

    Each row: ``YYYY-MM-DD HH:MM  O=xxx.xx  H=xxx.xx  L=xxx.xx  C=xxx.xx  V=x.xxM``
    """
    if not candles:
        return "  (no candle data available)"

    rows: List[str] = []
    recent = candles[-limit:] if len(candles) > limit else candles

    for c in recent:
        ts = c.get("timestamp") or c.get("time") or c.get("date", "")
        if isinstance(ts, datetime):
            ts_str = ts.strftime("%Y-%m-%d %H:%M")
        else:
            ts_str = str(ts)[:16]

        o = _fmt_price(c.get("open", c.get("o", 0)))
        h = _fmt_price(c.get("high", c.get("h", 0)))
        lo = _fmt_price(c.get("low", c.get("l", 0)))
        cl = _fmt_price(c.get("close", c.get("c", 0)))

        vol = c.get("volume", c.get("v", 0))
        vol_str = _fmt_volume(vol)

        rows.append(
            f"  {ts_str}  O={o}  H={h}  L={lo}  C={cl}  V={vol_str}"
        )

    return "\n".join(rows)


def format_news(
    news_items: List[Dict[str, Any]],
    max_items: int = 8,
) -> str:
    """Format news items into a prioritised summary.

    Sorts high-impact items first, then by recency.  Returns at most
    *max_items* entries.
    """
    if not news_items:
        return "  (no recent news)"

    # Sort by impact desc, then by published_at desc
    def sort_key(item: Dict[str, Any]) -> tuple:
        impact_order = {"high": 0, "medium": 1, "low": 2}
        impact = impact_order.get(str(item.get("impact", "low")).lower(), 2)
        ts = item.get("published_at") or item.get("timestamp") or ""
        return (impact, str(ts))

    sorted_items = sorted(news_items, key=sort_key)[:max_items]

    lines: List[str] = []
    for item in sorted_items:
        impact = str(item.get("impact", "medium")).upper()
        title = item.get("title", item.get("headline", ""))
        source = item.get("source", "")
        ts = item.get("published_at", item.get("timestamp", ""))
        if isinstance(ts, datetime):
            ts = ts.strftime("%m-%d %H:%M")
        else:
            ts = str(ts)[:16]

        sentiment_tag = ""
        sentiment = item.get("sentiment_score", None)
        if sentiment is not None:
            if sentiment > 0.3:
                sentiment_tag = " [BULLISH]"
            elif sentiment < -0.3:
                sentiment_tag = " [BEARISH]"
            else:
                sentiment_tag = " [NEUTRAL]"

        lines.append(f"  [{impact}] {ts} | {source} | {title}{sentiment_tag}")

    return "\n".join(lines)


def build_market_context(
    symbol: str,
    market: str,
    timeframe: str,
    candles: List[Dict[str, Any]],
    indicators: Dict[str, Any],
    news: List[Dict[str, Any]],
    sentiment_score: float = 0.0,
    fear_greed: Optional[int] = None,
    onchain: Optional[Dict[str, Any]] = None,
    portfolio: Optional[Dict[str, Any]] = None,
    mtf: Optional[Dict[str, Any]] = None,
    levels: Optional[Dict[str, Any]] = None,
) -> str:
    """Build the fully populated market context string.

    This is a convenience function that fills in *MARKET_CONTEXT_TEMPLATE*
    from structured data dicts so callers don't have to know the template keys.
    """
    ind = indicators or {}
    port = portfolio or {}
    mtf = mtf or {}
    levels = levels or {}

    current_price = _fmt_price(ind.get("close", candles[-1].get("close", 0) if candles else 0))

    # Fear & greed label
    fg = fear_greed or 50
    if fg <= 20:
        fg_label = "Extreme Fear"
    elif fg <= 40:
        fg_label = "Fear"
    elif fg <= 60:
        fg_label = "Neutral"
    elif fg <= 80:
        fg_label = "Greed"
    else:
        fg_label = "Extreme Greed"

    # On-chain section
    if market == "crypto" and onchain:
        onchain_section = _ONCHAIN_SECTION_TEMPLATE.format(
            exchange_flow=onchain.get("exchange_flow", "N/A"),
            whale_activity=onchain.get("whale_activity", "N/A"),
            funding_rate=onchain.get("funding_rate", 0),
            oi_change=onchain.get("oi_change", 0),
            ls_ratio=onchain.get("ls_ratio", "N/A"),
        )
    else:
        onchain_section = _ONCHAIN_SECTION_EMPTY

    fib_levels_str = ", ".join(
        f"{k}={_fmt_price(v)}"
        for k, v in levels.get("fibonacci", {}).items()
    ) or "N/A"

    resistance_str = ", ".join(
        _fmt_price(r) for r in levels.get("resistance", [])[:5]
    ) or "N/A"
    support_str = ", ".join(
        _fmt_price(s) for s in levels.get("support", [])[:5]
    ) or "N/A"

    squeeze_status = "ON" if ind.get("squeeze_on") else ("OFF" if ind.get("squeeze_off") else "N/A")

    return MARKET_CONTEXT_TEMPLATE.format(
        symbol=symbol,
        market=market.upper(),
        timeframe=timeframe,
        timestamp=datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        current_price=current_price,
        price_change_24h=_fmt_pct(ind.get("price_change_24h", 0)),
        high_24h=_fmt_price(ind.get("high_24h", 0)),
        low_24h=_fmt_price(ind.get("low_24h", 0)),
        volume_24h=_fmt_volume(ind.get("volume_24h", 0)),
        avg_volume=_fmt_volume(ind.get("avg_volume", 0)),
        volume_ratio=round(float(ind.get("volume_ratio", 1.0)), 2),
        candles_str=format_candles(candles),
        sma20=_fmt_price(ind.get("sma20", 0)),
        sma50=_fmt_price(ind.get("sma50", 0)),
        sma200=_fmt_price(ind.get("sma200", 0)),
        ema9=_fmt_price(ind.get("ema9", 0)),
        ema21=_fmt_price(ind.get("ema21", 0)),
        ema55=_fmt_price(ind.get("ema55", 0)),
        price_vs_sma20=_fmt_pct(ind.get("price_vs_sma20", 0)),
        price_vs_sma200=_fmt_pct(ind.get("price_vs_sma200", 0)),
        rsi14=_fmt_ind(ind.get("rsi14", 50)),
        rsi7=_fmt_ind(ind.get("rsi7", 50)),
        rsi21=_fmt_ind(ind.get("rsi21", 50)),
        macd_line=_fmt_ind(ind.get("macd_line", 0)),
        macd_signal=_fmt_ind(ind.get("macd_signal", 0)),
        macd_hist=_fmt_ind(ind.get("macd_hist", 0)),
        stoch_k=_fmt_ind(ind.get("stoch_k", 50)),
        stoch_d=_fmt_ind(ind.get("stoch_d", 50)),
        williams_r=_fmt_ind(ind.get("williams_r", -50)),
        cci=_fmt_ind(ind.get("cci", 0)),
        mfi=_fmt_ind(ind.get("mfi", 50)),
        bb_upper=_fmt_price(ind.get("bb_upper", 0)),
        bb_mid=_fmt_price(ind.get("bb_mid", 0)),
        bb_lower=_fmt_price(ind.get("bb_lower", 0)),
        bb_width=_fmt_ind(ind.get("bb_width", 0)),
        bb_pct=_fmt_ind(ind.get("bb_pct", 0.5)),
        atr14=_fmt_ind(ind.get("atr14", 0)),
        atr7=_fmt_ind(ind.get("atr7", 0)),
        obv=_fmt_volume(ind.get("obv", 0)),
        vwap=_fmt_price(ind.get("vwap", 0)),
        adx=_fmt_ind(ind.get("adx", 0)),
        di_plus=_fmt_ind(ind.get("di_plus", 0)),
        di_minus=_fmt_ind(ind.get("di_minus", 0)),
        supertrend=_fmt_price(ind.get("supertrend", 0)),
        supertrend_direction=str(ind.get("supertrend_direction", "N/A")),
        squeeze_status=squeeze_status,
        squeeze_momentum=_fmt_ind(ind.get("squeeze_momentum", 0)),
        trend_weekly=mtf.get("trend_weekly", "N/A"),
        trend_daily=mtf.get("trend_daily", "N/A"),
        trend_4h=mtf.get("trend_4h", "N/A"),
        trend_1h=mtf.get("trend_1h", "N/A"),
        trend_15m=mtf.get("trend_15m", "N/A"),
        alignment_score=round(float(mtf.get("alignment_score", 0)), 2),
        overall_bias=mtf.get("overall_bias", "N/A"),
        resistance_levels=resistance_str,
        support_levels=support_str,
        nearest_resistance=_fmt_price(levels.get("nearest_resistance", 0)),
        dist_resistance=_fmt_pct(levels.get("dist_resistance_pct", 0)),
        nearest_support=_fmt_price(levels.get("nearest_support", 0)),
        dist_support=_fmt_pct(levels.get("dist_support_pct", 0)),
        fib_levels=fib_levels_str,
        market_regime=ind.get("market_regime", "UNKNOWN"),
        vol_percentile=_fmt_ind(ind.get("vol_percentile", 50)),
        news_str=format_news(news),
        sentiment_score=round(float(sentiment_score), 3),
        fear_greed=fg,
        fear_greed_label=fg_label,
        onchain_section=onchain_section,
        current_exposure=_fmt_pct(port.get("exposure_pct", 0)),
        open_positions=port.get("open_positions", 0),
        correlation_risk=port.get("correlation_risk", "LOW"),
        daily_pnl=_fmt_pct(port.get("daily_pnl_pct", 0)),
    )


# ---------------------------------------------------------------------------
# Private formatting utilities
# ---------------------------------------------------------------------------

def _fmt_price(value: Any) -> str:
    try:
        v = float(value)
        if v >= 10_000:
            return f"{v:,.0f}"
        elif v >= 100:
            return f"{v:.2f}"
        elif v >= 1:
            return f"{v:.4f}"
        else:
            return f"{v:.6f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_pct(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "0.00"


def _fmt_ind(value: Any) -> str:
    try:
        v = float(value)
        return f"{v:.2f}"
    except (TypeError, ValueError):
        return "N/A"


def _fmt_volume(value: Any) -> str:
    try:
        v = float(value)
        if v >= 1_000_000_000:
            return f"{v/1_000_000_000:.2f}B"
        elif v >= 1_000_000:
            return f"{v/1_000_000:.2f}M"
        elif v >= 1_000:
            return f"{v/1_000:.1f}K"
        return f"{v:.0f}"
    except (TypeError, ValueError):
        return "N/A"
