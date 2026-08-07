"""Timezone bucketing for the hourly daily-push job (FR8).

The job runs every hour; these tests pin WHICH devices it selects at a given
UTC instant — including DST transitions and a user who changed timezone.
"""

from datetime import UTC, datetime

from app.jobs.daily_push_job import PUSH_HOUR, devices_due


def device(tz: str, last_push: str | None = None, token: str = "tok"):
    return {
        "id": token, "apns_token": token, "device_timezone": tz,
        "locale": "vi", "last_push_date": last_push,
    }


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


def test_pushes_only_during_local_push_hour():
    saigon = device("Asia/Ho_Chi_Minh")          # UTC+7 → 08:00 local = 01:00 UTC
    assert devices_due([saigon], at("2026-08-07T01:00:00")) == [saigon]
    assert devices_due([saigon], at("2026-08-07T00:00:00")) == []
    assert devices_due([saigon], at("2026-08-07T02:30:00")) == []


def test_whole_push_hour_counts_not_just_exact_oclock():
    """Cron may fire a few minutes late — anywhere in the 8am hour is fine."""
    saigon = device("Asia/Ho_Chi_Minh")
    assert devices_due([saigon], at("2026-08-07T01:59:00")) == [saigon]


def test_two_zones_are_pushed_at_different_utc_hours():
    saigon = device("Asia/Ho_Chi_Minh", token="vn")     # UTC+7
    new_york = device("America/New_York", token="us")   # UTC-4 in August (EDT)
    assert devices_due([saigon, new_york], at("2026-08-07T01:00:00")) == [saigon]
    assert devices_due([saigon, new_york], at("2026-08-07T12:00:00")) == [new_york]


def test_dst_shifts_the_utc_hour_automatically():
    """New York is UTC-5 in winter, UTC-4 in summer — 8am local must track
    the offset without any stored state."""
    winter = device("America/New_York", token="ny")
    assert devices_due([winter], at("2026-01-15T13:00:00")) == [winter]   # EST
    assert devices_due([winter], at("2026-01-15T12:00:00")) == []
    summer = device("America/New_York", token="ny")
    assert devices_due([summer], at("2026-07-15T12:00:00")) == [summer]   # EDT
    assert devices_due([summer], at("2026-07-15T13:00:00")) == []


def test_already_pushed_today_is_skipped():
    saigon = device("Asia/Ho_Chi_Minh", last_push="2026-08-07")
    assert devices_due([saigon], at("2026-08-07T01:00:00")) == []


def test_yesterdays_marker_does_not_block_today():
    saigon = device("Asia/Ho_Chi_Minh", last_push="2026-08-06")
    assert devices_due([saigon], at("2026-08-07T01:00:00")) == [saigon]


def test_marker_uses_device_local_date_not_utc_date():
    """Saigon's 8am is the PREVIOUS UTC day for zones ahead of UTC — the
    marker must be the local date or the guard misfires."""
    # 2026-08-07 01:00 UTC == 2026-08-07 08:00 in Saigon (same date here),
    # but Kiritimati (UTC+14) at 8am local is still 2026-08-06 in UTC.
    kiritimati = device("Pacific/Kiritimati", last_push="2026-08-07")
    assert devices_due([kiritimati], at("2026-08-06T18:00:00")) == []
    fresh = device("Pacific/Kiritimati", last_push="2026-08-06")
    assert devices_due([fresh], at("2026-08-06T18:00:00")) == [fresh]


def test_travelling_user_gets_pushed_in_the_new_timezone():
    """The device re-registers its timezone; scheduling follows the newest
    value, so a flight VN → US moves the push, not duplicates it."""
    before_flight = device("Asia/Ho_Chi_Minh", last_push="2026-08-07", token="phone")
    assert devices_due([before_flight], at("2026-08-07T12:00:00")) == []
    after_flight = device("America/New_York", last_push="2026-08-07", token="phone")
    # Same UTC instant, new zone: it is 08:00 in New York but the device was
    # already pushed on 2026-08-07 (local date there is still the 7th).
    assert devices_due([after_flight], at("2026-08-07T12:00:00")) == []
    # Next local day it fires again in the new zone.
    assert devices_due([after_flight], at("2026-08-08T12:00:00")) == [after_flight]


def test_unknown_timezone_is_skipped_not_crashed():
    assert devices_due([device("Mars/Olympus_Mons")], at("2026-08-07T01:00:00")) == []


def test_push_hour_constant_is_eight():
    assert PUSH_HOUR == 8
