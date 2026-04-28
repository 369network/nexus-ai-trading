"""
NEXUS ALPHA - Integration Test: Binance WebSocket
Requires BINANCE_TESTNET_API_KEY and BINANCE_TESTNET_SECRET environment variables.

Run with:
    pytest tests/integration/test_binance_ws.py -v -m integration
    pytest tests/integration/test_binance_ws.py -v --timeout=120
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import time
from typing import Any, Dict, List, Optional

import pytest

# Skip entire module if testnet credentials are not set
pytestmark = pytest.mark.integration

TESTNET_WS_URL = "wss://testnet.binance.vision/ws"
PROD_WS_URL    = "wss://stream.binance.com:9443/ws"
TIMEOUT_SECONDS = 90
SYMBOL          = "btcusdt"


# ---------------------------------------------------------------------------
# Utilities
# ---------------------------------------------------------------------------

def _requires_binance_testnet():
    """Skip if Binance testnet credentials are missing."""
    api_key = os.getenv("BINANCE_TESTNET_API_KEY", "")
    if not api_key:
        pytest.skip(
            "BINANCE_TESTNET_API_KEY not set — skipping Binance WebSocket integration test"
        )


def _requires_aiohttp():
    try:
        import aiohttp
    except ImportError:
        pytest.skip("aiohttp not installed")


# ---------------------------------------------------------------------------
# Test: Connect and receive candle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_binance_ws_connect_and_receive_candle():
    """
    Connect to Binance WebSocket (production public endpoint — no auth needed for klines)
    and receive at least one candle within 90 seconds.
    """
    _requires_aiohttp()
    import aiohttp

    received_candles: List[Dict[str, Any]] = []
    connected = False
    error: Optional[Exception] = None

    stream_url = f"{PROD_WS_URL}/{SYMBOL}@kline_1m"

    async def run_ws():
        nonlocal connected, error
        ssl_ctx = ssl.create_default_context()
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(
                    stream_url,
                    ssl=ssl_ctx,
                    timeout=aiohttp.ClientTimeout(total=TIMEOUT_SECONDS + 10),
                    heartbeat=20,
                ) as ws:
                    connected = True
                    start_time = time.monotonic()

                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("e") == "kline":
                                k = data.get("k", {})
                                candle = {
                                    "timestamp":  k.get("t"),
                                    "open":       float(k.get("o", 0)),
                                    "high":       float(k.get("h", 0)),
                                    "low":        float(k.get("l", 0)),
                                    "close":      float(k.get("c", 0)),
                                    "volume":     float(k.get("v", 0)),
                                    "is_closed":  k.get("x", False),
                                    "symbol":     k.get("s"),
                                }
                                received_candles.append(candle)

                                if len(received_candles) >= 1:
                                    await ws.close()
                                    return

                        elif msg.type in (aiohttp.WSMsgType.ERROR, aiohttp.WSMsgType.CLOSE):
                            break

                        elapsed = time.monotonic() - start_time
                        if elapsed > TIMEOUT_SECONDS:
                            break

        except Exception as exc:
            error = exc

    await asyncio.wait_for(run_ws(), timeout=TIMEOUT_SECONDS + 15)

    # Assertions
    assert error is None, f"WebSocket error: {error}"
    assert connected, "Failed to establish WebSocket connection"
    assert len(received_candles) >= 1, (
        f"Expected at least 1 candle within {TIMEOUT_SECONDS}s, received {len(received_candles)}"
    )

    # Validate candle structure
    candle = received_candles[0]
    assert candle["timestamp"] is not None
    assert candle["open"] > 0
    assert candle["high"] >= candle["low"]
    assert candle["high"] >= candle["open"]
    assert candle["high"] >= candle["close"]
    assert candle["low"] <= candle["open"]
    assert candle["low"] <= candle["close"]
    assert candle["volume"] >= 0
    assert candle["symbol"].upper() == SYMBOL.upper()


@pytest.mark.asyncio
async def test_binance_ws_reconnect_after_disconnect():
    """
    Test that a WebSocket reconnection succeeds after a forced disconnect.
    Simulates network disruption by closing and reopening the connection.
    """
    _requires_aiohttp()
    import aiohttp

    reconnect_successful = False
    ssl_ctx = ssl.create_default_context()
    stream_url = f"{PROD_WS_URL}/{SYMBOL}@kline_1m"

    async def single_connect_and_close():
        """Connect, receive one message, close, then reconnect."""
        nonlocal reconnect_successful

        # First connection: receive one message then force-close
        first_received = False
        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(
                    stream_url, ssl=ssl_ctx,
                    timeout=aiohttp.ClientTimeout(total=60),
                    heartbeat=20,
                ) as ws:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("e") == "kline":
                                first_received = True
                                # Force close to simulate disconnect
                                await ws.close(code=1001, message=b"simulated disconnect")
                                break
                        if not first_received:
                            await asyncio.sleep(0.1)
                        break
            except Exception:
                pass

        if not first_received:
            return  # Could not receive first message

        # Brief pause (simulate reconnect delay)
        await asyncio.sleep(2)

        # Second connection: should succeed without error
        second_received = False
        async with aiohttp.ClientSession() as session:
            try:
                async with session.ws_connect(
                    stream_url, ssl=ssl_ctx,
                    timeout=aiohttp.ClientTimeout(total=60),
                    heartbeat=20,
                ) as ws:
                    async for msg in ws:
                        if msg.type == aiohttp.WSMsgType.TEXT:
                            data = json.loads(msg.data)
                            if data.get("e") == "kline":
                                second_received = True
                                await ws.close()
                                break
                        break
            except Exception:
                pass

        reconnect_successful = second_received

    await asyncio.wait_for(single_connect_and_close(), timeout=120)
    assert reconnect_successful, "Failed to reconnect after disconnect"


@pytest.mark.asyncio
async def test_binance_ws_kline_data_format():
    """Verify that the Binance kline WebSocket message follows expected format."""
    _requires_aiohttp()
    import aiohttp

    received_message: Optional[Dict] = None
    ssl_ctx = ssl.create_default_context()
    stream_url = f"{PROD_WS_URL}/{SYMBOL}@kline_1m"

    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(
            stream_url, ssl=ssl_ctx,
            timeout=aiohttp.ClientTimeout(total=60),
            heartbeat=20,
        ) as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if data.get("e") == "kline":
                        received_message = data
                        await ws.close()
                        break

    assert received_message is not None, "No kline message received"

    # Validate top-level message structure
    assert "e" in received_message, "Missing event type 'e'"
    assert received_message["e"] == "kline"
    assert "E" in received_message, "Missing event time 'E'"
    assert "s" in received_message, "Missing symbol 's'"

    # Validate kline object
    k = received_message.get("k", {})
    required_fields = ["t", "T", "s", "i", "o", "c", "h", "l", "v", "n", "x"]
    for field in required_fields:
        assert field in k, f"Missing kline field '{field}'"

    # Type checks
    assert isinstance(k["t"], int), "open_ts must be integer"
    assert isinstance(k["x"], bool), "is_closed must be boolean"
    assert float(k["h"]) >= float(k["l"]), "high must be >= low"
    assert float(k["v"]) >= 0, "volume must be non-negative"


@pytest.mark.asyncio
async def test_binance_ws_testnet_with_auth():
    """
    Test Binance testnet WebSocket with API authentication.
    Requires BINANCE_TESTNET_API_KEY environment variable.
    """
    _requires_binance_testnet()
    _requires_aiohttp()
    import aiohttp

    api_key = os.getenv("BINANCE_TESTNET_API_KEY")
    testnet_url = f"{TESTNET_WS_URL}/{SYMBOL}@kline_1m"
    ssl_ctx = ssl.create_default_context()

    received = False
    headers = {"X-MBX-APIKEY": api_key}

    async with aiohttp.ClientSession(headers=headers) as session:
        try:
            async with session.ws_connect(
                testnet_url, ssl=ssl_ctx,
                timeout=aiohttp.ClientTimeout(total=30),
                heartbeat=20,
            ) as ws:
                async for msg in ws:
                    if msg.type == aiohttp.WSMsgType.TEXT:
                        data = json.loads(msg.data)
                        if data.get("e") == "kline":
                            received = True
                            await ws.close()
                            break
                    break
        except Exception as exc:
            pytest.skip(f"Testnet WebSocket unavailable: {exc}")

    assert received, "No candle received from Binance testnet WebSocket"
