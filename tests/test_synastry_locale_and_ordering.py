"""Synastry behaviour that only shows up at the router level.

Two things this endpoint got wrong and must not get wrong again: it read the
content language from the stored profile while every other generated-text
endpoint follows Accept-Language, and it wrote the partner row only after
the model call had already been paid for.
"""

from fastapi import Request
from fastapi.testclient import TestClient

import app.api.synastry_router as synastry
from app.core.auth import require_user
from app.main import app

client = TestClient(app)

PARTNER_BODY = {
    "name": "Mai",
    "birth_date": "1990-04-12",
    "birth_time": "08:30:00",
    "birth_time_unknown": False,
    "lat": 21.0278,
    "lon": 105.8342,
    "tz_of_birth": "Asia/Ho_Chi_Minh",
}

# The caller's own chart comes from their profile; `locale` here is the value
# the endpoint used to trust and must now ignore.
PROFILE = {
    "user_id": "test-user-id",
    "birth_date": "1988-01-02",
    "birth_time": "10:00:00",
    "birth_time_unknown": False,
    "lat": 10.8231,
    "lon": 106.6297,
    "tz_of_birth": "Asia/Ho_Chi_Minh",
    "locale": "vi",
    # Set by a database trigger on every write; the reading records it so a
    # later birth-data edit can be told apart from no edit at all.
    "profile_revision": 1,
}


# The response model parses these as UUIDs.
FAKE_IDS = {
    "partner_profiles": "22222222-2222-2222-2222-222222222222",
    "synastry_results": "33333333-3333-3333-3333-333333333333",
}


