# src/llm/base_llm.py
"""Abstract base interface for all LLM clients used in NEXUS ALPHA."""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ---------------------------------------------------------------------------
# Response container
# ---------------------------------------------------------------------------

@dataclass
class LLMResponse:
    """Standardised response returned by every LLM client."""

    text: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    cost_usd: float
    # Optional metadata for downstream consumers
    raw_response: Optional[object] = field(default=None, repr=False)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __str__(self) -> str:
        return (
            f"LLMResponse(model={self.model}, tokens={self.total_tokens}, "
            f"cost=${self.cost_usd:.6f}, latency={self.latency_ms:.1f}ms)"
        )


# ---------------------------------------------------------------------------
# Per-model cost registry (USD per 1 M tokens)
# ---------------------------------------------------------------------------

MODEL_COSTS: dict[str, dict[str, float]] = {
    # Claude
    "claude-sonnet-4-6": {"input": 3.00, "output": 15.00},
    "claude-3-5-sonnet-20241022": {"input": 3.00, "output": 15.00},
    "claude-3-opus-20240229": {"input": 15.00, "output": 75.00},
    "claude-3-haiku-20240307": {"input": 0.25, "output": 1.25},
    # OpenAI
    "gpt-4o": {"input": 5.00, "output": 15.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
    "gpt-4-turbo": {"input": 10.00, "output": 30.00},
    # Qwen (Alibaba dashscope)
    "qwen-plus": {"input": 0.50, "output": 1.50},
    "qwen-turbo": {"input": 0.10, "output": 0.30},
    # Ollama / local models — effectively free
    "llama3.1:8b": {"input": 0.00, "output": 0.00},
    "llama3.1:70b": {"input": 0.00, "output": 0.00},
    "mistral:7b": {"input": 0.00, "output": 0.00},
}


def compute_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Return cost in USD for a given model and token counts."""
    costs = MODEL_COSTS.get(model, {"input": 0.00, "output": 0.00})
    return (
        input_tokens * costs["input"] / 1_000_000
        + output_tokens * costs["output"] / 1_000_000
    )


# ---------------------------------------------------------------------------
# Abstract base class
# ---------------------------------------------------------------------------

class BaseLLM(ABC):
    """Abstract base for every LLM backend.

    Subclasses must implement :meth:`query`.  The ``_timed_query`` helper
    wraps the call and records wall-clock latency so subclasses need not
    duplicate timing logic.
    """

    def __init__(self, model: str, **kwargs):
        self.model = model
        self._total_cost_usd: float = 0.0
        self._total_input_tokens: int = 0
        self._total_output_tokens: int = 0
        self._call_count: int = 0

    # ------------------------------------------------------------------
    # Core interface
    # ------------------------------------------------------------------

    @abstractmethod
    async def query(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Send a prompt pair to the LLM and return an :class:`LLMResponse`.

        Parameters
        ----------
        system_prompt:
            Instruction / persona / context for the model.
        user_prompt:
            The actual user query / market data blob.
        temperature:
            Sampling temperature (0.0 = deterministic).
        max_tokens:
            Maximum tokens in the completion.

        Returns
        -------
        LLMResponse
            Populated response including cost and latency metrics.
        """

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _record_usage(self, response: LLMResponse) -> None:
        """Accumulate running cost and token totals."""
        self._total_cost_usd += response.cost_usd
        self._total_input_tokens += response.input_tokens
        self._total_output_tokens += response.output_tokens
        self._call_count += 1

    def get_usage_summary(self) -> dict:
        """Return aggregated usage statistics for this client instance."""
        return {
            "model": self.model,
            "call_count": self._call_count,
            "total_input_tokens": self._total_input_tokens,
            "total_output_tokens": self._total_output_tokens,
            "total_cost_usd": round(self._total_cost_usd, 6),
        }

    @staticmethod
    def _elapsed_ms(start: float) -> float:
        """Return milliseconds elapsed since *start* (monotonic time)."""
        return (time.monotonic() - start) * 1000.0

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}(model={self.model!r}, "
            f"calls={self._call_count}, cost=${self._total_cost_usd:.4f})"
        )
