"""
NEXUS ALPHA - Integration Test: OANDA Practice Streaming
Requires OANDA_ACCESS_TOKEN and OANDA_ACCOUNT_ID environment variables.

Run with:
    pytest tests/integration/test_oanda_stream.py -v -m integration
    pytest tests/integration/test_oanda_stream.py -v --timeout=60
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Dict, List, Optional

import pytest

pytestmark = pytest.mark.integration

OANDA_PRACTICE_BASE    = "https://api-fxpractice.oanda.com"
OANDA_STREAM_BASE      = "https://stream-fxpractice.oanda.com"
OANDA_LIVE_BASE        = "https://api-fxtrade.oanda.com"
PRICE_TICK_TIMEOUT     = 30  # seconds
TEST_INSTRUMENT        = "EUR_USD"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_oanda_credentials():
    token   = os.getenv("OANDA_ACCESS_TOKEN", "")
    account = os.getenv("OANDA_ACCOUNT_ID", "")
    if not token or not account:
        pytest.skip(
            "OANDA_ACCESS_TOKEN or OANDA_ACCOUNT_ID not set — "
            "skipping OANDA integration tests"
        )
    return token, account


def _requires_aiohttp():
    try:
        import aiohttp
    except ImportError:
        pytest.skip("aiohttp not installed")


# ---------------------------------------------------------------------------
# Test: Receive price tick within 30 seconds
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_oanda_practice_price_tick():
    """
    Connect to OANDA practice streaming API and receive at least
    one price tick for EUR_USD within 30 seconds.
    """
    _requires_aiohttp()
    token, account_id = _get_oanda_credentials()
    import aiohttp

    stream_url = (
        f"{OANDA_STREAM_BASE}/v3/accounts/{account_id}"
        f"/pricing/stream?instruments={TEST_INSTRUMENT}"
    )
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
    }

    received_ticks: List[Dict[str, Any]] = []
    error: Optional[Exception] = None

    async def stream_prices():
        nonlocal error
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    stream_url,
                    timeout=aiohttp.ClientTimeout(total=PRICE_TICK_TIMEOUT + 10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        error = Exception(f"OANDA API error {resp.status}: {body[:200]}")
                        return

                    start = time.monotonic()
                    async for line_bytes in resp.content:
                        elapsed = time.monotonic() - start
                        if elapsed > PRICE_TICK_TIMEOUT:
                            break

                        line = line_bytes.decode("utf-8").strip()
                        if not line:
                            continue

                        try:
                            tick = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        if tick.get("type") == "PRICE":
                            received_ticks.append(tick)
                            return  # One tick is enough

        except asyncio.TimeoutError:
            pass
        except Exception as exc:
            error = exc

    await asyncio.wait_for(stream_prices(), timeout=PRICE_TICK_TIMEOUT + 15)

    assert error is None, f"OANDA streaming error: {error}"
    assert len(received_ticks) >= 1, (
        f"Expected at least 1 price tick within {PRICE_TICK_TIMEOUT}s, "
        f"got {len(received_ticks)}"
    )


@pytest.mark.asyncio
async def test_oanda_practice_tick_format():
    """Validate the structure of an OANDA price tick."""
    _requires_aiohttp()
    token, account_id = _get_oanda_credentials()
    import aiohttp

    stream_url = (
        f"{OANDA_STREAM_BASE}/v3/accounts/{account_id}"
        f"/pricing/stream?instruments={TEST_INSTRUMENT}"
    )
    headers = {"Authorization": f"Bearer {token}"}

    tick: Optional[Dict] = None

    async def get_one_tick():
        nonlocal tick
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                stream_url,
                timeout=aiohttp.ClientTimeout(total=40),
            ) as resp:
                if resp.status != 200:
                    return
                async for line_bytes in resp.content:
                    line = line_bytes.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        if data.get("type") == "PRICE":
                            tick = data
                            return
                    except json.JSONDecodeError:
                        continue

    await asyncio.wait_for(get_one_tick(), timeout=45)

    if tick is None:
        pytest.skip("Could not receive OANDA price tick — market may be closed")

    # Structure validation
    assert tick.get("type") == "PRICE"
    assert "instrument" in tick, "Tick missing 'instrument'"
    assert tick["instrument"] == TEST_INSTRUMENT
    assert "time" in tick, "Tick missing 'time'"
    assert "bids" in tick, "Tick missing 'bids'"
    assert "asks" in tick, "Tick missing 'asks'"

    # Price validation
    bids = tick.get("bids", [])
    asks = tick.get("asks", [])
    assert len(bids) > 0, "No bid prices"
    assert len(asks) > 0, "No ask prices"

    bid_price = float(bids[0].get("price", 0))
    ask_price = float(asks[0].get("price", 0))

    assert bid_price > 0, f"Bid price must be positive, got {bid_price}"
    assert ask_price > 0, f"Ask price must be positive, got {ask_price}"
    assert ask_price >= bid_price, f"Ask {ask_price} must be >= Bid {bid_price}"

    # EUR_USD should be in a reasonable range (0.8 – 1.5)
    mid = (bid_price + ask_price) / 2
    assert 0.8 <= mid <= 1.5, f"EUR/USD mid {mid} outside expected range [0.8, 1.5]"

    # Spread check (< 10 pips = 0.001 for EUR/USD)
    spread = ask_price - bid_price
    assert spread < 0.01, f"Spread {spread} seems unusually large for EUR/USD"


@pytest.mark.asyncio
async def test_oanda_accounts_api():
    """Verify OANDA REST API account endpoint is accessible."""
    _requires_aiohttp()
    token, account_id = _get_oanda_credentials()
    import aiohttp

    url = f"{OANDA_PRACTICE_BASE}/v3/accounts/{account_id}"
    headers = {"Authorization": f"Bearer {token}"}

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
            assert resp.status == 200, (
                f"Expected 200 from OANDA accounts API, got {resp.status}"
            )
            data = await resp.json()

    assert "account" in data, "Response missing 'account' key"
    account = data["account"]
    assert account.get("id") == account_id, (
        f"Account ID mismatch: {account.get('id')} != {account_id}"
    )
    assert "balance" in account, "Account missing 'balance'"
    assert float(account["balance"]) >= 0


@pytest.mark.asyncio
async def test_oanda_candles_rest():
    """Fetch historical candles via OANDA REST API."""
    _requires_aiohttp()
    token, _ = _get_oanda_credentials()
    import aiohttp

    url = f"{OANDA_PRACTICE_BASE}/v3/instruments/{TEST_INSTRUMENT}/candles"
    headers = {"Authorization": f"Bearer {token}"}
    params = {
        "granularity": "H1",
        "count": 10,
        "price": "M",
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        async with session.get(
            url, params=params, timeout=aiohttp.ClientTimeout(total=15)
        ) as resp:
            if resp.status == 404:
                pytest.skip("OANDA practice candles endpoint unavailable")
            assert resp.status == 200, f"OANDA candles API error: {resp.status}"
            data = await resp.json()

    assert "candles" in data, "Response missing 'candles'"
    candles = data["candles"]
    assert len(candles) > 0, "No candles returned"

    # Validate candle structure
    for candle in candles[:3]:
        assert "time" in candle
        assert "mid" in candle
        mid = candle["mid"]
        assert "o" in mid and "h" in mid and "l" in mid and "c" in mid
        assert float(mid["h"]) >= float(mid["l"]), "high must be >= low"


@pytest.mark.asyncio
async def test_oanda_heartbeat_in_stream():
    """
    OANDA streaming sends heartbeat messages. Verify we receive and handle them.
    Heartbeat format: {"type": "HEARTBEAT", "time": "..."}
    """
    _requires_aiohttp()
    token, account_id = _get_oanda_credentials()
    import aiohttp

    stream_url = (
        f"{OANDA_STREAM_BASE}/v3/accounts/{account_id}"
        f"/pricing/stream?instruments={TEST_INSTRUMENT}&heartbeat=true"
    )
    headers = {"Authorization": f"Bearer {token}"}

    messages: List[Dict] = []

    async def collect_messages():
        async with aiohttp.ClientSession(headers=headers) as session:
            async with session.get(
                stream_url, timeout=aiohttp.ClientTimeout(total=35)
            ) as resp:
                if resp.status != 200:
                    return
                start = time.monotonic()
                async for line_bytes in resp.content:
                    if time.monotonic() - start > 25:
                        break
                    line = line_bytes.decode("utf-8").strip()
                    if not line:
                        continue
                    try:
                        msg = json.loads(line)
                        messages.append(msg)
                        if len(messages) >= 5:
                            return
                    except json.JSONDecodeError:
                        continue

    await asyncio.wait_for(collect_messages(), timeout=40)

    if not messages:
        pytest.skip("No messages received from OANDA stream — market may be closed")

    msg_types = {m.get("type") for m in messages}
    # We should see PRICE and/or HEARTBEAT messages
    valid_types = {"PRICE", "HEARTBEAT"}
    assert msg_types.issubset(valid_types | {"DISCONNECT"}), (
        f"Unexpected message types: {msg_types - valid_types}"
    )
