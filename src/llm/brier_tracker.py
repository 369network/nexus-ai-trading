# src/llm/brier_tracker.py
"""Brier score tracker for LLM prediction calibration.

Stores prediction confidence and actual outcomes, computes per-model/per-market
Brier scores, and exposes the best-performing model for each market.

Persistence model
-----------------
* Sync path  (_persist_prediction / _persist_outcome / load_from_supabase):
  kept intact but only active when the caller supplies a *sync* supabase client.
  These paths are currently unused — kept for backwards compat.

* Async path (_persist_prediction_async / _persist_outcome_async /
  load_from_supabase_async): used by main.py which runs inside an async event
  loop and holds an async PostgREST client (supabase-py >= 2.x).

  record_prediction() and record_outcome() detect whether an event loop is
  running and fire asyncio.ensure_future() for the async persist so the hot
  path is never blocked.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default model weights when insufficient data (<10 predictions)
DEFAULT_WEIGHTS: Dict[str, float] = {
    "claude-sonnet-4-6": 0.35,
    "gpt-4o": 0.30,
    "qwen-plus": 0.20,
    "llama3.1:8b": 0.15,
}

MIN_PREDICTIONS_FOR_SCORE = 10


@dataclass
class PredictionRecord:
    model: str
    market: str
    symbol: str
    direction: str       # "LONG" | "SHORT" | "NEUTRAL"
    confidence: float    # 0.0 – 1.0
    timestamp: datetime


@dataclass
class OutcomeRecord:
    model: str
    market: str
    symbol: str
    actual_direction: str   # "LONG" | "SHORT" | "NEUTRAL"
    timestamp: datetime


class BrierTracker:
    """Records LLM directional predictions and their outcomes, then computes
    Brier scores to rank models per market.

    The Brier score is: BS = (f - o)^2
    where f = predicted probability and o = binary outcome (1 if correct, 0 if not).
    Lower is better.
    """

    def __init__(self, supabase_client=None) -> None:
        """
        Parameters
        ----------
        supabase_client:
            Optional async Supabase client (supabase-py >= 2.x) for persisting
            records.  If None, data is held in-memory only (useful for testing).
        """
        self._supabase = supabase_client
        self._predictions: List[PredictionRecord] = []
        self._outcomes: List[OutcomeRecord] = []

    # ------------------------------------------------------------------
    # Write path
    # ------------------------------------------------------------------

    def record_prediction(
        self,
        model: str,
        market: str,
        symbol: str,
        direction: str,
        confidence: float,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record a directional prediction with its confidence."""
        ts = timestamp or datetime.now(tz=timezone.utc)
        record = PredictionRecord(
            model=model,
            market=market,
            symbol=symbol,
            direction=direction,
            confidence=max(0.0, min(1.0, confidence)),
            timestamp=ts,
        )
        self._predictions.append(record)

        # Fire async persist if we are inside a running event loop
        if self._supabase is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._persist_prediction_async(record))
            except RuntimeError:
                # No running loop — fall back to sync (legacy path)
                self._persist_prediction(record)

    def record_outcome(
        self,
        model: str,
        market: str,
        symbol: str,
        actual_direction: str,
        timestamp: Optional[datetime] = None,
    ) -> None:
        """Record the actual market outcome for a prior prediction."""
        ts = timestamp or datetime.now(tz=timezone.utc)
        record = OutcomeRecord(
            model=model,
            market=market,
            symbol=symbol,
            actual_direction=actual_direction,
            timestamp=ts,
        )
        self._outcomes.append(record)

        if self._supabase is not None:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._persist_outcome_async(record))
            except RuntimeError:
                self._persist_outcome(record)

    # ------------------------------------------------------------------
    # Brier score computation
    # ------------------------------------------------------------------

    def compute_brier_score(
        self,
        model: str,
        market: str,
        window_days: int = 30,
    ) -> Optional[float]:
        """Compute Brier score for *model* on *market* over the last *window_days*.

        Returns None if fewer than MIN_PREDICTIONS_FOR_SCORE matched pairs exist.
        """
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=window_days)
        matched = self._match_predictions_to_outcomes(model, market, cutoff)

        if len(matched) < MIN_PREDICTIONS_FOR_SCORE:
            return None

        total = 0.0
        for pred, outcome in matched:
            is_correct = float(pred.direction == outcome.actual_direction)
            total += (pred.confidence - is_correct) ** 2

        return total / len(matched)

    def compute_all_scores(self, window_days: int = 30) -> Dict[str, Dict[str, float]]:
        """Return Brier scores for every (model, market) combination.

        Returns
        -------
        dict
            ``{model_name: {market: brier_score}}``
            Markets with insufficient data are omitted.
        """
        scores: Dict[str, Dict[str, float]] = {}

        models = {p.model for p in self._predictions}
        markets = {p.market for p in self._predictions}

        for model in models:
            for market in markets:
                score = self.compute_brier_score(model, market, window_days)
                if score is not None:
                    scores.setdefault(model, {})[market] = score

        return scores

    def get_best_model(self, market: str, window_days: int = 30) -> str:
        """Return the model name with the lowest Brier score for *market*.

        Falls back to the model with the highest default weight if there is
        insufficient prediction data.
        """
        scores = self.compute_all_scores(window_days)

        market_scores: Dict[str, float] = {}
        for model, market_map in scores.items():
            if market in market_map:
                market_scores[model] = market_map[market]

        if not market_scores:
            # Insufficient data — return highest-weight default
            return max(DEFAULT_WEIGHTS, key=DEFAULT_WEIGHTS.__getitem__)

        return min(market_scores, key=market_scores.__getitem__)

    def get_dynamic_weights(
        self, market: str, window_days: int = 30
    ) -> Dict[str, float]:
        """Compute softmax-like weights from inverse Brier scores.

        Models with more accurate predictions receive higher weights.
        Falls back to DEFAULT_WEIGHTS when data is insufficient.
        """
        scores = self.compute_all_scores(window_days)

        market_scores: Dict[str, float] = {}
        for model, market_map in scores.items():
            if market in market_map:
                market_scores[model] = market_map[market]

        if not market_scores:
            return dict(DEFAULT_WEIGHTS)

        # Invert Brier score: lower Brier → higher weight
        # Add small epsilon to avoid division by zero
        inv_scores = {m: 1.0 / (s + 1e-6) for m, s in market_scores.items()}
        total = sum(inv_scores.values())
        weights = {m: v / total for m, v in inv_scores.items()}

        # Ensure all known models have an entry (use tiny weight for models
        # without enough data rather than excluding them entirely)
        for model in DEFAULT_WEIGHTS:
            if model not in weights:
                weights[model] = 0.01
        # Re-normalise
        total = sum(weights.values())
        return {m: v / total for m, v in weights.items()}

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _match_predictions_to_outcomes(
        self,
        model: str,
        market: str,
        cutoff: datetime,
    ) -> List[tuple[PredictionRecord, OutcomeRecord]]:
        """Match predictions to outcomes by (symbol, market, model) proximity."""
        preds = [
            p for p in self._predictions
            if p.model == model and p.market == market and p.timestamp >= cutoff
        ]

        matched: List[tuple[PredictionRecord, OutcomeRecord]] = []
        for pred in preds:
            # Find the earliest outcome for this symbol after the prediction
            candidates = [
                o for o in self._outcomes
                if (
                    o.model == model
                    and o.market == market
                    and o.symbol == pred.symbol
                    and o.timestamp >= pred.timestamp
                )
            ]
            if candidates:
                outcome = min(candidates, key=lambda o: o.timestamp)
                matched.append((pred, outcome))

        return matched

    # ------------------------------------------------------------------
    # Async persistence (primary path — supabase-py >= 2.x async client)
    # ------------------------------------------------------------------

    async def _persist_prediction_async(self, record: PredictionRecord) -> None:
        """Persist a prediction record to Supabase (async)."""
        if self._supabase is None:
            return
        try:
            await (
                self._supabase
                .table("model_performance")
                .insert({
                    "record_type": "prediction",
                    "model": record.model,
                    "market": record.market,
                    "symbol": record.symbol,
                    "direction": record.direction,
                    "confidence": record.confidence,
                    "timestamp": record.timestamp.isoformat(),
                })
                .execute()
            )
        except Exception as exc:
            logger.debug("BrierTracker: async prediction persist failed: %s", exc)

    async def _persist_outcome_async(self, record: OutcomeRecord) -> None:
        """Persist an outcome record to Supabase (async)."""
        if self._supabase is None:
            return
        try:
            await (
                self._supabase
                .table("model_performance")
                .insert({
                    "record_type": "outcome",
                    "model": record.model,
                    "market": record.market,
                    "symbol": record.symbol,
                    "actual_direction": record.actual_direction,
                    "timestamp": record.timestamp.isoformat(),
                })
                .execute()
            )
        except Exception as exc:
            logger.debug("BrierTracker: async outcome persist failed: %s", exc)

    async def load_from_supabase_async(self, window_days: int = 90) -> None:
        """Populate in-memory records from Supabase for the given window (async)."""
        if self._supabase is None:
            return

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=window_days)
        try:
            result = await (
                self._supabase
                .table("model_performance")
                .select("*")
                .gte("timestamp", cutoff.isoformat())
                .execute()
            )
            loaded_preds = 0
            loaded_outcomes = 0
            for row in result.data:
                if row["record_type"] == "prediction":
                    self._predictions.append(PredictionRecord(
                        model=row["model"],
                        market=row["market"],
                        symbol=row["symbol"],
                        direction=row["direction"],
                        confidence=row["confidence"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                    ))
                    loaded_preds += 1
                elif row["record_type"] == "outcome":
                    self._outcomes.append(OutcomeRecord(
                        model=row["model"],
                        market=row["market"],
                        symbol=row["symbol"],
                        actual_direction=row["actual_direction"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                    ))
                    loaded_outcomes += 1
            logger.info(
                "BrierTracker: loaded %d predictions + %d outcomes from Supabase",
                loaded_preds, loaded_outcomes,
            )
        except Exception as exc:
            logger.warning(
                "BrierTracker: async load from Supabase failed (will run in-memory): %s",
                exc,
            )

    # ------------------------------------------------------------------
    # Sync persistence (legacy / no-event-loop fallback)
    # ------------------------------------------------------------------

    def _persist_prediction(self, record: PredictionRecord) -> None:
        """Persist a prediction record to Supabase (sync — legacy path)."""
        if self._supabase is None:
            return
        try:
            self._supabase.table("model_performance").insert({
                "record_type": "prediction",
                "model": record.model,
                "market": record.market,
                "symbol": record.symbol,
                "direction": record.direction,
                "confidence": record.confidence,
                "timestamp": record.timestamp.isoformat(),
            }).execute()
        except Exception as exc:
            logger.error("BrierTracker: sync prediction persist failed: %s", exc)

    def _persist_outcome(self, record: OutcomeRecord) -> None:
        """Persist an outcome record to Supabase (sync — legacy path)."""
        if self._supabase is None:
            return
        try:
            self._supabase.table("model_performance").insert({
                "record_type": "outcome",
                "model": record.model,
                "market": record.market,
                "symbol": record.symbol,
                "actual_direction": record.actual_direction,
                "timestamp": record.timestamp.isoformat(),
            }).execute()
        except Exception as exc:
            logger.error("BrierTracker: sync outcome persist failed: %s", exc)

    def load_from_supabase(self, window_days: int = 90) -> None:
        """Populate in-memory records from Supabase for the given window (sync).

        Deprecated: prefer load_from_supabase_async() inside an async context.
        """
        if self._supabase is None:
            return

        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=window_days)
        try:
            result = (
                self._supabase
                .table("model_performance")
                .select("*")
                .gte("timestamp", cutoff.isoformat())
                .execute()
            )
            for row in result.data:
                if row["record_type"] == "prediction":
                    self._predictions.append(PredictionRecord(
                        model=row["model"],
                        market=row["market"],
                        symbol=row["symbol"],
                        direction=row["direction"],
                        confidence=row["confidence"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                    ))
                elif row["record_type"] == "outcome":
                    self._outcomes.append(OutcomeRecord(
                        model=row["model"],
                        market=row["market"],
                        symbol=row["symbol"],
                        actual_direction=row["actual_direction"],
                        timestamp=datetime.fromisoformat(row["timestamp"]),
                    ))
            logger.info(
                "BrierTracker: loaded %d predictions and %d outcomes from Supabase (sync)",
                len(self._predictions), len(self._outcomes),
            )
        except Exception as exc:
            logger.error("BrierTracker: sync load from Supabase failed: %s", exc)
