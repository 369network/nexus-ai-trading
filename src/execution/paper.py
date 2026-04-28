"""
NEXUS ALPHA - Paper Executor
================================
Wraps PaperTrader with DB persistence, lifecycle management, and
settings integration. Overrides _persist_trade to write every paper
fill (with fee and slippage) to Supabase's trades table.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

from src.execution.base_executor import Order
from src.execution.paper_trader import PaperTrader

logger = logging.getLogger(__name__)

# Binance spot taker fee (Tier 0 VIP): 0.10% per side
# BNB discount not applied in paper mode — use conservative flat rate
BINANCE_TAKER_FEE = 0.001        # 0.10%
# Estimated market-impact slippage for a $1k–$10k BTC/ETH order
DEFAULT_SLIPPAGE_PCT = 0.0005    # 0.05% one-way (typical for top-5 pairs)


class PaperExecutor(PaperTrader):
    """
    Paper trading executor.

    On every fill PaperExecutor:
      1. Applies Binance-realistic brokerage fee (0.10% taker)
      2. Applies square-root market-impact slippage model
      3. Persists the trade to Supabase `trades` table via db client

    Parameters
    ----------
    settings : Settings
        Application settings (used to read initial_capital).
    db : SupabaseClient
        Database client for persisting trade records.
    initial_capital : float
        Starting paper capital in USD.  Defaults to 10,000.
    market : str
        Default market segment for this executor.
    """

    def __init__(
        self,
        settings: Optional[Any] = None,
        db: Optional[Any] = None,
        initial_capital: float = 10_000.0,
        market: str = "crypto",
    ) -> None:
        # Allow settings to override initial capital
        if settings is not None:
            try:
                initial_capital = float(
                    getattr(settings, "initial_capital", initial_capital)
                )
            except (TypeError, ValueError):
                pass

        super().__init__(initial_capital=initial_capital, market=market)
        self._settings = settings
        self._db = db

        logger.info(
            "PaperExecutor created: capital=$%.2f market=%s fee=%.3f%% slippage=%.3f%%",
            initial_capital, market,
            BINANCE_TAKER_FEE * 100,
            DEFAULT_SLIPPAGE_PCT * 100,
        )

    async def init(self) -> None:
        """Lifecycle init — logs readiness."""
        logger.info(
            "PaperExecutor initialised: capital=$%.2f | fee=%.3f%% | slippage=%.3f%%",
            self._portfolio.capital,
            BINANCE_TAKER_FEE * 100,
            DEFAULT_SLIPPAGE_PCT * 100,
        )

    # ------------------------------------------------------------------
    # DB persistence (overrides PaperTrader no-op)
    # ------------------------------------------------------------------

    async def _persist_trade(
        self,
        order: Order,
        fill_price: float,
        filled_qty: float,
        fee_usd: float,
    ) -> None:
        """Persist paper fill to Supabase `trades` table (matching exact schema)."""
        if self._db is None:
            logger.debug("PaperExecutor: no DB client — skipping trade persistence")
            return

        slippage_pct = getattr(order, "slippage_pct", DEFAULT_SLIPPAGE_PCT)
        notional = filled_qty * fill_price

        # Normalise direction to the DB enum: 'LONG' or 'SHORT'
        raw_dir = (order.direction or "buy").lower()
        direction_enum = "LONG" if raw_dir in ("buy", "long") else "SHORT"

        # Normalise status to the DB enum values (trades table uses OPEN/CLOSED/CANCELLED/PARTIAL)
        raw_status = order.status.value if hasattr(order.status, "value") else str(order.status)
        status_map = {
            "FILLED": "CLOSED",
            "PARTIALLY_FILLED": "PARTIAL",
            "PENDING": "OPEN",
            "CANCELLED": "CANCELLED",
        }
        status_enum = status_map.get(raw_status, "CLOSED")

        now_iso = datetime.now(tz=timezone.utc).isoformat()

        # use client_order_id as the natural key for upsert deduplication.
        # Field names match the actual Supabase trades table schema:
        #   direction / side  -> DB has both 'side' (original) and 'direction' (added column)
        #   commission        -> DB column for fees
        #   slippage          -> DB column (decimal fraction)
        #   meta              -> DB column for JSONB metadata
        #   opened_at         -> DB column for entry timestamp
        record = {
            # --- identity ---
            "client_order_id":   order.client_order_id,
            "exchange_order_id": order.order_id,
            "execution_mode":    "paper",
            # --- trade basics ---
            "market":            order.market,
            "symbol":            order.symbol,
            "side":              direction_enum,       # original DB column (LONG/SHORT)
            "direction":         direction_enum,       # added column (same value)
            "status":            status_enum,          # OPEN / CLOSED / CANCELLED / PARTIAL
            # --- pricing ---
            "entry_price":       round(fill_price, 8),
            "opened_at":         now_iso,              # DB column name for entry timestamp
            "entry_time":        now_iso,              # added column (redundant but kept for queries)
            # --- sizing ---
            "quantity":          round(filled_qty, 8),
            "quantity_filled":   round(filled_qty, 8),
            "position_value":    round(notional, 2),
            # --- risk levels ---
            "stop_loss":         order.stop_loss,
            "take_profit_1":     order.take_profit,
            # --- costs ---
            "commission":        round(fee_usd, 8),    # DB column name for fees
            "fees_paid":         round(fee_usd, 8),    # added column (same value)
            "slippage":          round(slippage_pct, 6),     # DB column (fraction)
            "slippage_pct":      round(slippage_pct, 6),     # added column (same value)
            # --- metadata ---
            "meta":              {                      # original DB JSONB column
                "notional_usd":      round(notional, 2),
                "net_cost_usd":      round(notional + fee_usd, 2),
                "portfolio_capital": round(self._portfolio.capital, 2),
                "portfolio_equity":  round(self._portfolio.total_equity, 2),
                "daily_pnl":         round(self._portfolio.daily_pnl, 2),
                "fee_rate_pct":      round(BINANCE_TAKER_FEE * 100, 3),
                "slippage_pct":      round(slippage_pct * 100, 4),
            },
            "trade_metadata":    {                      # added column (same payload)
                "notional_usd":      round(notional, 2),
                "net_cost_usd":      round(notional + fee_usd, 2),
                "portfolio_capital": round(self._portfolio.capital, 2),
                "portfolio_equity":  round(self._portfolio.total_equity, 2),
                "daily_pnl":         round(self._portfolio.daily_pnl, 2),
                "fee_rate_pct":      round(BINANCE_TAKER_FEE * 100, 3),
                "slippage_pct":      round(slippage_pct * 100, 4),
            },
        }

        try:
            await self._db.client.table("trades").upsert(
                record,
                on_conflict="client_order_id",
            ).execute()
            logger.info(
                "✅ PAPER TRADE recorded: %s %s %.4f @ $%.2f | "
                "fee=$%.2f (%.3f%%) | slippage=%.3f%% | net_cost=$%.2f",
                direction_enum, order.symbol, filled_qty, fill_price,
                fee_usd, BINANCE_TAKER_FEE * 100,
                slippage_pct * 100, notional + fee_usd,
            )
        except Exception as exc:
            logger.error(
                "PaperExecutor._persist_trade failed: %s | %s %s",
                exc, order.symbol, order.direction,
            )


__all__ = ["PaperExecutor"]
