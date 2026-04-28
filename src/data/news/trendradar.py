"""
NEXUS ALPHA - News Aggregator (TrendRadar)
============================================
Aggregates financial news from multiple RSS feeds concurrently.

Features:
* 30+ curated financial / crypto RSS sources
* Concurrent fetching with asyncio
* Category detection per source
* Impact scoring (high/medium/low)
* Symbol extraction via dict lookup + regex
* Deduplication by URL AND Levenshtein similarity (threshold 20)
* LRU-bounded seen-URL set (max 10 000 entries)
* Actual published timestamps from feed entries
* High-impact items queued for LLM analysis

Environment variables:
    NEWS_HIGH_IMPACT_QUEUE_SIZE    (default: 100)
"""

from __future__ import annotations

import asyncio
import collections
import logging
import queue
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

import aiohttp
import feedparser

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# RSS feed catalogue
# ---------------------------------------------------------------------------

RSS_FEEDS: Dict[str, str] = {
    # Global macro / finance
    "reuters_markets":    "https://feeds.reuters.com/reuters/businessNews",
    "bloomberg_markets":  "https://feeds.bloomberg.com/markets/news.rss",
    "ft_markets":         "https://www.ft.com/rss/home/uk",
    "wsj_markets":        "https://feeds.a.dj.com/rss/RSSMarketsMain.xml",
    "cnbc_markets":       "https://www.cnbc.com/id/10000664/device/rss/rss.html",
    "marketwatch":        "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "investing_com":      "https://www.investing.com/rss/news.rss",
    "seeking_alpha":      "https://seekingalpha.com/feed.xml",
    "zerohedge":          "https://feeds.feedburner.com/zerohedge/feed",
    "the_economist":      "https://www.economist.com/finance-and-economics/rss.xml",

    # Crypto-specific
    "coindesk":           "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "cointelegraph":      "https://cointelegraph.com/rss",
    "decrypt":            "https://decrypt.co/feed",
    "theblock":           "https://www.theblock.co/rss.xml",
    "bitcoin_magazine":   "https://bitcoinmagazine.com/feed",
    "cryptonews":         "https://cryptonews.com/news/feed/",
    "crypto_briefing":    "https://cryptobriefing.com/feed/",
    "ambcrypto":          "https://ambcrypto.com/feed/",
    "newsbtc":            "https://www.newsbtc.com/feed/",
    "beincrypto":         "https://beincrypto.com/feed/",

    # India / NSE
    "economic_times":     "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "moneycontrol":       "https://www.moneycontrol.com/rss/business.xml",
    "livemint":           "https://www.livemint.com/rss/markets",
    "business_standard":  "https://www.business-standard.com/rss/markets-news.rss",
    "financial_express":  "https://www.financialexpress.com/market/feed/",
    "ndtv_profit":        "https://feeds.feedburner.com/ndtvprofit-latest",

    # Forex / macro
    "forexlive":          "https://www.forexlive.com/feed",
    "dailyfx":            "https://www.dailyfx.com/feeds/all",
    "fxstreet":           "https://www.fxstreet.com/rss/news",

    # Alternative / macro signals
    "federal_reserve":    "https://www.federalreserve.gov/feeds/h15.xml",
    "ecb":                "https://www.ecb.europa.eu/rss/press.html",
}

# ---------------------------------------------------------------------------
# Category and impact rules
# ---------------------------------------------------------------------------

_CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    "crypto":     ["bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain",
                   "defi", "nft", "altcoin", "binance", "coinbase"],
    "forex":      ["dollar", "eur/usd", "gbp", "forex", "fed rate", "central bank",
                   "monetary policy", "inflation", "interest rate"],
    "equity_in":  ["nifty", "sensex", "bse", "nse", "sebi", "indian stock",
                   "reliance", "tcs", "infosys", "hdfc"],
    "equity_us":  ["s&p 500", "nasdaq", "dow jones", "nyse", "earnings",
                   "ipo", "sec", "federal reserve"],
    "macro":      ["gdp", "cpi", "unemployment", "trade war", "recession",
                   "geopolitical", "oil", "commodity"],
}

_HIGH_IMPACT_KEYWORDS = [
    "crash", "surge", "all-time high", "bank failure", "bankruptcy",
    "fed rate hike", "rate cut", "rate decision", "emergency", "halt",
    "circuit breaker", "war", "sanction", "hack", "exploit",
    "whale", "liquidation cascade", "contagion", "bailout",
]

_MEDIUM_IMPACT_KEYWORDS = [
    "earnings", "revenue", "profit", "forecast", "guidance",
    "upgrade", "downgrade", "acquisition", "merger", "ipo",
    "regulatory", "inflation", "gdp", "employment",
]

# ---------------------------------------------------------------------------
# Symbol dictionary for NER-lite extraction
# ---------------------------------------------------------------------------

