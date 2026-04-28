# src/signals/sentiment_signals.py
"""Sentiment signal generation with market-specific lexicons."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Crypto sentiment lexicon (200+ terms)
# ---------------------------------------------------------------------------

CRYPTO_SENTIMENT_LEXICON: Dict[str, float] = {
    # Strongly bullish (+0.8 to +1.0)
    "moon": 0.9, "mooning": 1.0, "to the moon": 1.0, "lambo": 0.85,
    "all time high": 0.9, "ath": 0.85, "breakout": 0.8, "massive rally": 0.9,
    "btc etf approved": 1.0, "institutional adoption": 0.85, "accumulation": 0.75,
    "whale buying": 0.85, "exchange outflow": 0.8, "supply shock": 0.85,
    "halving": 0.8, "deflationary": 0.75, "staking yield": 0.7, "tvl surge": 0.8,
    "protocol upgrade": 0.7, "partnership": 0.7, "listing": 0.75, "launch": 0.7,
    "bullish divergence": 0.8, "golden cross": 0.85, "support held": 0.75,
    "buy the dip": 0.7, "oversold bounce": 0.75, "smart money buying": 0.85,
    "parabolic": 0.8, "explosion": 0.8, "skyrocket": 0.85, "surge": 0.75,
    "adoption": 0.7, "mainstream": 0.7, "network growth": 0.75, "record high": 0.9,
    "major milestone": 0.75, "defi boom": 0.8, "nft craze": 0.65, "layer 2": 0.7,
    "institutional buy": 0.85, "corporate treasury": 0.8, "etf inflow": 0.85,

    # Moderately bullish (+0.3 to +0.8)
    "bullish": 0.7, "buy": 0.5, "long": 0.5, "uptrend": 0.6, "rally": 0.65,
    "recovery": 0.55, "rebound": 0.6, "bounce": 0.55, "positive": 0.5,
    "gains": 0.55, "green": 0.5, "pump": 0.6, "higher": 0.45, "up": 0.35,
    "strong": 0.5, "momentum": 0.55, "strength": 0.5, "support": 0.5,
    "accumulate": 0.65, "hodl": 0.6, "diamond hands": 0.65, "dca": 0.55,
    "fundamentals": 0.5, "undervalued": 0.65, "cheap": 0.5, "value": 0.5,
    "upgrade": 0.6, "improvement": 0.5, "growth": 0.55, "expanding": 0.5,

    # Neutral (close to 0)
    "stable": 0.05, "consolidation": 0.0, "sideways": -0.05, "range": -0.05,
    "flat": 0.0, "neutral": 0.0, "unchanged": 0.0, "steady": 0.1, "hold": 0.1,
    "wait": 0.0, "watch": 0.05, "monitor": 0.0, "uncertain": -0.1, "unclear": -0.1,

    # Moderately bearish (-0.3 to -0.7)
    "bearish": -0.7, "sell": -0.5, "short": -0.5, "downtrend": -0.6,
    "correction": -0.5, "pullback": -0.45, "decline": -0.55, "drop": -0.5,
    "fall": -0.5, "dip": -0.35, "weakness": -0.5, "pressure": -0.45,
    "red": -0.5, "loss": -0.55, "negative": -0.5, "lower": -0.45, "down": -0.35,
    "overvalued": -0.6, "expensive": -0.5, "resistance": -0.4, "rejection": -0.55,
    "dump": -0.65, "panic": -0.7, "fear": -0.55, "concern": -0.45, "worry": -0.45,
    "risk": -0.35, "warning": -0.45, "caution": -0.4, "volatile": -0.3,

    # Strongly bearish (-0.8 to -1.0)
    "crash": -0.9, "collapse": -0.95, "rug pull": -1.0, "scam": -1.0, "hack": -0.9,
    "exploit": -0.85, "exchange collapse": -1.0, "bankruptcy": -0.95, "fraud": -1.0,
    "regulation ban": -0.9, "sec lawsuit": -0.85, "btc ban": -0.9, "china ban": -0.8,
    "massive sell": -0.85, "whale dump": -0.85, "exchange inflow": -0.8,
    "liquidation": -0.75, "cascade": -0.8, "bear trap": -0.7, "death cross": -0.85,
    "breakdown": -0.8, "support broken": -0.8, "delisting": -0.85, "fud": -0.7,
    "rekt": -0.8, "capitulation": -0.75, "contagion": -0.85, "insolvency": -0.9,
    "inflation": -0.4, "interest rate hike": -0.5, "federal reserve": -0.3,
    "tightening": -0.5, "quantitative tightening": -0.55,
}


# ---------------------------------------------------------------------------
# Forex sentiment lexicon (50+ terms)
# ---------------------------------------------------------------------------

FOREX_SENTIMENT_LEXICON: Dict[str, float] = {
    # Bullish for the base currency
    "rate hike": 0.75, "hawkish": 0.8, "strong economy": 0.7, "gdp beat": 0.75,
    "nfp beat": 0.8, "employment strong": 0.7, "inflation high": 0.6,
    "trade surplus": 0.65, "current account surplus": 0.65, "capital inflow": 0.7,
    "dollar strength": 0.6, "risk on": 0.5, "growth": 0.5, "expansion": 0.55,
    "central bank buy": 0.75, "rate increase": 0.75, "tightening": 0.65,
    "hot cpi": 0.6, "strong pmi": 0.6, "consumer confidence high": 0.55,
    "yield spread widening": 0.65, "carry trade": 0.5, "reserve currency": 0.6,

    # Bearish for the base currency
    "rate cut": -0.8, "dovish": -0.8, "weak economy": -0.7, "gdp miss": -0.75,
    "recession": -0.85, "unemployment high": -0.7, "deflation": -0.6,
    "trade deficit": -0.6, "capital outflow": -0.7, "political risk": -0.65,
    "war": -0.8, "sanctions": -0.75, "geopolitical tension": -0.6,
    "dollar weakness": -0.55, "risk off": -0.5, "contraction": -0.6,
    "rate decrease": -0.75, "easing": -0.65, "qe": -0.6, "stimulus": -0.5,
    "central bank sell": -0.7, "currency intervention": -0.5,
    "yield spread narrowing": -0.6, "debt crisis": -0.85,

    # Neutral
    "hold rates": 0.0, "on hold": 0.0, "as expected": 0.0, "in line": 0.05,
}


# ---------------------------------------------------------------------------
# US Stocks sentiment lexicon (50+ terms)
# ---------------------------------------------------------------------------

STOCKS_US_SENTIMENT_LEXICON: Dict[str, float] = {
    # Bullish
    "earnings beat": 0.85, "revenue beat": 0.8, "guidance raised": 0.9,
    "buyback": 0.7, "dividend increase": 0.65, "share repurchase": 0.7,
    "analyst upgrade": 0.75, "price target raised": 0.7, "outperform": 0.7,
    "record revenue": 0.8, "record profit": 0.8, "margin expansion": 0.75,
    "market share gain": 0.7, "new product launch": 0.65, "fed pivot": 0.8,
    "interest rate cut": 0.75, "soft landing": 0.7, "risk on": 0.6,
    "growth acceleration": 0.75, "innovation": 0.6, "acquisition": 0.55,
    "cost cutting": 0.6, "efficiency": 0.55, "ai integration": 0.7,
    "strong consumer": 0.65, "low unemployment": 0.6, "gdp growth": 0.65,

    # Bearish
    "earnings miss": -0.85, "revenue miss": -0.8, "guidance cut": -0.9,
    "guidance withdrawn": -0.85, "analyst downgrade": -0.75,
    "price target cut": -0.7, "underperform": -0.7, "sell rating": -0.75,
    "margin compression": -0.75, "layoffs": -0.6, "restructuring": -0.5,
    "debt downgrade": -0.8, "bankruptcy": -0.95, "accounting fraud": -1.0,
    "sec investigation": -0.85, "antitrust": -0.7, "recession": -0.8,
    "rate hike": -0.65, "inflation persistent": -0.7, "credit crunch": -0.8,
    "consumer slowdown": -0.65, "inventory buildup": -0.55,
    "supply chain": -0.45, "tariff": -0.55, "trade war": -0.7,

    # Neutral
    "in line": 0.0, "as expected": 0.0, "maintained guidance": 0.1,
}


# ---------------------------------------------------------------------------
# Indian Stocks sentiment lexicon (50+ terms)
# ---------------------------------------------------------------------------

STOCKS_IN_SENTIMENT_LEXICON: Dict[str, float] = {
    # Bullish
    "fii buying": 0.8, "dii buying": 0.7, "promoter buying": 0.85,
    "qip": 0.65, "ipo listing gains": 0.75, "results beat": 0.85,
    "pat growth": 0.75, "revenue growth": 0.7, "order inflow": 0.75,
    "rbi rate cut": 0.7, "monetary easing": 0.65, "fiscal stimulus": 0.65,
    "infrastructure spend": 0.7, "make in india": 0.65, "pli scheme": 0.7,
    "gst collection high": 0.65, "iip growth": 0.65, "pmI above 50": 0.7,
    "promoter stake increase": 0.8, "bulk deal buy": 0.7, "nifty 50 high": 0.75,
    "sensex high": 0.75, "sector tailwind": 0.65, "capex cycle": 0.7,
    "credit growth": 0.65, "npls declining": 0.7,

    # Bearish
    "fii selling": -0.8, "promoter pledging": -0.85, "promoter sell": -0.8,
    "results miss": -0.85, "pat decline": -0.75, "margin pressure": -0.7,
    "rbi rate hike": -0.65, "currency weakness": -0.6, "rupee fall": -0.7,
    "current account deficit": -0.6, "fiscal deficit wide": -0.6,
    "npa high": -0.75, "debt trap": -0.8, "corporate fraud": -1.0,
    "sebi action": -0.8, "promoter jail": -0.95, "regulatory issue": -0.7,
    "gst shortfall": -0.55, "iip negative": -0.65, "trade deficit": -0.55,
    "monsoon deficit": -0.6, "inflation high": -0.55, "crude oil high": -0.6,
    "global slowdown": -0.65, "global risk off": -0.7,

    # Neutral
    "as expected": 0.0, "in line": 0.0, "maintained": 0.05,
}


# ---------------------------------------------------------------------------
# Signal generator
# ---------------------------------------------------------------------------

class SentimentSignalGenerator:
    """Convert raw text and sentiment data into normalised signal scores."""

    _LEXICONS: Dict[str, Dict[str, float]] = {
        "crypto": CRYPTO_SENTIMENT_LEXICON,
        "forex": FOREX_SENTIMENT_LEXICON,
        "stocks_us": STOCKS_US_SENTIMENT_LEXICON,
        "stocks_in": STOCKS_IN_SENTIMENT_LEXICON,
        "commodity": FOREX_SENTIMENT_LEXICON,  # re-use macro lexicon for commodities
    }

    def score_text(self, text: str, market: str) -> float:
        """Score a single text string using the market-appropriate lexicon.

        Returns
        -------
        float
            -1.0 (strongly bearish) to +1.0 (strongly bullish).
        """
        lexicon = self._LEXICONS.get(market, CRYPTO_SENTIMENT_LEXICON)
        text_lower = text.lower()

        total_score = 0.0
        match_count = 0

        for term, weight in lexicon.items():
            # Use word-boundary aware matching for single-word terms
            pattern = r"\b" + re.escape(term) + r"\b"
            matches = re.findall(pattern, text_lower)
            if matches:
                total_score += weight * len(matches)
                match_count += len(matches)

        if match_count == 0:
            return 0.0

        # Average and clip to [-1, +1]
        raw = total_score / match_count
        return max(-1.0, min(1.0, raw))

    def score_news_items(
        self,
        items: List[Dict[str, Any]],
        market: str,
        max_age_hours: float = 48.0,
    ) -> float:
        """Score a list of news items, weighted by impact and recency.

        Parameters
        ----------
        items:
            List of news dicts with keys: title, body (optional), impact,
            published_at, sentiment_score (optional pre-computed).
        market:
            Market type for lexicon selection.
        max_age_hours:
            Items older than this threshold are given zero weight.
        """
        if not items:
            return 0.0

        now = datetime.now(tz=timezone.utc)
        impact_weights = {"high": 3.0, "medium": 1.5, "low": 0.5}

        total_weight = 0.0
        weighted_score = 0.0

        for item in items:
            # Impact weight
            impact = str(item.get("impact", "medium")).lower()
            imp_w = impact_weights.get(impact, 1.0)

            # Recency weight (exponential decay — half-weight after 12h)
            ts = item.get("published_at", item.get("timestamp"))
            if isinstance(ts, str):
                try:
                    ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    ts = now
            elif ts is None:
                ts = now

            if not ts.tzinfo:
                ts = ts.replace(tzinfo=timezone.utc)

            age_hours = (now - ts).total_seconds() / 3600
            if age_hours > max_age_hours:
                continue
            recency_w = 0.5 ** (age_hours / 12.0)  # half-weight every 12h

            # Score — use pre-computed score if available
            if "sentiment_score" in item and item["sentiment_score"] is not None:
                score = float(item["sentiment_score"])
            else:
                text = str(item.get("title", "")) + " " + str(item.get("body", ""))
                score = self.score_text(text, market)

            w = imp_w * recency_w
            weighted_score += score * w
            total_weight += w

        if total_weight == 0:
            return 0.0

        return max(-1.0, min(1.0, weighted_score / total_weight))

    def score_fear_greed(self, fg_index: int) -> float:
        """Convert Fear & Greed index (0–100) to a contrarian signal score.

        High fear → contrarian bullish (+)
        High greed → contrarian bearish (-)
        Neutral zone → near zero

        Returns
        -------
        float
            -1.0 to +1.0 (contrarian).
        """
        # Normalise to [-1, +1] and invert (contrarian)
        normalised = (fg_index - 50) / 50.0  # -1=extreme fear, +1=extreme greed
        # Apply a dead-zone in the neutral range (40–60 → near zero)
        if abs(normalised) < 0.2:
            return 0.0
        # Contrarian: invert and amplify at extremes
        contrarian = -normalised
        return max(-1.0, min(1.0, contrarian))

    def score_social_data(
        self, social: Dict[str, Any], market: str
    ) -> float:
        """Score social media data for sentiment signal."""
        if not social:
            return 0.0

        scores = []

        twitter_sent = social.get("twitter_sentiment")
        if twitter_sent is not None:
            scores.append(float(twitter_sent))

        reddit_sent = social.get("reddit_sentiment")
        if reddit_sent is not None:
            scores.append(float(reddit_sent))

        # Google trends: high interest ≠ direction — treat as neutral boost
        # Only the combined sentiment matters

        return sum(scores) / len(scores) if scores else 0.0

    def generate(
        self,
        news: List[Dict[str, Any]],
        social: Optional[Dict[str, Any]],
        fear_greed: Optional[int],
        market: str,
        news_weight: float = 0.6,
        social_weight: float = 0.3,
        fg_weight: float = 0.1,
    ) -> float:
        """Generate a combined sentiment signal.

        Returns
        -------
        float
            -1.0 to +1.0 composite sentiment signal.
        """
        news_score = self.score_news_items(news, market)
        social_score = self.score_social_data(social or {}, market)
        fg_score = self.score_fear_greed(fear_greed or 50)

        total_weight = news_weight + social_weight + fg_weight
        combined = (
            news_score * news_weight
            + social_score * social_weight
            + fg_score * fg_weight
        ) / total_weight

        return max(-1.0, min(1.0, combined))
