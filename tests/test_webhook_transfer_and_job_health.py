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


class RecordingService:
    def __init__(self):
        self.updates: list[tuple] = []

    def table(self, _name):
        return RecordingProfiles(self.updates)


def transfer_event(transferred_from):
    return {"event": {"type": "TRANSFER", "transferred_from": transferred_from}}


LOSER = "44444444-4444-4444-4444-444444444444"


def test_transfer_revokes_the_account_it_moved_away_from():
    """One purchase must not leave two accounts permanently entitled."""
    service = RecordingService()
    revoked = webhook._revoke_transferred_from(service, transfer_event([LOSER]), "evt-1")
    assert revoked is True
    assert service.updates == [(LOSER, {"is_premium": False})]


def test_transfer_never_grants_to_the_receiving_account():
    """The new owner is entitled by its own verified purchase event; granting
    from a webhook field would mean the payload can hand out premium."""
    service = RecordingService()
    payload = transfer_event([LOSER])
    payload["event"]["transferred_to"] = ["55555555-5555-5555-5555-555555555555"]
    webhook._revoke_transferred_from(service, payload, "evt-2")
    assert [values for _id, values in service.updates] == [{"is_premium": False}]


def test_transfer_ignores_anonymous_revenuecat_ids():
    """RC sends "$RCAnonymousID:..." before the app aliases a user; those
    never matched a profile and must not reach the uuid column."""
    service = RecordingService()
    revoked = webhook._revoke_transferred_from(
        service, transfer_event(["$RCAnonymousID:abc"]), "evt-3"
    )
    assert revoked is False
    assert service.updates == []


def test_transfer_without_the_field_changes_nothing():
    service = RecordingService()
    assert webhook._revoke_transferred_from(service, {"event": {}}, "evt-4") is False
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
