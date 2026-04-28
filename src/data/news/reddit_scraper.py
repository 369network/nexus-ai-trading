"""
NEXUS ALPHA — Reddit Sentiment Scraper
=======================================
Async Reddit scraper using asyncpraw for subreddit monitoring and
symbol-level sentiment scoring.

Scraped subreddits:
  r/wallstreetbets, r/stocks, r/investing, r/CryptoCurrency,
  r/Bitcoin, r/options, r/IndiaInvestments, r/StockMarket

Environment variables:
  REDDIT_CLIENT_ID      — Reddit API app client ID
  REDDIT_CLIENT_SECRET  — Reddit API app secret
  REDDIT_USER_AGENT     — User agent string (e.g. "nexus-alpha:v1.0 by u/yourname")
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional, Set

import structlog

from src.utils.logging import get_logger
from src.utils.rate_limiter import RateLimiter
from src.utils.retry import retry_with_backoff
from src.utils.timezone import IST, UTC, now_utc

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SUBREDDITS: list[str] = [
    "wallstreetbets",
    "stocks",
    "investing",
    "CryptoCurrency",
    "Bitcoin",
    "options",
    "IndiaInvestments",
    "StockMarket",
]

# Rate limit: Reddit public API allows 60 requests/minute
_REDDIT_RATE = 60.0 / 60.0  # 1 request/second sustained (60/min)
_REDDIT_BURST = 10.0         # Allow burst of 10 before throttling

# Sentiment keyword dictionaries — no external deps required
_BULLISH_KEYWORDS: frozenset[str] = frozenset(
    [
        "moon", "bull", "bullish", "buy", "long", "calls", "surge", "pump",
        "rally", "breakout", "ath", "all time high", "uptrend", "squeeze",
        "strong", "growth", "profit", "gains", "yolo", "undervalued",
        "accumulate", "bottom", "dip", "buy the dip", "fundamentals",
        "upgrade", "beat", "exceed", "green", "rip", "send it", "hodl",
        "hold", "diamond hands", "infinite money glitch", "to the moon",
    ]
)

_BEARISH_KEYWORDS: frozenset[str] = frozenset(
    [
        "bear", "bearish", "sell", "short", "puts", "dump", "crash", "rug",
        "correction", "overvalued", "downtrend", "resistance", "ceiling",
        "weak", "loss", "red", "tank", "collapse", "bubble", "scam",
        "fraud", "bankrupt", "debt", "recession", "inflation", "rate hike",
        "miss", "disappoint", "downgrade", "warning", "puts printing",
        "paper hands", "fud", "fear", "uncertainty", "doubt",
    ]
)


# ---------------------------------------------------------------------------
# BoundedSet — fixed-capacity deduplication set
# ---------------------------------------------------------------------------


class BoundedSet:
    """
    A set-like container with a fixed maximum capacity.

    When capacity is exceeded, the oldest inserted items are evicted (FIFO).
    Used for deduplication of post IDs without unbounded memory growth.

    Args:
        maxsize: Maximum number of elements to hold.
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
class PostSummary:
    """Condensed post information for sentiment reporting."""

    post_id: str
    subreddit: str
    title: str
    score: int                      # Reddit upvote score
    num_comments: int
    sentiment_score: float          # -1.0 to 1.0
    url: str
    created_utc: datetime


@dataclass
class SentimentResult:
    """
    Aggregated Reddit sentiment result for a specific symbol.

    Attributes:
        symbol: The ticker symbol analysed (e.g. "AAPL", "BTC").
        score: Aggregate sentiment score in [-1.0, 1.0].
               -1 = overwhelmingly bearish, +1 = overwhelmingly bullish.
        mention_count: Total number of times the symbol was mentioned
                       (across posts + comments).
        post_count: Number of distinct posts that mentioned the symbol.
        top_posts: Up to 5 most-upvoted posts mentioning the symbol.
        timestamp_utc: When this result was computed (UTC).
        timestamp_ist: Same moment expressed in IST for display.
    """

    symbol: str
    score: float
    mention_count: int
    post_count: int
    top_posts: List[PostSummary]
    timestamp_utc: datetime
    timestamp_ist: datetime


# ---------------------------------------------------------------------------
# Sentiment scoring helpers
# ---------------------------------------------------------------------------


def _score_text(text: str) -> float:
    """
    Compute a sentiment score for a piece of text.

    Uses a simple keyword counting approach without external dependencies.
    Score is in [-1.0, 1.0] where -1 = very bearish, +1 = very bullish.

    Args:
        text: The text to score (title + selftext, or comment body).

    Returns:
        Sentiment score as a float in [-1.0, 1.0].
    """
    lower = text.lower()
    bullish_hits = sum(1 for kw in _BULLISH_KEYWORDS if kw in lower)
    bearish_hits = sum(1 for kw in _BEARISH_KEYWORDS if kw in lower)

    total = bullish_hits + bearish_hits
    if total == 0:
        return 0.0

    return (bullish_hits - bearish_hits) / total


