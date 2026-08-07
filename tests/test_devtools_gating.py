"""The DEV_TOOLS flag is the entire security boundary around the browser
console: its helpers grant premium on demand, so shipping them reachable
would be a free-premium switch. These tests pin that they are absent by
default (the flag defaults to false and .env.example ships false).
"""

import importlib

from fastapi.testclient import TestClient

import app.main
from app.core.config import get_settings


def client_with_dev_tools(enabled: bool, monkeypatch) -> TestClient:
    monkeypatch.setenv("DEV_TOOLS", "true" if enabled else "false")
    get_settings.cache_clear()
    importlib.reload(app.main)
    return TestClient(app.main.app)


def test_console_and_helpers_absent_by_default(monkeypatch):
    client = client_with_dev_tools(False, monkeypatch)
    assert client.get("/devtools/").status_code == 404
    assert client.get("/v1/dev/config").status_code == 404
    # Helper routes must not even be registered — a 401 here would mean the
    # route exists and only auth stands between an attacker and premium.
    assert client.get("/v1/dev/state").status_code == 404
    assert client.post("/v1/dev/premium", json={"is_premium": True}).status_code == 404


def test_normal_api_unaffected_when_disabled(monkeypatch):
    client = client_with_dev_tools(False, monkeypatch)
    assert client.get("/health").status_code == 200
    assert client.post("/v1/tarot/draw").status_code == 401  # auth still guards


def test_helpers_require_auth_when_enabled(monkeypatch):
    client = client_with_dev_tools(True, monkeypatch)
    assert client.get("/v1/dev/config").status_code == 200      # public bootstrap
    assert client.get("/v1/dev/state").status_code == 401       # but state needs a JWT
    assert client.post("/v1/dev/premium", json={"is_premium": True}).status_code == 401


def teardown_module() -> None:
    # Leave the module in its default state for the rest of the suite.
    get_settings.cache_clear()
    importlib.reload(app.main)
