"""
NEXUS ALPHA - Paper Executor
================================
Wraps PaperTrader with DB persistence, lifecycle management, and
settings integration. Overrides _persist_trade to write every paper
fill (with fee and slippage) to Supabase's trades table.

Trade lifecycle:
  - OPEN (BUY): New row inserted with status='OPEN'.
                client_order_id tracked in _open_trade_client_ids.
  - CLOSE (SELL): Existing OPEN row updated to status='CLOSED' with
                  exit_price, pnl, pnl_pct, closed_at.
                  No separate exit row is created.

This means closed trades in Supabase always have both entry and exit
info in a single row, enabling proper performance metric computation.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from src.execution.base_executor import Order
from src.execution.paper_trader import PaperTrader, TradeRecord

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
      3. On BUY: inserts a new 'OPEN' trade row in Supabase
      4. On SELL: updates the matching 'OPEN' row to 'CLOSED'
                  with exit_price and realised P&L

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

        # Map symbol → client_order_id of the open DB row for that symbol.
        # Used to UPDATE the row (not INSERT a new one) when a position closes.
        self._open_trade_client_ids: Dict[str, str] = {}

        # Map symbol → realized_pnl captured by _close_position() override.
        # Cleared after each use in _persist_trade().
        self._last_realized_pnl: Dict[str, float] = {}

        logger.info(
            "PaperExecutor created: capital=$%.2f market=%s fee=%.3f%% slippage=%.3f%%",
            initial_capital, market,
            BINANCE_TAKER_FEE * 100,
            DEFAULT_SLIPPAGE_PCT * 100,
        )

    async def init(self) -> None:
        """Lifecycle init — restore portfolio state from Supabase, then log readiness."""
        await self._restore_state_from_db()
        logger.info(
            "PaperExecutor initialised: capital=$%.2f | fee=%.3f%% | slippage=%.3f%%",
            self._portfolio.capital,
            BINANCE_TAKER_FEE * 100,
            DEFAULT_SLIPPAGE_PCT * 100,
        )

    async def _restore_state_from_db(self) -> None:
        """Restore portfolio capital and open positions from Supabase on startup.

        This ensures the paper trading portfolio is consistent across bot restarts:
        1. Load the latest portfolio_snapshot to restore equity / capital.
        2. Load all OPEN paper trades to rebuild the in-memory positions dict.
        """
        if self._db is None:
            logger.debug("PaperExecutor: no DB client — starting with fresh capital")
            return

        # ── Step 1: restore capital from last portfolio snapshot ──────────────
        try:
            res = await self._db.client.table("portfolio_snapshots") \
                .select("equity, cash, open_positions") \
                .order("created_at", desc=True) \
                .limit(1) \
                .execute()

            if res.data:
                snap = res.data[0]
                restored_cash = float(snap.get("cash") or self._portfolio.capital)
                restored_equity = float(snap.get("equity") or restored_cash)

                self._portfolio.capital = restored_cash
                self._portfolio.peak_equity = max(
                    self._portfolio.peak_equity, restored_equity
                )
                logger.info(
                    "PaperExecutor: restored capital=$%.2f from last portfolio snapshot",
                    restored_cash,
                )
        except Exception as exc:
            logger.warning("PaperExecutor: could not restore snapshot: %s", exc)

        # ── Step 2: restore open positions from OPEN paper trades ─────────────
        # Aggregate by symbol: multiple OPEN rows for the same symbol are merged
        # using a quantity-weighted average entry price (matches in-memory behaviour).
        try:
            from src.execution.base_executor import Position

            res = await self._db.client.table("trades") \
                .select("symbol, direction, entry_price, quantity, client_order_id, stop_loss, take_profit_1") \
                .eq("status", "OPEN") \
                .eq("execution_mode", "paper") \
                .execute()

            if res.data:
                # Group rows by symbol for aggregation
                agg: Dict[str, Dict] = {}
                for row in res.data:
                    symbol = row.get("symbol")
                    qty    = float(row.get("quantity") or 0)
                    entry  = float(row.get("entry_price") or 0)
                    coid   = row.get("client_order_id") or ""

                    if not symbol or qty <= 0 or entry <= 0:
                        continue

                    if symbol not in agg:
                        agg[symbol] = {
                            "total_qty": 0.0,
                            "weighted_entry": 0.0,
                            "stop_loss": row.get("stop_loss"),
                            "take_profit_1": row.get("take_profit_1"),
                            # Track the LATEST client_order_id for the CLOSE path
                            "latest_coid": coid,
                        }
                    s = agg[symbol]
                    new_total = s["total_qty"] + qty
                    s["weighted_entry"] = (
                        s["weighted_entry"] * s["total_qty"] + entry * qty
                    ) / new_total if new_total > 0 else entry
                    s["total_qty"] = new_total
                    s["latest_coid"] = coid   # last row per symbol wins for close-tracking

                for symbol, s in agg.items():
                    sl_raw = s["stop_loss"]
                    tp_raw = s["take_profit_1"]
                    pos = Position(
                        symbol=symbol,
                        direction="LONG",
                        size=s["total_qty"],
                        entry_price=s["weighted_entry"],
                        current_price=s["weighted_entry"],
                        unrealized_pnl=0.0,
                        stop_loss=float(sl_raw) if sl_raw else None,
                        take_profit_levels=[float(tp_raw)] if tp_raw else [],
                        market=self._market,
                        exchange="paper",
                        open_time=time.time(),
                    )
                    self._portfolio.positions[symbol] = pos

                    # Re-register client_order_id so the CLOSE path can UPDATE the row
                    if s["latest_coid"]:
                        self._open_trade_client_ids[symbol] = s["latest_coid"]

                    logger.info(
                        "PaperExecutor: restored position %s — qty=%.4f avg_entry=%.4f",
                        symbol, s["total_qty"], s["weighted_entry"],
                    )

                # Deduct position notional from capital to avoid double-counting
                recovered_notional = sum(
                    p.size * p.entry_price
                    for p in self._portfolio.positions.values()
                )
                # Only deduct if capital hasn't already accounted for open positions
                # (snapshot.cash should be free cash, so no double-deduction needed)
                logger.info(
                    "PaperExecutor: %d open positions restored (notional=$%.2f)",
                    len(self._portfolio.positions), recovered_notional,
                )
        except Exception as exc:
            logger.warning("PaperExecutor: could not restore open positions: %s", exc)

    # ------------------------------------------------------------------
    # Portfolio state accessor (used by NexusBot._metrics_loop)
    # ------------------------------------------------------------------

    def get_portfolio_state(self) -> Any:
        """Return a SimpleNamespace snapshot of portfolio metrics.

        The NexusBot._metrics_loop calls
        ``execution_engine.get_portfolio_state()`` which delegates here.
        Returning a non-None object allows the loop to:
          • update Prometheus gauges (equity, daily_pnl, drawdown, etc.)
          • fire the periodic 5-minute Supabase portfolio_snapshots write

        Field names match what _metrics_loop reads via ``getattr``.
        """
        from types import SimpleNamespace

        p = self._portfolio
        initial = p.initial_capital or 1.0
        peak = p.peak_equity or initial
        equity = p.total_equity
        drawdown_pct = (
            round(((peak - equity) / peak) * 100 * -1, 4) if peak > 0 else 0.0
        )

        return SimpleNamespace(
            equity=equity,
            daily_pnl=getattr(p, "daily_pnl", 0.0),
            open_positions=len(p.positions),
            drawdown_pct=drawdown_pct,
            trade_history=getattr(p, "trade_history", []),
            positions=p.positions,
        )

    # ------------------------------------------------------------------
    # Override _close_position to capture realized P&L for DB persistence
    # and to populate trade_history for win-rate calculation
    # ------------------------------------------------------------------

    def _close_position(self, symbol: str, size: float, fill_price: float) -> float:
        """
        Override to:
          1. Capture realised P&L for later DB update.
          2. Append a TradeRecord to portfolio.trade_history so that
             win_rate is correctly computed in _persist_portfolio_snapshot.
        """
        entry_price = 0.0
        open_time = time.time()
        pos = self._portfolio.positions.get(symbol)
        if pos is not None:
            entry_price = pos.entry_price
            open_time = getattr(pos, "open_time", time.time())

        pnl = super()._close_position(symbol, size, fill_price)

        # Store for _persist_trade() to use
        self._last_realized_pnl[symbol] = pnl

        # Append to trade_history so _persist_portfolio_snapshot has win_rate data
        self._portfolio.trade_history.append(
            TradeRecord(
                trade_id=f"paper_{uuid.uuid4().hex[:8]}",
                symbol=symbol,
                direction="LONG",
                size=size,
                entry_price=entry_price,
                exit_price=fill_price,
                pnl=pnl,
                fee=0.0,   # fee handled separately in _persist_trade
                slippage_pct=0.0,
                market=self._market,
                open_time=open_time,
                close_time=time.time(),
                reason="close",
            )
        )
        return pnl

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
        """
        Persist paper fill to Supabase `trades` table.

        BUY  → INSERT new row with status='OPEN'.
        SELL → UPDATE matching 'OPEN' row to status='CLOSED' with P&L.
        """
        if self._db is None:
            logger.debug("PaperExecutor: no DB client — skipping trade persistence")
            return

        slippage_pct = getattr(order, "slippage_pct", DEFAULT_SLIPPAGE_PCT)
        notional = filled_qty * fill_price

        # Normalise direction to the DB enum: 'LONG' or 'SHORT'
        raw_dir = (order.direction or "buy").lower()
        direction_enum = "LONG" if raw_dir in ("buy", "long") else "SHORT"
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        # ── CLOSE PATH ────────────────────────────────────────────────
        # If this is a SELL and we have a tracked open trade for this symbol,
        # update that existing row instead of creating a new one.
        if direction_enum == "SHORT":
            original_client_id = self._open_trade_client_ids.pop(order.symbol, None)
            if original_client_id:
                realized_pnl = self._last_realized_pnl.pop(order.symbol, 0.0)
                net_pnl = round(realized_pnl - fee_usd, 4)

                # Fetch entry data to compute pnl_pct
                pnl_pct = 0.0
                try:
                    res = await self._db.client.table("trades") \
                        .select("entry_price, quantity") \
                        .eq("client_order_id", original_client_id) \
                        .limit(1) \
                        .execute()
                    if res.data:
                        ep = float(res.data[0].get("entry_price") or fill_price)
                        qty = float(res.data[0].get("quantity") or filled_qty)
                        notional_entry = ep * qty
                        pnl_pct = round((net_pnl / notional_entry * 100), 4) if notional_entry > 0 else 0.0
                except Exception as exc:
                    logger.debug("PaperExecutor: could not fetch entry for pnl_pct: %s", exc)

                update_payload = {
                    "status":      "CLOSED",
                    "exit_price":  round(fill_price, 8),
                    "closed_at":   now_iso,
                    "pnl":         net_pnl,
                    "pnl_pct":     pnl_pct,
                    "commission":  round(fee_usd, 8),
                    "fees_paid":   round(fee_usd, 8),
                }
                try:
                    await self._db.client.table("trades") \
                        .update(update_payload) \
                        .eq("client_order_id", original_client_id) \
                        .execute()
                    logger.info(
                        "🔒 PAPER TRADE CLOSED: %s %s pnl=$%.2f (%.2f%%) | exit=$%.4f | fee=$%.4f",
                        order.symbol, direction_enum, net_pnl, pnl_pct, fill_price, fee_usd,
                    )
                except Exception as exc:
                    logger.error("PaperExecutor._close_trade_in_db failed: %s", exc)

                await self._persist_portfolio_snapshot()
                return

            # No tracked open trade — record SELL as a standalone row (e.g. short open)
            logger.debug(
                "PaperExecutor: no open trade tracked for %s; recording SELL as OPEN row",
                order.symbol,
            )

        # ── OPEN PATH (BUY) or untracked SELL ─────────────────────────
        raw_status = order.status.value if hasattr(order.status, "value") else str(order.status)
        status_map = {
            "FILLED": "OPEN",
            "PARTIALLY_FILLED": "PARTIAL",
            "PENDING": "OPEN",
            "CANCELLED": "CANCELLED",
        }
        status_enum = status_map.get(raw_status, "OPEN")

        # Field names match the actual Supabase trades table schema.
        record = {
            # --- identity ---
            "client_order_id":   order.client_order_id,
            "exchange_order_id": order.order_id,
            "execution_mode":    "paper",
            # --- trade basics ---
            "market":            order.market,
            "symbol":            order.symbol,
            "side":              direction_enum,
            "direction":         direction_enum,
            "status":            status_enum,
            # --- pricing ---
            "entry_price":       round(fill_price, 8),
            "opened_at":         now_iso,
            "entry_time":        now_iso,
            # --- sizing ---
            "quantity":          round(filled_qty, 8),
            "quantity_filled":   round(filled_qty, 8),
            "position_value":    round(notional, 2),
            # --- risk levels ---
            "stop_loss":         order.stop_loss,
            "take_profit_1":     order.take_profit,
            # --- costs ---
            "commission":        round(fee_usd, 8),
            "fees_paid":         round(fee_usd, 8),
            "slippage":          round(slippage_pct, 6),
            "slippage_pct":      round(slippage_pct, 6),
            # --- metadata ---
            "meta": {
                "notional_usd":      round(notional, 2),
                "net_cost_usd":      round(notional + fee_usd, 2),
                "portfolio_capital": round(self._portfolio.capital, 2),
                "portfolio_equity":  round(self._portfolio.total_equity, 2),
                "daily_pnl":         round(self._portfolio.daily_pnl, 2),
                "fee_rate_pct":      round(BINANCE_TAKER_FEE * 100, 3),
                "slippage_pct":      round(slippage_pct * 100, 4),
            },
            "trade_metadata": {
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
                "✅ PAPER TRADE OPENED: %s %s %.4f @ $%.2f | fee=$%.4f | slippage=%.3f%%",
                direction_enum, order.symbol, filled_qty, fill_price,
                fee_usd, slippage_pct * 100,
            )
            # Track this BUY trade so we can UPDATE it on close
            if direction_enum == "LONG":
                self._open_trade_client_ids[order.symbol] = order.client_order_id
        except Exception as exc:
            logger.error(
                "PaperExecutor._persist_trade failed: %s | %s %s",
                exc, order.symbol, order.direction,
            )
            return  # Don't attempt snapshot if trade write failed

        await self._persist_portfolio_snapshot()

    async def _persist_portfolio_snapshot(self) -> None:
        """
        Write current portfolio state to Supabase `portfolio_snapshots` table.
        Called after every paper trade fill so the dashboard shows live equity.

        Matches actual DB columns:
          equity, cash, positions_value, daily_pnl, daily_pnl_pct,
          total_pnl, drawdown_pct, open_positions, win_rate, portfolio_heat
        """
        if self._db is None:
            return
        try:
            equity = round(self._portfolio.total_equity, 2)
            cash   = round(self._portfolio.capital, 2)
            initial = self._portfolio.initial_capital or 1.0
            peak   = self._portfolio.peak_equity or initial
            daily  = round(self._portfolio.daily_pnl, 2)
            total_pnl = round(equity - initial, 2)
            n_positions = len(self._portfolio.positions)

            # Drawdown = (peak - current) / peak  (negative fraction → percentage)
            drawdown_pct = round(((peak - equity) / peak) * 100 * -1, 4) if peak > 0 else 0.0

            # Portfolio heat = fraction of initial capital currently at risk
            total_notional = round(self._portfolio.total_notional, 2)
            portfolio_heat = round(total_notional / initial, 4) if initial > 0 else 0.0

            # Win rate from trade history (populated by _close_position override)
            history = getattr(self._portfolio, "trade_history", [])
            wins = sum(1 for t in history if getattr(t, "pnl", 0) > 0)
            win_rate = round(wins / len(history), 4) if history else 0.0

            snapshot = {
                "equity":          equity,
                "cash":            cash,
                "positions_value": round(total_notional, 2),
                "daily_pnl":       daily,
                "daily_pnl_pct":   round(daily / initial * 100, 4),
                "total_pnl":       total_pnl,
                "drawdown_pct":    drawdown_pct,
                "open_positions":  n_positions,
                "win_rate":        win_rate,
                "portfolio_heat":  portfolio_heat,
            }

            await self._db.client.table("portfolio_snapshots").insert(snapshot).execute()
            logger.info(
                "📊 Portfolio snapshot: equity=$%.2f cash=$%.2f positions=%d "
                "daily_pnl=$%.2f drawdown=%.2f%% win_rate=%.1f%%",
                equity, cash, n_positions, daily, abs(drawdown_pct), win_rate * 100,
            )
        except Exception as exc:
            logger.error("PaperExecutor._persist_portfolio_snapshot failed: %s", exc)


__all__ = ["PaperExecutor"]
