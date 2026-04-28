"""
NEXUS ALPHA — Interactive Brokers (IBKR) Data Provider
========================================================
Async IBKR data provider using ib_insync with auto-reconnect, contract
auto-detection, and yfinance fallback for historical data when IBKR is
unavailable.

Environment variables:
  IBKR_HOST         — TWS/IBC gateway host (default: 127.0.0.1)
  IBKR_PORT         — TWS/IBC gateway port (default: 7497 paper, 7496 live)
  IBKR_CLIENT_ID    — Client ID for this connection (default: 10)
  IBKR_READONLY     — If "true", use read-only API mode (default: false)

Ports:
  7496 — TWS live trading
  7497 — TWS paper trading
  4001 — IBC Gateway live
  4002 — IBC Gateway paper
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

from src.data.base import OHLCV
from src.utils.logging import get_logger
from src.utils.retry import retry_with_backoff
from src.utils.timezone import UTC, now_utc

log = get_logger(__name__)

# ---------------------------------------------------------------------------
# Default connection parameters
# ---------------------------------------------------------------------------

_DEFAULT_HOST = os.getenv("IBKR_HOST", "127.0.0.1")
_DEFAULT_PORT = int(os.getenv("IBKR_PORT", "7497"))   # Paper trading default
_DEFAULT_CLIENT_ID = int(os.getenv("IBKR_CLIENT_ID", "10"))

# Map from NEXUS interval strings to IBKR bar size strings
_INTERVAL_TO_IBKR: Dict[str, str] = {
    "1m":  "1 min",
    "2m":  "2 mins",
    "3m":  "3 mins",
    "5m":  "5 mins",
    "10m": "10 mins",
    "15m": "15 mins",
    "30m": "30 mins",
    "1h":  "1 hour",
    "2h":  "2 hours",
    "3h":  "3 hours",
    "4h":  "4 hours",
    "1d":  "1 day",
    "1w":  "1 week",
    "1M":  "1 month",
}

# Map from NEXUS interval strings to IBKR duration strings
# Maximum history available depends on bar size
_INTERVAL_TO_MAX_DURATION: Dict[str, str] = {
    "1m":  "1 D",
    "2m":  "2 D",
    "3m":  "3 D",
    "5m":  "5 D",
    "10m": "5 D",
    "15m": "10 D",
    "30m": "20 D",
    "1h":  "1 M",
    "2h":  "2 M",
    "3h":  "3 M",
    "4h":  "3 M",
    "1d":  "1 Y",
    "1w":  "2 Y",
    "1M":  "5 Y",
}


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AccountSummary:
    """Parsed IBKR account summary."""

    net_liquidation: float
    cash_balance: float
    buying_power: float
    unrealized_pnl: float
    realized_pnl: float
    gross_position_value: float
    currency: str
    account_id: str


@dataclass
class IBKRPosition:
    """A single open position from IBKR."""

    account: str
    symbol: str
    exchange: str
    currency: str
    position: float          # Positive = long, negative = short
    avg_cost: float
    market_price: float
    market_value: float
    unrealized_pnl: float
    realized_pnl: float
    asset_type: str          # STK, FUT, OPT, CASH, CRYPTO


@dataclass
class OptionContract:
    """A single option contract from the IBKR options chain."""

    symbol: str
    expiry: str
    strike: float
    right: str              # "C" or "P"
    bid: float
    ask: float
    last: float
    volume: int
    open_interest: int
    implied_vol: float
    delta: float
    gamma: float
    theta: float
    vega: float


# ---------------------------------------------------------------------------
# IBKRDataProvider
# ---------------------------------------------------------------------------


class IBKRDataProvider:
    """
    Interactive Brokers data provider using ib_insync.

    Provides historical data, real-time 5-second bars, account information,
    positions, and options chains. Falls back to yfinance for historical data
    when IBKR is unavailable or not configured.

    All public methods are safe to call without an active IBKR connection —
    they will attempt to connect if not already connected, and fall back to
    yfinance on failure.

    Usage::

        provider = IBKRDataProvider()
        await provider.connect()
        candles = await provider.get_historical_data("AAPL", "5 D", "15m")
        await provider.disconnect()

    Or as a context manager::

        async with IBKRDataProvider() as provider:
            candles = await provider.get_historical_data("AAPL", "5 D", "15m")
    """

    def __init__(
        self,
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
        client_id: int = _DEFAULT_CLIENT_ID,
    ) -> None:
        self._host = host
        self._port = port
        self._client_id = client_id
        self._ib: Any = None                # ib_insync.IB instance
        self._connected = False
        self._reconnect_attempts = 0
        self._max_reconnects = 5
        self._health_check_task: Optional[asyncio.Task] = None
        self._realtime_subscriptions: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def __aenter__(self) -> "IBKRDataProvider":
        await self.connect()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.disconnect()

    async def connect(
        self,
        host: Optional[str] = None,
        port: Optional[int] = None,
        client_id: Optional[int] = None,
    ) -> bool:
        """
        Connect to TWS or IBC Gateway.

        Args:
            host: Override host (defaults to instance value).
            port: Override port (defaults to instance value).
            client_id: Override client ID (defaults to instance value).

        Returns:
            True if connection successful, False otherwise.
        """
        connect_host = host or self._host
        connect_port = port or self._port
        connect_id = client_id or self._client_id

        try:
            import ib_insync  # type: ignore[import]
        except ImportError:
            log.warning(
                "ib_insync not installed — IBKR provider will use yfinance fallback. "
                "Install with: pip install ib_insync"
            )
            return False

        try:
            self._ib = ib_insync.IB()
            await asyncio.wait_for(
                self._ib.connectAsync(
                    host=connect_host,
                    port=connect_port,
                    clientId=connect_id,
                    readonly=os.getenv("IBKR_READONLY", "false").lower() == "true",
                ),
                timeout=15.0,
            )
            self._connected = True
            self._reconnect_attempts = 0

            # Register disconnect handler for auto-reconnect
            self._ib.disconnectedEvent += self._on_disconnect

            log.info(
                "IBKR connected",
                host=connect_host,
                port=connect_port,
                client_id=connect_id,
            )

            # Start health check
            self._health_check_task = asyncio.create_task(
                self._health_check_loop()
            )
            return True

        except asyncio.TimeoutError:
            log.warning(
                "IBKR connection timed out — will use yfinance fallback",
                host=connect_host,
                port=connect_port,
            )
            self._ib = None
            return False

        except Exception as exc:
            log.warning(
                "IBKR connection failed — will use yfinance fallback",
                host=connect_host,
                port=connect_port,
                error=str(exc),
            )
            self._ib = None
            return False

    async def disconnect(self) -> None:
        """Disconnect from IBKR and cancel all subscriptions."""
        if self._health_check_task:
            self._health_check_task.cancel()
            try:
                await self._health_check_task
            except asyncio.CancelledError:
                pass
            self._health_check_task = None

        # Cancel all realtime bar subscriptions
        for symbol, bars in self._realtime_subscriptions.items():
            try:
                if self._ib and self._connected:
                    self._ib.cancelRealTimeBars(bars)
            except Exception:
                pass
        self._realtime_subscriptions.clear()

        if self._ib and self._connected:
            try:
                self._ib.disconnect()
            except Exception:
                pass

        self._connected = False
        self._ib = None
        log.info("IBKR disconnected")

    def _on_disconnect(self) -> None:
        """Called by ib_insync on unexpected disconnect."""
        self._connected = False
        log.warning("IBKR connection lost — scheduling reconnect")
        asyncio.create_task(self._auto_reconnect())

    async def _auto_reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        for attempt in range(1, self._max_reconnects + 1):
            delay = min(2 ** attempt, 60)
            log.info(
                "IBKR reconnect attempt",
                attempt=attempt,
                max_attempts=self._max_reconnects,
                delay_s=delay,
            )
            await asyncio.sleep(delay)
            success = await self.connect()
            if success:
                log.info("IBKR reconnected successfully", attempt=attempt)
                return

        log.error(
            "IBKR reconnect failed after all attempts — using yfinance fallback",
            max_attempts=self._max_reconnects,
        )

    async def _health_check_loop(self) -> None:
        """Periodically verify the IBKR connection is alive."""
        while self._connected:
            await asyncio.sleep(30)
            try:
                if self._ib and self._connected:
                    # ServerVersion is available if connected
                    _ = self._ib.serverVersion()
            except Exception:
                log.warning("IBKR health check failed — connection may be lost")
                self._connected = False

    # ------------------------------------------------------------------
    # Contract resolution
    # ------------------------------------------------------------------

    async def get_contract(self, symbol: str, asset_type: str = "STK") -> Any:
        """
        Auto-detect and qualify an IBKR contract for the given symbol.

        Handles stocks (STK), futures (FUT), options (OPT), forex (CASH),
        and crypto (CRYPTO).

        Args:
            symbol: Ticker symbol (e.g. "AAPL", "BTC", "EUR.USD", "ESZ24").
            asset_type: Override asset type detection.

        Returns:
            Qualified ib_insync Contract, or None if not found.
        """
        if not self._connected or not self._ib:
            return None

        try:
            import ib_insync

            # Detect asset type from symbol patterns
            if "/" in symbol:
                # Forex pair: EUR/USD → CASH
                base, quote = symbol.split("/", 1)
                contract = ib_insync.Forex(f"{base}{quote}")
            elif symbol.upper() in ("BTC", "ETH", "SOL", "XRP", "ADA"):
                contract = ib_insync.Crypto(symbol.upper(), "PAXOS", "USD")
            elif len(symbol) > 6 and symbol[-2:].isdigit():
                # Likely a futures symbol like ESZ24
                contract = ib_insync.Future(symbol)
            else:
                contract = ib_insync.Stock(symbol, "SMART", "USD")

            qualified = await self._ib.qualifyContractsAsync(contract)
            if qualified:
                return qualified[0]
            return None

        except Exception as exc:
            log.error(
                "Contract resolution failed",
                symbol=symbol,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Historical data
    # ------------------------------------------------------------------

    async def get_historical_data(
        self,
        symbol: str,
        duration: Optional[str] = None,
        bar_size: str = "15m",
        end_datetime: Optional[str] = None,
        what_to_show: str = "TRADES",
    ) -> List[OHLCV]:
        """
        Fetch historical OHLCV candles from IBKR.

        Falls back to yfinance if IBKR is unavailable.

        Args:
            symbol: Ticker symbol.
            duration: IBKR duration string ("1 D", "1 W", "1 M", "1 Y").
                      If None, uses the maximum appropriate for bar_size.
            bar_size: NEXUS interval string (e.g. "15m", "1h", "1d").
            end_datetime: End datetime string "YYYYMMDD HH:MM:SS" (UTC).
                          If None, uses current time.
            what_to_show: IBKR data type: TRADES, MIDPOINT, BID, ASK.

        Returns:
            List of OHLCV candles sorted ascending by timestamp.
        """
        ibkr_bar_size = _INTERVAL_TO_IBKR.get(bar_size)
        if not ibkr_bar_size:
            log.warning(
                "Unsupported bar size — falling back to yfinance",
                bar_size=bar_size,
                supported=list(_INTERVAL_TO_IBKR.keys()),
            )
            return await self._yfinance_fallback(symbol, bar_size)

        effective_duration = duration or _INTERVAL_TO_MAX_DURATION.get(bar_size, "1 M")

        if not self._connected or not self._ib:
            log.debug(
                "IBKR not connected — using yfinance fallback",
                symbol=symbol,
                bar_size=bar_size,
            )
            return await self._yfinance_fallback(symbol, bar_size)

        contract = await self.get_contract(symbol)
        if contract is None:
            log.warning(
                "Could not resolve contract — using yfinance fallback",
                symbol=symbol,
            )
            return await self._yfinance_fallback(symbol, bar_size)

        try:
            bars = await self._ib.reqHistoricalDataAsync(
                contract,
                endDateTime=end_datetime or "",
                durationStr=effective_duration,
                barSizeSetting=ibkr_bar_size,
                whatToShow=what_to_show,
                useRTH=False,
                formatDate=1,
            )

            candles: List[OHLCV] = []
            for bar in bars:
                ts_ms = int(
                    datetime.strptime(bar.date, "%Y%m%d %H:%M:%S")
                    .replace(tzinfo=UTC)
                    .timestamp()
                    * 1000
                )
                candles.append(
                    OHLCV(
                        timestamp_ms=ts_ms,
                        open=float(bar.open),
                        high=float(bar.high),
                        low=float(bar.low),
                        close=float(bar.close),
                        volume=float(bar.volume),
                        quote_volume=float(bar.volume) * float(bar.close),
                        trades=int(getattr(bar, "barCount", 0)),
                        vwap=float(getattr(bar, "average", bar.close)),
                        taker_buy_volume=0.0,
                        source="ibkr",
                        market=self._detect_market(symbol),
                        symbol=symbol,
                        interval=bar_size,
                        complete=True,
                    )
                )

            log.debug(
                "IBKR historical data fetched",
                symbol=symbol,
                bar_size=bar_size,
                count=len(candles),
            )
            return sorted(candles, key=lambda c: c.timestamp_ms)

        except Exception as exc:
            log.warning(
                "IBKR historical data fetch failed — using yfinance fallback",
                symbol=symbol,
                error=str(exc),
            )
            return await self._yfinance_fallback(symbol, bar_size)

    # ------------------------------------------------------------------
    # Real-time 5-second bars
    # ------------------------------------------------------------------

    async def get_realtime_bars(
        self,
        symbol: str,
        callback: Callable[[OHLCV], None],
        what_to_show: str = "TRADES",
    ) -> None:
        """
        Subscribe to real-time 5-second bars for a symbol.

        Calls ``callback(ohlcv)`` each time a new 5-second bar arrives.
        The subscription runs until ``cancel_realtime_bars(symbol)`` is called
        or the provider is disconnected.

        Args:
            symbol: Ticker symbol.
            callback: Callable invoked for each new OHLCV bar.
            what_to_show: IBKR data type: TRADES, MIDPOINT, BID, ASK.

        Raises:
            RuntimeError: If IBKR is not connected.
        """
        if not self._connected or not self._ib:
            raise RuntimeError(
                "IBKR not connected. Call connect() first or use get_historical_data "
                "which falls back to yfinance."
            )

        contract = await self.get_contract(symbol)
        if contract is None:
            raise RuntimeError(f"Could not resolve IBKR contract for {symbol}")

        def _on_bar(bars: Any, has_new: bool) -> None:
            if not has_new or not bars:
                return
            bar = bars[-1]
            try:
                ts_ms = int(
                    datetime.strptime(str(bar.time), "%Y-%m-%d %H:%M:%S")
                    .replace(tzinfo=UTC)
                    .timestamp()
                    * 1000
                )
                ohlcv = OHLCV(
                    timestamp_ms=ts_ms,
                    open=float(bar.open),
                    high=float(bar.high),
                    low=float(bar.low),
                    close=float(bar.close),
                    volume=float(bar.volume),
                    quote_volume=float(bar.volume) * float(bar.close),
                    trades=int(getattr(bar, "count", 0)),
                    vwap=float(getattr(bar, "average", bar.close)),
                    taker_buy_volume=0.0,
                    source="ibkr_rt",
                    market=self._detect_market(symbol),
                    symbol=symbol,
                    interval="5s",
                    complete=True,
                )
                callback(ohlcv)
            except Exception as exc:
                log.error(
                    "Error processing real-time bar",
                    symbol=symbol,
                    error=str(exc),
                )

        bars_ref = self._ib.reqRealTimeBars(
            contract,
            barSize=5,
            whatToShow=what_to_show,
            useRTH=False,
        )
        bars_ref.updateEvent += _on_bar
        self._realtime_subscriptions[symbol] = bars_ref

        log.info(
            "Real-time bars subscribed",
            symbol=symbol,
            what_to_show=what_to_show,
        )

    async def cancel_realtime_bars(self, symbol: str) -> None:
        """Cancel a real-time bar subscription for the given symbol."""
        bars_ref = self._realtime_subscriptions.pop(symbol, None)
        if bars_ref and self._ib and self._connected:
            try:
                self._ib.cancelRealTimeBars(bars_ref)
                log.debug("Real-time bars cancelled", symbol=symbol)
            except Exception as exc:
                log.warning(
                    "Error cancelling real-time bars",
                    symbol=symbol,
                    error=str(exc),
                )

    # ------------------------------------------------------------------
    # Account and positions
    # ------------------------------------------------------------------

    async def get_account_summary(self) -> Optional[AccountSummary]:
        """
        Fetch the current IBKR account summary.

        Returns:
            AccountSummary dataclass, or None if not connected.
        """
        if not self._connected or not self._ib:
            return None

        try:
            tags = [
                "NetLiquidation",
                "CashBalance",
                "BuyingPower",
                "UnrealizedPnL",
                "RealizedPnL",
                "GrossPositionValue",
                "Currency",
            ]
            summary = await self._ib.accountSummaryAsync()
            values: Dict[str, str] = {
                item.tag: item.value
                for item in summary
                if item.tag in tags
            }

            return AccountSummary(
                net_liquidation=float(values.get("NetLiquidation", 0)),
                cash_balance=float(values.get("CashBalance", 0)),
                buying_power=float(values.get("BuyingPower", 0)),
                unrealized_pnl=float(values.get("UnrealizedPnL", 0)),
                realized_pnl=float(values.get("RealizedPnL", 0)),
                gross_position_value=float(values.get("GrossPositionValue", 0)),
                currency=values.get("Currency", "USD"),
                account_id=self._ib.wrapper.accounts[0] if self._ib.wrapper.accounts else "",
            )
        except Exception as exc:
            log.error("Failed to fetch account summary", error=str(exc))
            return None

    async def get_positions(self) -> List[IBKRPosition]:
        """
        Fetch all open positions from IBKR.

        Returns:
            List of IBKRPosition objects for all open positions.
        """
        if not self._connected or not self._ib:
            return []

        try:
            raw_positions = await self._ib.reqPositionsAsync()
            positions: List[IBKRPosition] = []

            for pos in raw_positions:
                if pos.position == 0:
                    continue

                contract = pos.contract
                pnl_data = getattr(pos, "pnl", None)
                unrealized = float(getattr(pnl_data, "unrealizedPnL", 0) or 0)
                realized = float(getattr(pnl_data, "realizedPnL", 0) or 0)
                market_price = float(getattr(pnl_data, "marketPrice", pos.avgCost) or pos.avgCost)
                market_value = float(getattr(pnl_data, "marketValue", 0) or 0)

                positions.append(
                    IBKRPosition(
                        account=pos.account,
                        symbol=contract.symbol,
                        exchange=contract.exchange or "SMART",
                        currency=contract.currency or "USD",
                        position=float(pos.position),
                        avg_cost=float(pos.avgCost),
                        market_price=market_price,
                        market_value=market_value,
                        unrealized_pnl=unrealized,
                        realized_pnl=realized,
                        asset_type=contract.secType,
                    )
                )

            log.debug("IBKR positions fetched", count=len(positions))
            return positions

        except Exception as exc:
            log.error("Failed to fetch positions", error=str(exc))
            return []

    async def get_options_chain(
        self,
        symbol: str,
        expiry: Optional[str] = None,
    ) -> List[OptionContract]:
        """
        Fetch the full options chain for a symbol from IBKR.

        Falls back to yfinance options chain if IBKR unavailable.

        Args:
            symbol: Underlying ticker symbol.
            expiry: Expiry date string "YYYYMMDD". If None, uses nearest expiry.

        Returns:
            List of OptionContract objects (both calls and puts).
        """
        if not self._connected or not self._ib:
            log.debug("IBKR not connected — using yfinance options chain", symbol=symbol)
            return await self._yfinance_options_fallback(symbol, expiry)

        try:
            import ib_insync

            stock_contract = ib_insync.Stock(symbol, "SMART", "USD")
            chains = await self._ib.reqSecDefOptParamsAsync(
                underlyingSymbol=symbol,
                futFopExchange="",
                underlyingSecType="STK",
                underlyingConId=stock_contract.conId,
            )

            if not chains:
                return await self._yfinance_options_fallback(symbol, expiry)

            chain = chains[0]
            available_expiries = sorted(chain.expirations)

            if expiry:
                selected_expiry = expiry if expiry in available_expiries else available_expiries[0]
            else:
                selected_expiry = available_expiries[0] if available_expiries else None

            if not selected_expiry:
                return []

            option_contracts: List[OptionContract] = []
            for strike in chain.strikes:
                for right in ["C", "P"]:
                    opt = ib_insync.Option(
                        symbol,
                        selected_expiry,
                        strike,
                        right,
                        "SMART",
                    )
                    try:
                        qualified = await self._ib.qualifyContractsAsync(opt)
                        if not qualified:
                            continue

                        ticker = self._ib.reqMktData(qualified[0], "100,101,106", False, False)
                        await asyncio.sleep(0.5)  # Allow data to populate

                        option_contracts.append(
                            OptionContract(
                                symbol=symbol,
                                expiry=selected_expiry,
                                strike=float(strike),
                                right=right,
                                bid=float(ticker.bid or 0),
                                ask=float(ticker.ask or 0),
                                last=float(ticker.last or 0),
                                volume=int(ticker.volume or 0),
                                open_interest=int(ticker.openInterest or 0),
                                implied_vol=float(getattr(ticker, "impliedVol", 0) or 0),
                                delta=float(getattr(ticker, "modelGreeks", None) and
                                            ticker.modelGreeks.delta or 0),
                                gamma=float(getattr(ticker, "modelGreeks", None) and
                                            ticker.modelGreeks.gamma or 0),
                                theta=float(getattr(ticker, "modelGreeks", None) and
                                            ticker.modelGreeks.theta or 0),
                                vega=float(getattr(ticker, "modelGreeks", None) and
                                           ticker.modelGreeks.vega or 0),
                            )
                        )
                        self._ib.cancelMktData(qualified[0])
                    except Exception:
                        continue

            log.debug(
                "IBKR options chain fetched",
                symbol=symbol,
                expiry=selected_expiry,
                contracts=len(option_contracts),
            )
            return option_contracts

        except Exception as exc:
            log.warning(
                "IBKR options chain failed — using yfinance fallback",
                symbol=symbol,
                error=str(exc),
            )
            return await self._yfinance_options_fallback(symbol, expiry)

    # ------------------------------------------------------------------
    # Fallback methods
    # ------------------------------------------------------------------

    async def _yfinance_fallback(
        self,
        symbol: str,
        bar_size: str,
        period: str = "1mo",
    ) -> List[OHLCV]:
        """
        Fetch historical data via yfinance when IBKR is unavailable.

        Args:
            symbol: Ticker symbol (yfinance format).
            bar_size: NEXUS interval string.
            period: yfinance period string.

        Returns:
            List of OHLCV candles, empty list on failure.
        """
        log.debug(
            "Using yfinance fallback for historical data",
            symbol=symbol,
            bar_size=bar_size,
        )
        try:
            from src.data.providers.yfinance_provider import YFinanceProvider

            yf = YFinanceProvider()
            candles = await yf.get_historical(
                symbol=symbol,
                interval=bar_size,
                period=period,
            )
            return candles
        except Exception as exc:
            log.error(
                "yfinance fallback also failed",
                symbol=symbol,
                error=str(exc),
            )
            return []

    async def _yfinance_options_fallback(
        self,
        symbol: str,
        expiry: Optional[str],
    ) -> List[OptionContract]:
        """Fetch options chain via yfinance when IBKR is unavailable."""
        try:
            from src.data.providers.yfinance_provider import YFinanceProvider

            yf = YFinanceProvider()
            chain_data = await yf.get_options_chain(symbol, expiry)
            options: List[OptionContract] = []

            for side, right in [("calls", "C"), ("puts", "P")]:
                for row in chain_data.get(side, []):
                    options.append(
                        OptionContract(
                            symbol=symbol,
                            expiry=chain_data.get("expiry", ""),
                            strike=float(row.get("strike", 0)),
                            right=right,
                            bid=float(row.get("bid", 0)),
                            ask=float(row.get("ask", 0)),
                            last=float(row.get("lastPrice", 0)),
                            volume=int(row.get("volume", 0) or 0),
                            open_interest=int(row.get("openInterest", 0) or 0),
                            implied_vol=float(row.get("impliedVolatility", 0)),
                            delta=0.0,
                            gamma=0.0,
                            theta=0.0,
                            vega=0.0,
                        )
                    )
            return options
        except Exception as exc:
            log.warning(
                "yfinance options fallback failed",
                symbol=symbol,
                error=str(exc),
            )
            return []

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _detect_market(symbol: str) -> str:
        """Infer market type from symbol format."""
        upper = symbol.upper()
        crypto_symbols = {"BTC", "ETH", "SOL", "XRP", "ADA", "AVAX", "DOT"}
        forex_patterns = ["/"]

        if upper in crypto_symbols:
            return "crypto"
        if any(p in symbol for p in forex_patterns):
            return "forex"
        if upper.startswith("^") or upper.endswith(".NS") or upper.endswith(".BO"):
            return "indian_stocks"
        return "us_stocks"

    @property
    def is_connected(self) -> bool:
        """Return True if the IBKR connection is active."""
        return self._connected and self._ib is not None