_SYMBOL_DICT: Dict[str, str] = {
    # Crypto
    "bitcoin": "BTC", "btc": "BTC", "ethereum": "ETH", "eth": "ETH",
    "solana": "SOL", "sol": "SOL", "bnb": "BNB", "xrp": "XRP",
    "cardano": "ADA", "ada": "ADA", "dogecoin": "DOGE", "doge": "DOGE",
    "matic": "MATIC", "polygon": "MATIC", "avax": "AVAX", "dot": "DOT",
    "link": "LINK", "ltc": "LTC", "litecoin": "LTC",
    # US equities
    "apple": "AAPL", "microsoft": "MSFT", "google": "GOOGL",
    "alphabet": "GOOGL", "amazon": "AMZN", "meta": "META",
    "tesla": "TSLA", "nvidia": "NVDA", "netflix": "NFLX",
    # India
    "reliance": "RELIANCE", "tcs": "TCS", "infosys": "INFY",
    "hdfc bank": "HDFCBANK", "icici bank": "ICICIBANK",
    "wipro": "WIPRO", "itc": "ITC", "sbi": "SBIN",
    # Indices
    "nifty": "NIFTY", "sensex": "SENSEX", "s&p 500": "SPX",
    "nasdaq": "QQQ", "dow jones": "DJI",
    # Forex
    "eur/usd": "EUR_USD", "gbp/usd": "GBP_USD", "usd/jpy": "USD_JPY",
}

_TICKER_REGEX = re.compile(r"\b([A-Z]{2,5})\b")


# ---------------------------------------------------------------------------
# Bounded LRU set for seen URLs
# ---------------------------------------------------------------------------

class BoundedSet:
    """
    A set with a maximum capacity that evicts oldest entries (LRU order)
    when the limit is exceeded.

    Parameters
    ----------
    maxsize:
        Maximum number of entries.
    """

    def __init__(self, maxsize: int = 10_000) -> None:
        self._maxsize = maxsize
        self._data: "collections.OrderedDict[str, None]" = collections.OrderedDict()

    def __contains__(self, item: str) -> bool:
        if item in self._data:
            # Move to end (most-recently used)
            self._data.move_to_end(item)
            return True
        return False

    def add(self, item: str) -> None:
        if item in self._data:
            self._data.move_to_end(item)
            return
        self._data[item] = None
        if len(self._data) > self._maxsize:
            self._data.popitem(last=False)  # evict oldest

    def __len__(self) -> int:
        return len(self._data)


# ---------------------------------------------------------------------------
# News item
# ---------------------------------------------------------------------------

@dataclass
class NewsItem:
    """A single parsed news article."""

    url:       str
    title:     str
    source:    str
    published: datetime
    summary:   str         = ""
    category:  str         = "general"
    impact:    str         = "low"
    symbols:   List[str]   = field(default_factory=list)


# ---------------------------------------------------------------------------
# Aggregator
# ---------------------------------------------------------------------------

