"""Dev-only helpers backing the browser test console.

Mounted ONLY when `DEV_TOOLS=true` (see app.main). These endpoints hand out
premium and reset state, so shipping them enabled would be a free-premium
switch — the flag is the entire security boundary, hence the hard check
here as well as at mount time.
"""

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.core.config import get_settings
from app.core.supabase_client import get_service_client

router = APIRouter(prefix="/dev", tags=["devtools"])


def _guard() -> None:
    if not get_settings().dev_tools:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")


class PremiumRequest(BaseModel):
    is_premium: bool


class DevStateResponse(BaseModel):
    user_id: str
    is_premium: bool
    has_profile: bool
    draws_today: int
    qa_today: int
    reports: int


@router.post("/premium", response_model=DevStateResponse)
def set_premium(request: Request, body: PremiumRequest) -> DevStateResponse:
    """Flip the caller's entitlement without going through RevenueCat."""
    _guard()
    user_id: str = request.state.user_id
    get_service_client().table("profiles").update(
        {"is_premium": body.is_premium}
    ).eq("user_id", user_id).execute()
    return _state(user_id)


@router.get("/state", response_model=DevStateResponse)
def dev_state(request: Request) -> DevStateResponse:
    """Everything the console shows in its status bar, in one call."""
    _guard()
    return _state(request.state.user_id)


@router.post("/reset-quotas", response_model=DevStateResponse)
def reset_quotas(request: Request) -> DevStateResponse:
    """Clears today's draw + Q&A rows so quota paths can be retested
    without waiting for UTC midnight."""
    _guard()
    user_id: str = request.state.user_id
    service = get_service_client()
    service.table("tarot_draws").delete().eq("user_id", user_id).execute()
    service.table("tarot_qa_log").delete().eq("user_id", user_id).execute()
    return _state(user_id)


@router.post("/reset-reports", response_model=DevStateResponse)
def reset_reports(request: Request) -> DevStateResponse:
    """Deletes the caller's reports so the request→worker→done flow can be
    replayed (POST otherwise returns the existing one by design)."""
    _guard()
    user_id: str = request.state.user_id
    get_service_client().table("natal_reports").delete().eq("user_id", user_id).execute()
    return _state(user_id)


def _state(user_id: str) -> DevStateResponse:
    service = get_service_client()
    profile = (
        service.table("profiles").select("is_premium, birth_date")
        .eq("user_id", user_id).limit(1).execute().data
    )
    from app.core.entitlements import utc_today

    draws = (
        service.table("tarot_draws").select("id", count="exact")
        .eq("user_id", user_id).eq("drawn_on", utc_today()).execute().count or 0
    )
    qa = (
        service.table("tarot_qa_log").select("id", count="exact")
        .eq("user_id", user_id).eq("asked_on", utc_today()).execute().count or 0
    )
    reports = (
        service.table("natal_reports").select("id", count="exact")
        .eq("user_id", user_id).execute().count or 0
    )
    return DevStateResponse(
        user_id=user_id,
        is_premium=bool(profile and profile[0]["is_premium"]),
        has_profile=bool(profile and profile[0]["birth_date"]),
        draws_today=draws,
        qa_today=qa,
        reports=reports,
    )
