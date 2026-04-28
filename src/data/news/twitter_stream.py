"""
NEXUS ALPHA — Twitter/X Sentiment Client
=========================================
Async Twitter/X data client using tweepy v4 for both streaming and
search-based sentiment collection.

Features:
  - Filtered stream with dynamic rule management
  - Recent tweet search via v2 API
  - Keyword-based BULLISH/BEARISH sentiment scoring
  - BoundedSet(50000) deduplication on tweet IDs
  - Graceful fallback when bearer token is not configured

Environment variables:
  TWITTER_BEARER_TOKEN       — X/Twitter API v2 Bearer Token (required)
  TWITTER_API_KEY            — v1.1 API key (optional, for streaming)
  TWITTER_API_SECRET         — v1.1 API secret (optional)
  TWITTER_ACCESS_TOKEN       — OAuth 1.0a access token (optional)
  TWITTER_ACCESS_SECRET      — OAuth 1.0a access token secret (optional)
"""

from __future__ import annotations

import asyncio
import os
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Deque, Dict, List, Optional, Set

from src.utils.logging import get_logger
from src.utils.rate_limiter import RateLimiter
from src.utils.retry import retry_with_backoff
from src.utils.timezone import IST, UTC, now_utc

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Sentiment keyword sets
# ---------------------------------------------------------------------------

_BULLISH_TERMS: frozenset[str] = frozenset(
    [
        "buy", "long", "calls", "bullish", "bull", "moon", "pump", "rally",
        "breakout", "surge", "squeeze", "dip", "hodl", "accumulate", "strong",
        "uptrend", "green", "ath", "upgrade", "beat", "exceeded", "solid",
        "undervalued", "oversold", "support", "holding", "diamond hands",
        "to the moon", "send it", "buy the dip", "yolo", "gains",
    ]
)

_BEARISH_TERMS: frozenset[str] = frozenset(
    [
        "sell", "short", "puts", "bearish", "bear", "crash", "dump", "tank",
        "correction", "overvalued", "overbought", "resistance", "ceiling",
        "downtrend", "red", "weak", "miss", "disappointed", "downgrade",
        "bubble", "rug", "scam", "fraud", "warning", "collapse", "fud",
        "fear", "uncertainty", "doubt", "paper hands",
    ]
)

# ---------------------------------------------------------------------------
# BoundedSet
# ---------------------------------------------------------------------------


class BoundedSet:
    """
    Fixed-capacity set with FIFO eviction for tweet ID deduplication.

    Args:
        maxsize: Maximum number of IDs to retain in memory.
    """

    def __init__(self, maxsize: int) -> None:
        self._maxsize = maxsize
        self._set: set[str] = set()
        self._queue: Deque[str] = deque()

    def __contains__(self, item: str) -> bool:
        return item in self._set

    def add(self, item: str) -> None:
        if item in self._set:
            return
        if len(self._set) >= self._maxsize:
            oldest = self._queue.popleft()
            self._set.discard(oldest)
        self._set.add(item)
        self._queue.append(item)

    def __len__(self) -> int:
        return len(self._set)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class TweetSummary:
    """Condensed tweet information."""

    tweet_id: str
    text: str
    author_id: Optional[str]
    sentiment_score: float          # -1.0 to 1.0
    public_metrics: Dict[str, int]  # retweets, likes, replies, impressions
    created_at: datetime
    url: str


@dataclass
class TweetSentiment:
    """
    Aggregated Twitter/X sentiment for a symbol.

    Attributes:
        symbol: Ticker symbol analysed.
        score: Aggregate sentiment in [-1.0, 1.0].
        volume_24h: Approximate tweet volume in the last 24 hours.
        viral_tweets: Most-liked/retweeted tweets (up to 5).
        timestamp_utc: When the result was computed (UTC).
        timestamp_ist: Same moment in IST.
    """

    symbol: str
    score: float
    volume_24h: int
    viral_tweets: List[TweetSummary]
    timestamp_utc: datetime
    timestamp_ist: datetime


# ---------------------------------------------------------------------------
# Sentiment scoring
# ---------------------------------------------------------------------------