class NewsAggregator:
    """
    Concurrent RSS news aggregator with deduplication and impact scoring.

    Usage
    -----
    ::

        agg = NewsAggregator()
        items = await agg.fetch_all_feeds()
    """

    def __init__(
        self,
        feeds: Optional[Dict[str, str]] = None,
        high_impact_queue_size: int = 100,
    ) -> None:
        self._feeds = feeds or RSS_FEEDS
        self._seen_urls  = BoundedSet(maxsize=10_000)
        self._seen_titles: List[str] = []   # for Levenshtein dedup
        self.high_impact_queue: queue.Queue = queue.Queue(
            maxsize=high_impact_queue_size
        )

    # ------------------------------------------------------------------

    async def fetch_all_feeds(self) -> List[NewsItem]:
        """
        Fetch and parse all configured RSS feeds concurrently.

        Returns
        -------
        List[NewsItem]
            Deduplicated, scored news items sorted newest-first.
        """
        connector = aiohttp.TCPConnector(limit=20)
        timeout   = aiohttp.ClientTimeout(total=15)

        async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
            tasks = [
                self._fetch_feed(session, source, url)
                for source, url in self._feeds.items()
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        items: List[NewsItem] = []
        for result in results:
            if isinstance(result, Exception):
                logger.debug("Feed fetch error: %s", result)
                continue
            items.extend(result)

        # Sort newest first
        items.sort(key=lambda x: x.published, reverse=True)
        return items

    # ------------------------------------------------------------------

    async def _fetch_feed(
        self,
        session: aiohttp.ClientSession,
        source: str,
        url: str,
    ) -> List[NewsItem]:
        """Fetch and parse a single RSS feed."""
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    return []
                text = await resp.text(errors="replace")
        except Exception as exc:
            logger.debug("Feed %s error: %s", source, exc)
            return []

        feed = feedparser.parse(text)
        items: List[NewsItem] = []

        for entry in feed.entries:
            item = self._parse_entry(entry, source)
            if item is None:
                continue
            items.append(item)

            if item.impact == "high":
                try:
                    self.high_impact_queue.put_nowait(item)
                except queue.Full:
                    pass

        return items

    # ------------------------------------------------------------------

    def _parse_entry(
        self,
        entry: Any,
        source: str,
    ) -> Optional[NewsItem]:
        """Parse a single feedparser entry into a NewsItem."""
        url   = getattr(entry, "link",  "") or ""
        title = getattr(entry, "title", "") or ""

        if not url or not title:
            return None

        # URL-based deduplication
        if url in self._seen_urls:
            return None
        self._seen_urls.add(url)

        # Title-based similarity deduplication (Levenshtein distance < 20)
        if self._is_duplicate_title(title):
            return None
        self._seen_titles.append(title)
        if len(self._seen_titles) > 5_000:
            self._seen_titles = self._seen_titles[-2_500:]  # trim

        # Published timestamp
        published = self._extract_timestamp(entry)

        summary = getattr(entry, "summary", "") or ""
        category = self._categorize(source, title)
        impact   = self._assess_impact(title, category)
        symbols  = self._extract_symbols(title)

        return NewsItem(
            url=url,
            title=title,
            source=source,
            published=published,
            summary=summary[:500],
            category=category,
            impact=impact,
            symbols=symbols,
        )

    # ------------------------------------------------------------------

    def _extract_timestamp(self, entry: Any) -> datetime:
        """Extract the published time from a feedparser entry."""
        if hasattr(entry, "published_parsed") and entry.published_parsed:
            import calendar
            ts = calendar.timegm(entry.published_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        # Fallback: updated_parsed
        if hasattr(entry, "updated_parsed") and entry.updated_parsed:
            import calendar
            ts = calendar.timegm(entry.updated_parsed)
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return datetime.now(tz=timezone.utc)

    # ------------------------------------------------------------------

    def _categorize(self, source: str, title: str) -> str:
        """
        Determine the market category for a news item.

        Parameters
        ----------
        source:
            Feed source identifier.
        title:
            Article headline.

        Returns
        -------
        str
            Category string: ``"crypto"``, ``"forex"``, ``"equity_in"``,
            ``"equity_us"``, ``"macro"``, or ``"general"``.
        """
        # Source-based shortcuts
        source_map = {
            "coindesk": "crypto", "cointelegraph": "crypto",
            "decrypt":  "crypto", "theblock":      "crypto",
            "newsbtc":  "crypto", "beincrypto":    "crypto",
            "forexlive":"forex",  "fxstreet":      "forex",
            "economic_times": "equity_in", "moneycontrol": "equity_in",
            "livemint":        "equity_in",
        }
        if source in source_map:
            return source_map[source]

        title_lower = title.lower()
        for category, keywords in _CATEGORY_KEYWORDS.items():
            if any(kw in title_lower for kw in keywords):
                return category

        return "general"

    # ------------------------------------------------------------------

    def _assess_impact(self, title: str, category: str) -> str:
        """
        Classify the market impact of a headline.

        Parameters
        ----------
        title:
            Article headline.
        category:
            Detected market category.

        Returns
        -------
        str
            ``"high"``, ``"medium"``, or ``"low"``.
        """
        title_lower = title.lower()

        if any(kw in title_lower for kw in _HIGH_IMPACT_KEYWORDS):
            return "high"

        if any(kw in title_lower for kw in _MEDIUM_IMPACT_KEYWORDS):
            return "medium"

        return "low"

    # ------------------------------------------------------------------

    def _extract_symbols(self, title: str) -> List[str]:
        """
        Extract market symbols from a headline using a dict lookup
        followed by ticker-pattern regex.

        Parameters
        ----------
        title:
            Article headline.

        Returns
        -------
        List[str]
            Deduplicated list of extracted symbol strings.
        """
        title_lower = title.lower()
        found: Set[str] = set()

        # Dict-based lookup (longest match first)
        for phrase in sorted(_SYMBOL_DICT, key=len, reverse=True):
            if phrase in title_lower:
                found.add(_SYMBOL_DICT[phrase])

        # Regex: ALL-CAPS tickers 2-5 chars
        for match in _TICKER_REGEX.finditer(title):
            sym = match.group(1)
            # Filter common English words
            if sym not in ("THE", "AND", "FOR", "ARE", "NOT",
                           "BUT", "ALL", "NEW", "TOP", "BIG", "USA"):
                found.add(sym)

        return list(found)

    # ------------------------------------------------------------------

    @staticmethod
    def _levenshtein(a: str, b: str) -> int:
        """
        Compute the Levenshtein edit distance between strings *a* and *b*.

        Returns early if the distance already exceeds 20.
        """
        if abs(len(a) - len(b)) > 20:
            return 21  # fast path

        if not a:
            return len(b)
        if not b:
            return len(a)

        prev = list(range(len(b) + 1))
        for i, ca in enumerate(a):
            curr = [i + 1]
            for j, cb in enumerate(b):
                curr.append(min(
                    prev[j + 1] + 1,
                    curr[j]     + 1,
                    prev[j] + (0 if ca == cb else 1),
                ))
                if curr[-1] > 20:
                    return 21   # early exit
            prev = curr

        return prev[len(b)]

    def _is_duplicate_title(self, title: str) -> bool:
        """Return True if *title* is similar to a recently seen title."""
        title_short = title[:80]
        for seen in self._seen_titles[-200:]:   # only check last 200
            if self._levenshtein(title_short, seen[:80]) < 20:
                return True
        return False
