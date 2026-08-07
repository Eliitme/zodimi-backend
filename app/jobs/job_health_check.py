"""Detects background jobs that died silently.

The VPS crontab has no dashboard: a job that stops running produces no
signal at all. This compares each job's last success against how often it
is supposed to run and exits non-zero when one is overdue — wire it to
whatever alerting exists (cron MAILTO, uptime probe, manual check).

Run:  docker compose exec api python -m app.jobs.job_health_check
"""

import logging
import sys
from datetime import UTC, datetime, timedelta

from app.core.supabase_client import get_service_client

logger = logging.getLogger("stellune.jobs.health")

# job_name → how long silence is acceptable (generous vs the real cadence).
MAX_SILENCE = {
    "daily_tarot_pregen": timedelta(hours=25),      # runs daily
    "daily_push": timedelta(hours=2),               # runs hourly
    "stale_report_watchdog": timedelta(minutes=30),  # runs every 5 minutes
}


def overdue_jobs(now: datetime | None = None) -> list[str]:
    now = now or datetime.now(UTC)
    rows = get_service_client().table("job_runs").select("*").execute().data
    last_success = {r["job_name"]: r.get("last_success_at") for r in rows}

    problems = []
    for job_name, max_silence in MAX_SILENCE.items():
        stamp = last_success.get(job_name)
        if stamp is None:
            problems.append(f"{job_name}: never ran")
            continue
        age = now - datetime.fromisoformat(stamp)
        if age > max_silence:
            problems.append(f"{job_name}: silent for {age}")
    return problems


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    problems = overdue_jobs()
    for problem in problems:
        logger.error("JOB HEALTH: %s", problem)
    if not problems:
        logger.info("all background jobs healthy")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
