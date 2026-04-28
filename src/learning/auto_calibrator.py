"""
Auto Calibrator for NEXUS ALPHA Learning System.

Dynamically adjusts trading thresholds based on recent performance:
    - Edge thresholds: become more selective if win rate falls below 40%
    - Signal weights: inversely proportional to Brier scores
    - Stop multipliers: widen if >50% of stops are hit prematurely
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Calibration boundaries
MIN_EDGE_THRESHOLD = 0.40
MAX_EDGE_THRESHOLD = 0.85
MIN_STOP_MULT = 0.8
MAX_STOP_MULT = 5.0
MIN_WEIGHT = 0.05
MAX_WEIGHT = 1.0

# Win rate targets
WIN_RATE_LOW = 0.40   # Below this → increase edge threshold (be more selective)
WIN_RATE_HIGH = 0.65  # Above this → decrease threshold slightly (take more trades)


class AutoCalibrator:
    """
    Adaptive threshold and weight calibration system.

    Analyses recent trade results and signal data to recalibrate:
        1. Edge thresholds (signal quality gates)
        2. Signal source weights (based on Brier score accuracy)
        3. Stop loss ATR multipliers (based on premature stop analysis)

    Parameters
    ----------
    history : optional
        Initial calibration state (loaded from storage).
    """

    def __init__(self, history: Optional[Dict[str, Any]] = None) -> None:
        self._calibration_state: Dict[str, Dict[str, float]] = {}
        if history:
            self._calibration_state = history
        logger.info("AutoCalibrator initialised")

    # ------------------------------------------------------------------
    # Edge threshold calibration
    # ------------------------------------------------------------------

    def calibrate_edge_thresholds(
        self,
        market: str,
        recent_trades: List[Dict[str, Any]],
        current_threshold: float = 0.60,
        strategy_name: str = "default",
    ) -> float:
        """
        Adjust signal quality threshold based on recent win rate.

        Parameters
        ----------
        market : str
            Target market.
        recent_trades : list
            Recent trade records containing 'pnl' or 'won' fields.
        current_threshold : float
            Current edge threshold (0.0–1.0).
        strategy_name : str
            Strategy to calibrate.

        Returns
        -------
        float
            New threshold value, clamped to [MIN_EDGE_THRESHOLD, MAX_EDGE_THRESHOLD].
        """
        if not recent_trades:
            logger.debug("[Calibrator] No recent trades for %s/%s", market, strategy_name)
            return current_threshold

        win_rate = self._compute_win_rate(recent_trades)
        n_trades = len(recent_trades)

        logger.info(
            "[Calibrator] %s/%s | trades=%d | win_rate=%.1f%% | current_threshold=%.3f",
            market, strategy_name, n_trades, win_rate * 100, current_threshold,
        )

        # Determine adjustment
        if win_rate < WIN_RATE_LOW:
            # Too many losers → raise bar (be more selective)
            deficit = WIN_RATE_LOW - win_rate
            adjustment = +deficit * 0.5  # Scale adjustment with severity
            new_threshold = current_threshold + adjustment
            logger.info(
                "[Calibrator] Win rate %.1f%% < %.1f%% → raising threshold by +%.3f",
                win_rate * 100, WIN_RATE_LOW * 100, adjustment,
            )
        elif win_rate > WIN_RATE_HIGH:
            # Strategy doing well → slightly lower bar to take more trades
            surplus = win_rate - WIN_RATE_HIGH
            adjustment = -surplus * 0.25  # More conservative downward adjustment
            new_threshold = current_threshold + adjustment
            logger.info(
                "[Calibrator] Win rate %.1f%% > %.1f%% → lowering threshold by %.3f",
                win_rate * 100, WIN_RATE_HIGH * 100, abs(adjustment),
            )
        else:
            new_threshold = current_threshold  # In target range; no change
            logger.debug("[Calibrator] Win rate in target range; no change")

        new_threshold = float(np.clip(new_threshold, MIN_EDGE_THRESHOLD, MAX_EDGE_THRESHOLD))

        # Store calibration
        key = f"{market}:{strategy_name}"
        self._calibration_state.setdefault(key, {})["edge_threshold"] = new_threshold

        return new_threshold

    # ------------------------------------------------------------------
    # Signal weight calibration
    # ------------------------------------------------------------------

    def calibrate_signal_weights(
        self,
        brier_scores: Dict[str, float],
        current_weights: Optional[Dict[str, float]] = None,
    ) -> Dict[str, float]:
        """
        Compute new signal weights inversely proportional to Brier scores.

        Brier score: mean squared error between predicted probability and outcome.
        Lower Brier score → better forecaster → higher weight.

        Parameters
        ----------
        brier_scores : dict
            {signal_source_name: brier_score} where brier_score is in [0, 1].
            0.0 = perfect predictor, 0.25 = uninformative, 1.0 = inverse.
        current_weights : dict, optional
            Existing weights (used for smoothing if provided).

        Returns
        -------
        dict
            {signal_source_name: normalised_weight} summing to 1.0.
        """
        if not brier_scores:
            return current_weights or {}

        # Convert Brier scores to inverse scores (lower BS → higher weight)
        # Add small epsilon to avoid division by zero
        inverse_scores: Dict[str, float] = {}
        for source, bs in brier_scores.items():
            bs = float(np.clip(bs, 0.0, 1.0))
            # Map: 0 BS → weight = 1/ε, 0.25 BS → neutral, 1.0 BS → near 0
            inverse_scores[source] = 1.0 / (bs + 0.05)

        total = sum(inverse_scores.values())
        if total <= 0:
            n = len(inverse_scores)
            return {k: 1.0 / n for k in inverse_scores}

        # Normalise
        new_weights = {k: v / total for k, v in inverse_scores.items()}

        # Exponential smoothing if current weights exist (α = 0.3 for new weights)
        if current_weights:
            alpha = 0.3
            smoothed = {}
            all_sources = set(new_weights.keys()) | set(current_weights.keys())
            for source in all_sources:
                new_w = new_weights.get(source, 0.0)
                old_w = current_weights.get(source, new_w)
                smoothed[source] = alpha * new_w + (1 - alpha) * old_w
            # Re-normalise
            total_s = sum(smoothed.values())
            new_weights = {k: v / total_s for k, v in smoothed.items()}

        # Clamp individual weights
        new_weights = {
            k: float(np.clip(v, MIN_WEIGHT, MAX_WEIGHT))
            for k, v in new_weights.items()
        }
        # Re-normalise after clamping
        total_clamped = sum(new_weights.values())
        if total_clamped > 0:
            new_weights = {k: v / total_clamped for k, v in new_weights.items()}

        logger.info(
            "[Calibrator] Signal weights updated from %d Brier scores",
            len(brier_scores),
        )
        for source, w in sorted(new_weights.items(), key=lambda x: x[1], reverse=True):
            logger.debug("  %s: %.4f (Brier=%.4f)", source, w, brier_scores.get(source, 0))

        return new_weights

    # ------------------------------------------------------------------
    # Stop multiplier calibration
    # ------------------------------------------------------------------

    def calibrate_stop_multipliers(
        self,
        recent_stopped_trades: List[Dict[str, Any]],
        current_multiplier: float = 2.0,
        strategy_name: str = "default",
    ) -> float:
        """
        Adjust ATR stop multiplier based on premature stop analysis.

        If more than 50% of stopped trades recovered after the stop,
        the multiplier is too tight — increase it.

        Parameters
        ----------
        recent_stopped_trades : list
            Trades that hit stop loss. Each should contain:
                - 'premature': bool (True if price recovered after stop)
                - 'atr_mult_used': float (multiplier used)
        current_multiplier : float
            Current ATR stop multiplier.
        strategy_name : str
            Strategy being calibrated.

        Returns
        -------
        float
            New multiplier, clamped to [MIN_STOP_MULT, MAX_STOP_MULT].
        """
        if not recent_stopped_trades:
            return current_multiplier

        premature_count = sum(
            1 for t in recent_stopped_trades if t.get("premature", False)
        )
        total = len(recent_stopped_trades)
        premature_rate = premature_count / total

        logger.info(
            "[Calibrator] %s stop analysis | stopped=%d | premature=%.1f%%",
            strategy_name, total, premature_rate * 100,
        )

        if premature_rate > 0.50:
            # More than half of stops were premature → widen stops
            # Increase multiplier by premature_rate beyond 50%
            excess = premature_rate - 0.50
            adjustment = 1.0 + excess * 0.5  # Scale: 100% premature → +25% wider
            new_mult = current_multiplier * adjustment
            logger.info(
                "[Calibrator] %d%% premature stops → widening mult from %.2f to %.2f",
                int(premature_rate * 100), current_multiplier, new_mult,
            )
        elif premature_rate < 0.20:
            # Very few premature stops; could tighten slightly
            new_mult = current_multiplier * 0.97  # 3% reduction
            logger.debug(
                "[Calibrator] Low premature rate; tightening mult to %.2f", new_mult
            )
        else:
            new_mult = current_multiplier

        new_mult = float(np.clip(new_mult, MIN_STOP_MULT, MAX_STOP_MULT))
        self._calibration_state.setdefault(strategy_name, {})["stop_multiplier"] = new_mult
        return new_mult

    # ------------------------------------------------------------------
    # Composite calibration
    # ------------------------------------------------------------------

    def run_full_calibration(
        self,
        market: str,
        strategy_name: str,
        recent_trades: List[Dict[str, Any]],
        brier_scores: Dict[str, float],
        current_params: Dict[str, Any],
    ) -> Dict[str, Any]:
        """
        Run all calibrations and return updated params dict.

        Parameters
        ----------
        market : str
            Target market.
        strategy_name : str
            Strategy identifier.
        recent_trades : list
            Recent trades (last 7-14 days).
        brier_scores : dict
            Brier scores per signal source.
        current_params : dict
            Current strategy parameters.

        Returns
        -------
        dict
            Updated parameters with calibrated values.
        """
        updated = dict(current_params)

        # 1. Edge threshold
        if "edge_threshold" in current_params:
            new_threshold = self.calibrate_edge_thresholds(
                market, recent_trades,
                current_threshold=current_params["edge_threshold"],
                strategy_name=strategy_name,
            )
            updated["edge_threshold"] = new_threshold

        # 2. Signal weights
        if brier_scores and "signal_weights" in current_params:
            new_weights = self.calibrate_signal_weights(
                brier_scores,
                current_weights=current_params["signal_weights"],
            )
            updated["signal_weights"] = new_weights

        # 3. Stop multiplier
        if "atr_stop_mult" in current_params:
            stopped = [t for t in recent_trades if t.get("exit_reason") == "stop_loss"]
            if stopped:
                new_mult = self.calibrate_stop_multipliers(
                    stopped,
                    current_multiplier=current_params["atr_stop_mult"],
                    strategy_name=strategy_name,
                )
                updated["atr_stop_mult"] = new_mult

        logger.info(
            "[Calibrator] Full calibration complete for %s/%s",
            market, strategy_name,
        )
        return updated

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_win_rate(trades: List[Dict[str, Any]]) -> float:
        """Compute win rate from a list of trade dicts."""
        if not trades:
            return 0.5
        wins = sum(
            1 for t in trades
            if t.get("won", False) or float(t.get("pnl", 0)) > 0
        )
        return wins / len(trades)

    def get_calibration_state(self) -> Dict[str, Any]:
        """Return current calibration state for persistence."""
        return dict(self._calibration_state)

    def load_calibration_state(self, state: Dict[str, Any]) -> None:
        """Load previously stored calibration state."""
        self._calibration_state = state
        logger.info("Calibration state loaded: %d entries", len(state))

    def __repr__(self) -> str:
        return f"<AutoCalibrator entries={len(self._calibration_state)}>"