def _score_tweet(text: str) -> float:
    """
    Score a tweet's sentiment using keyword matching.

    Returns a float in [-1.0, 1.0]:
      +1.0 = entirely bullish keywords
      -1.0 = entirely bearish keywords
       0.0 = neutral or no matches

    Args:
        text: Raw tweet text.
    """
    lower = text.lower()
    bull = sum(1 for kw in _BULLISH_TERMS if kw in lower)
    bear = sum(1 for kw in _BEARISH_TERMS if kw in lower)
    total = bull + bear
    if total == 0:
        return 0.0
    return max(-1.0, min(1.0, (bull - bear) / total))


def _engagement_weight(metrics: Dict[str, int]) -> float:
    """
    Compute an engagement-based weight for a tweet.

    Higher engagement = higher weight in the aggregate sentiment score.
    Uses log-scale to prevent viral tweets from dominating.

    Args:
        metrics: Public metrics dict with keys like 'like_count', 'retweet_count'.
    """
    import math

    likes = metrics.get("like_count", 0)
    retweets = metrics.get("retweet_count", 0)
    replies = metrics.get("reply_count", 0)

    raw = likes + retweets * 2 + replies
    return max(1.0, math.log1p(raw))


# ---------------------------------------------------------------------------
# TwitterStream
# ---------------------------------------------------------------------------


