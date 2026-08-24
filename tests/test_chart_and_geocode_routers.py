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


def test_reports_require_auth():
    assert client.post("/v1/reports/natal").status_code == 401
    assert client.get(
        "/v1/reports/natal/00000000-0000-0000-0000-000000000000"
    ).status_code == 401
    assert client.get("/v1/reports/natal/latest").status_code == 401


def test_tarot_requires_auth():
    assert client.post("/v1/tarot/draw").status_code == 401
    assert client.post("/v1/tarot/ask", json={"question": "hi"}).status_code == 401


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


def test_natal_chart_is_rate_limited():
    """Swiss Ephemeris is the priciest thing this API does per request and a
    guest account is free to create, so an unlimited chart endpoint is one
    script away from saturating the container's CPU."""
    from app.api.charts_router import CHART_LIMIT

    override_auth()
    try:
        for _ in range(CHART_LIMIT.max_calls):
            assert client.post("/v1/charts/natal", json=NATAL_BODY).status_code == 200
        assert client.post("/v1/charts/natal", json=NATAL_BODY).status_code == 429
    finally:
        clear_overrides()


def test_one_request_builds_the_subject_once(monkeypatch):
    """The handler needs the subject twice — for the chart and for its
    aspects — and used to build it twice, running the ephemeris twice for a
    single call."""
    import app.api.charts_router as charts

    builds = []
    real_build = charts.build_subject

    def counting_build(*args, **kwargs):
        builds.append(1)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(charts, "build_subject", counting_build)
    override_auth()
    try:
        assert client.post("/v1/charts/natal", json=NATAL_BODY).status_code == 200
    finally:
        clear_overrides()
    assert len(builds) == 1
