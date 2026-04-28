"""
NEXUS ALPHA - Edge Filter re-export
=======================================
Re-exports EdgeFilter from src.signals.edge_filter so that
main.py can import via src.strategies.edge_filter.
"""

from src.signals.edge_filter import EdgeFilter

__all__ = ["EdgeFilter"]