def _mentions_symbol(text: str, symbol: str) -> bool:
    """
    Return True if the text mentions the given symbol.

    Handles both ticker formats (AAPL, $AAPL, BTC, $BTC, BTC/USDT, BTCUSDT).
    Case-insensitive matching with word boundary checking to avoid false positives
    (e.g. "GO" matching "GOOGLE").

    Args:
        text: The text to search.
        symbol: Ticker symbol, optionally with $ prefix.
    """
    clean = symbol.lstrip("$").upper()
    text_upper = text.upper()

    # Match $SYMBOL, SYMBOL as word, or SYMBOL/*, *SYMBOL (crypto pairs)
    patterns = [
        rf"\${re.escape(clean)}\b",
        rf"\b{re.escape(clean)}\b",
        rf"\b{re.escape(clean)}[/\-]",
    ]
    return any(re.search(p, text_upper) for p in patterns)


# ---------------------------------------------------------------------------
# RedditScraper
# ---------------------------------------------------------------------------


class RedditScraper:
    """
    Async Reddit scraper for trading sentiment analysis.

    Uses asyncpraw for non-blocking Reddit API calls with built-in
    rate limiting and post-ID deduplication.

    Usage::

        async with RedditScraper() as scraper:
            result = await scraper.get_sentiment_score("AAPL")
            print(result.score, result.mention_count)

    Environment Variables:
        REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET, REDDIT_USER_AGENT
    """

    def __init__(self) -> None:
        self._client: Any = None  # asyncpraw.Reddit
        self._seen_ids = BoundedSet(10_000)
        self._rate_limiter = RateLimiter(
            rate=_REDDIT_RATE,
            capacity=_REDDIT_BURST,
            name="reddit",
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "RedditScraper":
        await self._init_client()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    async def _init_client(self) -> None:
        """Initialise the asyncpraw Reddit client from environment variables."""
        try:
            import asyncpraw  # type: ignore[import]
        except ImportError as exc:
            raise ImportError(
                "asyncpraw is required for RedditScraper. "
                "Install with: pip install asyncpraw"
            ) from exc

        client_id = os.getenv("REDDIT_CLIENT_ID", "")
        client_secret = os.getenv("REDDIT_CLIENT_SECRET", "")
        user_agent = os.getenv(
            "REDDIT_USER_AGENT", "nexus-alpha:v1.0 (by u/nexus_alpha_bot)"
        )

        if not client_id or not client_secret:
            log.warning(
                "Reddit credentials not configured — RedditScraper will operate "
                "in read-only mode with limited rate limits",
                env_vars_needed=["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
            )

        self._client = asyncpraw.Reddit(
            client_id=client_id or "placeholder",
            client_secret=client_secret or "placeholder",
            user_agent=user_agent,
        )
        log.info("Reddit client initialised", user_agent=user_agent)

    async def close(self) -> None:
        """Close the asyncpraw client session."""
        if self._client is not None:
            try:
                await self._client.close()
            except Exception as exc:
                log.warning("Error closing Reddit client", error=str(exc))
            finally:
                self._client = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @retry_with_backoff(max_retries=3, base_delay=2.0, max_delay=30.0)
    async def get_hot_posts(
        self,
        subreddit: str,
        limit: int = 25,
    ) -> List[PostSummary]:
        """
        Fetch the top hot posts from a subreddit.

        Args:
            subreddit: Subreddit name without the r/ prefix.
            limit: Maximum number of posts to retrieve (max 100).

        Returns:
            List of PostSummary objects for each post.

        Raises:
            asyncprawcore.exceptions.ResponseException: On API errors.
            asyncio.TimeoutError: If the request takes too long.
        """
        if self._client is None:
            raise RuntimeError("RedditScraper not initialised — use async with")

        await self._rate_limiter.wait()

        results: List[PostSummary] = []
        try:
            sub = await self._client.subreddit(subreddit)
            async for post in sub.hot(limit=min(limit, 100)):
                post_id = str(post.id)
                self._seen_ids.add(post_id)

                created = datetime.fromtimestamp(post.created_utc, tz=UTC)
                text = f"{post.title} {getattr(post, 'selftext', '')}"
                sentiment = _score_text(text)

                results.append(
                    PostSummary(
                        post_id=post_id,
                        subreddit=subreddit,
                        title=post.title,
                        score=post.score,
                        num_comments=post.num_comments,
                        sentiment_score=sentiment,
                        url=f"https://reddit.com{post.permalink}",
                        created_utc=created,
                    )
                )
        except Exception as exc:
            log.error(
                "Failed to fetch hot posts",
                subreddit=subreddit,
                error=str(exc),
                error_type=type(exc).__name__,
            )
            raise

        log.debug(
            "Fetched hot posts",
            subreddit=subreddit,
            count=len(results),
        )
        return results

    async def get_sentiment_score(
        self,
        symbol: str,
        subreddits: Optional[List[str]] = None,
        post_limit: int = 25,
        include_comments: bool = False,
        max_comments_per_post: int = 20,
    ) -> SentimentResult:
        """
        Scrape multiple subreddits and compute aggregated sentiment for a symbol.

        For each subreddit: fetches hot posts, filters to those mentioning
        the symbol, scores them. Optionally also scores top-level comments
        for mentioned posts.

        Args:
            symbol: Ticker symbol to search for (e.g. "AAPL", "BTC", "$GME").
            subreddits: Override default subreddit list.
            post_limit: Number of hot posts to pull from each subreddit.
            include_comments: If True, also score comments (much slower).
            max_comments_per_post: Max comments to score per post.

        Returns:
            SentimentResult with aggregate score and metadata.
        """
        if self._client is None:
            raise RuntimeError("RedditScraper not initialised — use async with")

        target_subs = subreddits or _SUBREDDITS
        all_posts: List[PostSummary] = []
        total_mentions = 0
        scored_texts: List[float] = []

        for sub_name in target_subs:
            try:
                posts = await self.get_hot_posts(sub_name, limit=post_limit)
            except Exception as exc:
                log.warning(
                    "Skipping subreddit due to error",
                    subreddit=sub_name,
                    error=str(exc),
                )
                continue

            for post in posts:
                post_text = post.title
                if not _mentions_symbol(post_text, symbol):
                    continue

                total_mentions += 1
                all_posts.append(post)
                scored_texts.append(post.sentiment_score)

                # Optionally collect comment sentiment
                if include_comments:
                    comment_scores = await self._score_comments(
                        post.post_id, symbol, max_comments_per_post
                    )
                    scored_texts.extend(comment_scores)
                    total_mentions += len(comment_scores)

        # Compute aggregate score with upvote-weighting
        aggregate_score = self._compute_weighted_score(all_posts, scored_texts)

        # Sort posts by Reddit score for the top_posts list
        top_posts = sorted(all_posts, key=lambda p: p.score, reverse=True)[:5]

        now = now_utc()
        return SentimentResult(
            symbol=symbol,
            score=aggregate_score,
            mention_count=total_mentions,
            post_count=len(all_posts),
            top_posts=top_posts,
            timestamp_utc=now,
            timestamp_ist=now.astimezone(IST),
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _score_comments(
        self,
        post_id: str,
        symbol: str,
        max_comments: int,
    ) -> List[float]:
        """
        Fetch and score top-level comments from a post that mention the symbol.

        Args:
            post_id: Reddit post ID (without t3_ prefix).
            symbol: Symbol to filter comments by.
            max_comments: Maximum number of comments to process.

        Returns:
            List of sentiment scores for matching comments.
        """
        scores: List[float] = []
        try:
            await self._rate_limiter.wait()
            submission = await self._client.submission(id=post_id)
            await submission.comments.replace_more(limit=0)  # Don't expand MoreComments

            count = 0
            for comment in submission.comments:
                if count >= max_comments:
                    break
                body = getattr(comment, "body", "")
                if body and _mentions_symbol(body, symbol):
                    scores.append(_score_text(body))
                    count += 1
        except Exception as exc:
            log.debug(
                "Could not score comments for post",
                post_id=post_id,
                error=str(exc),
            )
        return scores

    @staticmethod
    def _compute_weighted_score(
        posts: List[PostSummary],
        raw_scores: List[float],
    ) -> float:
        """
        Compute the weighted sentiment score.

        Posts with higher Reddit upvote scores receive more weight. Pure text
        scores (from comments) receive equal weight of 1.

        Args:
            posts: PostSummary objects (for upvote weighting).
            raw_scores: Raw sentiment scores for all texts (posts + comments).

        Returns:
            Weighted average sentiment in [-1.0, 1.0].
        """
        if not raw_scores:
            return 0.0

        # Simple upvote-weighted average for post scores; equal weight for comments
        total_weight = 0.0
        weighted_sum = 0.0

        post_scores = [(p.sentiment_score, max(1, p.score)) for p in posts]
        comment_scores = [(s, 1) for s in raw_scores[len(posts):]]

        all_scored = post_scores + comment_scores
        for score, weight in all_scored:
            weighted_sum += score * weight
            total_weight += weight

        if total_weight == 0:
            return 0.0

        result = weighted_sum / total_weight
        return max(-1.0, min(1.0, result))

    # ------------------------------------------------------------------
    # Batch convenience method
    # ------------------------------------------------------------------

    async def get_batch_sentiment(
        self,
        symbols: List[str],
        delay_between_symbols: float = 1.0,
    ) -> Dict[str, SentimentResult]:
        """
        Get sentiment scores for multiple symbols sequentially.

        Adds a delay between symbols to respect rate limits and avoid
        hitting the burst capacity.

        Args:
            symbols: List of ticker symbols to score.
            delay_between_symbols: Seconds to wait between symbol requests.

        Returns:
            Dict mapping symbol -> SentimentResult.
        """
        results: Dict[str, SentimentResult] = {}
        for symbol in symbols:
            try:
                result = await self.get_sentiment_score(symbol)
                results[symbol] = result
                log.info(
                    "Reddit sentiment computed",
                    symbol=symbol,
                    score=round(result.score, 3),
                    mentions=result.mention_count,
                )
            except Exception as exc:
                log.error(
                    "Failed to get sentiment for symbol",
                    symbol=symbol,
                    error=str(exc),
                )
            if delay_between_symbols > 0:
                await asyncio.sleep(delay_between_symbols)
        return results
