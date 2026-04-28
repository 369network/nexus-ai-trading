"""
NEXUS ALPHA - Kite WebSocket Provider
========================================
Full KiteConnect WebSocket client with:
* Instrument subscription and per-token callback dispatch
* Daily authentication via request_token + TOTP
* Access token persistence to disk (valid for 1 trading day)
* Instrument token cache to avoid repeated API calls

Environment variables:
    KITE_API_KEY
    KITE_API_SECRET
    KITE_TOTP_SECRET    Base-32 TOTP secret for automated login
    KITE_USER_ID        Zerodha user ID
    KITE_PASSWORD       Zerodha password
    KITE_TOKEN_FILE     Path for storing the access token (default: .kite_token)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_TOKEN_FILE = Path(".kite_token")
_TOKEN_VALIDITY_DAYS = 1   # KiteConnect tokens expire daily

TickCallback = Callable[[dict], None]


class KiteWebSocketProvider:
    """
    KiteConnect WebSocket client for Zerodha Kite.

    Manages authentication (with TOTP auto-fill), instrument token
    caching, and real-time tick streaming.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        api_secret: Optional[str] = None,
        totp_secret: Optional[str] = None,
        token_file: Optional[Path] = None,
    ) -> None:
        self._api_key    = api_key    or os.getenv("KITE_API_KEY", "")
        self._api_secret = api_secret or os.getenv("KITE_API_SECRET", "")
        self._totp_secret= totp_secret or os.getenv("KITE_TOTP_SECRET", "")
        self._token_file = token_file or Path(
            os.getenv("KITE_TOKEN_FILE", str(_DEFAULT_TOKEN_FILE))
        )

        self._access_token: Optional[str] = None
        self._kite = None          # kiteconnect.KiteConnect instance
        self._kws  = None          # kiteconnect.KiteTicker instance

        # Per-token callbacks: {instrument_token: [callback, ...]}
        self._callbacks: Dict[int, List[TickCallback]] = {}

        # Instrument token cache
        self._instrument_cache: Dict[str, List[Dict]] = {}

        self._running = False

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    def _load_token(self) -> Optional[str]:
        """Load a previously stored access token if it is still valid today."""
        if not self._token_file.exists():
            return None
        try:
            payload = json.loads(self._token_file.read_text())
            token_date = payload.get("date")
            token      = payload.get("access_token")
            if token_date == date.today().isoformat() and token:
                logger.info("KiteWS: loaded cached access token.")
                return token
        except Exception as exc:
            logger.warning("KiteWS: failed to load token file: %s", exc)
        return None

    def _save_token(self, token: str) -> None:
        """Persist the access token to disk with the current date."""
        payload = {
            "date":         date.today().isoformat(),
            "access_token": token,
        }
        try:
            self._token_file.write_text(json.dumps(payload))
            logger.info("KiteWS: access token saved to %s", self._token_file)
        except Exception as exc:
            logger.warning("KiteWS: could not save token: %s", exc)

    def get_login_url(self) -> str:
        """
        Return the Kite login URL for manual authentication.

        Returns
        -------
        str
            URL to open in a browser to get the request_token.
        """
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=self._api_key)
        return kite.login_url()

    def complete_login(self, request_token: str) -> str:
        """
        Exchange a request_token for an access_token and persist it.

        Parameters
        ----------
        request_token:
            The one-time token obtained after browser-based Kite login.

        Returns
        -------
        str
            Access token.
        """
        from kiteconnect import KiteConnect
        kite = KiteConnect(api_key=self._api_key)
        session = kite.generate_session(request_token, api_secret=self._api_secret)
        access_token = session["access_token"]
        self._access_token = access_token
        self._save_token(access_token)
        return access_token

    def _generate_totp(self) -> Optional[str]:
        """Generate a current TOTP code from the stored secret."""
        if not self._totp_secret:
            return None
        try:
            import pyotp
            return pyotp.TOTP(self._totp_secret).now()
        except ImportError:
            logger.error("pyotp not installed.")
            return None

    async def _ensure_authenticated(self) -> None:
        """
        Ensure a valid access token is available.

        Tries in order:
        1. Already in memory.
        2. Persisted token file (valid today).
        3. Automated login using KITE_USER_ID / KITE_PASSWORD / TOTP.
        """
        if self._access_token:
            return

        cached = self._load_token()
        if cached:
            self._access_token = cached
            return

        # Automated login via Selenium / requests (simplified headless flow)
        user_id  = os.getenv("KITE_USER_ID", "")
        password = os.getenv("KITE_PASSWORD", "")
        totp_code = self._generate_totp()

        if not user_id or not password or not totp_code:
            raise RuntimeError(
                "Kite authentication required. Set KITE_USER_ID, "
                "KITE_PASSWORD, KITE_TOTP_SECRET env vars, or call "
                "complete_login(request_token) manually."
            )

        # Use requests-based automated login
        try:
            import requests

            session = requests.Session()
            # Step 1: POST credentials
            r = session.post(
                "https://kite.zerodha.com/api/login",
                data={"user_id": user_id, "password": password},
                timeout=20,
            )
            r.raise_for_status()
            resp1 = r.json()
            request_id = resp1["data"]["request_id"]

            # Step 2: POST TOTP
            r2 = session.post(
                "https://kite.zerodha.com/api/twofa",
                data={
                    "user_id":    user_id,
                    "request_id": request_id,
                    "twofa_value": totp_code,
                },
                timeout=20,
            )
            r2.raise_for_status()

            # Step 3: Exchange request_token
            # In a real flow the redirect URL contains the request_token
            # We parse it from the final redirect URL
            final_url = r2.url
            if "request_token=" in final_url:
                req_token = final_url.split("request_token=")[1].split("&")[0]
                self.complete_login(req_token)
                return

        except Exception as exc:
            logger.error("Kite automated login failed: %s", exc)
            raise RuntimeError(
                "Kite automated login failed. Use get_login_url() and "
                "complete_login(request_token) for manual authentication."
            ) from exc

    # ------------------------------------------------------------------
    # WebSocket connection
    # ------------------------------------------------------------------

    async def connect(
        self,
        instrument_tokens: List[int],
        mode: str = "full",
    ) -> None:
        """
        Subscribe to real-time ticks for the given instrument tokens.

        Parameters
        ----------
        instrument_tokens:
            List of integer KiteConnect instrument tokens.
        mode:
            ``"ltp"`` (last price only), ``"quote"`` (OHLCV), or
            ``"full"`` (includes market depth).
        """
        await self._ensure_authenticated()

        from kiteconnect import KiteTicker, KiteConnect

        self._kite = KiteConnect(api_key=self._api_key)
        self._kite.set_access_token(self._access_token)

        self._kws = KiteTicker(self._api_key, self._access_token)

        def on_ticks(ws, ticks):
            for tick in ticks:
                token = tick.get("instrument_token")
                cbs = self._callbacks.get(token, [])
                for cb in cbs:
                    try:
                        cb(tick)
                    except Exception as exc:
                        logger.exception("Kite tick callback error: %s", exc)

        def on_connect(ws, response):
            logger.info("KiteWS: connected – subscribing %d tokens.", len(instrument_tokens))
            ws.subscribe(instrument_tokens)
            ws.set_mode(ws.MODE_FULL if mode == "full" else
                        ws.MODE_QUOTE if mode == "quote" else ws.MODE_LTP,
                        instrument_tokens)

        def on_close(ws, code, reason):
            logger.warning("KiteWS: closed code=%s reason=%s", code, reason)

        def on_error(ws, code, reason):
            logger.error("KiteWS: error code=%s reason=%s", code, reason)

        self._kws.on_ticks     = on_ticks
        self._kws.on_connect   = on_connect
        self._kws.on_close     = on_close
        self._kws.on_error     = on_error

        self._running = True
        loop = asyncio.get_event_loop()
        # KiteTicker uses threads internally; run its connect in executor
        await loop.run_in_executor(None, self._kws.connect, True)

    # ------------------------------------------------------------------

    def on_tick(self, token: int, callback: TickCallback) -> None:
        """
        Register a callback for ticks on a specific instrument token.

        Parameters
        ----------
        token:
            KiteConnect instrument token (integer).
        callback:
            Callable with signature ``(tick: dict) -> None``.
        """
        self._callbacks.setdefault(token, []).append(callback)

    # ------------------------------------------------------------------
    # Historical data
    # ------------------------------------------------------------------

    async def get_historical_data(
        self,
        token: int,
        from_date: str,
        to_date: str,
        interval: str = "minute",
        continuous: bool = False,
        oi: bool = False,
    ) -> List[Dict[str, Any]]:
        """
        Fetch historical OHLCV data for an instrument.

        Parameters
        ----------
        token:
            KiteConnect instrument token.
        from_date:
            Start date string ``"YYYY-MM-DD HH:MM:SS"`` or ``"YYYY-MM-DD"``.
        to_date:
            End date string.
        interval:
            ``"minute"``, ``"3minute"``, ``"5minute"``, ``"10minute"``,
            ``"15minute"``, ``"30minute"``, ``"60minute"``, ``"day"``.
        continuous:
            Whether to use continuous futures data.
        oi:
            Include open interest data.

        Returns
        -------
        List[dict]
            Each dict: ``{date, open, high, low, close, volume}``.
        """
        await self._ensure_authenticated()

        if self._kite is None:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=self._api_key)
            self._kite.set_access_token(self._access_token)

        loop = asyncio.get_event_loop()
        data = await loop.run_in_executor(
            None,
            lambda: self._kite.historical_data(
                token, from_date, to_date, interval, continuous, oi
            ),
        )
        return data

    # ------------------------------------------------------------------
    # Instruments
    # ------------------------------------------------------------------

    async def get_instruments(
        self,
        exchange: str = "NSE",
    ) -> List[Dict[str, Any]]:
        """
        Fetch the full instruments list for an exchange with caching.

        Results are cached for the lifetime of the provider instance to
        avoid re-fetching on every run.

        Parameters
        ----------
        exchange:
            Exchange code, e.g. ``"NSE"``, ``"NFO"``, ``"BSE"``,
            ``"BFO"``, ``"MCX"``, ``"CDS"``.

        Returns
        -------
        List[dict]
            Each dict contains ``instrument_token``, ``tradingsymbol``,
            ``exchange``, ``segment``, ``expiry``, ``strike``, ``lot_size``.
        """
        if exchange in self._instrument_cache:
            logger.debug("Kite: returning cached instruments for %s.", exchange)
            return self._instrument_cache[exchange]

        await self._ensure_authenticated()

        if self._kite is None:
            from kiteconnect import KiteConnect
            self._kite = KiteConnect(api_key=self._api_key)
            self._kite.set_access_token(self._access_token)

        loop = asyncio.get_event_loop()
        instruments = await loop.run_in_executor(
            None, lambda: self._kite.instruments(exchange)
        )
        self._instrument_cache[exchange] = instruments
        logger.info("Kite: cached %d instruments for %s.", len(instruments), exchange)
        return instruments

    # ------------------------------------------------------------------

    async def close(self) -> None:
        """Disconnect the WebSocket and clean up resources."""
        self._running = False
        if self._kws is not None:
            try:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._kws.close)
            except Exception:
                pass
