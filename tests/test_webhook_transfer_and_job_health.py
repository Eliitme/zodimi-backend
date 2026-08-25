"""Two silent-failure modes in the background half of the system.

A transferred subscription used to leave the account it moved away from
entitled forever, and the report worker wrote a heartbeat that no health
check ever read — so when it died, reports simply queued up.
"""

from datetime import UTC, datetime, timedelta

import app.api.iap_webhook_router as webhook
import app.jobs.job_health_check as health


class RecordingProfiles:
    def __init__(self, updates):
        self.updates = updates
        self._values = None

    def update(self, values):
        self._values = values
        return self

    def eq(self, _column, value):
        self.updates.append((value, self._values))
        return self

    def execute(self):
        return type("R", (), {"data": []})()


class RecordingEvents:
    """`iap_events` reads, so the per-loser ordering guard has something to
    answer. `history` maps user id → the newest event timestamp we hold."""

    def __init__(self, history):
        self.history = history
        self._user_id = None

    def select(self, *_a, **_kw): return self
    def order(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self

    def eq(self, _column, value):
        self._user_id = value
        return self

    def execute(self):
        stamp = self.history.get(self._user_id)
        rows = [{"event_timestamp": stamp}] if stamp else []
        return type("R", (), {"data": rows})()


class RecordingService:
    def __init__(self, history=None):
        self.updates: list[tuple] = []
        self.history = history or {}

    def table(self, name):
        if name == "iap_events":
            return RecordingEvents(self.history)
        return RecordingProfiles(self.updates)


def transfer_event(transferred_from):
    return {"event": {"type": "TRANSFER", "transferred_from": transferred_from}}


LOSER = "44444444-4444-4444-4444-444444444444"
TRANSFER_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def test_transfer_revokes_the_account_it_moved_away_from():
    """One purchase must not leave two accounts permanently entitled."""
    service = RecordingService()
    revoked = webhook._revoke_transferred_from(service, transfer_event([LOSER]), "evt-1", TRANSFER_AT)
    assert revoked is True
    assert service.updates == [(LOSER, {"is_premium": False})]


def test_transfer_never_grants_to_the_receiving_account():
    """The new owner is entitled by its own verified purchase event; granting
    from a webhook field would mean the payload can hand out premium."""
    service = RecordingService()
    payload = transfer_event([LOSER])
    payload["event"]["transferred_to"] = ["55555555-5555-5555-5555-555555555555"]
    webhook._revoke_transferred_from(service, payload, "evt-2", TRANSFER_AT)
    assert [values for _id, values in service.updates] == [{"is_premium": False}]


def test_transfer_ignores_anonymous_revenuecat_ids():
    """RC sends "$RCAnonymousID:..." before the app aliases a user; those
    never matched a profile and must not reach the uuid column."""
    service = RecordingService()
    revoked = webhook._revoke_transferred_from(
        service, transfer_event(["$RCAnonymousID:abc"]), "evt-3", TRANSFER_AT
    )
    assert revoked is False
    assert service.updates == []


def test_transfer_without_the_field_changes_nothing():
    service = RecordingService()
    assert webhook._revoke_transferred_from(service, {"event": {}}, "evt-4", TRANSFER_AT) is False
    assert service.updates == []


def test_event_ordering_compares_instants_not_strings():
    """The two sides come from different formatters; a lexical comparison
    only happens to work while they agree on fractional seconds."""
    older = webhook._parsed("2026-01-01T10:00:00+00:00")
    newer = webhook._parsed("2026-01-01T10:00:00.500000+00:00")
    assert newer > older
    assert webhook._parsed("2026-01-01T10:00:00").tzinfo is not None


def test_report_worker_is_monitored():
    """It was writing a heartbeat nobody read: the watchdog only rescues rows
    already claimed into `generating`, so a dead worker left `queued` rows
    untouched and unreported."""
    assert "natal_report_worker" in health.MAX_SILENCE


def test_overdue_reports_a_silent_worker(monkeypatch):
    long_ago = (datetime.now(UTC) - timedelta(hours=3)).isoformat()
    rows = [{"job_name": "natal_report_worker", "last_success_at": long_ago}]
    monkeypatch.setattr(
        health, "get_service_client",
        lambda: type("S", (), {
            "table": lambda _self, _n: type("T", (), {
                "select": lambda _s, *_a: type("Q", (), {
                    "execute": lambda _q: type("R", (), {"data": rows})()
                })()
            })()
        })(),
    )
    problems = health.overdue_jobs()
    assert any("natal_report_worker" in p for p in problems)


def test_naive_timestamp_does_not_crash_the_monitor(monkeypatch):
    """The thing that reports jobs dying must not be the fragile part."""
    naive = (datetime.now(UTC) - timedelta(minutes=1)).replace(tzinfo=None).isoformat()
    rows = [{"job_name": "natal_report_worker", "last_success_at": naive}]
    monkeypatch.setattr(
        health, "get_service_client",
        lambda: type("S", (), {
            "table": lambda _self, _n: type("T", (), {
                "select": lambda _s, *_a: type("Q", (), {
                    "execute": lambda _q: type("R", (), {"data": rows})()
                })()
            })()
        })(),
    )
    problems = health.overdue_jobs()
    assert not any("natal_report_worker" in p for p in problems)


def test_transfer_still_revokes_when_the_new_owner_is_a_stranger():
    """A transfer is addressed to whoever GAINS the subscription, and that
    account may be one we have never seen. Dropping the event on that basis
    left the losing side — an account we do know — entitled forever."""
    service = RecordingService()
    revoked = webhook._revoke_transferred_from(
        service, transfer_event([LOSER]), "evt-5", TRANSFER_AT
    )
    assert revoked is True
    assert service.updates == [(LOSER, {"is_premium": False})]


def test_a_late_transfer_cannot_strip_a_resubscription():
    """Transfer away, then buy your own. Delivery order is not guaranteed, so
    the transfer can land after the new purchase — and must not revoke it."""
    later_purchase = (TRANSFER_AT + timedelta(days=1)).isoformat()
    service = RecordingService(history={LOSER: later_purchase})

    revoked = webhook._revoke_transferred_from(
        service, transfer_event([LOSER]), "evt-6", TRANSFER_AT
    )

    assert revoked is False
    assert service.updates == []


def test_an_older_history_does_not_block_the_revoke():
    earlier = (TRANSFER_AT - timedelta(days=1)).isoformat()
    service = RecordingService(history={LOSER: earlier})

    assert webhook._revoke_transferred_from(
        service, transfer_event([LOSER]), "evt-7", TRANSFER_AT
    ) is True


def test_one_stale_loser_does_not_stop_the_others():
    other = "66666666-6666-6666-6666-666666666666"
    service = RecordingService(
        history={LOSER: (TRANSFER_AT + timedelta(days=1)).isoformat()}
    )

    revoked = webhook._revoke_transferred_from(
        service, transfer_event([LOSER, other]), "evt-8", TRANSFER_AT
    )

    assert revoked is True
    assert service.updates == [(other, {"is_premium": False})]


# --- endpoint level: the gap was in the handler's routing, not the helper ---

class FakeInsertable:
    def __init__(self, name, service):
        self.name = name
        self.service = service
        self._insert = None
        self._user_id = None

    def select(self, *_a, **_kw): return self
    def order(self, *_a, **_kw): return self
    def limit(self, *_a, **_kw): return self

    def eq(self, _column, value):
        self._user_id = value
        return self

    def insert(self, values):
        self._insert = values
        return self

    def update(self, values):
        self._insert = None
        self._update = values
        return self

    def execute(self):
        if self._insert is not None:
            self.service.inserted.append((self.name, self._insert))
            return type("R", (), {"data": [self._insert]})()
        if getattr(self, "_update", None) is not None:
            self.service.updates.append((self._user_id, self._update))
            return type("R", (), {"data": []})()
        if self.name == "profiles":
            known = self._user_id in self.service.profiles
            return type("R", (), {"data": [{"user_id": self._user_id}] if known else []})()
        stamp = self.service.history.get(self._user_id)
        return type("R", (), {"data": [{"event_timestamp": stamp}] if stamp else []})()


class FakeWebhookService:
    def __init__(self, profiles=(), history=None):
        self.profiles = set(profiles)
        self.history = history or {}
        self.inserted: list[tuple] = []
        self.updates: list[tuple] = []

    def table(self, name):
        return FakeInsertable(name, self)


SECRET = "webhook-secret"
STRANGER = "77777777-7777-7777-7777-777777777777"


def post_transfer(monkeypatch, service, app_user_id):
    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr(
        webhook, "get_settings",
        lambda: type("S", (), {"revenuecat_webhook_secret": SECRET})(),
    )
    monkeypatch.setattr(webhook, "get_service_client", lambda: service)
    return TestClient(app).post(
        "/v1/iap/revenuecat-webhook",
        headers={"Authorization": SECRET},
        json={"event": {
            "id": "evt-transfer",
            "type": "TRANSFER",
            "app_user_id": app_user_id,
            "event_timestamp_ms": int(TRANSFER_AT.timestamp() * 1000),
            "transferred_from": [LOSER],
            "transferred_to": [app_user_id],
        }},
    )


def test_endpoint_revokes_even_when_the_recipient_is_unknown(monkeypatch):
    """The whole point of the fix: the handler used to drop this event at the
    profile lookup, because the recipient is the subject and the recipient is
    a stranger — while the account being revoked was one of ours all along."""
    service = FakeWebhookService(profiles={LOSER})

    response = post_transfer(monkeypatch, service, STRANGER)

    assert response.json() == {"status": "applied"}
    assert service.updates == [(LOSER, {"is_premium": False})]


def test_endpoint_revokes_when_the_recipient_id_is_not_even_a_uuid(monkeypatch):
    """RevenueCat uses "$RCAnonymousID:..." until the app aliases the user."""
    service = FakeWebhookService(profiles={LOSER})

    response = post_transfer(monkeypatch, service, "$RCAnonymousID:abc123")

    assert response.json() == {"status": "applied"}
    assert service.updates == [(LOSER, {"is_premium": False})]


def test_unplaceable_event_is_stored_rather_than_thrown_away(monkeypatch):
    """It cannot move an entitlement, but the row is what makes a redelivery
    idempotent — and the column is nullable for exactly this case."""
    service = FakeWebhookService(profiles={LOSER})

    post_transfer(monkeypatch, service, STRANGER)

    table, row = service.inserted[0]
    assert table == "iap_events"
    assert row["user_id"] is None
    assert row["revenuecat_event_id"] == "evt-transfer"
