"""Premium/quota checks shared by every AI-cost endpoint.

`is_premium` reads `profiles.is_premium`, which only the backend writes
(from verified RevenueCat events — column-scoped grants block clients).
"""

from datetime import UTC, datetime

from app.core.supabase_client import get_service_client

QA_DAILY_LIMIT = 20


def utc_today() -> str:
    # Quotas reset on UTC midnight — deterministic and unspoofable by
    # clients (a device-timezone reset would let users shift the window).
    return datetime.now(UTC).date().isoformat()


def is_premium(user_id: str) -> bool:
    rows = (
        get_service_client().table("profiles")
        .select("is_premium").eq("user_id", user_id).limit(1).execute().data
    )
    return bool(rows and rows[0]["is_premium"])


def qa_questions_used_today(user_id: str) -> int:
    rows = (
        get_service_client().table("tarot_qa_log")
        .select("id", count="exact")
        .eq("user_id", user_id).eq("asked_on", utc_today())
        .execute()
    )
    return rows.count or 0
