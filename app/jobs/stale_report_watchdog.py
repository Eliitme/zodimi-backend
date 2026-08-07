"""Re-queues reports stuck in `generating` past a TTL — the recovery path
for a worker killed mid-job (deploy restart, crash, OOM). retry_count is
preserved, so a report that keeps dying still converges to `failed`.

Run via cron every 5 minutes:
    docker compose exec api python -m app.jobs.stale_report_watchdog
"""

import logging
from datetime import UTC, datetime, timedelta

from app.core.supabase_client import get_service_client
from app.jobs.job_heartbeat import record_success

logger = logging.getLogger("stellune.jobs.watchdog")

JOB_NAME = "stale_report_watchdog"
STALE_TTL_MINUTES = 15


def run() -> int:
    cutoff = (datetime.now(UTC) - timedelta(minutes=STALE_TTL_MINUTES)).isoformat()
    rows = (
        get_service_client().table("natal_reports")
        .update({"status": "queued", "generating_started_at": None})
        .eq("status", "generating")
        .lt("generating_started_at", cutoff)
        .execute().data
    )
    if rows:
        logger.warning("re-queued %d stale generating report(s)", len(rows))
    record_success(JOB_NAME)
    return len(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run()
