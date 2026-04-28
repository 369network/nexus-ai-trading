"""
NEXUS ALPHA - Black Swan Protection
======================================
Detects and responds to extreme, tail-risk market events.

Detection triggers:
  - VIX > 40
  - Crypto flash crash > 20% in 1 hour
  - Multiple markets moving > 3 sigma simultaneously

Response:
  - Reduce all position sizes by 50%
  - Widen all stops by 50%
  - Halt new position opening for 24 hours
  - Send CRITICAL alert on all channels
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection thresholds
# ---------------------------------------------------------------------------
VIX_THRESHOLD             = 40.0      # VIX index level
CRYPTO_CRASH_THRESHOLD    = 0.20      # 20% drop in 1 hour
MULTI_SIGMA_THRESHOLD     = 3.0       # Standard deviations
MULTI_MARKET_MIN_TRIGGERS = 2         # Minimum markets breaching 3σ

# Response parameters
POSITION_SIZE_REDUCTION   = 0.50      # Reduce to 50%
STOP_WIDEN_FACTOR         = 1.50      # Widen stops by 50%
NO_NEW_POSITIONS_HOURS    = 24        # Hours to halt new trades

# ---------------------------------------------------------------------------
# Internal state (module-level flag; callers should also check)
# ---------------------------------------------------------------------------
_black_swan_active: bool = False
_black_swan_until: float = 0.0


class BlackSwanProtection:
    """
    Monitors for black-swan market events and orchestrates emergency responses.

    State is maintained at the instance level and also reflected in the
    module-level ``_black_swan_active`` flag for fast cross-module checks.

    Usage
    -----
    bs = BlackSwanProtection()
    if bs.detect_black_swan(all_market_data):
        await bs.execute_protection(portfolio, executors)
    """

    def __init__(self) -> None:
        self._active: bool = False
        self._active_until: float = 0.0
        self._trigger_reasons: List[str] = []

    # ------------------------------------------------------------------
    # Detection
    # ------------------------------------------------------------------

    def detect_black_swan(self, market_data_all_markets: Dict[str, Any]) -> bool:
        """
        Scan all market data for black-swan conditions.

        Parameters
        ----------
        market_data_all_markets : dict
            Nested dict keyed by market/symbol.  Expected keys per entry:
              - 'vix' (float, optional): current VIX reading
              - 'price_now' (float): current price
              - 'price_1h_ago' (float): price 1 hour ago (for crash detection)
              - 'returns_series' (list[float]): recent returns for σ computation
              - 'market' (str): market segment

        Returns
        -------
        bool
            True if a black-swan event is detected.  Protection must be
            executed by the caller via ``execute_protection()``.
        """
        global _black_swan_active, _black_swan_until

        reasons: List[str] = []

        # Already in black-swan mode?
        if self._active and time.time() < self._active_until:
            logger.warning("BlackSwan already active until %s", self._active_until)
            return True

        # ----------------------------------------------------------------
        # Check 1: VIX > 40
        # ----------------------------------------------------------------
        for key, data in market_data_all_markets.items():
            vix = data.get("vix")
            if vix is not None and vix > VIX_THRESHOLD:
                reason = f"VIX={vix:.1f} > threshold {VIX_THRESHOLD}"
                reasons.append(reason)
                logger.critical("BlackSwan TRIGGER: %s", reason)
                break  # One VIX reading is enough

        # ----------------------------------------------------------------
        # Check 2: Crypto flash crash > 20% in 1 hour
        # ----------------------------------------------------------------
        for key, data in market_data_all_markets.items():
            market = data.get("market", "")
            if market != "crypto":
                continue

            price_now = data.get("price_now")
            price_1h  = data.get("price_1h_ago")
            if price_now is None or price_1h is None or price_1h <= 0:
                continue

            drop_pct = (price_1h - price_now) / price_1h
            if drop_pct >= CRYPTO_CRASH_THRESHOLD:
                reason = f"Crypto crash: {key} dropped {drop_pct:.1%} in 1h"
                reasons.append(reason)
                logger.critical("BlackSwan TRIGGER: %s", reason)

        # ----------------------------------------------------------------
        # Check 3: Multiple markets moving > 3σ simultaneously
        # ----------------------------------------------------------------
        sigma_breaches: List[str] = []
        for key, data in market_data_all_markets.items():
            returns_series = data.get("returns_series")
            if not returns_series or len(returns_series) < 10:
                continue

            arr = np.array(returns_series, dtype=float)
            if len(arr) < 5:
                continue

            mu = float(np.mean(arr))
            sigma = float(np.std(arr))
            if sigma <= 0:
                continue

            # Most recent return
            last_return = float(arr[-1])
            z_score = abs((last_return - mu) / sigma)
            if z_score >= MULTI_SIGMA_THRESHOLD:
                sigma_breaches.append(f"{key} z={z_score:.1f}σ")

        if len(sigma_breaches) >= MULTI_MARKET_MIN_TRIGGERS:
            reason = (
                f"{len(sigma_breaches)} markets moved >3σ simultaneously: "
                + ", ".join(sigma_breaches[:5])
            )
            reasons.append(reason)
            logger.critical("BlackSwan TRIGGER: %s", reason)

        # ----------------------------------------------------------------
        # Verdict
        # ----------------------------------------------------------------
        if reasons:
            self._active = True
            self._active_until = time.time() + NO_NEW_POSITIONS_HOURS * 3600
            self._trigger_reasons = reasons
            _black_swan_active = True
            _black_swan_until  = self._active_until
            logger.critical(
                "BLACK SWAN DETECTED – halting new positions for %dh | reasons: %s",
                NO_NEW_POSITIONS_HOURS, " | ".join(reasons),
            )
            return True

        return False

    # ------------------------------------------------------------------
    # Response execution
    # ------------------------------------------------------------------

    async def execute_protection(
        self,
        portfolio: Any,
        executors: List[Any],
    ) -> None:
        """
        Execute the full black-swan protection protocol asynchronously.

        Actions:
          1. Reduce all position sizes by 50% (close partial positions)
          2. Widen all stop-loss orders by 50%
          3. Halt new position opening for 24 hours
          4. Send CRITICAL alerts on all channels
          5. Log system state to Supabase

        Parameters
        ----------
        portfolio : Any
            Portfolio object with a list of open positions.
        executors : List[Any]
            List of executor instances (BaseExecutor subclasses).
        """
        logger.critical(
            "BLACK SWAN PROTECTION EXECUTING | reasons=%s",
            " | ".join(self._trigger_reasons),
        )

        # ---- Step 1: Reduce position sizes by 50% (close half of each) ----
        close_tasks = []
        for executor in executors:
            try:
                positions = await executor.get_positions()
                for pos in positions:
                    half_size = pos.size * POSITION_SIZE_REDUCTION
                    if half_size > 0:
                        task = executor.close_position(
                            symbol=pos.symbol,
                            size=half_size,
                            reason="black_swan_protection_50pct_reduce",
                        )
                        close_tasks.append(task)
            except Exception as exc:  # noqa: BLE001
                logger.error("BlackSwan: failed to get positions from %s: %s", executor, exc)

        if close_tasks:
            results = await asyncio.gather(*close_tasks, return_exceptions=True)
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error("BlackSwan: partial close failed for task %d: %s", i, result)
            logger.critical(
                "BlackSwan: %d partial position closures executed",
                len([r for r in results if not isinstance(r, Exception)]),
            )

        # ---- Step 2: Widen stops by 50% ----
        # This is advisory – executors that support amend orders should apply it.
        # In practice the caller / position manager should re-calculate stops.
        logger.critical(
            "BlackSwan: STOP WIDEN directive – all stops should be widened by %.0f%%",
            (STOP_WIDEN_FACTOR - 1) * 100,
        )

        # ---- Step 3: No new positions flag already set in detect() ----
        logger.critical(
            "BlackSwan: NEW POSITIONS HALTED for %d hours until %s",
            NO_NEW_POSITIONS_HOURS,
            time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(self._active_until)),
        )

        # ---- Step 4: Alert all channels ----
        await self._send_alerts()

        # ---- Step 5: Log to Supabase ----
        await self._log_to_db()

    def is_active(self) -> bool:
        """Return True if black-swan protection mode is currently active."""
        if self._active and time.time() >= self._active_until:
            self._active = False
            logger.info("BlackSwan: protection window expired – resuming normal operation")
        return self._active

    def time_remaining_seconds(self) -> float:
        """Return seconds until protection mode expires (0 if inactive)."""
        if not self._active:
            return 0.0
        return max(0.0, self._active_until - time.time())

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send_alerts(self) -> None:
        """Attempt to send CRITICAL alerts via all configured channels."""
        message = (
            "BLACK SWAN PROTECTION ACTIVATED\n"
            f"Triggers: {' | '.join(self._trigger_reasons)}\n"
            f"Actions: 50% position reduction, stops widened 50%, "
            f"no new positions for {NO_NEW_POSITIONS_HOURS}h\n"
            f"Expires: {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(self._active_until))}"
        )

        try:
            from src.utils.alert_manager import AlertManager  # type: ignore[import]
            alert_manager = AlertManager()
            await alert_manager.send_critical(message)
            logger.critical("BlackSwan: alerts dispatched")
        except ImportError:
            logger.warning("BlackSwan: AlertManager not available – logging alert only")
            logger.critical("CRITICAL ALERT: %s", message)
        except Exception as exc:  # noqa: BLE001
            logger.error("BlackSwan: alert dispatch failed: %s", exc)
            logger.critical("CRITICAL ALERT (fallback): %s", message)

    async def _log_to_db(self) -> None:
        """Persist the black-swan event to Supabase."""
        from datetime import datetime, timezone
        record = {
            "event_type": "black_swan",
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
            "reasons": self._trigger_reasons,
            "halt_until": self._active_until,
            "size_reduction_pct": POSITION_SIZE_REDUCTION * 100,
            "stop_widen_factor": STOP_WIDEN_FACTOR,
        }
        try:
            from src.db.supabase_client import get_supabase_client  # type: ignore[import]
            client = get_supabase_client()
            client.table("system_events").insert(record).execute()
            logger.info("BlackSwan: event logged to Supabase")
        except ImportError:
            logger.warning("BlackSwan: Supabase client not available – DB log skipped")
        except Exception as exc:  # noqa: BLE001
            logger.error("BlackSwan: Supabase log failed: %s | record=%s", exc, record)


def is_black_swan_active() -> bool:
    """
    Module-level fast check: is black-swan protection currently active?

    Can be called without instantiating BlackSwanProtection.
    """
    return _black_swan_active and time.time() < _black_swan_until
