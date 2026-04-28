"""
NEXUS ALPHA - Paper Trader
============================
Full paper trading simulation engine.

Simulates:
  - Slippage per market (square-root market impact model)
  - Exchange fees per market
  - Fill latency (50–200 ms random)
  - Partial fills on limit orders (80% full fill, 20% partial)
  - Portfolio state (capital, positions, trade history, daily P&L, drawdown)
  - Supabase persistence on every trade
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from src.execution.base_executor import BaseExecutor, Order, OrderStatus, OrderType, Position
from src.execution.slippage_model import SlippageModel

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paper trading constants
# ---------------------------------------------------------------------------

FILL_LATENCY_MIN_MS = 50
FILL_LATENCY_MAX_MS = 200
FULL_FILL_PROBABILITY = 0.80     # 80% chance of full fill on LIMIT orders
PARTIAL_FILL_MIN_PCT  = 0.30     # Partial fill is 30–80% of order size


@dataclass
class TradeRecord:
    """Record of a completed paper trade."""
    trade_id: str
    symbol: str
    direction: str
    size: float
    entry_price: float
    exit_price: float
    pnl: float
    fee: float
    slippage_pct: float
    market: str
    open_time: float
    close_time: float
    reason: str


@dataclass
class PaperPortfolio:
    """Paper trading portfolio state."""
    capital: float                               # Current available cash
    initial_capital: float
    positions: Dict[str, Position] = field(default_factory=dict)
    trade_history: List[TradeRecord] = field(default_factory=list)
    open_orders: List[Order] = field(default_factory=list)
    daily_pnl: float = 0.0
    peak_equity: float = 0.0
    daily_reset_ts: float = field(default_factory=time.time)

    @property
    def unrealized_pnl(self) -> float:
        return sum(p.unrealized_pnl for p in self.positions.values())

    @property
    def total_equity(self) -> float:
        return self.capital + self.unrealized_pnl

    @property
    def total_notional(self) -> float:
        return sum(p.notional_usd for p in self.positions.values())

    # ------------------------------------------------------------------
    # Convenience aliases used by main.py metric updater
    # ------------------------------------------------------------------

    @property
    def equity(self) -> float:
        """Alias for total_equity — current NAV in USD."""
        return self.total_equity

    @property
    def portfolio_value(self) -> float:
        """Alias for total_equity."""
        return self.total_equity

    @property
    def open_positions(self) -> int:
        """Number of currently open positions."""
        return len(self.positions)

    @property
    def drawdown_pct(self) -> float:
        """
        Current drawdown from peak equity, as a negative percentage.
        Returns 0.0 when there is no drawdown or peak is not yet established.
        """
        peak = self.peak_equity or self.initial_capital
        if peak <= 0:
            return 0.0
        equity = self.total_equity
        if equity >= peak:
            return 0.0
        return round((equity - peak) / peak * 100.0, 4)


class PaperTrader(BaseExecutor):
    """
    Full-featured paper trading simulator.

    Maintains portfolio state in memory and persists to Supabase on
    every trade.  Designed as a drop-in replacement for live executors.

    Parameters
    ----------
    initial_capital : float
        Starting capital in USD.
    market : str
        Default market segment for this paper trader instance.
    """

    def __init__(
        self,
        initial_capital: float = 100_000.0,
        market: str = "crypto",
    ) -> None:
        self._market = market
        self._slippage = SlippageModel()
        self._portfolio = PaperPortfolio(
            capital=initial_capital,
            initial_capital=initial_capital,
            peak_equity=initial_capital,
        )

    @property
    def exchange_name(self) -> str:
        return "paper"

    # ------------------------------------------------------------------
    # Portfolio access helpers
    # ------------------------------------------------------------------

    def get_portfolio_state(self) -> PaperPortfolio:
        """Return the current paper portfolio state."""
        return self._portfolio

    def get_capital(self) -> float:
        return self._portfolio.capital

    def get_daily_pnl(self) -> float:
        return self._portfolio.daily_pnl

    def _check_daily_reset(self) -> None:
        """Reset daily P&L at midnight UTC."""
        now = time.time()
        today_start = now - (now % 86400)
        if self._portfolio.daily_reset_ts < today_start:
            self._portfolio.daily_pnl = 0.0
            self._portfolio.daily_reset_ts = today_start

    def _update_peak_equity(self) -> None:
        equity = self._portfolio.total_equity
        if equity > self._portfolio.peak_equity:
            self._portfolio.peak_equity = equity

    # ------------------------------------------------------------------
    # Fill simulation
    # ------------------------------------------------------------------

    async def _simulate_fill(
        self,
        order: Order,
        market_price: float,
        daily_volume_usd: float = 50_000_000.0,
    ) -> tuple[float, float, float]:
        """
        Simulate order fill: latency, slippage, partial fills.

        Returns
        -------
        tuple[fill_price, filled_qty, fee_usd]
        """
        # Simulate fill latency
        latency_ms = random.uniform(FILL_LATENCY_MIN_MS, FILL_LATENCY_MAX_MS)
        await asyncio.sleep(latency_ms / 1000.0)

        # Determine filled quantity
        if order.order_type_hint == "LIMIT":
            # 80% full fill, 20% partial
            if random.random() < FULL_FILL_PROBABILITY:
                filled_qty = order.size
            else:
                partial_pct = random.uniform(PARTIAL_FILL_MIN_PCT, 0.80)
                filled_qty = order.size * partial_pct
        else:
            filled_qty = order.size  # MARKET: always full

        # Apply slippage
        size_usd = filled_qty * market_price
        fill_price = self._slippage.apply_slippage(
            price=market_price,
            market=order.market,
            size_usd=size_usd,
            daily_volume_usd=daily_volume_usd,
            direction=order.direction,
        )

        # Calculate fee
        notional = filled_qty * fill_price
        fee_usd = self._slippage.estimate_fee(order.market, notional)

        return fill_price, filled_qty, fee_usd

    # ------------------------------------------------------------------
    # BaseExecutor implementation
    # ------------------------------------------------------------------

    async def place_order(
        self,
        symbol: str,
        direction: str,
        size: float,
        order_type: OrderType,
        price: float = 0.0,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Order:
        """
        Paper-fill an order with slippage, latency, and fee simulation.

        The order is created, then immediately filled against the provided
        price (which should be the current market mid-price from the signal).
        """
        self._check_daily_reset()

        if self._portfolio.capital <= 0:
            raise RuntimeError("PaperTrader: insufficient capital")

        order_id = f"paper_{uuid.uuid4().hex[:12]}"
        client_id = f"nexus_{uuid.uuid4().hex[:12]}"

        # Create the order object (market comes from the instance, not from
        # an order that doesn't exist yet — fixed UnboundLocalError)
        order = Order(
            order_id=order_id,
            symbol=symbol,
            direction=direction,
            size=size,
            price=price,
            status=OrderStatus.PENDING,
            timestamp=time.time(),
            market=self._market,
            exchange=self.exchange_name,
            stop_loss=stop_loss,
            take_profit=take_profit,
            client_order_id=client_id,
        )
        # Attach a hint for fill simulation (non-frozen dataclass workaround)
        order.order_type_hint = order_type.value  # type: ignore[attr-defined]

        # Use provided price as market price; for MARKET orders this is the mid
        market_price = price if price > 0 else self._get_last_known_price(symbol)
        if market_price <= 0:
            raise ValueError(f"PaperTrader: no price available for {symbol}")

        # Check capital
        required_capital = size * market_price
        if required_capital > self._portfolio.capital * 1.05:  # small buffer for limit orders
            raise RuntimeError(
                f"PaperTrader: insufficient capital ${self._portfolio.capital:.2f} "
                f"for order requiring ~${required_capital:.2f}"
            )

        # Simulate fill
        fill_price, filled_qty, fee_usd = await self._simulate_fill(order, market_price)

        # Update order — attach fee so ExecutionEngine can read it back
        order.filled_qty = filled_qty
        order.avg_fill_price = fill_price
        order.fee_usd = fee_usd          # type: ignore[attr-defined]  (dynamic attr)
        slippage_pct = abs(fill_price - market_price) / max(market_price, 1e-9)
        order.slippage_pct = slippage_pct  # type: ignore[attr-defined]
        order.status = OrderStatus.FILLED if filled_qty >= size * 0.99 else OrderStatus.PARTIALLY_FILLED

        # Update portfolio
        notional = filled_qty * fill_price
        if direction.lower() in ("buy", "long"):
            self._portfolio.capital -= (notional + fee_usd)
            self._open_position(symbol, direction, filled_qty, fill_price, stop_loss,
                                take_profit, self._market)
        else:
            # Closing or shorting
            pnl = self._close_position(symbol, filled_qty, fill_price)
            self._portfolio.capital += (notional - fee_usd)
            self._portfolio.daily_pnl += pnl - fee_usd

        self._update_peak_equity()
        await self._persist_trade(order, fill_price, filled_qty, fee_usd)

        logger.info(
            "PaperTrader.place_order: %s %s qty=%.4f fill=%.4f fee=$%.4f → %s",
            symbol, direction, filled_qty, fill_price, fee_usd, order.status.value,
        )
        return order

    def _get_last_known_price(self, symbol: str) -> float:
        """Return the last known price from an open position, or 0."""
        pos = self._portfolio.positions.get(symbol)
        return pos.current_price if pos else 0.0

    def _open_position(
        self,
        symbol: str,
        direction: str,
        size: float,
        fill_price: float,
        stop_loss: Optional[float],
        take_profit: Optional[float],
        market: str,
    ) -> None:
        """Open or add to a paper position."""
        existing = self._portfolio.positions.get(symbol)
        if existing and existing.direction == direction:
            # Add to existing: recalculate weighted average entry
            total_size = existing.size + size
            avg_entry = (
                (existing.entry_price * existing.size + fill_price * size) / total_size
            )
            existing.size = total_size
            existing.entry_price = avg_entry
            existing.current_price = fill_price
        else:
            tp_levels = [take_profit] if take_profit else []
            self._portfolio.positions[symbol] = Position(
                symbol=symbol,
                direction=direction,
                size=size,
                entry_price=fill_price,
                current_price=fill_price,
                unrealized_pnl=0.0,
                stop_loss=stop_loss,
                take_profit_levels=tp_levels,
                market=market,
                exchange=self.exchange_name,
                open_time=time.time(),
            )

    def _close_position(self, symbol: str, size: float, fill_price: float) -> float:
        """Close or reduce a position and return realised P&L."""
        pos = self._portfolio.positions.get(symbol)
        if pos is None:
            return 0.0

        if pos.direction.lower() in ("long", "buy"):
            pnl = (fill_price - pos.entry_price) * size
        else:
            pnl = (pos.entry_price - fill_price) * size

        pos.size -= size
        if pos.size <= 1e-8:
            del self._portfolio.positions[symbol]
        else:
            pos.current_price = fill_price
            pos.unrealized_pnl = (
                (fill_price - pos.entry_price) * pos.size
                if pos.direction.lower() in ("long", "buy")
                else (pos.entry_price - fill_price) * pos.size
            )

        return pnl

    def update_mark_to_market(self, symbol: str, current_price: float) -> None:
        """Update unrealized P&L for an open position."""
        pos = self._portfolio.positions.get(symbol)
        if pos is None:
            return
        pos.current_price = current_price
        if pos.direction.lower() in ("long", "buy"):
            pos.unrealized_pnl = (current_price - pos.entry_price) * pos.size
        else:
            pos.unrealized_pnl = (pos.entry_price - current_price) * pos.size

    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open paper order."""
        self._portfolio.open_orders = [
            o for o in self._portfolio.open_orders if o.order_id != order_id
        ]
        return True

    async def close_position(
        self,
        symbol: str,
        size: float,
        reason: str = "manual",
    ) -> Order:
        pos = self._portfolio.positions.get(symbol)
        if pos is None:
            raise ValueError(f"PaperTrader: no position for {symbol}")

        close_dir = "sell" if pos.direction.lower() in ("long", "buy") else "buy"
        return await self.place_order(
            symbol=symbol,
            direction=close_dir,
            size=size,
            order_type=OrderType.MARKET,
            price=pos.current_price,
        )

    async def cancel_all_orders(self) -> List[Order]:
        cancelled = list(self._portfolio.open_orders)
        self._portfolio.open_orders.clear()
        return cancelled

    async def close_all_positions(self) -> List[Order]:
        symbols = list(self._portfolio.positions.keys())
        orders: List[Order] = []
        for symbol in symbols:
            pos = self._portfolio.positions.get(symbol)
            if pos:
                try:
                    o = await self.close_position(pos.symbol, pos.size, reason="emergency_close")
                    orders.append(o)
                except Exception as exc:  # noqa: BLE001
                    logger.error("PaperTrader.close_all_positions: %s failed: %s", symbol, exc)
        return orders

    async def get_positions(self) -> List[Position]:
        return list(self._portfolio.positions.values())

    async def get_open_orders(self) -> List[Order]:
        return list(self._portfolio.open_orders)

    async def get_account_balance(self) -> float:
        return self._portfolio.capital

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    async def _persist_trade(
        self,
        order: Order,
        fill_price: float,
        filled_qty: float,
        fee_usd: float,
    ) -> None:
        """
        Save trade snapshot to Supabase.

        Base implementation is a no-op — override in PaperExecutor (which has
        db access) to actually persist.  This keeps PaperTrader free of DB deps.
        """
        logger.debug(
            "PaperTrader._persist_trade: %s %s fill=%.4f qty=%.4f fee=%.4f (no DB client)",
            order.symbol, order.direction, fill_price, filled_qty, fee_usd,
        )
