"""
NEXUS ALPHA - Execution Engine Package
========================================
Smart order routing, multi-exchange executors, paper trading simulation,
slippage modeling, and emergency shutdown procedures.
"""

from src.execution.base_executor import BaseExecutor, Order, Position, OrderStatus, OrderType, Direction
from src.execution.paper_trader import PaperTrader
from src.execution.smart_router import SmartOrderRouter
from src.execution.slippage_model import SlippageModel
from src.execution.emergency_shutdown import emergency_shutdown, SHUTDOWN_FLAG

__all__ = [
    "BaseExecutor",
    "Order",
    "Position",
    "OrderStatus",
    "OrderType",
    "Direction",
    "PaperTrader",
    "SmartOrderRouter",
    "SlippageModel",
    "emergency_shutdown",
    "SHUTDOWN_FLAG",
]
