"""LLM abstraction — the rest of the codebase never imports a vendor SDK.

Provider selection, in order: OpenRouter when OPENROUTER_API_KEY is set,
then Gemini Flash when GEMINI_API_KEY is set, otherwise the deterministic
FakeProvider (local dev / CI — no key, no cost, no network).
Token usage is logged per call (counts only, never prompt/PII contents).
"""

import logging
import time
from collections.abc import Iterator
from functools import lru_cache
from typing import Protocol

from app.core.config import get_settings

logger = logging.getLogger("stellune.llm")

GEMINI_MODEL = "gemini-2.5-flash"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
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


class OpenRouterProvider:
    """OpenRouter behind the OpenAI-compatible SDK.

    One key reaches Anthropic, OpenAI, Google and open models, so the model
    is an env var rather than a code change — which is what makes an A/B on
    real Vietnamese output cheap.

    The official SDK parses the streaming response for us on purpose: SSE
    parsing is exactly where this codebase has been bitten before, and a
    hand-rolled reader would have to get chunk splitting and the terminal
    sentinel right for no benefit.
    """

    def __init__(self, api_key: str, model: str):
        from openai import OpenAI

        self._model = model
        self._client = OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_BASE_URL,
            timeout=REQUEST_TIMEOUT_S,
            # Attribution shown on OpenRouter's dashboard; no PII.
            default_headers={"X-Title": "Stellune"},
        )

    def _messages(self, system: str, prompt: str) -> list[dict[str, str]]:
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ]

    def generate(self, system: str, prompt: str) -> str:
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            if attempt:
                # Same backoff reasoning as the Gemini path: immediate
                # retries just burn the rate limit.
                time.sleep(2 ** attempt)
            try:
                started = time.monotonic()
                response = self._client.chat.completions.create(
                    model=self._model, messages=self._messages(system, prompt)
                )
                usage = response.usage
                logger.info(
                    "llm generate ok model=%s in=%s out=%s elapsed=%.1fs",
                    self._model,
                    getattr(usage, "prompt_tokens", "?"),
                    getattr(usage, "completion_tokens", "?"),
                    time.monotonic() - started,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                logger.warning("llm generate attempt %d failed: %s", attempt + 1, exc)
        raise LLMUnavailableError(str(last_error))

    def stream(self, system: str, prompt: str) -> Iterator[str]:
        try:
            stream = self._client.chat.completions.create(
                model=self._model, messages=self._messages(system, prompt), stream=True
            )
            for chunk in stream:
                if not chunk.choices:
                    continue
                text = chunk.choices[0].delta.content
                if text:
                    yield text
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
def get_provider(model: str | None = None) -> LLMProvider:
    """The configured provider, optionally on a specific model.

    Endpoints do not all want the same trade-off: tarot Q&A streams while
    the user waits, so latency dominates, whereas the premium report is
    generated by a background worker and can afford a slower, better model.
    Callers that care pass `model`; everyone else gets OPENROUTER_MODEL.

    Only OpenRouter can honour the override — it is the one provider that
    reaches more than a single vendor's models. The others ignore it rather
    than pretend, so a misconfigured override never silently routes
    somewhere unexpected.
    """
    settings = get_settings()
    if settings.openrouter_api_key:
        chosen = model or settings.openrouter_model
        logger.info("using OpenRouter model=%s", chosen)
        return OpenRouterProvider(settings.openrouter_api_key, chosen)
    if settings.gemini_api_key:
        return GeminiFlashProvider(settings.gemini_api_key)
    logger.warning("no LLM key set — using FakeProvider (dev mode)")
    return FakeProvider()
