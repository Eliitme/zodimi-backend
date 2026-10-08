"""Room endpoints: who may do what, and what a refusal gives away.

The database-level rules — the accept lock, the merge collision, the
cascades — are proved against a real Postgres by
`scripts/verify-room-lifecycle.sql`. What is left here is everything the
router decides before it gets that far, which is mostly about keeping a
stranger from learning that a room exists.
"""

import uuid

import pytest
from fastapi import Request
from fastapi.testclient import TestClient

import app.api.invitations_router as invitations
import app.api.room_results_router as results
import app.api.rooms_router as rooms
from app.ai import llm_provider
from app.core.auth import require_user
from app.core.config import get_settings
from app.main import app

client = TestClient(app)

OWNER = "11111111-1111-4111-8111-111111111111"
INVITEE = "22222222-2222-4222-8222-222222222222"
STRANGER = "33333333-3333-4333-8333-333333333333"
ROOM = "44444444-4444-4444-8444-444444444444"

PROFILE = {
    "user_id": OWNER,
    "birth_date": "1988-01-02",
    "birth_time": "10:00:00",
    "birth_time_unknown": False,
    "lat": 10.8231,
    "lon": 106.6297,
    "tz_of_birth": "Asia/Ho_Chi_Minh",
    "profile_revision": 1,
}


def member(user_id: str, role: str, consented: bool = True) -> dict:
    return {
        "id": str(uuid.uuid4()),
        "room_id": ROOM,
        "user_id": user_id,
        "role": role,
        "consent_at": "2026-01-01T00:00:00Z" if consented else None,
        "consent_version": "room-consent-v1",
        "profile_revision": 1,
        "revoked_at": None,
    }


#: Column defaults a real insert would apply. Without them the router reads
#: a key the fake never wrote and fails on something the database would have
#: handled.
TABLE_DEFAULTS = {
    "astro_room_members": {"revoked_at": None, "consent_at": None,
                           "consent_version": None, "profile_revision": None},
    "astro_room_invitations": {"revoked_at": None, "accepted_at": None,
                               "accepted_by": None, "attempt_count": 0},
    "astro_rooms": {"status": "pending", "client_request_id": None},
    "astro_room_results": {"status": "queued", "premium_content": None},
}


class FakeTable:
    """Just enough PostgREST to drive a router.

    Filters are recorded and applied on execute, because the routers rely on
    them for scoping — a fake that ignored `.eq()` would make every
    permission test pass for the wrong reason.
    """

    def __init__(self, name: str, store: dict):
        self.name = name
        self.store = store
        self.filters: list[tuple[str, object]] = []
        self.nulls: list[str] = []
        self._pending = None
        self._op = None

    def select(self, *_a, **_kw):
        return self

    def eq(self, column, value):
        self.filters.append((column, value))
        return self

    def in_(self, column, values):
        self.filters.append((column, list(values)))
        return self

    def is_(self, column, _value):
        self.nulls.append(column)
        return self

    def limit(self, *_a, **_kw):
        return self

    def order(self, *_a, **_kw):
        return self

    def insert(self, values):
        self._op, self._pending = "insert", values
        return self

    def upsert(self, values, **_kw):
        self._op, self._pending = "upsert", values
        return self

    def update(self, values):
        self._op, self._pending = "update", values
        return self

    def _rows(self) -> list[dict]:
        rows = self.store.get(self.name, [])
        for column, value in self.filters:
            if isinstance(value, list):
                rows = [r for r in rows if str(r.get(column)) in {str(v) for v in value}]
            else:
                rows = [r for r in rows if str(r.get(column)) == str(value)]
        for column in self.nulls:
            rows = [r for r in rows if r.get(column) is None]
        return rows

    def execute(self):
        if self._op in ("insert", "upsert"):
            row = {"id": str(uuid.uuid4()), "created_at": "2026-01-01T00:00:00Z",
                   **TABLE_DEFAULTS.get(self.name, {}), **self._pending}
            self.store.setdefault(self.name, []).append(row)
            self.store.setdefault("_writes", []).append((self.name, self._op, self._pending))
            return type("R", (), {"data": [row], "count": None})()
        if self._op == "update":
            for row in self._rows():
                row.update(self._pending)
            self.store.setdefault("_writes", []).append((self.name, "update", self._pending))
            return type("R", (), {"data": [], "count": None})()
        rows = self._rows()
        return type("R", (), {"data": rows, "count": len(rows)})()


