"""
NEXUS ALPHA - Emergency Shutdown
===================================
Executes a full system emergency shutdown:

1. Cancel all open orders across all exchanges (parallel)
2. Close all open positions at market (parallel)
3. Send CRITICAL alert to all channels
4. Log complete system state to Supabase
5. Set SHUTDOWN flag preventing any new orders
6. Require manual restart flag reset

This module is intentionally written to be dependency-light so it can
run even when other subsystems have failed.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Global shutdown flag
# ---------------------------------------------------------------------------

class _ShutdownFlag:
    """
    Thread-safe and async-safe shutdown flag.

    Once set, it cannot be cleared programmatically – a manual operator
    action (calling ``reset()``) is required to resume trading.
    """

    def __init__(self) -> None:
        self._active: bool = False
        self._reason: Optional[str] = None
        self._timestamp: Optional[float] = None
        self._lock = threading.Lock()

    def set(self, reason: str) -> None:
        with self._lock:
            if not self._active:
                self._active = True
                self._reason = reason
                self._timestamp = time.time()
                logger.critical(
                    "SHUTDOWN FLAG SET: %s (at %s)",
                    reason,
                    datetime.fromtimestamp(self._timestamp, tz=timezone.utc).isoformat(),
                )

    def is_set(self) -> bool:
        with self._lock:
            return self._active

    def reset(self, operator_confirmation: str) -> bool:
        """
        Reset the shutdown flag.  Requires operator confirmation string
        ``"CONFIRM_MANUAL_RESTART"`` to prevent accidental resets.

        Returns
        -------
        bool
            True if reset succeeded.
        """
        if operator_confirmation != "CONFIRM_MANUAL_RESTART":
            logger.error(
                "SHUTDOWN FLAG reset rejected: wrong confirmation string '%s'",
                operator_confirmation,
            )
            return False
        with self._lock:
            self._active = False
            logger.critical("SHUTDOWN FLAG CLEARED by manual operator action")
            return True

    @property
    def reason(self) -> Optional[str]:
        return self._reason

    @property
    def timestamp(self) -> Optional[float]:
        return self._timestamp

    def __bool__(self) -> bool:
        return self.is_set()


# Module-level singleton – import this to check/set the shutdown state
SHUTDOWN_FLAG = _ShutdownFlag()


# ---------------------------------------------------------------------------
# Emergency shutdown procedure
# ---------------------------------------------------------------------------

async def emergency_shutdown(
    executors: List[Any],
    alert_manager: Optional[Any],
    reason: str,
) -> Dict[str, Any]:
    """
    Execute a full emergency shutdown of the NEXUS ALPHA trading system.

    This coroutine is designed to run to completion even under partial
    failures.  All steps are attempted regardless of earlier errors.

    Parameters
    ----------
    executors : List[BaseExecutor]
        All active executor instances (Binance, OANDA, Kite, Alpaca, IBKR,
        PaperTrader).  Pass all instances even if some may have already failed.
    alert_manager : Optional[Any]
        Alert manager instance with ``send_critical(message)`` method.
        Pass None if the alert system itself has failed.
    reason : str
        Human-readable reason for the shutdown (logged and persisted).

    Returns
    -------
    dict
        Summary of the shutdown outcome with counts and any errors.
    """
    start_time = time.time()
    shutdown_report: Dict[str, Any] = {
        "reason": reason,
        "start_time": datetime.fromtimestamp(start_time, tz=timezone.utc).isoformat(),
        "orders_cancelled": 0,
        "positions_closed": 0,
        "alert_sent": False,
        "state_persisted": False,
        "errors": [],
    }

    logger.critical("=" * 70)
    logger.critical("EMERGENCY SHUTDOWN INITIATED")
    logger.critical("Reason: %s", reason)
    logger.critical("=" * 70)

    # ---- Step 1: Set shutdown flag IMMEDIATELY ----
    SHUTDOWN_FLAG.set(reason)

    # ---- Step 2: Cancel all orders across all exchanges (parallel) ----
    cancel_tasks = [_safe_cancel_all(executor) for executor in executors]
    cancel_results = await asyncio.gather(*cancel_tasks, return_exceptions=True)

    total_cancelled = 0
    for result in cancel_results:
        if isinstance(result, int):
            total_cancelled += result
        elif isinstance(result, Exception):
            err = f"Cancel orders failed: {result}"
            shutdown_report["errors"].append(err)
            logger.error("EMERGENCY_SHUTDOWN: %s", err)

    shutdown_report["orders_cancelled"] = total_cancelled
    logger.critical("EMERGENCY_SHUTDOWN: %d orders cancelled", total_cancelled)

    # Brief pause to let cancellations propagate
    await asyncio.sleep(0.5)

    # ---- Step 3: Close all positions at market (parallel) ----
    close_tasks = [_safe_close_all(executor) for executor in executors]
    close_results = await asyncio.gather(*close_tasks, return_exceptions=True)

    total_closed = 0
    for result in close_results:
        if isinstance(result, int):
            total_closed += result
        elif isinstance(result, Exception):
            err = f"Close positions failed: {result}"
            shutdown_report["errors"].append(err)
            logger.error("EMERGENCY_SHUTDOWN: %s", err)

    shutdown_report["positions_closed"] = total_closed
    logger.critical("EMERGENCY_SHUTDOWN: %d positions closed", total_closed)

    # ---- Step 4: Send CRITICAL alert ----
    alert_message = _build_alert_message(reason, shutdown_report)
    alert_sent = await _send_alert(alert_manager, alert_message)
    shutdown_report["alert_sent"] = alert_sent

    # ---- Step 5: Log complete system state to Supabase ----
    persisted = await _persist_shutdown_state(shutdown_report, executors)
    shutdown_report["state_persisted"] = persisted

    # ---- Shutdown summary ----
    elapsed = time.time() - start_time
    shutdown_report["elapsed_seconds"] = elapsed

    logger.critical("=" * 70)
    logger.critical("EMERGENCY SHUTDOWN COMPLETE in %.2fs", elapsed)
    logger.critical("  Orders cancelled: %d", total_cancelled)
    logger.critical("  Positions closed: %d", total_closed)
    logger.critical("  Alert sent: %s", alert_sent)
    logger.critical("  State persisted: %s", persisted)
    logger.critical("  Errors: %d", len(shutdown_report["errors"]))
    logger.critical("  SHUTDOWN FLAG: %s", SHUTDOWN_FLAG.is_set())
    logger.critical("=" * 70)
    logger.critical(
        "SYSTEM IS NOW HALTED.  To restart, call: "
        "SHUTDOWN_FLAG.reset('CONFIRM_MANUAL_RESTART')"
    )

    return shutdown_report


# ---------------------------------------------------------------------------
# Helper coroutines
# ---------------------------------------------------------------------------

async def _safe_cancel_all(executor: Any) -> int:
    """Cancel all orders for one executor, returning count cancelled."""
    try:
        cancelled = await executor.cancel_all_orders()
        count = len(cancelled) if isinstance(cancelled, list) else 0
        logger.info(
            "EMERGENCY_SHUTDOWN: %s – %d orders cancelled",
            executor.exchange_name, count,
        )
        return count
    except Exception as exc:
        logger.error(
            "EMERGENCY_SHUTDOWN: cancel_all_orders failed for %s: %s",
            getattr(executor, "exchange_name", "unknown"), exc,
        )
        return 0


async def _safe_close_all(executor: Any) -> int:
    """Close all positions for one executor, returning count closed."""
    try:
        closed = await executor.close_all_positions()
        count = len(closed) if isinstance(closed, list) else 0
        logger.info(
            "EMERGENCY_SHUTDOWN: %s – %d positions closed",
            executor.exchange_name, count,
        )
        return count
    except Exception as exc:
        logger.error(
            "EMERGENCY_SHUTDOWN: close_all_positions failed for %s: %s",
            getattr(executor, "exchange_name", "unknown"), exc,
        )
        return 0


async def _send_alert(alert_manager: Optional[Any], message: str) -> bool:
    """Attempt to send alert through all available channels."""
    # Always log the alert regardless
    logger.critical("EMERGENCY ALERT:\n%s", message)

    if alert_manager is None:
        logger.warning("EMERGENCY_SHUTDOWN: no AlertManager – alert logged only")
        return False

    try:
        await alert_manager.send_critical(message)
        logger.critical("EMERGENCY_SHUTDOWN: CRITICAL alert dispatched successfully")
        return True
    except Exception as exc:
        logger.error("EMERGENCY_SHUTDOWN: alert dispatch failed: %s", exc)
        # Try Telegram directly as last resort
        try:
            await _send_telegram_fallback(message)
            return True
        except Exception:
            return False


async def _send_telegram_fallback(message: str) -> None:
    """Direct Telegram notification as last-resort fallback."""
    import aiohttp
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": f"NEXUS ALPHA EMERGENCY:\n{message}"}
    async with aiohttp.ClientSession() as session:
        async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status != 200:
                raise RuntimeError(f"Telegram fallback failed: {resp.status}")


def _build_alert_message(reason: str, report: Dict[str, Any]) -> str:
    """Build the CRITICAL alert message."""
    return (
        "===== NEXUS ALPHA EMERGENCY SHUTDOWN =====\n"
        f"REASON: {reason}\n"
        f"TIME:   {report['start_time']}\n"
        f"ORDERS CANCELLED: {report['orders_cancelled']}\n"
        f"POSITIONS CLOSED: {report['positions_closed']}\n"
        f"ERRORS: {len(report.get('errors', []))}\n"
        "ALL TRADING IS NOW HALTED.\n"
        "Manual restart required: SHUTDOWN_FLAG.reset('CONFIRM_MANUAL_RESTART')\n"
        "=========================================="
    )


async def _persist_shutdown_state(
    report: Dict[str, Any],
    executors: List[Any],
) -> bool:
    """Save full shutdown state to Supabase system_events table."""
    now_iso = datetime.now(tz=timezone.utc).isoformat()

    # Collect account states
    account_states: List[Dict[str, Any]] = []
    for executor in executors:
        try:
            balance = await asyncio.wait_for(executor.get_account_balance(), timeout=5.0)
            account_states.append({
                "exchange": getattr(executor, "exchange_name", "unknown"),
                "balance_usd": balance,
            })
        except Exception as exc:  # noqa: BLE001
            account_states.append({
                "exchange": getattr(executor, "exchange_name", "unknown"),
                "error": str(exc),
            })

    record = {
        "event_type": "emergency_shutdown",
        "timestamp": now_iso,
        "reason": report.get("reason"),
        "orders_cancelled": report.get("orders_cancelled", 0),
        "positions_closed": report.get("positions_closed", 0),
        "errors": report.get("errors", []),
        "account_states": account_states,
        "elapsed_seconds": report.get("elapsed_seconds", 0),
    }

    try:
        from src.db.supabase_client import get_supabase_client  # type: ignore[import]
        loop = asyncio.get_event_loop()
        client = get_supabase_client()
        await loop.run_in_executor(
            None,
            lambda: client.table("system_events").insert(record).execute(),
        )
        logger.info("EMERGENCY_SHUTDOWN: state persisted to Supabase")
        return True
    except ImportError:
        logger.warning("EMERGENCY_SHUTDOWN: Supabase client unavailable – state not persisted")
        return False
    except Exception as exc:
        logger.error("EMERGENCY_SHUTDOWN: Supabase persist failed: %s", exc)
        return False


def check_shutdown() -> None:
    """
    Convenience guard function: raise RuntimeError if shutdown flag is set.

    Place this at the start of any trade execution path to prevent
    orders during shutdown.

    Raises
    ------
    RuntimeError
        If SHUTDOWN_FLAG is set.
    """
    if SHUTDOWN_FLAG.is_set():
        raise RuntimeError(
            f"NEXUS ALPHA is in SHUTDOWN state (reason: {SHUTDOWN_FLAG.reason}). "
            "Call SHUTDOWN_FLAG.reset('CONFIRM_MANUAL_RESTART') to resume."
        )
