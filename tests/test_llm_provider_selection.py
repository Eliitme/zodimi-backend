"""Provider selection and the OpenRouter adapter.

No network: the OpenAI client is replaced with a stub. What matters here is
the wiring — which provider gets chosen, and whether text survives the
adapter intact. Model behaviour is a separate, manual gate.
"""

from types import SimpleNamespace

import pytest

from app.ai import llm_provider
from app.ai.llm_provider import (
    FakeProvider,
    LLMUnavailableError,
    OpenRouterProvider,
    get_provider,
)


def _settings(**overrides):
    base = {"openrouter_api_key": "", "openrouter_model": "m", "gemini_api_key": ""}
    return SimpleNamespace(**{**base, **overrides})


class _StubCompletions:
    """Stands in for client.chat.completions."""

    def __init__(self, text="", chunks=None, fail_times=0):
        self.text = text
        self.chunks = chunks or []
        self.fail_times = fail_times
        self.calls = 0
        self.last_kwargs = None

    def create(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        if self.calls <= self.fail_times:
            raise RuntimeError("upstream 503")
        if kwargs.get("stream"):
            return iter(
                SimpleNamespace(
                    choices=[SimpleNamespace(delta=SimpleNamespace(content=c))]
                )
                for c in self.chunks
            )
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=self.text))],
            usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2),
        )


def _provider_with(stub, model="anthropic/claude-sonnet-4.5"):
    provider = OpenRouterProvider.__new__(OpenRouterProvider)
    provider._model = model
    provider._client = SimpleNamespace(chat=SimpleNamespace(completions=stub))
    return provider


class TestSelection:
    def test_falls_back_to_fake_with_no_keys(self, monkeypatch):
        monkeypatch.setattr(llm_provider, "get_settings", lambda: _settings())
        assert isinstance(get_provider(), FakeProvider)

    def test_openrouter_wins_over_gemini(self, monkeypatch):
        # Both keys present is the realistic migration state; OpenRouter is
        # documented as taking precedence, so assert it rather than trust it.
        monkeypatch.setattr(
            llm_provider,
            "get_settings",
            lambda: _settings(openrouter_api_key="k", gemini_api_key="g"),
        )
        created = {}
        monkeypatch.setattr(
            llm_provider,
            "OpenRouterProvider",
            lambda key, model: created.update(key=key, model=model) or "chosen",
        )
        assert get_provider() == "chosen"
        assert created == {"key": "k", "model": "m"}

    def test_model_comes_from_settings(self, monkeypatch):
        # Switching model must be an env change, not a code change.
        monkeypatch.setattr(
            llm_provider,
            "get_settings",
            lambda: _settings(openrouter_api_key="k", openrouter_model="meta/llama-4"),
        )
        seen = {}
        monkeypatch.setattr(
            llm_provider,
            "OpenRouterProvider",
            lambda key, model: seen.update(model=model) or "p",
        )
        get_provider()
        assert seen["model"] == "meta/llama-4"


class TestOpenRouterAdapter:
    def test_generate_returns_content_and_sends_both_roles(self):
        stub = _StubCompletions(text="Mặt Trời Cự Giải")
        assert _provider_with(stub).generate("sys", "hỏi") == "Mặt Trời Cự Giải"
        roles = [m["role"] for m in stub.last_kwargs["messages"]]
        assert roles == ["system", "user"]

    def test_generate_preserves_newlines(self):
        # The report contract splits on "\n## "; an adapter that flattened
        # newlines would silently produce a one-section report. This is also
        # the shape that broke SSE framing once already.
        text = "## Phần một\n\nNội dung.\n\n## Phần hai\n\nNội dung."
        assert _provider_with(_StubCompletions(text=text)).generate("s", "p") == text

    def test_generate_retries_then_succeeds(self, monkeypatch):
        monkeypatch.setattr(llm_provider.time, "sleep", lambda _: None)
        stub = _StubCompletions(text="ok", fail_times=2)
        assert _provider_with(stub).generate("s", "p") == "ok"
        assert stub.calls == 3

    def test_generate_raises_after_exhausting_retries(self, monkeypatch):
        monkeypatch.setattr(llm_provider.time, "sleep", lambda _: None)
        stub = _StubCompletions(fail_times=99)
        with pytest.raises(LLMUnavailableError):
            _provider_with(stub).generate("s", "p")
        assert stub.calls == llm_provider.MAX_RETRIES + 1

    def test_stream_yields_text_and_skips_empty_deltas(self):
        # Real streams interleave empty deltas (role-only first chunk, and
        # keep-alives); forwarding them would emit stray empty SSE events.
        stub = _StubCompletions(chunks=["Bạn ", None, "là ", "", "Cự Giải"])
        out = list(_provider_with(stub).stream("s", "p"))
        assert "".join(out) == "Bạn là Cự Giải"

    def test_stream_survives_multiline_chunks(self):
        stub = _StubCompletions(chunks=["dòng một\n", "\ndòng hai"])
        assert "".join(_provider_with(stub).stream("s", "p")) == "dòng một\n\ndòng hai"

    def test_stream_wraps_transport_errors(self):
        stub = _StubCompletions(fail_times=1)
        with pytest.raises(LLMUnavailableError):
            list(_provider_with(stub).stream("s", "p"))

class TestPerEndpointModel:
    """Interactive endpoints and the background report worker do not want the
    same trade-off, so the model is selectable per call."""

    def test_override_picks_that_model(self, monkeypatch):
        monkeypatch.setattr(
            llm_provider, "get_settings",
            lambda: _settings(openrouter_api_key="k", openrouter_model="fast/one"),
        )
        seen = {}
        monkeypatch.setattr(
            llm_provider, "OpenRouterProvider",
            lambda key, model: seen.setdefault("model", model),
        )
        get_provider("slow/better")
        assert seen["model"] == "slow/better"

    def test_no_override_uses_the_default_model(self, monkeypatch):
        monkeypatch.setattr(
            llm_provider, "get_settings",
            lambda: _settings(openrouter_api_key="k", openrouter_model="fast/one"),
        )
        seen = {}
        monkeypatch.setattr(
            llm_provider, "OpenRouterProvider",
            lambda key, model: seen.setdefault("model", model),
        )
        get_provider()
        assert seen["model"] == "fast/one"

    def test_override_is_ignored_without_openrouter(self, monkeypatch):
        # Gemini and the fake serve one model each. Honouring an override
        # would claim a routing that cannot happen.
        monkeypatch.setattr(llm_provider, "get_settings", lambda: _settings())
        assert isinstance(get_provider("anthropic/claude-sonnet-4.5"), FakeProvider)

    def test_each_model_gets_its_own_cached_provider(self, monkeypatch):
        monkeypatch.setattr(
            llm_provider, "get_settings",
            lambda: _settings(openrouter_api_key="k", openrouter_model="fast/one"),
        )
        monkeypatch.setattr(
            llm_provider, "OpenRouterProvider",
            lambda key, model: f"provider:{model}",
        )
        assert get_provider() != get_provider("slow/better")
        assert get_provider("slow/better") == get_provider("slow/better")
