"""Daily 8am push, per user timezone (FR8).

Runs HOURLY from cron; each run pushes to devices whose LOCAL time is
currently in the 8am hour. Working in IANA zones means DST is handled for
free (the offset is resolved at run time, not stored).

Idempotency: `devices.last_push_date` holds the device-local date of the
last push, so a device is never notified twice in its own day even if the
job runs late, twice, or the user crosses timezones.

Run via cron every hour:
    docker compose exec api python -m app.jobs.daily_push_job
"""

import logging
from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.core.supabase_client import get_service_client
from app.jobs.job_heartbeat import record_failure, record_success
from app.push.apns_client import DAILY_CARD, send_batch_sync

logger = logging.getLogger("stellune.jobs.daily_push")

JOB_NAME = "daily_push"
PUSH_HOUR = 8
PAGE_SIZE = 1000  # PostgREST caps a request at max_rows; page explicitly.


def devices_due(devices: list[dict], now_utc: datetime | None = None) -> list[dict]:
    """Devices whose local time is in the push hour AND that have not been
    pushed yet on their local date. Pure function — unit-testable without
    a database or a clock."""
    now = now_utc or datetime.now(UTC)
    due = []
    for device in devices:
        try:
            local = now.astimezone(ZoneInfo(device["device_timezone"]))
        except (ZoneInfoNotFoundError, ValueError):
            logger.warning("device has unknown timezone — skipped")
            continue
        if local.hour != PUSH_HOUR:
            continue
        if device.get("last_push_date") == local.date().isoformat():
            continue  # already pushed on this device's local day
        due.append(device)
    return due


def _fetch_all_devices(service) -> list[dict]:
    """Paged read: a single select silently truncates at PostgREST's
    max_rows, which would leave devices beyond that limit never pushed."""
    devices: list[dict] = []
    offset = 0
    while True:
        page = (
            service.table("devices")
            .select("id, apns_token, device_timezone, locale, last_push_date")
            .order("id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute().data
        )
        devices.extend(page)
        if len(page) < PAGE_SIZE:
            return devices
        offset += PAGE_SIZE


def run(now_utc: datetime | None = None) -> int:
    now = now_utc or datetime.now(UTC)
    service = get_service_client()
    due = devices_due(_fetch_all_devices(service), now)
    if not due:
        record_success(JOB_NAME)
        return 0

    messages = []
    for device in due:
        title, body = DAILY_CARD.get(device.get("locale") or "en", DAILY_CARD["en"])
        messages.append((device["apns_token"], title, body))
    results = send_batch_sync(messages)

    sent, failures = 0, 0
    # strict: a length mismatch means a send was silently dropped — better
    # to fail loudly than to mark the wrong device as pushed.
    for device, result in zip(due, results, strict=True):
        # One bad device must not abort the rest of this hour's bucket —
        # the skipped ones would fall outside the push hour by the next run
        # and lose the whole day.
        try:
            if result.delivered:
                sent += 1
            if result.token_is_dead:
                service.table("devices").delete().eq("id", device["id"]).execute()
                continue
            local_date = now.astimezone(ZoneInfo(device["device_timezone"])).date().isoformat()
            # Marked regardless of delivery: a flaky token would otherwise be
            # retried every hour for the rest of its local day.
            service.table("devices").update({"last_push_date": local_date}) \
                .eq("id", device["id"]).execute()
        except Exception as exc:
            failures += 1
            logger.warning("device bookkeeping failed: %s", exc)

    if failures:
        record_failure(JOB_NAME, f"{failures} device(s) failed bookkeeping")
    else:
        record_success(JOB_NAME)
    logger.info("daily push: %d sent, %d failed", sent, failures)
    return sent


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