class FakeService:
    def __init__(self, store: dict):
        self.store = store

    def table(self, name):
        return FakeTable(name, self.store)

    def rpc(self, name, params):
        self.store.setdefault("_rpc", []).append((name, params))
        return type("Q", (), {
            "execute": lambda _self: type("R", (), {
                "data": [{"code": "ok", "room_id": ROOM}]
            })()
        })()


@pytest.fixture
def store(monkeypatch):
    data: dict = {
        "profiles": [PROFILE, {**PROFILE, "user_id": INVITEE}, {**PROFILE, "user_id": STRANGER}],
        "astro_rooms": [{
            "id": ROOM, "owner_user_id": OWNER, "topic": "friendship",
            "status": "pending", "client_request_id": None,
            "created_at": "2026-01-01T00:00:00Z",
        }],
        "astro_room_members": [member(OWNER, "owner")],
        "astro_room_invitations": [],
        "astro_room_results": [],
    }
    service = FakeService(data)
    for module in (rooms, invitations, results):
        monkeypatch.setattr(module, "get_service_client", lambda: service)
    monkeypatch.setenv("ROOM_INVITE_SECRET", "test-secret")
    monkeypatch.setenv("WEB_BASE_URL", "")
    get_settings.cache_clear()
    yield data
    get_settings.cache_clear()


def sign_in_as(user_id: str) -> None:
    def authenticated(request: Request) -> str:
        request.state.user_id = user_id
        request.state.jwt_claims = {}
        return user_id

    app.dependency_overrides[require_user] = authenticated


def teardown_function():
    app.dependency_overrides.clear()


# ------------------------------------------------------------- membership

def test_a_stranger_cannot_tell_a_private_room_from_a_missing_one(store):
    sign_in_as(STRANGER)
    # 404 rather than 403 everywhere: a 403 confirms the room exists, which
    # turns room ids into something worth enumerating.
    assert client.get(f"/v1/rooms/{ROOM}").status_code == 404
    assert client.post(f"/v1/rooms/{ROOM}/invitations", json={}).status_code == 404
    assert client.post(f"/v1/rooms/{ROOM}/preview").status_code == 404
    assert client.get(f"/v1/rooms/{ROOM}/result").status_code == 404
    assert client.post(f"/v1/rooms/{ROOM}/close").status_code == 404


def test_only_the_owner_can_invite_revoke_or_close(store):
    store["astro_room_members"].append(member(INVITEE, "invitee"))
    sign_in_as(INVITEE)
    # The invitee is a member, so these are 403 rather than 404 — they
    # already know the room exists.
    assert client.post(f"/v1/rooms/{ROOM}/invitations", json={}).status_code == 403
    assert client.post(f"/v1/rooms/{ROOM}/invitations/revoke").status_code == 403
    assert client.post(f"/v1/rooms/{ROOM}/close").status_code == 403


def test_a_room_summary_names_no_one(store):
    store["astro_room_members"].append(member(INVITEE, "invitee"))
    sign_in_as(OWNER)
    body = client.get(f"/v1/rooms/{ROOM}").json()
    assert body["member_count"] == 2
    assert body["both_consented"] is True
    # The other person's identity is not in the payload at all.
    rendered = str(body)
    assert INVITEE not in rendered
    for leak in ("birth", "lat", "lon", "email"):
        assert leak not in rendered.lower()


# ------------------------------------------------------------- invitations

def test_an_invite_link_carries_only_an_opaque_token(store):
    sign_in_as(OWNER)
    body = client.post(f"/v1/rooms/{ROOM}/invitations", json={}).json()
    # AC-06. Nothing identifying in the URL, and the stored row holds a hash
    # rather than the token that was handed out.
    token = body["invite_url"].removeprefix("/m/")
    assert body["invite_url"] == f"/m/{token}"
    assert ROOM not in body["invite_url"] and OWNER not in body["invite_url"]
    stored = store["astro_room_invitations"][0]["token_hash"]
    assert token not in stored


