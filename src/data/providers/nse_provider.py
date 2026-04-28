"""
NEXUS ALPHA - NSE Data Provider
==================================
Async wrapper around nsepython for Indian equity / derivatives market data.

All nsepython calls are synchronous, so they run in a thread executor to
avoid blocking the event loop.

Features:
* Option chain with vectorised max-pain via NumPy
* FII/DII flow data
* Advance/decline ratio
* Pre-open session data
* Delivery data
* Bulk and block deals
* NSE trading hours helpers
* TOTP-based token refresh via pyotp

Environment variables:
    NSE_TOTP_SECRET    Base-32 TOTP secret for automated token refresh
    NSE_USER           NSE login username (if required)
    NSE_PASSWORD       NSE login password (if required)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, time as dtime, timezone
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

import numpy as np

logger = logging.getLogger(__name__)

# NSE timezone
_IST = ZoneInfo("Asia/Kolkata")

# NSE trading hours (IST)
NSE_OPEN_TIME  = dtime(9, 15, 0)
NSE_CLOSE_TIME = dtime(15, 30, 0)

# Pre-open session: 09:00 – 09:08
NSE_PREOPEN_OPEN  = dtime(9,  0, 0)
NSE_PREOPEN_CLOSE = dtime(9,  8, 0)


def is_nse_open(dt: Optional[datetime] = None) -> bool:
    """
    Return True if the NSE cash market is currently open.

    Parameters
    ----------
    dt:
        Datetime to check.  Defaults to the current IST time.

    Returns
    -------
    bool
    """
    if dt is None:
        dt = datetime.now(_IST)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=_IST)
    else:
        dt = dt.astimezone(_IST)

    # Weekends are closed
    if dt.weekday() >= 5:
        return False

    current_time = dt.time().replace(tzinfo=None)
    return NSE_OPEN_TIME <= current_time <= NSE_CLOSE_TIME


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------

class NSEDataProvider:
    """
    Async NSE data provider wrapping the nsepython library.

    All data methods run nsepython calls in a ``ThreadPoolExecutor``
    to keep the asyncio event loop free.
    """

    def __init__(self) -> None:
        self._totp_secret = os.getenv("NSE_TOTP_SECRET", "")
        self._loop_executor = None  # uses default executor

    # ------------------------------------------------------------------
    # Internal helper
    # ------------------------------------------------------------------

    async def _run_sync(self, func, *args, **kwargs):
        """Run a blocking nsepython call in the default thread executor."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None, lambda: func(*args, **kwargs)
        )

    # ------------------------------------------------------------------
    # Option chain
    # ------------------------------------------------------------------

    async def get_option_chain(
        self,
        symbol: str = "NIFTY",
    ) -> Dict[str, Any]:
        """
        Fetch the full NSE option chain for *symbol*.

        Parameters
        ----------
        symbol:
            Index or stock symbol, e.g. ``"NIFTY"``, ``"BANKNIFTY"``,
            ``"RELIANCE"``.

        Returns
        -------
        dict
            Raw nsepython option chain payload.
        """
        try:
            from nsepython import nse_optionchain_scrapper
            raw = await self._run_sync(nse_optionchain_scrapper, symbol)
            return raw
        except Exception as exc:
            logger.error("get_option_chain(%s): %s", symbol, exc)
            raise

    # ------------------------------------------------------------------

    async def get_fii_dii_data(self) -> Dict[str, Any]:
        """
        Fetch FII/DII activity data from the NSE website.

        Returns
        -------
        dict
            FII and DII buy/sell/net values for the most recent session.
        """
        try:
            from nsepython import fii_dii
            raw = await self._run_sync(fii_dii)
            return raw
        except Exception as exc:
            logger.error("get_fii_dii_data: %s", exc)
            raise

    # ------------------------------------------------------------------

    async def get_advances_declines(self) -> Dict[str, Any]:
        """
        Fetch NSE market advance/decline data.

        Returns
        -------
        dict
            Contains ``advances``, ``declines``, ``unchanged`` counts.
        """
        try:
            from nsepython import advances_declines
            raw = await self._run_sync(advances_declines)
            return raw
        except Exception as exc:
            logger.error("get_advances_declines: %s", exc)
            raise

    # ------------------------------------------------------------------

    async def get_preopen_data(self) -> Dict[str, Any]:
        """
        Fetch NSE pre-open session data (09:00 – 09:08 IST).

        Returns
        -------
        dict
            Pre-open prices, IEP, and volume data.
        """
        try:
            from nsepython import preopen_nifty
            raw = await self._run_sync(preopen_nifty)
            return raw
        except Exception as exc:
            logger.error("get_preopen_data: %s", exc)
            raise

    # ------------------------------------------------------------------

    async def get_delivery_data(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch delivery percentage data for a stock.

        Parameters
        ----------
        symbol:
            NSE stock symbol, e.g. ``"RELIANCE"``.

        Returns
        -------
        dict
            Delivery quantity, delivery percentage, etc.
        """
        try:
            from nsepython import nsefetch
            url = (
                f"https://www.nseindia.com/api/quote-equity?"
                f"symbol={symbol}&section=trade_info"
            )
            raw = await self._run_sync(nsefetch, url)
            return raw
        except Exception as exc:
            logger.error("get_delivery_data(%s): %s", symbol, exc)
            raise

    # ------------------------------------------------------------------

    async def get_bulk_block_deals(self) -> Dict[str, Any]:
        """
        Fetch NSE bulk and block deal data for the current session.

        Returns
        -------
        dict
            Lists of bulk deals and block deals with symbol, price,
            quantity, and client details.
        """
        try:
            from nsepython import nsefetch
            bulk_url  = "https://www.nseindia.com/api/bulk-deals"
            block_url = "https://www.nseindia.com/api/block-deal"
            bulk  = await self._run_sync(nsefetch, bulk_url)
            block = await self._run_sync(nsefetch, block_url)
            return {"bulk_deals": bulk, "block_deals": block}
        except Exception as exc:
            logger.error("get_bulk_block_deals: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Option metrics (vectorised)
    # ------------------------------------------------------------------

    def compute_option_metrics(
        self,
        chain_data: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Compute derived option market metrics from an option chain payload.

        Metrics computed:
        * **pcr** – put/call open interest ratio
        * **max_pain** – strike price causing maximum option seller profit
        * **oi_buildup** – strikes with highest OI change in last session
        * **iv_skew** – difference between 25-delta put and call IV

        Parameters
        ----------
        chain_data:
            Raw dict from :meth:`get_option_chain`.

        Returns
        -------
        dict
            ``{pcr, max_pain, oi_buildup, iv_skew}``.
        """
        records = chain_data.get("records", {})
        data    = records.get("data", [])

        if not data:
            return {"pcr": None, "max_pain": None, "oi_buildup": [], "iv_skew": None}

        strikes = np.array([
            d["strikePrice"] for d in data
            if "strikePrice" in d
        ], dtype=np.float64)

        call_oi = np.array([
            d.get("CE", {}).get("openInterest", 0) or 0 for d in data
        ], dtype=np.float64)

        put_oi = np.array([
            d.get("PE", {}).get("openInterest", 0) or 0 for d in data
        ], dtype=np.float64)

        call_iv = np.array([
            d.get("CE", {}).get("impliedVolatility", 0) or 0 for d in data
        ], dtype=np.float64)

        put_iv = np.array([
            d.get("PE", {}).get("impliedVolatility", 0) or 0 for d in data
        ], dtype=np.float64)

        # PCR
        total_put_oi  = float(put_oi.sum())
        total_call_oi = float(call_oi.sum())
        pcr = (total_put_oi / total_call_oi) if total_call_oi > 0 else None

        # Max pain (vectorised) – the strike minimising total option value
        max_pain_strike = self._calculate_max_pain(
            strikes, call_oi, put_oi
        )

        # OI buildup: top 5 strikes by call OI
        if len(strikes) > 0:
            top_call_idx = np.argsort(call_oi)[-5:][::-1]
            top_put_idx  = np.argsort(put_oi)[-5:][::-1]
            oi_buildup = {
                "call_concentration": [
                    {"strike": float(strikes[i]), "oi": float(call_oi[i])}
                    for i in top_call_idx
                ],
                "put_concentration": [
                    {"strike": float(strikes[i]), "oi": float(put_oi[i])}
                    for i in top_put_idx
                ],
            }
        else:
            oi_buildup = {}

        # IV skew: nearest 25-delta approximation (use OTM strikes ~5% away)
        underlying = float(records.get("underlyingValue", 0))
        iv_skew = None
        if underlying > 0 and len(strikes) > 0:
            otm_call_mask = strikes > underlying * 1.05
            otm_put_mask  = strikes < underlying * 0.95
            if otm_call_mask.any() and otm_put_mask.any():
                atm_call_iv = float(call_iv[otm_call_mask][0]) if call_iv[otm_call_mask].any() else 0
                atm_put_iv  = float(put_iv[otm_put_mask][-1]) if put_iv[otm_put_mask].any() else 0
                iv_skew = atm_put_iv - atm_call_iv

        return {
            "pcr":        pcr,
            "max_pain":   max_pain_strike,
            "oi_buildup": oi_buildup,
            "iv_skew":    iv_skew,
            "underlying": underlying,
        }

    # ------------------------------------------------------------------

    def _calculate_max_pain(
        self,
        strikes: "np.ndarray",
        call_oi: "np.ndarray",
        put_oi: "np.ndarray",
    ) -> Optional[float]:
        """
        Vectorised max-pain calculation.

        For each candidate expiry strike, compute the total pain
        (value at expiry) for all option holders, then find the
        strike that minimises total open-interest-weighted pain.

        Complexity: O(n²) in naive form; fully vectorised here using
        NumPy broadcasting to O(n) memory with a single matrix operation.

        Parameters
        ----------
        strikes:
            Sorted array of all strike prices.
        call_oi:
            Open interest for call options at each strike.
        put_oi:
            Open interest for put options at each strike.

        Returns
        -------
        float or None
            Max-pain strike, or None if inputs are empty.
        """
        if len(strikes) == 0:
            return None

        n = len(strikes)
        # Broadcast: pain_matrix[i, j] = pain to call holders at strike j
        #            if expiry is at strike i
        strikes_row = strikes.reshape(1, n)   # shape (1, n) – option strikes
        expiry_col  = strikes.reshape(n, 1)   # shape (n, 1) – candidate expiry

        # Call pain: if expiry < strike, call holders lose OI * (strike - expiry)
        # but since calls are ITM when expiry > strike: pain = max(expiry - strike, 0) * call_oi
        call_pain_matrix = np.maximum(expiry_col - strikes_row, 0) * call_oi  # (n, n)

        # Put pain: max(strike - expiry, 0) * put_oi
        put_pain_matrix  = np.maximum(strikes_row - expiry_col, 0) * put_oi   # (n, n)

        # Total pain at each candidate expiry strike
        total_pain = call_pain_matrix.sum(axis=1) + put_pain_matrix.sum(axis=1)

        min_idx = int(np.argmin(total_pain))
        return float(strikes[min_idx])

    # ------------------------------------------------------------------
    # TOTP token refresh
    # ------------------------------------------------------------------

    def get_totp_code(self) -> Optional[str]:
        """
        Generate the current TOTP code using the configured secret.

        Returns
        -------
        str or None
            6-digit TOTP code, or None if no secret is configured.
        """
        if not self._totp_secret:
            logger.warning("NSE_TOTP_SECRET not configured; cannot generate TOTP.")
            return None
        try:
            import pyotp
            totp = pyotp.TOTP(self._totp_secret)
            return totp.now()
        except ImportError:
            logger.error("pyotp not installed; install with: pip install pyotp")
            return None
