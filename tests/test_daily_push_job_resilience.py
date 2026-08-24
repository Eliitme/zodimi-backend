"""Daily-push job behaviour that the pure tz-bucket tests can't cover:
paging past PostgREST's row cap, dead-token reaping, and one bad device not
taking down the rest of the hour's bucket.
"""

from datetime import UTC, datetime

import app.jobs.daily_push_job as job
from app.push.apns_client import PushResult


class FakeTable:
    """Minimal PostgREST-shaped stub recording what the job did."""

    def __init__(self, store):
        self.store = store
        self._range = None
        self._deleting = False
        self._update = None
        self._id = None

    def select(self, *_args, **_kwargs): return self
    def order(self, *_args, **_kwargs): return self

    def in_(self, column, values):
        # Records the narrowing the job asks for; the fake still returns
        # everything, so tests keep exercising paging rather than the filter.
        self.store.setdefault("filtered", []).append((column, values))
        return self

    def eq(self, _col, value):
        self._id = value
        return self

    def range(self, start, end):
        self._range = (start, end)
        return self

    def delete(self):
        self._deleting = True
        return self

    def update(self, values):
        self._update = values
        return self

    def execute(self):
        if self._deleting:
            self.store["deleted"].append(self._id)
            return type("R", (), {"data": []})()
        if self._update is not None:
            self.store["updated"].append((self._id, self._update))
            return type("R", (), {"data": []})()
        start, end = self._range
        return type("R", (), {"data": self.store["devices"][start:end + 1]})()


class FakeService:
    def __init__(self, devices):
        self.store = {"devices": devices, "deleted": [], "updated": []}

    def table(self, _name):
        return FakeTable(self.store)


def make_devices(count, tz="Asia/Ho_Chi_Minh"):
    return [
        {"id": i, "apns_token": f"tok{i}", "device_timezone": tz,
         "locale": "vi", "last_push_date": None}
        for i in range(count)
    ]


def at_push_hour():
    # 01:00 UTC == 08:00 in Asia/Ho_Chi_Minh
    return datetime(2026, 8, 7, 1, 0, tzinfo=UTC)


def test_reads_every_device_past_the_row_cap(monkeypatch):
    """A single unpaged select truncates at max_rows, leaving devices beyond
    it never pushed."""
    service = FakeService(make_devices(2500))
    monkeypatch.setattr(job, "get_service_client", lambda: service)
    monkeypatch.setattr(job, "send_batch_sync",
                        lambda msgs: [PushResult(delivered=True) for _ in msgs])
    monkeypatch.setattr(job, "record_success", lambda _n: None)

    assert job.run(at_push_hour()) == 2500


def test_dead_tokens_are_deleted_not_marked(monkeypatch):
    service = FakeService(make_devices(3))
    monkeypatch.setattr(job, "get_service_client", lambda: service)
    monkeypatch.setattr(job, "send_batch_sync", lambda msgs: [
        PushResult(delivered=True),
        PushResult(delivered=False, reason="Unregistered"),
        PushResult(delivered=False),  # transient
    ])
    monkeypatch.setattr(job, "record_success", lambda _n: None)

    sent = job.run(at_push_hour())
    assert sent == 1
    assert service.store["deleted"] == [1]           # dead token reaped
    assert [i for i, _ in service.store["updated"]] == [0, 2]  # others marked


def test_one_bad_device_does_not_abort_the_bucket(monkeypatch):
    service = FakeService(make_devices(3))
    failures = {"n": 0}

    class ExplodingTable(FakeTable):
        def execute(self):
            if self._update is not None and self._id == 1:
                raise RuntimeError("db hiccup")
            return super().execute()

    class ExplodingService(FakeService):
        def table(self, _name):
            return ExplodingTable(self.store)

    service = ExplodingService(make_devices(3))
    monkeypatch.setattr(job, "get_service_client", lambda: service)
    monkeypatch.setattr(job, "send_batch_sync",
                        lambda msgs: [PushResult(delivered=True) for _ in msgs])
    monkeypatch.setattr(job, "record_failure", lambda _n, _e: failures.update(n=1))
    monkeypatch.setattr(job, "record_success", lambda _n: None)

    assert job.run(at_push_hour()) == 3          # all three still sent
    assert [i for i, _ in service.store["updated"]] == [0, 2]
    assert failures["n"] == 1                    # and the failure is reported


def test_only_zones_in_the_push_hour_are_read(monkeypatch):
    """Reading every device every hour is one full table scan per hour for a
    bucket that is, by definition, about one twenty-fourth of the rows."""
    service = FakeService(make_devices(3))
    monkeypatch.setattr(job, "get_service_client", lambda: service)
    monkeypatch.setattr(job, "send_batch_sync",
                        lambda msgs: [PushResult(delivered=True) for _ in msgs])
    monkeypatch.setattr(job, "record_success", lambda _n: None)

    job.run(at_push_hour())

    column, zones = service.store["filtered"][0]
    assert column == "device_timezone"
    assert "Asia/Ho_Chi_Minh" in zones      # 08:00 local at 01:00 UTC
    assert "Europe/London" not in zones     # 02:00 local — not this bucket


def test_zone_bucket_tracks_the_hour_not_a_fixed_offset():
    """Zones are resolved at run time so DST needs no stored offset."""
    zones = job.zones_in_push_hour(at_push_hour())
    assert all(
        job._local_hour(at_push_hour(), zone) == job.PUSH_HOUR for zone in zones
    )
    assert zones, "some zone is always in the push hour"
