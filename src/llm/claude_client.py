# src/llm/claude_client.py
"""Claude LLM client using the Anthropic SDK with prompt caching and retry logic."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Optional

import anthropic

from .base_llm import BaseLLM, LLMResponse, compute_cost

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_MODEL = "claude-sonnet-4-6"
MAX_RETRIES = 5
BASE_BACKOFF = 1.0   # seconds
MAX_BACKOFF = 60.0   # seconds


class ClaudeLLM(BaseLLM):
    """Anthropic Claude client with:

    - Prompt caching (``cache_control`` on system messages)
    - Exponential back-off on HTTP 429 / overload errors
    - Accurate per-query cost tracking ($3/1M input, $15/1M output)
    """

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = DEFAULT_MODEL,
        **kwargs,
    ) -> None:
        super().__init__(model=model, **kwargs)
        self._api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")
        if not self._api_key:
            logger.warning("ANTHROPIC_API_KEY not set; Claude calls will fail")

        # Async client — reused across all calls for connection pooling
        self._client = anthropic.AsyncAnthropic(api_key=self._api_key)

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
        """Send a query to Claude with automatic retry on overload."""
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

            except anthropic.RateLimitError as exc:
                last_exc = exc
                wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                logger.warning(
                    "Claude rate-limited (attempt %d/%d). Retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                attempt += 1

            except anthropic.APIStatusError as exc:
                # 529 = overload; treat same as rate limit
                if exc.status_code in (429, 529):
                    last_exc = exc
                    wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                    logger.warning(
                        "Claude overloaded (status %d, attempt %d/%d). Waiting %.1fs",
                        exc.status_code, attempt + 1, MAX_RETRIES, wait,
                    )
                    await asyncio.sleep(wait)
                    attempt += 1
                else:
                    logger.error("Claude API error: %s", exc)
                    raise

            except anthropic.APIConnectionError as exc:
                last_exc = exc
                wait = min(BASE_BACKOFF * (2 ** attempt), MAX_BACKOFF)
                logger.warning(
                    "Claude connection error (attempt %d/%d). Retrying in %.1fs",
                    attempt + 1, MAX_RETRIES, wait,
                )
                await asyncio.sleep(wait)
                attempt += 1

        raise RuntimeError(
            f"Claude query failed after {MAX_RETRIES} retries"
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
        """Execute a single API call and parse the response."""
        t0 = time.monotonic()

        # Use cache_control on the system message so Claude can cache it
        # across repeated calls with the same system prompt.
        system_block = [
            {
                "type": "text",
                "text": system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]

        message = await self._client.messages.create(
            model=self.model,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system_block,
            messages=[{"role": "user", "content": user_prompt}],
        )

        latency_ms = self._elapsed_ms(t0)

        # Extract text from first content block
        text = ""
        for block in message.content:
            if hasattr(block, "text"):
                text = block.text
                break

        # Token accounting (includes cache read/write tokens if present)
        usage = message.usage
        input_tokens = getattr(usage, "input_tokens", 0)
        output_tokens = getattr(usage, "output_tokens", 0)

        # Cache tokens are billed at lower rate; for simplicity we treat
        # cache_read_input_tokens as standard input tokens (conservative).
        cost = compute_cost(self.model, input_tokens, output_tokens)

        return LLMResponse(
            text=text,
            model=self.model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            cost_usd=cost,
            raw_response=message,
        )

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.close()