class TwitterStream:
    """
    Async Twitter/X client for sentiment tracking.

    Provides two modes:
    1. ``get_recent_tweets``: Searches the last 7 days for a symbol (requires
       API v2 Basic access or higher).
    2. ``stream_tweets``: Real-time filtered stream with rule management
       (requires Elevated access or higher).

    Gracefully degrades to neutral sentiment when no bearer token is
    configured — logs a warning rather than crashing.

    Usage (search)::

        async with TwitterStream() as ts:
            result = await ts.get_recent_tweets("AAPL")
            print(result.score, result.volume_24h)

    Usage (streaming)::

        async with TwitterStream() as ts:
            await ts.stream_tweets(["AAPL", "BTC"], callback=my_handler)
    """

    def __init__(self) -> None:
        self._bearer_token = os.getenv("TWITTER_BEARER_TOKEN", "")
        self._available = bool(self._bearer_token)
        self._client: Any = None          # tweepy.AsyncClient
        self._stream_client: Any = None   # NexusFilteredStream
        self._seen_ids = BoundedSet(50_000)
        self._rate_limiter = RateLimiter(
            rate=0.5,       # Tweepy handles per-endpoint limits internally
            capacity=5.0,   # Conservative additional layer
            name="twitter",
        )

        if not self._available:
            log.warning(
                "TWITTER_BEARER_TOKEN not set — TwitterStream will return neutral "
                "sentiment. Set the env var to enable live Twitter data.",
                env_var="TWITTER_BEARER_TOKEN",
            )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "TwitterStream":
        if self._available:
            await self._init_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def _init_client(self) -> None:
        """Initialise the tweepy v4 async client."""
        try:
            import tweepy  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "tweepy is required for TwitterStream. "
                "Install with: pip install tweepy"
            ) from exc

        self._client = tweepy.AsyncClient(
            bearer_token=self._bearer_token,
            wait_on_rate_limit=True,
        )
        log.info("Twitter async client initialised")

    async def close(self) -> None:
        """Release resources and stop any active stream."""
        if self._stream_client is not None:
            try:
                self._stream_client.disconnect()
            except Exception:
                pass
            self._stream_client = None
        self._client = None

    # ------------------------------------------------------------------
    # Rule management
    # ------------------------------------------------------------------

    async def add_stream_rules(self, symbols: List[str]) -> List[str]:
        """
        Add filtered stream rules for the given symbols.

        Creates cashtag-based rules: ``$AAPL OR $BTC lang:en``.

        Args:
            symbols: List of ticker symbols to track.

        Returns:
            List of created rule IDs (for later deletion).

        Raises:
            RuntimeError: If Twitter is not configured.
        """
        self._require_available()
        import tweepy  # type: ignore[import]

        rule_ids: List[str] = []
        for symbol in symbols:
            clean = symbol.lstrip("$").upper()
            query = f"(${clean} OR #{clean}) lang:en -is:retweet"
            try:
                await self._rate_limiter.wait()
                response = await self._client.add_rules(
                    tweepy.StreamRule(query, tag=clean)
                )
                if response.data:
                    for rule in response.data:
                        rule_ids.append(rule.id)
                        log.debug(
                            "Stream rule added",
                            symbol=clean,
                            rule_id=rule.id,
                            query=query,
                        )
            except Exception as exc:
                log.error(
                    "Failed to add stream rule",
                    symbol=clean,
                    error=str(exc),
                )
        return rule_ids

    async def delete_stream_rules(self, rule_ids: List[str]) -> None:
        """
        Delete filtered stream rules by ID.

        Args:
            rule_ids: Rule IDs previously returned by ``add_stream_rules``.
        """
        if not rule_ids or not self._available:
            return
        try:
            await self._rate_limiter.wait()
            await self._client.delete_rules(rule_ids)
            log.debug("Stream rules deleted", count=len(rule_ids))
        except Exception as exc:
            log.warning("Failed to delete stream rules", error=str(exc))

    # ------------------------------------------------------------------
    # Recent tweet search
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=5.0, max_delay=60.0)
    async def get_recent_tweets(
        self,
        symbol: str,
        max_results: int = 100,
    ) -> TweetSentiment:
        """
        Search for recent tweets mentioning a symbol and compute sentiment.

        Uses Twitter API v2 ``search_recent_tweets`` endpoint (last 7 days).
        Falls back to a neutral TweetSentiment when Twitter is not configured.

        Args:
            symbol: Ticker symbol (e.g. "AAPL", "BTC", "$ETH").
            max_results: Max tweets to retrieve (10–100, capped at 100).

        Returns:
            TweetSentiment with aggregate score and metadata.
        """
        now = now_utc()
        neutral = TweetSentiment(
            symbol=symbol,
            score=0.0,
            volume_24h=0,
            viral_tweets=[],
            timestamp_utc=now,
            timestamp_ist=now.astimezone(IST),
        )

        if not self._available:
            return neutral

        clean = symbol.lstrip("$").upper()
        query = f"(${clean} OR #{clean}) lang:en -is:retweet"

        try:
            await self._rate_limiter.wait()
            response = await self._client.search_recent_tweets(
                query=query,
                max_results=min(max_results, 100),
                tweet_fields=["created_at", "public_metrics", "author_id"],
            )
        except Exception as exc:
            log.error(
                "Twitter search failed",
                symbol=symbol,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            return neutral

        if not response.data:
            log.debug("No tweets found", symbol=symbol, query=query)
            return neutral

        scored: List[TweetSummary] = []
        for tweet in response.data:
            tweet_id = str(tweet.id)
            if tweet_id in self._seen_ids:
                continue
            self._seen_ids.add(tweet_id)

            metrics: Dict[str, int] = tweet.public_metrics or {}
            sentiment = _score_tweet(tweet.text)
            created = getattr(tweet, "created_at", None) or now

            scored.append(
                TweetSummary(
                    tweet_id=tweet_id,
                    text=tweet.text,
                    author_id=str(tweet.author_id) if tweet.author_id else None,
                    sentiment_score=sentiment,
                    public_metrics=metrics,
                    created_at=created,
                    url=f"https://twitter.com/i/web/status/{tweet_id}",
                )
            )

        aggregate = self._weighted_aggregate(scored)
        viral = sorted(
            scored,
            key=lambda t: t.public_metrics.get("like_count", 0)
            + t.public_metrics.get("retweet_count", 0) * 2,
            reverse=True,
        )[:5]

        now = now_utc()
        result = TweetSentiment(
            symbol=symbol,
            score=aggregate,
            volume_24h=len(scored),
            viral_tweets=viral,
            timestamp_utc=now,
            timestamp_ist=now.astimezone(IST),
        )

        log.info(
            "Twitter sentiment computed",
            symbol=symbol,
            score=round(result.score, 3),
            volume=result.volume_24h,
        )
        return result

    # ------------------------------------------------------------------
    # Real-time streaming
    # ------------------------------------------------------------------

    async def stream_tweets(
        self,
        symbols: List[str],
        callback: Callable[[TweetSummary, str], None],
        max_reconnect_attempts: int = 5,
    ) -> None:
        """
        Start a real-time filtered stream for the given symbols.

        Calls ``callback(tweet_summary, symbol)`` for each matched tweet.
        Runs until cancelled or disconnected.

        Args:
            symbols: Symbols to track (stream rules are added automatically).
            callback: Async or sync callable invoked for each incoming tweet.
            max_reconnect_attempts: How many times to reconnect on error.

        Raises:
            RuntimeError: If Twitter is not configured.
        """
        self._require_available()
        import tweepy  # type: ignore[import]

        rule_ids = await self.add_stream_rules(symbols)
        symbol_set = {s.lstrip("$").upper() for s in symbols}

        class _NexusStream(tweepy.AsyncStreamingClient):
            def __init__(inner_self, *args: Any, **kwargs: Any) -> None:
                super().__init__(*args, **kwargs)
                inner_self._callback = callback
                inner_self._seen = self._seen_ids
                inner_self._symbol_set = symbol_set

            async def on_tweet(inner_self, tweet: Any) -> None:
                tweet_id = str(tweet.id)
                if tweet_id in inner_self._seen:
                    return
                inner_self._seen.add(tweet_id)

                text = getattr(tweet, "text", "")
                sentiment = _score_tweet(text)

                summary = TweetSummary(
                    tweet_id=tweet_id,
                    text=text,
                    author_id=None,
                    sentiment_score=sentiment,
                    public_metrics={},
                    created_at=now_utc(),
                    url=f"https://twitter.com/i/web/status/{tweet_id}",
                )

                # Determine which symbol triggered the match
                matched_symbol = next(
                    (s for s in inner_self._symbol_set if f"${s}" in text.upper()),
                    "UNKNOWN",
                )

                try:
                    if asyncio.iscoroutinefunction(callback):
                        await callback(summary, matched_symbol)
                    else:
                        callback(summary, matched_symbol)
                except Exception as exc:
                    log.error(
                        "Stream callback error",
                        error=str(exc),
                        tweet_id=tweet_id,
                    )

            async def on_errors(inner_self, errors: Any) -> None:
                log.warning("Twitter stream errors", errors=str(errors))

            async def on_disconnect(inner_self) -> None:
                log.info("Twitter stream disconnected")

        stream = _NexusStream(
            bearer_token=self._bearer_token,
            wait_on_rate_limit=True,
        )
        self._stream_client = stream

        log.info(
            "Starting Twitter filtered stream",
            symbols=symbols,
            rule_count=len(rule_ids),
        )

        try:
            await stream.filter(
                tweet_fields=["created_at", "public_metrics"],
            )
        except asyncio.CancelledError:
            log.info("Twitter stream cancelled")
        except Exception as exc:
            log.error("Twitter stream error", error=str(exc))
        finally:
            await self.delete_stream_rules(rule_ids)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _require_available(self) -> None:
        """Raise RuntimeError if Twitter is not configured."""
        if not self._available:
            raise RuntimeError(
                "Twitter bearer token not configured. "
                "Set TWITTER_BEARER_TOKEN environment variable."
            )

    @staticmethod
    def _weighted_aggregate(tweets: List[TweetSummary]) -> float:
        """
        Compute an engagement-weighted average sentiment score.

        Args:
            tweets: Scored TweetSummary objects.

        Returns:
            Weighted sentiment in [-1.0, 1.0].
        """
        if not tweets:
            return 0.0

        total_weight = 0.0
        weighted_sum = 0.0

        for tweet in tweets:
            weight = _engagement_weight(tweet.public_metrics)
            weighted_sum += tweet.sentiment_score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        return max(-1.0, min(1.0, weighted_sum / total_weight))
