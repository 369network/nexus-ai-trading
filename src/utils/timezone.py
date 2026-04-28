"""
NEXUS ALPHA — Timezone Utilities
==================================
UTC conversion helpers, market-hours checking, and next-open calculation
for all supported markets (Crypto, Forex, Indian Stocks, US Stocks, Commodities).
"""

from __future__ import annotations

import datetime
from enum import Enum
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Named timezone objects
# ---------------------------------------------------------------------------

UTC = ZoneInfo("UTC")
IST = ZoneInfo("Asia/Kolkata")       # India Standard Time (UTC+5:30)
ET = ZoneInfo("America/New_York")    # Eastern Time (UTC-5/-4 depending on DST)
JST = ZoneInfo("Asia/Tokyo")         # Japan Standard Time (UTC+9)
GMT = ZoneInfo("Europe/London")      # London (UTC+0/+1 with BST)
AEST = ZoneInfo("Australia/Sydney")  # Sydney (UTC+10/+11)


# ---------------------------------------------------------------------------
# Conversion helpers
# ---------------------------------------------------------------------------


def to_utc(dt: datetime.datetime) -> datetime.datetime:
    """
    Convert an aware datetime to UTC.
    If dt is naive, it is assumed to already be UTC.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def to_ist(dt: datetime.datetime) -> datetime.datetime:
    """Convert datetime to IST (India Standard Time, UTC+5:30)."""
    return to_utc(dt).astimezone(IST)


def to_et(dt: datetime.datetime) -> datetime.datetime:
    """Convert datetime to ET (US Eastern Time, auto-handles DST)."""
    return to_utc(dt).astimezone(ET)


def to_jst(dt: datetime.datetime) -> datetime.datetime:
    """Convert datetime to JST (Japan Standard Time, UTC+9)."""
    return to_utc(dt).astimezone(JST)


def to_london(dt: datetime.datetime) -> datetime.datetime:
    """Convert datetime to London time (handles BST)."""
    return to_utc(dt).astimezone(GMT)


def now_utc() -> datetime.datetime:
    """Return the current moment as a timezone-aware UTC datetime."""
    return datetime.datetime.now(tz=UTC)


def now_ist() -> datetime.datetime:
    """Return the current moment in IST."""
    return now_utc().astimezone(IST)


def now_et() -> datetime.datetime:
    """Return the current moment in ET."""
    return now_utc().astimezone(ET)


def utc_timestamp() -> float:
    """Return current UTC unix timestamp (float seconds)."""
    return datetime.datetime.now(tz=UTC).timestamp()


def from_timestamp(ts: float, tz: ZoneInfo = UTC) -> datetime.datetime:
    """Convert a unix timestamp to a timezone-aware datetime."""
    return datetime.datetime.fromtimestamp(ts, tz=tz)


def format_ist(dt: datetime.datetime) -> str:
    """Format a datetime in IST as 'YYYY-MM-DD HH:MM:SS IST'."""
    return to_ist(dt).strftime("%Y-%m-%d %H:%M:%S IST")


def format_et(dt: datetime.datetime) -> str:
    """Format a datetime in ET as 'YYYY-MM-DD HH:MM:SS ET'."""
    return to_et(dt).strftime("%Y-%m-%d %H:%M:%S %Z")


# ---------------------------------------------------------------------------
# Market hours definitions
# ---------------------------------------------------------------------------


class Market(str, Enum):
    CRYPTO = "crypto"
    FOREX = "forex"
    INDIAN_STOCKS = "indian_stocks"
    US_STOCKS = "us_stocks"
    COMMODITIES = "commodities"


# Indian public holidays (approximate; update yearly)
_INDIAN_HOLIDAYS_2024_2025: frozenset[datetime.date] = frozenset(
    [
        # 2024
        datetime.date(2024, 1, 26),   # Republic Day
        datetime.date(2024, 3, 25),   # Holi
        datetime.date(2024, 3, 29),   # Good Friday
        datetime.date(2024, 4, 14),   # Dr Ambedkar Jayanti / Ram Navami
        datetime.date(2024, 4, 17),   # Ram Navami
        datetime.date(2024, 5, 1),    # Maharashtra Day
        datetime.date(2024, 8, 15),   # Independence Day
        datetime.date(2024, 10, 2),   # Gandhi Jayanti
        datetime.date(2024, 11, 1),   # Diwali Laxmi Pujan
        datetime.date(2024, 11, 15),  # Gurunanak Jayanti
        datetime.date(2024, 12, 25),  # Christmas
        # 2025
        datetime.date(2025, 2, 26),   # Mahashivratri
        datetime.date(2025, 3, 14),   # Holi
        datetime.date(2025, 4, 14),   # Dr Ambedkar Jayanti
        datetime.date(2025, 4, 18),   # Good Friday
        datetime.date(2025, 5, 1),    # Maharashtra Day
        datetime.date(2025, 8, 15),   # Independence Day
        datetime.date(2025, 10, 2),   # Gandhi Jayanti
        datetime.date(2025, 10, 20),  # Diwali Laxmi Pujan
        datetime.date(2025, 10, 21),  # Diwali-Balipratipada
        datetime.date(2025, 11, 5),   # Gurunanak Jayanti
        datetime.date(2025, 12, 25),  # Christmas
    ]
)

# US federal holidays (approximate)
_US_HOLIDAYS_2024_2025: frozenset[datetime.date] = frozenset(
    [
        datetime.date(2024, 1, 1),    # New Year's Day
        datetime.date(2024, 1, 15),   # MLK Day
        datetime.date(2024, 2, 19),   # Presidents' Day
        datetime.date(2024, 3, 29),   # Good Friday
        datetime.date(2024, 5, 27),   # Memorial Day
        datetime.date(2024, 6, 19),   # Juneteenth
        datetime.date(2024, 7, 4),    # Independence Day
        datetime.date(2024, 9, 2),    # Labor Day
        datetime.date(2024, 11, 28),  # Thanksgiving
        datetime.date(2024, 12, 25),  # Christmas
        datetime.date(2025, 1, 1),    # New Year's Day
        datetime.date(2025, 1, 20),   # MLK Day
        datetime.date(2025, 2, 17),   # Presidents' Day
        datetime.date(2025, 4, 18),   # Good Friday
        datetime.date(2025, 5, 26),   # Memorial Day
        datetime.date(2025, 6, 19),   # Juneteenth
        datetime.date(2025, 7, 4),    # Independence Day
        datetime.date(2025, 9, 1),    # Labor Day
        datetime.date(2025, 11, 27),  # Thanksgiving
        datetime.date(2025, 12, 25),  # Christmas
    ]
)


# ---------------------------------------------------------------------------
# Market status functions
# ---------------------------------------------------------------------------


def is_market_open(market: Market, at: datetime.datetime | None = None) -> bool:
    """
    Return True if the specified market is currently open (or at given time).

    Args:
        market: The market to check.
        at: Datetime to check (UTC). Defaults to now.
    """
    check_time = to_utc(at) if at else now_utc()

    if market == Market.CRYPTO:
        return _is_crypto_open(check_time)
    elif market == Market.FOREX:
        return _is_forex_open(check_time)
    elif market == Market.INDIAN_STOCKS:
        return _is_indian_stocks_open(check_time)
    elif market == Market.US_STOCKS:
        return _is_us_stocks_open(check_time)
    elif market == Market.COMMODITIES:
        return _is_commodities_open(check_time)
    else:
        raise ValueError(f"Unknown market: {market}")


def next_market_open(
    market: Market, after: datetime.datetime | None = None
) -> datetime.datetime:
    """
    Return the next open time for the given market (as UTC datetime).

    Args:
        market: The market to check.
        after: Starting point (UTC). Defaults to now.
    """
    start = to_utc(after) if after else now_utc()

    if is_market_open(market, start):
        # Already open; return current time
        return start

    # Step forward in 1-minute increments (max 7 days)
    candidate = start + datetime.timedelta(minutes=1)
    limit = start + datetime.timedelta(days=7)

    while candidate <= limit:
        if is_market_open(market, candidate):
            return candidate
        candidate += datetime.timedelta(minutes=1)

    raise RuntimeError(f"Could not find next open for {market} within 7 days")


def time_until_market_open(
    market: Market, at: datetime.datetime | None = None
) -> datetime.timedelta:
    """Return timedelta until market opens. Zero if already open."""
    check = to_utc(at) if at else now_utc()
    if is_market_open(market, check):
        return datetime.timedelta(0)
    next_open = next_market_open(market, check)
    return next_open - check


def market_session(at: datetime.datetime | None = None) -> dict[Market, bool]:
    """Return open/closed status for all markets."""
    check = to_utc(at) if at else now_utc()
    return {m: is_market_open(m, check) for m in Market}


# ---------------------------------------------------------------------------
# Per-market open checks (private)
# ---------------------------------------------------------------------------


def _is_crypto_open(_utc: datetime.datetime) -> bool:
    """Crypto trades 24/7."""
    return True


def _is_forex_open(utc: datetime.datetime) -> bool:
    """
    Forex is open Mon 00:00 UTC through Fri 21:00 UTC.
    Closed on weekends (Fri 21:00 UTC → Sun 22:00 UTC).
    """
    weekday = utc.weekday()  # 0=Monday, 6=Sunday

    # Full weekend: Saturday and Sunday (before 22:00 UTC)
    if weekday == 5:  # Saturday
        return False
    if weekday == 6 and utc.hour < 22:  # Sunday before 22:00 UTC
        return False
    if weekday == 4 and utc.hour >= 21:  # Friday after 21:00 UTC
        return False

    return True


def _is_indian_stocks_open(utc: datetime.datetime) -> bool:
    """
    NSE / BSE: Mon–Fri 09:15–15:30 IST = 03:45–10:00 UTC (approx).
    Accounts for IST = UTC+5:30.
    """
    local = to_ist(utc)
    date = local.date()

    # Weekends
    if date.weekday() >= 5:
        return False

    # Indian public holidays
    if date in _INDIAN_HOLIDAYS_2024_2025:
        return False

    # Session: 09:15–15:30 IST
    open_time = datetime.time(9, 15)
    close_time = datetime.time(15, 30)
    return open_time <= local.time() <= close_time


def _is_us_stocks_open(utc: datetime.datetime) -> bool:
    """
    NYSE / NASDAQ: Mon–Fri 09:30–16:00 ET.
    Accounts for ET and DST.
    """
    local = to_et(utc)
    date = local.date()

    # Weekends
    if date.weekday() >= 5:
        return False

    # US market holidays
    if date in _US_HOLIDAYS_2024_2025:
        return False

    # Session: 09:30–16:00 ET
    open_time = datetime.time(9, 30)
    close_time = datetime.time(16, 0)
    return open_time <= local.time() <= close_time


def _is_commodities_open(utc: datetime.datetime) -> bool:
    """
    Spot gold/silver and oil via OANDA/IBKR: near-24/5 with brief closures.
    Approximate: Mon 00:00 UTC – Fri 21:00 UTC (same as forex).
    Oil has a brief daily maintenance window (21:00–22:00 UTC).
    """
    return _is_forex_open(utc)


# ---------------------------------------------------------------------------
# Forex session helpers
# ---------------------------------------------------------------------------


def active_forex_session(at: datetime.datetime | None = None) -> list[str]:
    """
    Return the name(s) of the active forex session(s) at the given UTC time.
    Sessions can overlap (e.g., London/NY overlap 13:00–17:00 UTC).
    """
    utc = to_utc(at) if at else now_utc()
    hour = utc.hour
    sessions = []

    # Sydney: 22:00–07:00 UTC
    if hour >= 22 or hour < 7:
        sessions.append("sydney")

    # Tokyo: 00:00–09:00 UTC
    if 0 <= hour < 9:
        sessions.append("tokyo")

    # London: 08:00–17:00 UTC
    if 8 <= hour < 17:
        sessions.append("london")

    # New York: 13:00–22:00 UTC
    if 13 <= hour < 22:
        sessions.append("new_york")

    return sessions


def is_london_ny_overlap(at: datetime.datetime | None = None) -> bool:
    """Return True if we're in the London/NY overlap (13:00–17:00 UTC)."""
    utc = to_utc(at) if at else now_utc()
    return 13 <= utc.hour < 17