def test_reissuing_supersedes_the_previous_link(store):
    sign_in_as(OWNER)
    client.post(f"/v1/rooms/{ROOM}/invitations", json={})
    client.post(f"/v1/rooms/{ROOM}/invitations", json={})
    live = [i for i in store["astro_room_invitations"] if i["revoked_at"] is None]
    assert len(live) == 1, "two working links to one room"


def test_a_room_someone_already_joined_cannot_be_reinvited(store):
    store["astro_rooms"][0]["status"] = "ready"
    sign_in_as(OWNER)
    assert client.post(f"/v1/rooms/{ROOM}/invitations", json={}).status_code == 409


def test_a_malformed_token_never_reaches_the_database(store):
    sign_in_as(INVITEE)
    for bad in ("short", "has spaces here and is quite long indeed!!", "a" * 60):
        response = client.get(f"/v1/invitations/{bad}/preview")
        assert response.status_code == 422, bad
    # A path-traversal attempt does not match the route at all — also a
    # refusal, just a different one.
    assert client.get("/v1/invitations/..%2F..%2Fetc%2Fpasswd/preview").status_code in (404, 422)
    assert "astro_room_invitations" not in {w[0] for w in store.get("_writes", [])}


def test_joining_requires_saying_yes(store):
    sign_in_as(INVITEE)
    token = "a" * 43
    response = client.post(f"/v1/invitations/{token}/accept", json={"consent": False})
    assert response.status_code == 422
    # AC-07: a refusal writes nothing, not even a profile row.
    assert store.get("_rpc") is None
    assert store.get("_writes") is None


# ------------------------------------------------------------- the reading

def test_the_reading_needs_both_people_to_have_agreed(store):
    store["astro_room_members"].append(member(INVITEE, "invitee", consented=False))
    sign_in_as(OWNER)
    # A member, so they are told why rather than shown a 404.
    assert client.post(f"/v1/rooms/{ROOM}/preview").status_code == 403
    assert client.get(f"/v1/rooms/{ROOM}/result").status_code == 403


def test_a_withdrawn_member_closes_the_reading_for_the_other_one_too(store):
    withdrawn = member(INVITEE, "invitee")
    withdrawn["revoked_at"] = "2026-02-01T00:00:00Z"
    store["astro_room_members"].append(withdrawn)
    sign_in_as(OWNER)
    # D8: the reading existed because both agreed, so one withdrawal ends it
    # for both rather than only for the person who left.
    assert client.get(f"/v1/rooms/{ROOM}/result").status_code == 403


def test_the_free_preview_bills_no_model(store, monkeypatch):
    # AC-09, and the reason this endpoint can be offered to everyone a link
    # reaches. Any provider lookup at all is a failure, billed or not.
    def explode():
        raise AssertionError("the free preview reached for a model")

    # conftest clears this cache on teardown, so the stand-in has to keep
    # the attribute it replaces.
    explode.cache_clear = lambda: None
    monkeypatch.setattr(llm_provider, "get_provider", explode)
    store["astro_room_members"].append(member(INVITEE, "invitee"))
    sign_in_as(OWNER)

    body = client.post(f"/v1/rooms/{ROOM}/preview").json()
    assert [i["key"] for i in body["free_insights"]] == [
        "attraction", "communication", "differences"
    ]
    assert body["pair_revision"].startswith("sha256-")
    assert body["premium_report"]["status"] == "locked"
    assert body["share_policy"]["include_birth_data"] is False


def test_the_stored_result_holds_no_birth_data(store):
    store["astro_room_members"].append(member(INVITEE, "invitee"))
    sign_in_as(OWNER)
    client.post(f"/v1/rooms/{ROOM}/preview")
    stored = str(store["astro_room_results"][0])
    # Evidence is aspects between points; the dates that produced them stay
    # in `profiles`, which is the only place they belong.
    for leak in ("1988-01-02", "10:00:00", "10.8231", "106.6297"):
        assert leak not in stored


