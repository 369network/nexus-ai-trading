# src/llm/ensemble.py
"""LLM Ensemble: routes queries to the best model per market, cascades on failure,
and enforces daily cost guards.

BrierTracker integration
------------------------
The ensemble records a per-model BrierTracker prediction on every successful
LLM call by extracting ``decision`` and ``confidence`` from the JSON response.
This lets BrierTracker rank individual models (claude-sonnet-4-6, gpt-4o, …)
rather than just the aggregate "strategy_ensemble".

The BrierTracker instance is injected AFTER construction via
``set_brier_tracker()`` so that ``main.py`` can share a single, properly
async-connected instance across the whole system.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from .base_llm import BaseLLM, LLMResponse
from .brier_tracker import BrierTracker, DEFAULT_WEIGHTS

logger = logging.getLogger(__name__)

# Maximum daily LLM spend before we fall back to the cheapest model only
MAX_DAILY_LLM_COST_USD: float = 10.0

# Ordered fallback preference (most capable → cheapest)
MODEL_FALLBACK_ORDER: List[str] = [
    "claude-sonnet-4-6",
    "gpt-4o",
    "qwen-plus",
    "llama3.1:8b",
]

# Cost per call (rough upper bound used for guard checking)
_MODEL_CHEAPEST = "llama3.1:8b"

# LLM decision values → BrierTracker direction
_DECISION_TO_DIRECTION: Dict[str, str] = {
    "STRONG_BUY":  "LONG",
    "BUY":         "LONG",
    "SLIGHT_BUY":  "LONG",
    "NEUTRAL":     "NEUTRAL",
    "SLIGHT_SELL": "SHORT",
    "SELL":        "SHORT",
    "STRONG_SELL": "SHORT",
}


class LLMEnsemble:
    """Routes LLM queries to the best-performing model for each market.

    Features
    --------
    * Brier-score-based model selection per market
    * Cascading fallback when the primary model fails
    * Dual-model averaging for high-stakes decisions
    * Daily cost guard that forces the cheapest model once the budget is hit
    * Per-model BrierTracker prediction recording on every successful call
    """

    def __init__(
        self,
        clients: Dict[str, BaseLLM],
        brier_tracker: BrierTracker,
        max_daily_cost: float = MAX_DAILY_LLM_COST_USD,
    ) -> None:
        """
        Parameters
        ----------
        clients:
            Map of model-name → :class:`BaseLLM` instance.
            Expected keys match :data:`MODEL_FALLBACK_ORDER`.
        brier_tracker:
            Shared :class:`BrierTracker` for dynamic weight computation and
            per-model prediction recording.
        max_daily_cost:
            Once accumulated daily spend reaches this threshold the ensemble
            switches to the cheapest available model only.
        """
        self._clients = clients
        self._brier = brier_tracker
        self._max_daily_cost = max_daily_cost

        # Daily cost accounting
        self._daily_cost: float = 0.0
        self._cost_date: date = datetime.now(tz=timezone.utc).date()

    # ------------------------------------------------------------------
    # Dependency injection (allows main.py to share one BrierTracker)
    # ------------------------------------------------------------------

    def set_brier_tracker(self, brier: BrierTracker) -> None:
        """Replace the internal BrierTracker with a shared instance.

        Call this after both the ensemble and the authoritative BrierTracker
        have been constructed so all prediction records go to the same object.
        """
        self._brier = brier
        logger.debug("LLMEnsemble: BrierTracker replaced with shared instance")

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def query(
        self,
        system_prompt: str,
        user_prompt: str,
        market: str,
        high_stakes: bool = False,
    ) -> LLMResponse:
        """Send a query using the best model for *market*.

        Parameters
        ----------
        system_prompt:
            LLM system / instruction context.
        user_prompt:
            Market data and question.
        market:
            One of ``"crypto"``, ``"forex"``, ``"commodity"``, ``"stocks_in"``,
            ``"stocks_us"``.
        high_stakes:
            If ``True``, the top-2 models are both queried and their text is
            concatenated for downstream averaging.
        """
        self._refresh_daily_budget()

        symbol = self._extract_symbol(user_prompt)
        ordered = self._get_model_order(market)

        # Cost guard: if over daily budget, force cheapest model only
        if self._daily_cost >= self._max_daily_cost:
            logger.warning(
                "Daily LLM cost limit $%.2f reached ($%.2f spent). "
                "Falling back to cheapest model.",
                self._max_daily_cost, self._daily_cost,
            )
            ordered = [_MODEL_CHEAPEST]

        if high_stakes and len(ordered) >= 2 and self._daily_cost < self._max_daily_cost:
            return await self._dual_query(
                system_prompt, user_prompt, ordered[0], ordered[1],
                market=market, symbol=symbol,
            )

        return await self._cascade_query(
            system_prompt, user_prompt, ordered,
            market=market, symbol=symbol,
        )

    def get_dynamic_weights(self, market: str) -> Dict[str, float]:
        """Return Brier-score-derived model weights for *market*."""
        return self._brier.get_dynamic_weights(market)

    # ------------------------------------------------------------------
    # Internal routing
    # ------------------------------------------------------------------

    def _get_model_order(self, market: str) -> List[str]:
        """Return models ordered best → worst for *market* based on Brier scores."""
        weights = self._brier.get_dynamic_weights(market)

        # Sort by weight descending; fall back to default order for ties
        available = [m for m in MODEL_FALLBACK_ORDER if m in self._clients]
        ordered = sorted(available, key=lambda m: weights.get(m, 0.0), reverse=True)

        # Ensure full fallback chain even for models not in weights dict
        for m in MODEL_FALLBACK_ORDER:
            if m in self._clients and m not in ordered:
                ordered.append(m)

        return ordered

    async def _cascade_query(
        self,
        system_prompt: str,
        user_prompt: str,
        model_order: List[str],
        market: str = "",
        symbol: str = "",
    ) -> LLMResponse:
        """Try each model in order; return the first successful response."""
        last_exc: Optional[Exception] = None

        for model_name in model_order:
            client = self._clients.get(model_name)
            if client is None:
                continue
            try:
                response = await client.query(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                )
                self._accumulate_cost(response.cost_usd)
                logger.debug(
                    "Ensemble: %s responded (cost=$%.6f)", model_name, response.cost_usd
                )
                # Record per-model Brier prediction
                if market and symbol:
                    self._try_record_brier(model_name, market, symbol, response.text)
                return response
            except Exception as exc:
                logger.warning(
                    "Ensemble: %s failed (%s), trying next model", model_name, exc
                )
                last_exc = exc

        raise RuntimeError(
            f"All LLM models failed in cascade: {model_order}"
        ) from last_exc

    async def _dual_query(
        self,
        system_prompt: str,
        user_prompt: str,
        primary: str,
        secondary: str,
        market: str = "",
        symbol: str = "",
    ) -> LLMResponse:
        """Query two models and merge their responses for high-stakes decisions."""
        import asyncio

        async def _query_one(model_name: str) -> Tuple[str, LLMResponse]:
            client = self._clients[model_name]
            resp = await client.query(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
            return model_name, resp

        tasks = [
            _query_one(m)
            for m in (primary, secondary)
            if m in self._clients
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        valid: List[Tuple[str, LLMResponse]] = []
        for r in results:
            if isinstance(r, tuple):
                model_name, response = r
                valid.append((model_name, response))
                self._accumulate_cost(response.cost_usd)
                # Record per-model Brier prediction for each successful response
                if market and symbol:
                    self._try_record_brier(model_name, market, symbol, response.text)
            else:
                logger.warning("Dual query: one model failed: %s", r)

        if not valid:
            raise RuntimeError("Both models failed in dual query")

        if len(valid) == 1:
            return valid[0][1]

        # Merge: concatenate responses with a separator so downstream parsers
        # can handle both, then take the first model's metadata.
        (m0, r0), (m1, r1) = valid[0], valid[1]
        merged_text = (
            f"[PRIMARY MODEL: {m0}]\n{r0.text}\n\n"
            f"[SECONDARY MODEL: {m1}]\n{r1.text}"
        )
        return LLMResponse(
            text=merged_text,
            model=f"{m0}+{m1}",
            input_tokens=r0.input_tokens + r1.input_tokens,
            output_tokens=r0.output_tokens + r1.output_tokens,
            latency_ms=max(r0.latency_ms, r1.latency_ms),
            cost_usd=r0.cost_usd + r1.cost_usd,
        )

    # ------------------------------------------------------------------
    # BrierTracker helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_symbol(user_prompt: str) -> str:
        """Parse the trading symbol from the market context header in *user_prompt*.

        Looks for the pattern: ``MARKET CONTEXT: BTCUSDT | ...``
        Falls back to ``"UNKNOWN"`` if the pattern is not found.
        """
        m = re.search(r"MARKET CONTEXT:\s*(\S+)\s*\|", user_prompt)
        return m.group(1) if m else "UNKNOWN"

    def _try_record_brier(
        self,
        model_name: str,
        market: str,
        symbol: str,
        response_text: str,
    ) -> None:
        """Parse the LLM JSON response and record a per-model Brier prediction.

        Silently skips on any parse or tracking error so the trading pipeline
        is never blocked by calibration bookkeeping.
        """
        try:
            # Extract outermost JSON object from the response (handles markdown fences)
            json_match = re.search(r"\{[^{}]*\}", response_text, re.DOTALL)
            if not json_match:
                # Try a broader match for deeply nested JSON
                json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
            if not json_match:
                return

            data = json.loads(json_match.group())

            decision = str(data.get("decision", "")).strip().upper()
            direction = _DECISION_TO_DIRECTION.get(decision, "NEUTRAL")

            raw_conf = data.get("confidence")
            confidence = float(raw_conf) if raw_conf is not None else 0.5
            confidence = max(0.0, min(1.0, confidence))

            self._brier.record_prediction(
                model=model_name,
                market=market,
                symbol=symbol,
                direction=direction,
                confidence=confidence,
            )
            logger.debug(
                "BrierTracker: recorded %s/%s → %s (%.2f) via %s",
                market, symbol, direction, confidence, model_name,
            )
        except Exception as exc:
            logger.debug("BrierTracker: parse/record skipped for %s: %s", model_name, exc)

    # ------------------------------------------------------------------
    # Budget tracking
    # ------------------------------------------------------------------

    def _refresh_daily_budget(self) -> None:
        """Reset the daily cost counter when the calendar date rolls over."""
        today = datetime.now(tz=timezone.utc).date()
        if today != self._cost_date:
            logger.info(
                "Daily LLM cost reset (previous day: $%.4f)", self._daily_cost
            )
            self._daily_cost = 0.0
            self._cost_date = today

    def _accumulate_cost(self, cost: float) -> None:
        self._daily_cost += cost

    @property
    def daily_cost_usd(self) -> float:
        """Current day's accumulated LLM spend in USD."""
        return self._daily_cost

    @property
    def models(self) -> List[str]:
        """Names of the loaded LLM clients."""
        return list(self._clients.keys())

    async def init(self) -> None:
        """Lifecycle method — no-op (clients are ready at construction)."""
        logger.info("LLMEnsemble initialised with %d models: %s", len(self._clients), self.models)

    @classmethod
    def from_settings(cls, settings: Any, db: Any = None) -> "LLMEnsemble":
        """
        Build an LLMEnsemble from a Settings object.
        Automatically imports and constructs available LLM clients.

        Note: The BrierTracker created here uses supabase_client=None (in-memory).
        Call set_brier_tracker() after construction to inject the shared instance
        that has the async Supabase client wired in.
        """
        clients: Dict[str, "BaseLLM"] = {}

        # Claude
        if getattr(settings, "anthropic_api_key", ""):
            try:
                from src.llm.claude_client import ClaudeLLM
                clients["claude-sonnet-4-6"] = ClaudeLLM(api_key=settings.anthropic_api_key)
                logger.info("Claude client loaded")
            except Exception as exc:
                logger.warning("Claude client failed: %s", exc)

        # OpenAI
        if getattr(settings, "openai_api_key", ""):
            try:
                from src.llm.openai_client import OpenAILLM
                clients["gpt-4o"] = OpenAILLM(api_key=settings.openai_api_key)
                logger.info("OpenAI client loaded")
            except Exception as exc:
                logger.warning("OpenAI client failed: %s", exc)

        # Qwen
        if getattr(settings, "qwen_api_key", ""):
            try:
                from src.llm.qwen_client import QwenLLM
                clients["qwen-plus"] = QwenLLM(api_key=settings.qwen_api_key)
                logger.info("Qwen client loaded")
            except Exception as exc:
                logger.warning("Qwen client failed: %s", exc)

        # Ollama (local, always try)
        try:
            from src.llm.ollama_client import OllamaLLM
            clients["llama3.1:8b"] = OllamaLLM(
                host=getattr(settings, "ollama_host", "http://localhost:11434"),
                model=getattr(settings, "ollama_model", "llama3.1:8b"),
            )
            logger.info("Ollama client loaded")
        except Exception as exc:
            logger.debug("Ollama not available: %s", exc)

        # In-memory placeholder — replaced by set_brier_tracker() after Step 10
        brier = BrierTracker(supabase_client=None)
        max_cost = getattr(settings, "max_daily_llm_cost", 10.0)
        return cls(clients=clients, brier_tracker=brier, max_daily_cost=max_cost)
