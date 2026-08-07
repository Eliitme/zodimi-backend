"""Success/error heartbeat shared by all background jobs (see the
`job_runs` migration for why this exists)."""

import logging
from datetime import UTC, datetime

from app.core.supabase_client import get_service_client

logger = logging.getLogger("stellune.jobs")


def record_success(job_name: str) -> None:
    get_service_client().table("job_runs").upsert({
        "job_name": job_name,
        "last_success_at": datetime.now(UTC).isoformat(),
        "last_error": None,
        "last_error_at": None,
    }).execute()


def record_failure(job_name: str, error: str) -> None:
    # Loud in logs — this is the "alert" channel until real alerting exists.
    logger.error("JOB FAILED %s: %s", job_name, error)
    get_service_client().table("job_runs").upsert({
        "job_name": job_name,
        "last_error": error[:2000],
        "last_error_at": datetime.now(UTC).isoformat(),
    }).execute()