def test_a_second_preview_does_not_recompute(store):
    store["astro_room_members"].append(member(INVITEE, "invitee"))
    sign_in_as(OWNER)
    first = client.post(f"/v1/rooms/{ROOM}/preview").json()
    second = client.post(f"/v1/rooms/{ROOM}/preview").json()
    assert first == second
    assert len(store["astro_room_results"]) == 1


def test_editing_a_birth_chart_asks_for_a_new_reading_instead_of_serving_the_old_one(store):
    store["astro_room_members"].append(member(INVITEE, "invitee"))
    sign_in_as(OWNER)
    client.post(f"/v1/rooms/{ROOM}/preview")

    # The invitee edits their birth data: their membership records the
    # revision it consented with, so the pair revision moves.
    for row in store["astro_room_members"]:
        if row["user_id"] == INVITEE:
            row["profile_revision"] = 2

    assert client.get(f"/v1/rooms/{ROOM}/result").status_code == 404
    # And the old reading is kept rather than overwritten.
    assert len(store["astro_room_results"]) == 1


def test_reading_is_a_read_and_computing_is_a_write(store):
    # A GET that computed would hide two ephemeris runs behind a verb that
    # promises not to change anything.
    store["astro_room_members"].append(member(INVITEE, "invitee"))
    sign_in_as(OWNER)
    assert client.get(f"/v1/rooms/{ROOM}/result").status_code == 404
    assert store["astro_room_results"] == []


# ------------------------------------------------------------------ rooms

def test_creating_a_room_needs_your_own_birth_details(store):
    store["profiles"] = [{**PROFILE, "birth_date": None}]
    sign_in_as(OWNER)
    response = client.post("/v1/rooms", json={"topic": "friendship"})
    assert response.status_code == 422
    assert len(store["astro_rooms"]) == 1  # the fixture's, not a new one


def test_an_unknown_topic_is_refused(store):
    sign_in_as(OWNER)
    assert client.post("/v1/rooms", json={"topic": "marriage"}).status_code == 422


def test_the_same_request_id_returns_the_same_room(store):
    sign_in_as(OWNER)
    key = str(uuid.uuid4())
    first = client.post("/v1/rooms", json={"topic": "romance", "client_request_id": key})
    second = client.post("/v1/rooms", json={"topic": "romance", "client_request_id": key})
    assert first.json()["id"] == second.json()["id"]
    assert len([r for r in store["astro_rooms"] if r["client_request_id"] == key]) == 1


def test_creating_a_room_records_the_owners_consent(store):
    sign_in_as(OWNER)
    client.post("/v1/rooms", json={"topic": "romance"})
    owner_rows = [
        m for m in store["astro_room_members"]
        if m["role"] == "owner" and m["user_id"] == OWNER
    ]
    # Two now: the fixture's room and the new one. The new one must carry a
    # consent timestamp — a room cannot exist without the membership that
    # says who owns it.
    assert len(owner_rows) == 2
    assert all(row["consent_at"] for row in owner_rows)


def test_leaving_revokes_rather_than_deletes(store):
    store["astro_room_members"].append(member(INVITEE, "invitee"))
    sign_in_as(INVITEE)
    assert client.delete(f"/v1/rooms/{ROOM}/members/me").status_code == 200
    row = next(m for m in store["astro_room_members"] if m["user_id"] == INVITEE)
    # The row stays so the room can say it was left; the timestamp is what
    # closes the reading.
    assert row["revoked_at"] is not None
    assert store["astro_rooms"][0]["status"] == "revoked"


def test_the_reading_says_which_side_of_it_the_reader_is_on(store):
    # `p1` is the owner's point and `p2` the invitee's. Without knowing which
    # they are, "Saturn square Moon" is two different readings — whose Saturn
    # and whose Moon is most of what it means.
    store["astro_room_members"].append(member(INVITEE, "invitee"))

    sign_in_as(OWNER)
    assert client.post(f"/v1/rooms/{ROOM}/preview").json()["viewer_role"] == "owner"

    sign_in_as(INVITEE)
    assert client.get(f"/v1/rooms/{ROOM}/result").json()["viewer_role"] == "invitee"
