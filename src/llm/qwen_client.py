# src/llm/qwen_client.py
"""Qwen LLM client via Alibaba Cloud DashScope OpenAI-compatible endpoint."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

from openai import AsyncOpenAI, APIStatusError, APIConnectionError, RateLimitError

from .base_llm import BaseLLM, LLMResponse, compute_cost

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
DEFAULT_MODEL = "qwen-plus"
MAX_RETRIES = 5
BASE_BACKOFF = 1.0
MAX_BACKOFF = 60.0


class QwenLLM(BaseLLM):
    """Alibaba Cloud Qwen client via DashScope's OpenAI-compatible endpoint.

    Uses the ``openai`` SDK pointed at DashScope's base URL so that switching
    between providers requires no structural changes in the codebase.
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        base_url: str = DASHSCOPE_BASE_URL,
        **kwargs,
    ) -> None:
        super().__init__(model=model, **kwargs)
        self._api_key = api_key or os.environ.get("DASHSCOPE_API_KEY", "")
        if not self._api_key:
            logger.warning("DASHSCOPE_API_KEY not set; Qwen calls will fail")

        self._client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=base_url,
        )

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
        """Send a query to Qwen with exponential back-off retry."""
        attempt = 0
        last_exc: Optional[Exception] = None

        while attempt <= MAX_RETRIES:
            try:
                response = await self._send(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                self._record_usage(response)
                return response

            except RateLimitError as exc:
                last_exc = exc
                wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                logger.warning(
                    "Qwen rate-limited (attempt %d/%d). Retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                attempt += 1

            except APIStatusError as exc:
                if exc.status_code in (429, 503, 529):
                    last_exc = exc
                    wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                    logger.warning(
                        "Qwen API status %d (attempt %d/%d). Waiting %.1fs",
                        exc.status_code, attempt + 1, MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    attempt += 1
                else:
                    logger.error("Qwen API error %d: %s", exc.status_code, exc)
                    raise

            except APIConnectionError as exc:
                last_exc = exc
                wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                logger.warning(
                    "Qwen connection error (attempt %d/%d). Retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                attempt += 1

        raise RuntimeError(
            f"Qwen query failed after {MAX_RETRIES} retries"
        ) from last_exc

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
        """Execute one API call and wrap the result in :class:`LLMResponse`."""
        t0 = time.monotonic()

        completion = await self._client.chat.completions.create(
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )

        latency_ms = self._elapsed_ms(t0)

        text = completion.choices[0].message.content or ""
        input_tokens = completion.usage.prompt_tokens if completion.usage else 0
        output_tokens = completion.usage.completion_tokens if completion.usage else 0
        cost = compute_cost(self.model, input_tokens, output_tokens)

        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
            raw_response=completion,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()
