# src/llm/ollama_client.py
"""Ollama local LLM client for NEXUS ALPHA.

Uses HTTP calls to the Ollama REST API running on localhost:11434.
Default model is llama3.1:8b which fits comfortably in 8 GB of VRAM/RAM.
Falls back gracefully when Ollama is not running.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Optional

import aiohttp

from .base_llm import BaseLLM, LLMResponse, compute_cost

logger = logging.getLogger(__name__)

OLLAMA_BASE_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.1:8b"
REQUEST_TIMEOUT = 120  # seconds — local models can be slow


class OllamaLLM(BaseLLM):
    """Client for Ollama's local API.

    Pings ``/api/tags`` on construction to check availability.
    All subsequent calls will short-circuit gracefully if Ollama is down.
    """

    def __init__(
        self,
        base_url: str = OLLAMA_BASE_URL,
        model: str = DEFAULT_MODEL,
        **kwargs,
    ) -> None:
        super().__init__(model=model, **kwargs)
        self._base_url = base_url.rstrip("/")
        self._available: Optional[bool] = None  # lazy-checked on first query

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def query(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.1,
        max_tokens: int = 2000,
    ) -> LLMResponse:
        """Send a query to the local Ollama instance.

        Returns a *neutral* empty response if Ollama is unavailable rather
        than raising, so the ensemble can fall through to the next provider.
        """
        if not await self._is_available():
            logger.warning("Ollama not available — returning empty response")
            return self._unavailable_response()

        try:
            response = await self._send(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            self._record_usage(response)
            return response
        except Exception as exc:
            logger.error("Ollama query failed: %s", exc)
            self._available = False  # mark as unavailable for this session
            return self._unavailable_response()

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    async def health_check(self) -> bool:
        """Ping /api/tags to verify Ollama is running."""
        return await self._is_available()

    async def _is_available(self) -> bool:
        if self._available is not None:
            return self._available

        try:
            timeout = aiohttp.ClientTimeout(total=5)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(f"{self._base_url}/api/tags") as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        # Check that the desired model is pulled
                        models = [m.get("name", "") for m in data.get("models", [])]
                        if not any(self.model in m for m in models):
                            logger.warning(
                                "Ollama running but model %r not found. "
                                "Pull it with: ollama pull %s",
                                self.model, self.model,
                            )
                        self._available = True
                    else:
                        self._available = False
        except Exception:
            logger.info("Ollama not reachable at %s", self._base_url)
            self._available = False

        return self._available

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _send(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> LLMResponse:
        """Execute one Ollama chat completion call."""
        t0 = time.monotonic()
        url = f"{self._base_url}/api/chat"

        payload = {
            "model": self.model,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }

        timeout = aiohttp.ClientTimeout(total=REQUEST_TIMEOUT)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=payload) as resp:
                resp.raise_for_status()
                data = await resp.json()

        latency_ms = self._elapsed_ms(t0)

        text = data.get("message", {}).get("content", "")
        # Ollama reports token counts in eval_count / prompt_eval_count
        input_tokens = data.get("prompt_eval_count", 0)
        output_tokens = data.get("eval_count", 0)
        cost = compute_cost(self.model, input_tokens, output_tokens)  # 0.0 for local

        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
            raw_response=data,
        )

    @staticmethod
    def _unavailable_response() -> LLMResponse:
        """Neutral placeholder when Ollama is not reachable."""
        return LLMResponse(
            text="",
            model="ollama_unavailable",
            input_tokens=0,
            output_tokens=0,
            latency_ms=0.0,
            cost_usd=0.0,
        )
