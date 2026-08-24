"""The suite must never reach a paid API.

Provider selection reads the ambient .env, so a developer with a real
OPENROUTER_API_KEY silently turned the offline tests into live ones:
test_report_content_contract generated two full ten-section reports through
Claude on every run — 20 billed calls, three and a half minutes, and a
non-deterministic failure. The tests had always assumed FakeProvider without
ever pinning it.

Blanking the keys here makes that assumption explicit and unconditional. A
test that genuinely wants a real provider must construct it itself, which is
visible in review rather than dependent on whose machine it runs on.
"""

import pytest

from app.ai import llm_provider
from app.core import rate_limit
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def force_offline_llm(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "")
    monkeypatch.setenv("OPENROUTER_REPORT_MODEL", "")
    monkeypatch.setenv("GEMINI_API_KEY", "")
    # Both are lru_cached, so a value read before this fixture ran would
    # otherwise survive into the test.
    get_settings.cache_clear()
    llm_provider.get_provider.cache_clear()
    yield
    get_settings.cache_clear()
    llm_provider.get_provider.cache_clear()


@pytest.fixture(autouse=True)
def clean_rate_limit_windows():
    """Rate-limit state is module-level and in-process, so one test that
    exhausts a bucket would 429 the next one that happens to share a key.
    Endpoints started carrying limits beyond geocode, which is when this
    stopped being a detail of a single test file."""
    rate_limit.reset()
    yield
    rate_limit.reset()
