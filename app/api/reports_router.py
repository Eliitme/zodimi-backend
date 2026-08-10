"""Natal report request/read endpoints.

Content generation is the AI pipeline's job (worker picks up `queued` rows);
these endpoints only create and read rows. Service-role DB access bypasses
RLS, so BOTH handlers scope every query by the authenticated user id —
reading another user's report must be indistinguishable from it not
existing (404).

Creation is premium-gated (~10 LLM calls per report) and capped per day
against retry storms.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from postgrest.exceptions import APIError
from pydantic import BaseModel

from app.ai.safety_guardrails import append_disclaimer
from app.core.entitlements import is_premium, utc_today
from app.core.locale import request_locale
from app.core.supabase_client import get_service_client

router = APIRouter(prefix="/reports", tags=["reports"])

ACTIVE_STATUSES = ("queued", "generating")
# Retry-storm guard: new rows only appear after failures, so a handful/day
# is generous for legitimate use.
DAILY_REPORT_CAP = 5


FREE_SECTION_COUNT = 3


class ReportStatusResponse(BaseModel):
    id: UUID
    status: str
    content: str | None = None
    requested_at: str
    completed_at: str | None = None
    # Titles of sections withheld server-side for non-premium users. The
    # client renders these as locked rows — it never receives their text.
    locked_section_titles: list[str] | None = None


def _with_disclaimer(row: dict, locale: str) -> dict:
    """FR11's output post-filter, applied on read rather than on generation.

    Baking it into the stored content would put it after the last section,
    where `_gate_content` truncates it away for exactly the free-tier readers
    who most need it. Applying it here means both tiers get it.
    """
    if not row.get("content"):
        return row
    return {**row, "content": append_disclaimer(row["content"], locale)}


def _gate_content(row: dict, user_id: str) -> dict:
    """Server-side premium gate: a non-premium user (e.g. subscription
    expired after the report was generated) gets only the free preview —
    shipping full content and hiding it client-side is not a gate."""
    if row.get("status") != "done" or not row.get("content"):
        return row
    if is_premium(user_id):
        return row
    sections = row["content"].split("\n## ")
    if len(sections) <= FREE_SECTION_COUNT:
        return row
    gated = dict(row)
    gated["content"] = "\n## ".join(sections[:FREE_SECTION_COUNT])
    gated["locked_section_titles"] = [
        s.split("\n", 1)[0].strip() for s in sections[FREE_SECTION_COUNT:]
    ]
    return gated


@router.post("/natal", response_model=ReportStatusResponse, status_code=status.HTTP_201_CREATED)
def request_natal_report(request: Request) -> ReportStatusResponse:
    user_id: str = request.state.user_id
    service = get_service_client()

    if not is_premium(user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The in-depth report is a premium feature",
        )
    requested_today = (
        service.table("natal_reports").select("id", count="exact")
        .eq("user_id", user_id).gte("requested_at", f"{utc_today()}T00:00:00Z")
        .execute().count or 0
    )
    if requested_today >= DAILY_REPORT_CAP:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily report limit reached — please try again tomorrow",
        )

    profile = (
        service.table("profiles").select("birth_date").eq("user_id", user_id)
        .limit(1).execute().data
    )
    if not profile or profile[0]["birth_date"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Complete your birth details before requesting a report",
        )

    # Reports are expensive to generate: an existing active OR done report
    # is returned instead of queuing a duplicate. Explicit "regenerate"
    # semantics (e.g. after birth data changes) are a later product call.
    existing = (
        service.table("natal_reports").select("*").eq("user_id", user_id)
        .neq("status", "failed")
        .order("requested_at", desc=True).limit(1).execute().data
    )
    if existing:
        return ReportStatusResponse(**existing[0])

    try:
        row = (
            service.table("natal_reports").insert({"user_id": user_id}).execute().data[0]
        )
    except APIError as exc:
        # Unique-violation from a concurrent request (e.g. two devices on
        # one account): the other request created the row — return it.
        if exc.code != "23505":
            raise
        rows = (
            service.table("natal_reports").select("*").eq("user_id", user_id)
            .neq("status", "failed")
            .order("requested_at", desc=True).limit(1).execute().data
        )
        if not rows:  # concurrent row failed in the same instant — retry once
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Please retry the request",
            )
        row = rows[0]
    return ReportStatusResponse(**row)


@router.get("/natal/latest", response_model=ReportStatusResponse)
def get_latest_natal_report(request: Request) -> ReportStatusResponse:
    """Most recent non-failed report, so the client can restore a done or
    in-flight report when the screen reopens (defined before /{report_id}
    so the literal path wins route matching)."""
    user_id: str = request.state.user_id
    rows = (
        get_service_client()
        .table("natal_reports").select("*").eq("user_id", user_id)
        .neq("status", "failed")
        .order("requested_at", desc=True).limit(1).execute().data
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No report yet")
    return ReportStatusResponse(
        **_with_disclaimer(_gate_content(rows[0], user_id), request_locale(request))
    )


@router.get("/natal/{report_id}", response_model=ReportStatusResponse)
def get_natal_report(request: Request, report_id: UUID) -> ReportStatusResponse:
    user_id: str = request.state.user_id
    rows = (
        get_service_client()
        .table("natal_reports").select("*")
        .eq("id", str(report_id)).eq("user_id", user_id)  # ownership scope
        .limit(1).execute().data
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    return ReportStatusResponse(
        **_with_disclaimer(_gate_content(rows[0], user_id), request_locale(request))
    )
