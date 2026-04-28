"""
NEXUS ALPHA - Signal Fusion re-export
=======================================
Re-exports SignalFusionEngine from src.signals.fusion_engine so that
main.py can import via src.strategies.signal_fusion.
"""

from src.signals.fusion_engine import SignalFusionEngine

__all__ = ["SignalFusionEngine"]
