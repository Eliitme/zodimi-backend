"""Phase-1 smoke tests: /health is public, everything under /v1 needs a JWT."""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_200_without_token():
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_v1_endpoint_without_token_returns_401():
    resp = client.get("/v1/me")
    assert resp.status_code == 401


def test_v1_endpoint_with_garbage_token_returns_401():
    resp = client.get("/v1/me", headers={"Authorization": "Bearer not-a-jwt"})
    assert resp.status_code == 401
