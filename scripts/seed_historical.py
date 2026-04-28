#!/usr/bin/env python3
"""
NEXUS ALPHA - Historical Data Seeder
Downloads and stores 90 days of historical OHLCV data for all enabled markets.
Run once after database setup, then periodically to fill gaps.

Usage:
    python scripts/seed_historical.py
    python scripts/seed_historical.py --market crypto --symbol BTCUSDT
    python scripts/seed_historical.py --days 30 --force
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

logger = logging.getLogger("nexus_alpha.seed_historical")


# ---------------------------------------------------------------------------
# Dependencies: tqdm for progress, conditional imports per market
# ---------------------------------------------------------------------------

def _require(pkg: str) -> None:
    """Import check — raise with install hint on failure."""
    import importlib
    try:
        importlib.import_module(pkg.split(".")[0])
    except ImportError:
        raise SystemExit(f"Missing package '{pkg}'. Install with: pip install {pkg.split('.')[0]}")


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

async def get_latest_ts(
    sb: Any, market: str, symbol: str, timeframe: str
) -> Optional[datetime]:
    """Return the timestamp of the most recent stored candle, or None."""
    try:
        result = (
            sb.table("market_data")
            .select("timestamp")
            .eq("market", market)
            .eq("symbol", symbol)
            .eq("timeframe", timeframe)
            .order("timestamp", desc=True)
            .limit(1)
            .execute()
        )
        if result.data:
            ts_str = result.data[0]["timestamp"]
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        return None
    except Exception as exc:
        logger.warning("Could not query latest ts for %s/%s/%s: %s", market, symbol, timeframe, exc)
        return None


async def store_candles_batch(
    sb: Any,
    market: str,
    symbol: str,
    timeframe: str,
    candles: List[Dict[str, Any]],
) -> int:
    """Upsert a batch of candles. Returns count stored."""
    if not candles:
        return 0

    rows = []
    for c in candles:
        rows.append({
            "market": market,
            "symbol": symbol,
            "timeframe": timeframe,
            "timestamp": c["timestamp"],
            "open": float(c["open"]),
            "high": float(c["high"]),
            "low":  float(c["low"]),
            "close": float(c["close"]),
            "volume": float(c["volume"]),
            "is_closed": True,
        })

    try:
        # Upsert in chunks of 500
        chunk_size = 500
        total = 0
        for i in range(0, len(rows), chunk_size):
            chunk = rows[i:i + chunk_size]
            sb.table("market_data").upsert(
                chunk,
                on_conflict="market,symbol,timeframe,timestamp",
            ).execute()
            total += len(chunk)
        return total
    except Exception as exc:
        logger.error("DB upsert failed for %s/%s/%s: %s", market, symbol, timeframe, exc)
        return 0


# ---------------------------------------------------------------------------
# Market-specific fetchers
# ---------------------------------------------------------------------------

async def fetch_crypto_binance(
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime,
    api_key: str = "",
    api_secret: str = "",
) -> List[Dict[str, Any]]:
    """Fetch klines from Binance REST API (no auth needed for public data)."""
    import aiohttp

    TF_MAP = {
        "1m": "1m", "3m": "3m", "5m": "5m", "15m": "15m",
        "30m": "30m", "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1w",
    }
    interval = TF_MAP.get(timeframe, "1h")

    base_url = "https://api.binance.com/api/v3/klines"
    candles: List[Dict[str, Any]] = []
    start_ms = int(since.timestamp() * 1000)
    end_ms   = int(until.timestamp() * 1000)
    limit    = 1000

    async with aiohttp.ClientSession() as session:
        while start_ms < end_ms:
            params = {
                "symbol":    symbol,
                "interval":  interval,
                "startTime": start_ms,
                "endTime":   end_ms,
                "limit":     limit,
            }
            async with session.get(base_url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Binance API error %d: %s", resp.status, text[:200])
                    break
                data = await resp.json()

            if not data:
                break

            for k in data:
                candles.append({
                    "timestamp": datetime.fromtimestamp(k[0] / 1000, tz=timezone.utc).isoformat(),
                    "open":  k[1],
                    "high":  k[2],
                    "low":   k[3],
                    "close": k[4],
                    "volume": k[5],
                })

            last_open_ts = data[-1][0]
            if last_open_ts >= end_ms:
                break
            start_ms = last_open_ts + 1
            await asyncio.sleep(0.1)  # Rate limit: 1200 req/min

    return candles


async def fetch_forex_oanda(
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime,
    account_id: str,
    access_token: str,
    practice: bool = True,
) -> List[Dict[str, Any]]:
    """Fetch candles from OANDA REST v20 API."""
    import aiohttp

    TF_MAP = {
        "1m": "M1", "5m": "M5", "15m": "M15", "30m": "M30",
        "1h": "H1", "4h": "H4", "1d": "D", "1w": "W",
    }
    granularity = TF_MAP.get(timeframe, "H1")
    base = "https://api-fxtrade.oanda.com" if not practice else "https://api-fxpractice.oanda.com"
    url = f"{base}/v3/instruments/{symbol}/candles"

    candles: List[Dict[str, Any]] = []
    from_dt = since
    count = 500

    headers = {"Authorization": f"Bearer {access_token}"}
    async with aiohttp.ClientSession(headers=headers) as session:
        while from_dt < until:
            params = {
                "granularity": granularity,
                "from": from_dt.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "to": min(until, from_dt + timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "count": count,
                "price": "M",
            }
            async with session.get(url, params=params) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("OANDA error %d: %s", resp.status, text[:200])
                    break
                data = await resp.json()

            raw = data.get("candles", [])
            if not raw:
                break

            for c in raw:
                if not c.get("complete", True):
                    continue
                mid = c.get("mid", {})
                candles.append({
                    "timestamp": c["time"].replace("Z", "+00:00"),
                    "open":  mid.get("o", 0),
                    "high":  mid.get("h", 0),
                    "low":   mid.get("l", 0),
                    "close": mid.get("c", 0),
                    "volume": c.get("volume", 0),
                })

            last_time = raw[-1]["time"]
            from_dt = datetime.fromisoformat(last_time.replace("Z", "+00:00")) + timedelta(seconds=1)
            await asyncio.sleep(0.2)

    return candles


async def fetch_indian_stocks_zerodha(
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime,
    api_key: str,
    access_token: str,
) -> List[Dict[str, Any]]:
    """Fetch OHLCV from Zerodha Kite API."""
    try:
        from kiteconnect import KiteConnect
    except ImportError:
        logger.warning("kiteconnect not installed — falling back to nsepython for %s", symbol)
        return await _fetch_india_nsepython(symbol, timeframe, since, until)

    kite = KiteConnect(api_key=api_key)
    kite.set_access_token(access_token)

    TF_MAP = {
        "1m": "minute", "3m": "3minute", "5m": "5minute",
        "15m": "15minute", "30m": "30minute", "1h": "60minute",
        "1d": "day", "1w": "week",
    }
    interval = TF_MAP.get(timeframe, "60minute")

    # Find instrument token
    instruments = kite.instruments("NSE")
    token = None
    for inst in instruments:
        if inst["tradingsymbol"] == symbol:
            token = inst["instrument_token"]
            break

    if token is None:
        logger.error("Symbol %s not found in Kite NSE instruments.", symbol)
        return []

    candles = []
    from_dt = since

    while from_dt < until:
        # Kite allows max 60 days for intraday, 2000 days for EOD
        chunk_end = min(until, from_dt + timedelta(days=59 if timeframe not in ("1d", "1w") else 2000))
        try:
            data = kite.historical_data(
                instrument_token=token,
                from_date=from_dt,
                to_date=chunk_end,
                interval=interval,
                continuous=False,
            )
            for c in data:
                candles.append({
                    "timestamp": c["date"].astimezone(timezone.utc).isoformat(),
                    "open":  c["open"],
                    "high":  c["high"],
                    "low":   c["low"],
                    "close": c["close"],
                    "volume": c["volume"],
                })
        except Exception as exc:
            logger.error("Kite fetch error for %s: %s", symbol, exc)
            break

        from_dt = chunk_end + timedelta(seconds=1)
        await asyncio.sleep(0.5)

    return candles


async def _fetch_india_nsepython(
    symbol: str, timeframe: str, since: datetime, until: datetime
) -> List[Dict[str, Any]]:
    """Fallback: use nsepython for EOD data."""
    try:
        import nsepython  # type: ignore
    except ImportError:
        logger.error("nsepython not installed. pip install nsepython")
        return []

    # nsepython is synchronous — run in executor
    loop = asyncio.get_event_loop()
    try:
        data = await loop.run_in_executor(
            None,
            lambda: nsepython.equity_history(
                symbol=symbol,
                series="EQ",
                start_date=since.strftime("%d-%m-%Y"),
                end_date=until.strftime("%d-%m-%Y"),
            ),
        )
        candles = []
        for _, row in data.iterrows():
            candles.append({
                "timestamp": datetime.strptime(row["CH_TIMESTAMP"], "%Y-%m-%d").replace(tzinfo=timezone.utc).isoformat(),
                "open":  row["CH_OPENING_PRICE"],
                "high":  row["CH_TRADE_HIGH_PRICE"],
                "low":   row["CH_TRADE_LOW_PRICE"],
                "close": row["CH_CLOSING_PRICE"],
                "volume": row["CH_TOT_TRADED_QTY"],
            })
        return candles
    except Exception as exc:
        logger.error("nsepython fetch failed for %s: %s", symbol, exc)
        return []


async def fetch_us_stocks_alpaca(
    symbol: str,
    timeframe: str,
    since: datetime,
    until: datetime,
    api_key: str,
    api_secret: str,
    paper: bool = True,
) -> List[Dict[str, Any]]:
    """Fetch bars from Alpaca Markets API, with yfinance fallback."""
    try:
        import aiohttp

        TF_MAP = {
            "1m": "1Min", "5m": "5Min", "15m": "15Min", "30m": "30Min",
            "1h": "1Hour", "4h": "4Hour", "1d": "1Day",
        }
        tf = TF_MAP.get(timeframe, "1Hour")
        base = "https://data.alpaca.markets/v2"
        url = f"{base}/stocks/{symbol}/bars"

        headers = {
            "APCA-API-KEY-ID":     api_key,
            "APCA-API-SECRET-KEY": api_secret,
        }
        candles = []
        page_token = None

        async with aiohttp.ClientSession(headers=headers) as session:
            while True:
                params: Dict[str, Any] = {
                    "timeframe": tf,
                    "start":     since.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "end":       until.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "limit":     10000,
                    "adjustment": "all",
                }
                if page_token:
                    params["page_token"] = page_token

                async with session.get(url, params=params) as resp:
                    if resp.status != 200:
                        logger.warning("Alpaca %d — falling back to yfinance for %s", resp.status, symbol)
                        return await _fetch_us_yfinance(symbol, timeframe, since, until)
                    data = await resp.json()

                bars = data.get("bars", [])
                for b in bars:
                    candles.append({
                        "timestamp": b["t"],
                        "open":  b["o"],
                        "high":  b["h"],
                        "low":   b["l"],
                        "close": b["c"],
                        "volume": b["v"],
                    })

                page_token = data.get("next_page_token")
                if not page_token:
                    break
                await asyncio.sleep(0.2)

        return candles

    except Exception as exc:
        logger.warning("Alpaca fetch failed (%s) — falling back to yfinance for %s", exc, symbol)
        return await _fetch_us_yfinance(symbol, timeframe, since, until)


async def _fetch_us_yfinance(
    symbol: str, timeframe: str, since: datetime, until: datetime
) -> List[Dict[str, Any]]:
    """yfinance fallback for US stock data."""
    try:
        import yfinance as yf
    except ImportError:
        logger.error("yfinance not installed. pip install yfinance")
        return []

    TF_MAP = {
        "1m": "1m", "5m": "5m", "15m": "15m", "30m": "30m",
        "1h": "1h", "4h": "4h", "1d": "1d", "1w": "1wk",
    }
    interval = TF_MAP.get(timeframe, "1h")
    loop = asyncio.get_event_loop()

    def _download():
        ticker = yf.Ticker(symbol)
        hist = ticker.history(
            start=since.strftime("%Y-%m-%d"),
            end=until.strftime("%Y-%m-%d"),
            interval=interval,
        )
        return hist

    try:
        df = await loop.run_in_executor(None, _download)
        candles = []
        for ts, row in df.iterrows():
            candles.append({
                "timestamp": ts.to_pydatetime().astimezone(timezone.utc).isoformat(),
                "open":  float(row["Open"]),
                "high":  float(row["High"]),
                "low":   float(row["Low"]),
                "close": float(row["Close"]),
                "volume": float(row.get("Volume", 0)),
            })
        return candles
    except Exception as exc:
        logger.error("yfinance fetch failed for %s: %s", symbol, exc)
        return []


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

MARKET_SYMBOLS: Dict[str, List[Tuple[str, List[str]]]] = {
    "crypto": [
        ("BTCUSDT", ["1h", "4h", "1d"]),
        ("ETHUSDT", ["1h", "4h", "1d"]),
        ("BNBUSDT", ["1h", "4h"]),
        ("SOLUSDT", ["1h", "4h"]),
        ("XRPUSDT", ["1h", "4h"]),
    ],
    "forex": [
        ("EUR_USD", ["15m", "1h", "4h"]),
        ("GBP_USD", ["15m", "1h", "4h"]),
        ("USD_JPY", ["15m", "1h", "4h"]),
        ("AUD_USD", ["1h", "4h"]),
    ],
    "indian_stocks": [
        ("RELIANCE", ["15m", "1h", "1d"]),
        ("TCS", ["15m", "1h", "1d"]),
        ("INFY", ["15m", "1h", "1d"]),
        ("HDFCBANK", ["15m", "1h", "1d"]),
    ],
    "us_stocks": [
        ("AAPL", ["1h", "4h", "1d"]),
        ("MSFT", ["1h", "4h", "1d"]),
        ("NVDA", ["1h", "4h", "1d"]),
        ("TSLA", ["1h", "4h", "1d"]),
    ],
}


async def seed_market(
    sb: Any,
    market: str,
    symbol: str,
    timeframe: str,
    days: int,
    force: bool,
    settings: Dict[str, str],
) -> Tuple[str, int]:
    """Seed one (market, symbol, timeframe) combination. Returns (key, count)."""
    key = f"{market}/{symbol}/{timeframe}"
    until = datetime.now(timezone.utc)
    since = until - timedelta(days=days)

    if not force:
        latest = await get_latest_ts(sb, market, symbol, timeframe)
        if latest and (until - latest).total_seconds() < 3600:
            logger.info("[SKIP] %s — data is fresh (latest: %s)", key, latest.isoformat())
            return key, 0
        if latest and latest > since:
            since = latest + timedelta(seconds=1)
            logger.info("[RESUME] %s — filling from %s", key, since.isoformat())

    logger.info("[FETCH] %s from %s to %s", key, since.date(), until.date())

    candles: List[Dict[str, Any]] = []

    if market == "crypto":
        candles = await fetch_crypto_binance(
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            api_key=settings.get("BINANCE_API_KEY", ""),
            api_secret=settings.get("BINANCE_SECRET", ""),
        )
    elif market == "forex":
        candles = await fetch_forex_oanda(
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            account_id=settings.get("OANDA_ACCOUNT_ID", ""),
            access_token=settings.get("OANDA_ACCESS_TOKEN", ""),
            practice=settings.get("OANDA_PRACTICE", "true").lower() == "true",
        )
    elif market == "indian_stocks":
        candles = await fetch_indian_stocks_zerodha(
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            api_key=settings.get("KITE_API_KEY", ""),
            access_token=settings.get("KITE_ACCESS_TOKEN", ""),
        )
    elif market == "us_stocks":
        candles = await fetch_us_stocks_alpaca(
            symbol=symbol,
            timeframe=timeframe,
            since=since,
            until=until,
            api_key=settings.get("ALPACA_API_KEY", ""),
            api_secret=settings.get("ALPACA_SECRET_KEY", ""),
            paper=settings.get("ALPACA_PAPER", "true").lower() == "true",
        )

    if candles:
        stored = await store_candles_batch(sb, market, symbol, timeframe, candles)
        logger.info("[STORED] %s — %d candles", key, stored)
        return key, stored

    logger.warning("[EMPTY] %s — no candles fetched", key)
    return key, 0


async def main(args: argparse.Namespace) -> None:
    from supabase import create_client  # type: ignore

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # Load env
    from dotenv import load_dotenv
    load_dotenv()

    supabase_url = os.getenv("SUPABASE_URL", "")
    supabase_key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not supabase_url or not supabase_key:
        raise SystemExit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in .env")

    sb = create_client(supabase_url, supabase_key)

    settings = {
        "BINANCE_API_KEY":   os.getenv("BINANCE_API_KEY", ""),
        "BINANCE_SECRET":    os.getenv("BINANCE_SECRET", ""),
        "OANDA_ACCOUNT_ID":  os.getenv("OANDA_ACCOUNT_ID", ""),
        "OANDA_ACCESS_TOKEN":os.getenv("OANDA_ACCESS_TOKEN", ""),
        "OANDA_PRACTICE":    os.getenv("OANDA_PRACTICE", "true"),
        "KITE_API_KEY":      os.getenv("KITE_API_KEY", ""),
        "KITE_ACCESS_TOKEN": os.getenv("KITE_ACCESS_TOKEN", ""),
        "ALPACA_API_KEY":    os.getenv("ALPACA_API_KEY", ""),
        "ALPACA_SECRET_KEY": os.getenv("ALPACA_SECRET_KEY", ""),
        "ALPACA_PAPER":      os.getenv("ALPACA_PAPER", "true"),
    }

    # Build work list
    work: List[Tuple[str, str, str]] = []
    markets_to_seed = [args.market] if args.market else list(MARKET_SYMBOLS.keys())

    for market in markets_to_seed:
        for sym, timeframes in MARKET_SYMBOLS.get(market, []):
            if args.symbol and sym != args.symbol:
                continue
            tfs = [args.timeframe] if args.timeframe else timeframes
            for tf in tfs:
                work.append((market, sym, tf))

    logger.info("Seeding %d symbol/timeframe combinations over %d days…", len(work), args.days)

    try:
        from tqdm.asyncio import tqdm  # type: ignore
        use_tqdm = True
    except ImportError:
        use_tqdm = False

    results: Dict[str, int] = {}
    total_stored = 0

    if use_tqdm:
        from tqdm.asyncio import tqdm as atqdm
        for market, symbol, tf in atqdm(work, desc="Seeding"):
            key, count = await seed_market(sb, market, symbol, tf, args.days, args.force, settings)
            results[key] = count
            total_stored += count
    else:
        for i, (market, symbol, tf) in enumerate(work, 1):
            print(f"[{i}/{len(work)}] {market}/{symbol}/{tf}")
            key, count = await seed_market(sb, market, symbol, tf, args.days, args.force, settings)
            results[key] = count
            total_stored += count

    # Summary
    print("\n" + "=" * 60)
    print(f"SEED COMPLETE — Total candles stored: {total_stored:,}")
    print("=" * 60)
    for key, count in sorted(results.items()):
        status = f"{count:>8,} candles" if count > 0 else "  (skipped)"
        print(f"  {key:<40} {status}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed NEXUS ALPHA historical market data")
    parser.add_argument("--market", type=str, choices=["crypto", "forex", "indian_stocks", "us_stocks"],
                        help="Seed only this market (default: all enabled)")
    parser.add_argument("--symbol", type=str, help="Seed only this symbol")
    parser.add_argument("--timeframe", type=str, help="Seed only this timeframe")
    parser.add_argument("--days", type=int, default=90, help="Days of history to fetch (default: 90)")
    parser.add_argument("--force", action="store_true",
                        help="Re-fetch even if recent data exists")

    args = parser.parse_args()
    asyncio.run(main(args))
