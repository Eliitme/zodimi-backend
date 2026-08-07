"""LLM abstraction — the rest of the codebase never imports a vendor SDK.

Provider selection: Gemini Flash when GEMINI_API_KEY is set, otherwise the
deterministic FakeProvider (local dev / CI — no key, no cost, no network).
Token usage is logged per call (counts only, never prompt/PII contents).
"""

import logging
import time
from collections.abc import AsyncIterator, Iterator
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger("stellune.llm")

GEMINI_MODEL = "gemini-2.5-flash"
REQUEST_TIMEOUT_S = 60
MAX_RETRIES = 2


class LLMProvider(Protocol):
    def generate(self, system: str, prompt: str) -> str: ...
    def stream(self, system: str, prompt: str) -> Iterator[str]: ...


class GeminiFlashProvider:
    def __init__(self, api_key: str):
        from google import genai
        from google.genai import types

        self._types = types
        self._client = genai.Client(
            api_key=api_key,
            http_options=types.HttpOptions(timeout=REQUEST_TIMEOUT_S * 1000),
        )

    def _config(self, system: str):
        return self._types.GenerateContentConfig(system_instruction=system)

    def generate(self, system: str, prompt: str) -> str:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                # Exponential backoff — immediate retries just burn rate
                # limit (free tier is ~10 RPM).
                time.sleep(2 ** attempt)
            try:
                started = time.monotonic()
                response = self._client.models.generate_content(
                    model=GEMINI_MODEL, contents=prompt, config=self._config(system)
                )
                usage = response.usage_metadata
                logger.info(
                    "llm generate ok in=%s out=%s elapsed=%.1fs",
                    getattr(usage, "prompt_token_count", "?"),
                    getattr(usage, "candidates_token_count", "?"),
                    time.monotonic() - started,
                )
                return response.text or ""
            except Exception as exc:  # SDK raises many transport types
                last_error = exc
                logger.warning("llm generate attempt %d failed: %s", attempt + 1, exc)
        raise LLMUnavailableError(str(last_error))

    def stream(self, system: str, prompt: str) -> Iterator[str]:
        try:
            for chunk in self._client.models.generate_content_stream(
                model=GEMINI_MODEL, contents=prompt, config=self._config(system)
            ):
                if chunk.text:
                    yield chunk.text
        except Exception as exc:
            raise LLMUnavailableError(str(exc))


class FakeProvider:
    """Deterministic stand-in when no API key is configured. Output shape
    honours the report contract (starts with '## ') so every downstream
    consumer can be exercised end-to-end without a vendor account."""

    def generate(self, system: str, prompt: str) -> str:
        head = prompt.strip().splitlines()[0][:80] if prompt.strip() else ""
        return f"## Sample heading\n\n[fake-llm] {head}"

    def stream(self, system: str, prompt: str) -> Iterator[str]:
        for word in self.generate(system, prompt).split(" "):
            yield word + " "


class LLMUnavailableError(RuntimeError):
    """Provider failed after retries — callers decide degraded behaviour."""


@lru_cache
def get_provider() -> LLMProvider:
    settings = get_settings()
    if settings.gemini_api_key:
        return GeminiFlashProvider(settings.gemini_api_key)
    logger.warning("GEMINI_API_KEY empty — using FakeProvider (dev mode)")
    return FakeProvider()