class FakeQuery:
    def __init__(self, table, ops):
        self.table = table
        self.ops = ops
        self._insert = None

    def select(self, *_a, **_kw): return self
    def eq(self, *_a, **_kw): return self
    def gte(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self

    def insert(self, values):
        self._insert = values
        return self

    def execute(self):
        if self._insert is not None:
            self.ops.append(f"insert:{self.table}")
            row = {**self._insert, "id": FAKE_IDS[self.table],
                   "created_at": "2026-01-01T00:00:00Z"}
            return type("R", (), {"data": [row], "count": None})()
        if self.table == "profiles":
            return type("R", (), {"data": [PROFILE], "count": None})()
        # synastry_results daily-cap count
        return type("R", (), {"data": [], "count": 0})()


class FakeService:
    def __init__(self, ops):
        self.ops = ops

    def table(self, name):
        return FakeQuery(name, self.ops)


def install(monkeypatch, ops):
    """Stub everything outside the behaviour under test: entitlement, the DB,
    the ephemeris, and the prompt store."""
    monkeypatch.setattr(synastry, "is_premium", lambda _user_id: True)
    monkeypatch.setattr(synastry, "get_service_client", lambda: FakeService(ops))
    monkeypatch.setattr(synastry, "_subject_from", lambda _row: object())
    monkeypatch.setattr(synastry, "build_subject", lambda **_kw: object())
    monkeypatch.setattr(synastry, "calculate_synastry_aspects", lambda _a, _b: [])

    rendered = {}

    def fake_render(key, locale, **variables):
        rendered["key"] = key
        rendered["locale"] = locale
        rendered["variables"] = variables
        ops.append("render")
        return "prompt"

    monkeypatch.setattr(synastry, "render", fake_render)
    # The real dependency stashes these on request.state; an override that
    # only returns the id leaves the handler reading an unset attribute.
    def authenticated(request: Request) -> str:
        request.state.user_id = "test-user-id"
        request.state.jwt_claims = {}
        return "test-user-id"

    app.dependency_overrides[require_user] = authenticated
    return rendered


def teardown_function():
    app.dependency_overrides.clear()


def test_content_language_follows_the_header_not_the_profile(monkeypatch):
    """The account stores locale 'vi', but this reader is on an English
    device. Keying off the profile gave them Vietnamese synastry alongside
    English tarot in the same session."""
    ops: list[str] = []
    rendered = install(monkeypatch, ops)

    response = client.post(
        "/v1/synastry", json=PARTNER_BODY, headers={"Accept-Language": "en"}
    )

    assert response.status_code == 201
    assert rendered["locale"] == "en"
    assert "## Overview" in rendered["variables"]["sections"]
    assert "Tổng quan" not in rendered["variables"]["sections"]


def test_vietnamese_header_still_gets_vietnamese_sections(monkeypatch):
    ops: list[str] = []
    rendered = install(monkeypatch, ops)

    response = client.post(
        "/v1/synastry", json=PARTNER_BODY, headers={"Accept-Language": "vi"}
    )

    assert response.status_code == 201
    assert rendered["locale"] == "vi"
    assert "## Tổng quan" in rendered["variables"]["sections"]


def test_partner_is_stored_before_the_model_is_paid_for(monkeypatch):
    """An insert that fails after the reading exists has already spent the
    money and has nowhere to put the result."""
    ops: list[str] = []
    install(monkeypatch, ops)

    assert client.post("/v1/synastry", json=PARTNER_BODY).status_code == 201
    assert ops.index("insert:partner_profiles") < ops.index("render")


# ---------------------------------------------------------------- staleness

STORED_ASPECTS = [
    {"p1": "sun", "p2": "moon", "type": "trine", "orb": 2.1},
    {"p1": "venus", "p2": "mars", "type": "square", "orb": 4.3},
]


def _stored_result(revision: int | None, aspects: list | None):
    return {
        "id": FAKE_IDS["synastry_results"],
        "owner_user_id": "test-user-id",
        "content": "## Tổng quan\ntext",
        "created_at": "2026-01-01T00:00:00Z",
        "owner_profile_revision": revision,
        "inter_aspects": aspects,
        "partner_profiles": {
            "name": "Mai", "birth_date": "1990-04-12", "birth_time": "08:30:00",
            "birth_time_unknown": False, "lat": 21.0278, "lon": 105.8342,
            "tz_of_birth": "Asia/Ho_Chi_Minh",
        },
    }


def _install_read(monkeypatch, stored, profile_revision):
    """Reading path only: the stored result row and the owner's profile."""
    profile = {**PROFILE, "profile_revision": profile_revision}

    class ReadQuery(FakeQuery):
        def execute(self):
            if self.table == "profiles":
                return type("R", (), {"data": [profile], "count": None})()
            return type("R", (), {"data": [stored], "count": None})()

    class ReadService:
        def table(self, name):
            return ReadQuery(name, [])

    monkeypatch.setattr(synastry, "get_service_client", lambda: ReadService())
    # Would be called only on the legacy recompute path; make that visible.
    monkeypatch.setattr(synastry, "_subject_from", lambda _row: object())
    monkeypatch.setattr(
        synastry, "calculate_synastry_aspects",
        lambda _a, _b: [{"p1": "sun", "p2": "saturn", "type": "opposition", "orb": 1.0}],
    )

    def authenticated(request: Request) -> str:
        request.state.user_id = "test-user-id"
        request.state.jwt_claims = {}
        return "test-user-id"

    app.dependency_overrides[require_user] = authenticated


def test_the_reading_records_the_chart_it_was_written_for(monkeypatch):
    """Without this the row cannot answer "which chart?" at all, and every
    staleness check below has nothing to compare."""
    ops: list[str] = []
    install(monkeypatch, ops)
    captured = {}
    original = FakeQuery.insert

    def capture(self, values):
        if self.table == "synastry_results":
            captured.update(values)
        return original(self, values)

    monkeypatch.setattr(FakeQuery, "insert", capture)

    assert client.post("/v1/synastry", json=PARTNER_BODY).status_code == 201
    assert captured["owner_profile_revision"] == PROFILE["profile_revision"]
    assert captured["inter_aspects"] == []


def test_a_reading_returns_the_aspects_it_was_written_against(monkeypatch):
    """The regression. Recomputing here let an edited birth time move the
    evidence while the prose stayed, so the page argued with itself."""
    _install_read(monkeypatch, _stored_result(1, STORED_ASPECTS), profile_revision=1)

    body = client.get(f"/v1/synastry/{FAKE_IDS['synastry_results']}").json()

    assert [a["type"] for a in body["inter_aspects"]] == ["trine", "square"]
    assert body["is_stale"] is False


def test_an_edited_birth_chart_marks_the_reading_stale_without_moving_it(monkeypatch):
    # Same stored row, owner has since edited their birth data.
    _install_read(monkeypatch, _stored_result(1, STORED_ASPECTS), profile_revision=2)

    body = client.get(f"/v1/synastry/{FAKE_IDS['synastry_results']}").json()

    assert body["is_stale"] is True
    # Flagged, not rewritten: the reading still shows what it was written for.
    assert [a["type"] for a in body["inter_aspects"]] == ["trine", "square"]


def test_a_row_with_no_stored_aspects_falls_back_and_says_so(monkeypatch):
    """Rows from before the snapshot existed are the one case where prose and
    evidence genuinely cannot be reconciled, so they are always stale."""
    _install_read(monkeypatch, _stored_result(None, None), profile_revision=1)

    body = client.get(f"/v1/synastry/{FAKE_IDS['synastry_results']}").json()

    assert body["is_stale"] is True
    assert [a["type"] for a in body["inter_aspects"]] == ["opposition"]
