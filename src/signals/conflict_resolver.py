# src/signals/conflict_resolver.py
"""Conflict resolver for multi-timeframe and cross-signal disagreements."""

from __future__ import annotations

import logging
from typing import List, Optional

from .signal_types import FusedSignal, SignalDirection

logger = logging.getLogger(__name__)

# Timeframe ordering from highest to lowest priority
TF_PRIORITY = {
    "1W": 7, "W": 7, "weekly": 7,
    "1D": 6, "D": 6, "daily": 6,
    "4H": 5, "4h": 5,
    "1H": 4, "1h": 4,
    "30M": 3, "30m": 3,
    "15M": 2, "15m": 2,
    "5M": 1, "5m": 1,
    "1M": 0, "1m": 0,
}

# When LLM and technical strongly disagree, reduce confidence by this fraction
CONFIDENCE_PENALTY_STRONG_DISAGREE = 0.30


class ConflictResolver:
    """Resolves conflicts between signals from different timeframes and sources."""

    def resolve(self, signals: List[FusedSignal]) -> Optional[FusedSignal]:
        """Resolve a list of potentially conflicting signals into one.

        Rules:
        1. If only one signal, return it directly.
        2. If multiple timeframes agree, return the highest-confidence one.
        3. If timeframes disagree, use the higher timeframe's bias.
        4. If LLM and technical strongly disagree in the chosen signal,
           reduce confidence by 30% and check if it still passes threshold.

        Parameters
        ----------
        signals:
            List of :class:`FusedSignal` instances (may be from different TFs).

        Returns
        -------
        FusedSignal or None
            The resolved signal, or None if no actionable resolution.
        """
        if not signals:
            return None

        if len(signals) == 1:
            signal = signals[0]
            return self._apply_internal_conflict_check(signal)

        # Sort by timeframe priority (highest first)
        sorted_signals = sorted(
            signals,
            key=lambda s: TF_PRIORITY.get(s.timeframe, 3),
            reverse=True,
        )

        # Check if all directional signals agree
        directional = [s for s in sorted_signals if s.direction != SignalDirection.NEUTRAL]
        if not directional:
            logger.debug("ConflictResolver: all signals neutral — returning None")
            return None

        up_signals = [s for s in directional if s.direction == SignalDirection.LONG]
        down_signals = [s for s in directional if s.direction == SignalDirection.SHORT]

        if len(up_signals) > 0 and len(down_signals) == 0:
            # All agree: LONG
            best = max(up_signals, key=lambda s: s.confidence)
            logger.debug("ConflictResolver: unanimous LONG — using %s TF", best.timeframe)
            return self._apply_internal_conflict_check(best)

        elif len(down_signals) > 0 and len(up_signals) == 0:
            # All agree: SHORT
            best = max(down_signals, key=lambda s: s.confidence)
            logger.debug("ConflictResolver: unanimous SHORT — using %s TF", best.timeframe)
            return self._apply_internal_conflict_check(best)

        else:
            # Conflict between timeframes — use higher TF bias
            highest_tf_signal = sorted_signals[0]
            if highest_tf_signal.direction == SignalDirection.NEUTRAL:
                # Higher TF is neutral — use next one
                for s in sorted_signals[1:]:
                    if s.direction != SignalDirection.NEUTRAL:
                        highest_tf_signal = s
                        break

            if highest_tf_signal.direction == SignalDirection.NEUTRAL:
                logger.info(
                    "ConflictResolver: all timeframes neutral or conflicting — returning None"
                )
                return None

            # Apply confidence penalty for TF disagreement
            resolved = self._copy_signal(highest_tf_signal)
            resolved.confidence = resolved.confidence * 0.7  # 30% penalty for TF conflict

            logger.info(
                "ConflictResolver: TF conflict resolved using %s TF %s "
                "(confidence penalised to %.2f)",
                highest_tf_signal.timeframe,
                highest_tf_signal.direction.value,
                resolved.confidence,
            )

            return self._apply_internal_conflict_check(resolved)

    # ------------------------------------------------------------------
    # Internal conflict checks (LLM vs technical disagreement)
    # ------------------------------------------------------------------

    def _apply_internal_conflict_check(self, signal: FusedSignal) -> Optional[FusedSignal]:
        """Check if LLM and technical sub-signals strongly disagree within a signal.

        If they do, reduce confidence by CONFIDENCE_PENALTY_STRONG_DISAGREE.
        """
        tech = signal.technical_signal
        llm = signal.llm_signal

        # Strong disagreement: opposite signs AND both > 0.5 magnitude
        if tech * llm < 0 and abs(tech) > 0.5 and abs(llm) > 0.5:
            original_conf = signal.confidence
            penalised_conf = original_conf * (1 - CONFIDENCE_PENALTY_STRONG_DISAGREE)
            logger.info(
                "ConflictResolver: LLM (%.2f) and Technical (%.2f) strongly disagree for %s. "
                "Confidence penalised %.2f → %.2f",
                llm, tech, signal.symbol, original_conf, penalised_conf,
            )
            resolved = self._copy_signal(signal)
            resolved.confidence = penalised_conf
            # Recompute EV with penalised confidence
            resolved.compute_expected_value()
            return resolved

        return signal

    @staticmethod
    def _copy_signal(signal: FusedSignal) -> FusedSignal:
        """Return a shallow copy of a FusedSignal for safe mutation."""
        import copy
        return copy.copy(signal)
