"""Natal report request/read endpoints (thin CRUD).

Content generation is the AI pipeline's job (worker picks up `queued` rows);
these endpoints only create and read rows. Service-role DB access bypasses
RLS, so BOTH handlers scope every query by the authenticated user id —
reading another user's report must be indistinguishable from it not
existing (404).

TODO (AI pipeline phase): premium-gate report creation + rate limit before
the worker starts burning LLM budget.
"""

from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from postgrest.exceptions import APIError
from pydantic import BaseModel

from app.core.supabase_client import get_service_client

router = APIRouter(prefix="/reports", tags=["reports"])

ACTIVE_STATUSES = ("queued", "generating")


class ReportStatusResponse(BaseModel):
    id: UUID
    status: str
    content: str | None = None
    requested_at: str
    completed_at: str | None = None


@router.post("/natal", response_model=ReportStatusResponse, status_code=status.HTTP_201_CREATED)
def request_natal_report(request: Request) -> ReportStatusResponse:
    user_id: str = request.state.user_id
    service = get_service_client()

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
    # semantics (e.g. after birth data changes) are the AI phase's call.
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
        row = (
            service.table("natal_reports").select("*").eq("user_id", user_id)
            .in_("status", ACTIVE_STATUSES).limit(1).execute().data[0]
        )
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
    return ReportStatusResponse(**rows[0])


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
    return ReportStatusResponse(**rows[0])
