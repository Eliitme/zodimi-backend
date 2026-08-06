"""Router-level tests: auth enforcement + contract shape (astro math itself
is covered by test_chart_calculator.py)."""

from fastapi.testclient import TestClient

from app.core.auth import require_user
from app.main import app

client = TestClient(app)

NATAL_BODY = {
    "birth_date": "1955-02-24",
    "birth_time": "19:15:00",
    "birth_time_unknown": False,
    "lat": 37.7749,
    "lon": -122.4194,
    "tz_of_birth": "America/Los_Angeles",
}


def override_auth():
    app.dependency_overrides[require_user] = lambda: "test-user-id"


def clear_overrides():
    app.dependency_overrides.clear()


def test_natal_chart_requires_auth():
    assert client.post("/v1/charts/natal", json=NATAL_BODY).status_code == 401


def test_geocode_requires_auth():
    assert client.post("/v1/geocode", json={"place_text": "Hanoi"}).status_code == 401


def test_natal_chart_contract():
    override_auth()
    try:
        resp = client.post("/v1/charts/natal", json=NATAL_BODY)
        assert resp.status_code == 200
        data = resp.json()
        assert {p["name"] for p in data["planets"]} == {
            "sun", "moon", "mercury", "venus", "mars", "jupiter", "saturn",
            "uranus", "neptune", "pluto", "chiron", "north_node",
        }
        assert len(data["houses"]) == 12
        assert data["angles"]["asc"]["sign"] == "virgo"
        assert data["time_unknown"] is False
        assert data["aspects"], "aspects must be populated"
    finally:
        clear_overrides()


def test_dst_ambiguous_birth_time_succeeds():
    """Fall-back hour (occurs twice on the clock) must not 500 — resolves to
    standard time per the documented is_dst=False convention."""
    override_auth()
    try:
        body = {**NATAL_BODY, "birth_date": "2025-11-02",
                "birth_time": "01:30:00", "lat": 40.7, "lon": -74.0,
                "tz_of_birth": "America/New_York"}
        assert client.post("/v1/charts/natal", json=body).status_code == 200
    finally:
        clear_overrides()


def test_birth_year_outside_chiron_ephemeris_rejected():
    """Bundled Chiron data covers 1800-2399; outside → clean 422, not 500."""
    override_auth()
    try:
        for bad_date in ("1799-12-31", "2400-01-01"):
            resp = client.post(
                "/v1/charts/natal", json={**NATAL_BODY, "birth_date": bad_date}
            )
            assert resp.status_code == 422, bad_date
    finally:
        clear_overrides()


def test_missing_birth_time_marks_chart_time_unknown():
    """birth_time=None with unknown=False must still flag the noon fallback."""
    override_auth()
    try:
        body = {**NATAL_BODY, "birth_time": None, "birth_time_unknown": False}
        resp = client.post("/v1/charts/natal", json=body)
        assert resp.status_code == 200
        assert resp.json()["time_unknown"] is True
    finally:
        clear_overrides()


def test_natal_chart_rejects_unknown_timezone():
    override_auth()
    try:
        bad = {**NATAL_BODY, "tz_of_birth": "Mars/Olympus_Mons"}
        assert client.post("/v1/charts/natal", json=bad).status_code == 422
    finally:
        clear_overrides()


def test_geocode_returns_empty_list_when_not_found(monkeypatch):
    import app.api.geocode_router as gr

    override_auth()
    monkeypatch.setattr(gr, "geocode_place", lambda text: ())
    try:
        resp = client.post("/v1/geocode", json={"place_text": "xyzzy nowhere"})
        assert resp.status_code == 200
        assert resp.json() == {"results": []}
    finally:
        clear_overrides()
