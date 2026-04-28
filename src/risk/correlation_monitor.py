"""
NEXUS ALPHA - Correlation Monitor
====================================
Tracks rolling return correlations between all traded instruments.
Alerts when pairs exceed 0.70 and enforces position reduction at 0.85.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------
CORR_ALERT_THRESHOLD = 0.70   # Warn when any pair exceeds this
CORR_HARD_LIMIT      = 0.85   # Require position reduction
CORR_REDUCE_PCT      = 0.30   # Reduce the smaller position by 30%
MIN_PERIODS          = 20     # Minimum returns samples before computing corr
RETURNS_WINDOW       = 60     # Rolling window (periods)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class CorrelationRisk:
    """Result of the concentration/correlation check."""
    is_concentrated: bool
    correlated_pairs: List[Tuple[str, str, float]]   # (sym1, sym2, corr)
    suggested_reduction_pct: float                    # 0–1, applied to the smaller position
    alert_pairs: List[Tuple[str, str, float]] = field(default_factory=list)  # warn-level pairs


# ---------------------------------------------------------------------------
# CorrelationMonitor
# ---------------------------------------------------------------------------

class CorrelationMonitor:
    """
    Maintains a rolling return-correlation matrix for all tracked instruments.

    Update frequency: call ``update()`` after each bar close for every
    instrument.  The internal store keeps up to ``RETURNS_WINDOW`` periods
    of returns per symbol.

    Usage
    -----
    monitor = CorrelationMonitor()
    monitor.update("crypto", "BTCUSDT", returns_series)
    matrix = monitor.get_correlation_matrix()
    risk = monitor.check_concentration(portfolio_positions)
    """

    def __init__(self) -> None:
        # {symbol: pd.Series of returns (most recent at end)}
        self._returns: Dict[str, pd.Series] = {}
        # Cached correlation matrix (recomputed on update)
        self._corr_matrix: Optional[pd.DataFrame] = None
        self._dirty = True   # True when matrix needs recomputation

    # ------------------------------------------------------------------
    # Data ingestion
    # ------------------------------------------------------------------

    def update(self, market: str, symbol: str, returns: pd.Series) -> None:
        """
        Ingest new return observations for a symbol.

        Parameters
        ----------
        market : str
            Market segment (informational, stored in symbol key context).
        symbol : str
            Unique symbol identifier (e.g. "BTCUSDT", "EUR_USD").
        returns : pd.Series
            Series of periodic returns (e.g. daily pct changes).
            New observations are appended and the window is trimmed.
        """
        if returns.empty:
            return

        key = f"{market}:{symbol}"
        existing = self._returns.get(key, pd.Series(dtype=float))

        # Append new returns, keep last RETURNS_WINDOW periods
        combined = pd.concat([existing, returns]).dropna()
        self._returns[key] = combined.iloc[-RETURNS_WINDOW:]
        self._dirty = True

        logger.debug(
            "CorrelationMonitor.update: %s – %d return samples stored",
            key, len(self._returns[key]),
        )

    # ------------------------------------------------------------------
    # Matrix computation
    # ------------------------------------------------------------------

    def get_correlation_matrix(self) -> pd.DataFrame:
        """
        Return the current correlation matrix across all tracked symbols.

        Requires at least MIN_PERIODS return samples per symbol to include
        it in the matrix.  Symbols with fewer samples are excluded.

        Returns
        -------
        pd.DataFrame
            Square correlation matrix.  Returns empty DataFrame if fewer
            than 2 qualifying symbols exist.
        """
        if not self._dirty and self._corr_matrix is not None:
            return self._corr_matrix

        # Filter symbols with enough data
        eligible = {
            sym: series
            for sym, series in self._returns.items()
            if len(series) >= MIN_PERIODS
        }

        if len(eligible) < 2:
            self._corr_matrix = pd.DataFrame()
            self._dirty = False
            return self._corr_matrix

        # Align on common index and compute Pearson correlation
        df = pd.DataFrame(eligible)
        self._corr_matrix = df.corr(method="pearson")
        self._dirty = False

        logger.debug(
            "CorrelationMonitor: recomputed %dx%d matrix",
            len(self._corr_matrix), len(self._corr_matrix),
        )
        return self._corr_matrix

    # ------------------------------------------------------------------
    # Concentration check
    # ------------------------------------------------------------------

    def check_concentration(
        self, portfolio_positions: List[Dict[str, Any]]
    ) -> CorrelationRisk:
        """
        Identify highly correlated position pairs and suggest reductions.

        Parameters
        ----------
        portfolio_positions : List[dict]
            Each dict must have:
              - 'symbol' (str): trading symbol
              - 'market' (str): market segment
              - 'notional_usd' (float): current notional value

        Returns
        -------
        CorrelationRisk
            Concentration assessment with pair list and suggested reduction.
        """
        matrix = self.get_correlation_matrix()

        if matrix.empty or not portfolio_positions:
            return CorrelationRisk(
                is_concentrated=False,
                correlated_pairs=[],
                suggested_reduction_pct=0.0,
            )

        # Build lookup: correlation matrix key → position
        pos_by_key: Dict[str, Dict[str, Any]] = {}
        for pos in portfolio_positions:
            market = pos.get("market", "")
            symbol = pos.get("symbol", "")
            key = f"{market}:{symbol}"
            pos_by_key[key] = pos

        matrix_symbols = list(matrix.columns)
        alert_pairs: List[Tuple[str, str, float]] = []
        hard_pairs:  List[Tuple[str, str, float]] = []

        for i, s1 in enumerate(matrix_symbols):
            for j, s2 in enumerate(matrix_symbols):
                if j <= i:
                    continue

                # Only compare symbols we hold
                if s1 not in pos_by_key or s2 not in pos_by_key:
                    continue

                try:
                    corr = float(matrix.loc[s1, s2])
                except (KeyError, TypeError):
                    continue

                if abs(corr) >= CORR_HARD_LIMIT:
                    hard_pairs.append((s1, s2, corr))
                elif abs(corr) >= CORR_ALERT_THRESHOLD:
                    alert_pairs.append((s1, s2, corr))

        is_concentrated = bool(hard_pairs)

        # Determine suggested reduction
        suggested_reduction_pct = 0.0
        if hard_pairs:
            # For each highly correlated pair, reduce the smaller position by 30%
            suggested_reduction_pct = CORR_REDUCE_PCT
            for s1, s2, corr in hard_pairs:
                n1 = pos_by_key[s1].get("notional_usd", 0.0)
                n2 = pos_by_key[s2].get("notional_usd", 0.0)
                smaller = s1 if n1 <= n2 else s2
                logger.warning(
                    "CorrelationMonitor: %s ↔ %s corr=%.3f (hard limit %.2f) "
                    "– suggest reducing %s by %.0f%%",
                    s1, s2, corr, CORR_HARD_LIMIT, smaller, CORR_REDUCE_PCT * 100,
                )

        if alert_pairs:
            for s1, s2, corr in alert_pairs:
                logger.warning(
                    "CorrelationMonitor ALERT: %s ↔ %s corr=%.3f (alert threshold %.2f)",
                    s1, s2, corr, CORR_ALERT_THRESHOLD,
                )

        return CorrelationRisk(
            is_concentrated=is_concentrated,
            correlated_pairs=hard_pairs,
            suggested_reduction_pct=suggested_reduction_pct,
            alert_pairs=alert_pairs,
        )
