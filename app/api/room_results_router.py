"""The shared reading: compute it, then read it back.

No model is called anywhere in this module. The free preview is offered to
everyone a link reaches, so a model call here would be an unbounded bill
attached to a growth loop — and the rules engine behind it is deterministic,
which is what lets a result be stored once per pair revision and served
afterwards instead of recomputed. AC-09.
"""

from datetime import UTC, date, datetime
from datetime import time as dtime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from postgrest.exceptions import APIError

from app.astro.aspect_calculator import calculate_synastry_aspects
from app.astro.chart_calculator import build_subject
from app.astro.synastry_preview import build_free_insights
from app.core.locale import request_locale
from app.core.rate_limit import RateLimit, enforce
from app.core.supabase_client import get_service_client
from app.schemas.room_schemas import (
    FreeInsight,
    MemberReliability,
    PremiumReportState,
    RoomResult,
    SharePolicy,
)
from app.services.room_service import pair_revision_hash

router = APIRouter(prefix="/rooms", tags=["rooms"])

# The computation is pure arithmetic, but it runs Swiss Ephemeris twice.
PREVIEW_LIMIT = RateLimit(max_calls=20, per_seconds=60)

#: Shown locked until the paid reading ships. Titles only — the text does not
#: exist yet, and when it does it will not be sent to a reader without an
#: entitlement.
PREMIUM_SECTION_TITLES = {
    "vi": ["Sức hút", "Giao tiếp", "Xung đột", "Đường dài"],
    "en": ["Attraction", "Communication", "Friction", "The long run"],
}


def _subject_from(profile: dict):
    return build_subject(
        birth_date=date.fromisoformat(str(profile["birth_date"])),
        birth_time=dtime.fromisoformat(profile["birth_time"]) if profile["birth_time"] else None,
        birth_time_unknown=profile["birth_time_unknown"],
        lat=profile["lat"],
        lon=profile["lon"],
        tz_of_birth=profile["tz_of_birth"],
    )


def _role_of(user_id: str, owner: dict) -> str:
    return "owner" if owner["user_id"] == user_id else "invitee"


def _members(service, room_id: UUID, user_id: str) -> tuple[dict, dict]:
    """The owner and the invitee, both live and both consented.

    Raises 404 for a caller who is not a member — a stranger must not learn
    whether a room exists — and 403 when the room is real but its reading is
    closed, which is a state the reader can act on.
    """
    rows = (
        service.table("astro_room_members").select("*")
        .eq("room_id", str(room_id)).is_("revoked_at", "null").execute().data
    )
    if not any(m["user_id"] == user_id for m in rows):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")

    consented = [m for m in rows if m["consent_at"] is not None]
    if len(consented) != 2:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This reading needs both people to agree",
        )
    by_role = {m["role"]: m for m in consented}
    return by_role["owner"], by_role["invitee"]


def _presented(room_id: UUID, stored: dict, locale: str, viewer_role: str) -> RoomResult:
    insights = stored["free_insights_json"] or {}
    return RoomResult(
        room_id=room_id,
        status=stored["status"],
        viewer_role=viewer_role,
        pair_revision=stored["pair_revision_hash"],
        locale=locale,
        reliability=MemberReliability(**insights["reliability"]),
        free_insights=[FreeInsight(**i) for i in insights["free_insights"]],
        # Locked for everyone right now: the paid room reading is a later
        # phase, and the titles are all a reader is ever sent without an
        # entitlement.
        premium_report=PremiumReportState(
            status="locked",
            locked_section_titles=PREMIUM_SECTION_TITLES.get(
                locale, PREMIUM_SECTION_TITLES["vi"]
            ),
        ),
        share_policy=SharePolicy(can_export_sanitized_card=True),
    )


@router.post("/{room_id}/preview", response_model=RoomResult)
def compute_preview(request: Request, room_id: UUID) -> RoomResult:
    user_id: str = request.state.user_id
    enforce(request, "room_preview", PREVIEW_LIMIT)
    service = get_service_client()
    owner, invitee = _members(service, room_id, user_id)
    locale = request_locale(request)

    revision = pair_revision_hash(
        owner["profile_revision"], invitee["profile_revision"], locale
    )
    cached = (
        service.table("astro_room_results").select("*")
        .eq("room_id", str(room_id)).eq("pair_revision_hash", revision)
        .limit(1).execute().data
    )
    if cached:
        # Deterministic rules mean recomputing would produce the same bytes,
        # so the only thing a second computation buys is two more ephemeris
        # runs.
        return _presented(room_id, cached[0], locale, _role_of(user_id, owner))

    profiles = (
        service.table("profiles").select("*")
        .in_("user_id", [owner["user_id"], invitee["user_id"]]).execute().data
    )
    by_user = {p["user_id"]: p for p in profiles}
    a, b = by_user.get(owner["user_id"]), by_user.get(invitee["user_id"])
    if not a or not b or a["birth_date"] is None or b["birth_date"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Both people need complete birth details",
        )

    aspects = calculate_synastry_aspects(_subject_from(a), _subject_from(b))
    insights = build_free_insights(
        aspects,
        a_time_known=not a["birth_time_unknown"],
        b_time_known=not b["birth_time_unknown"],
        locale=locale,
    )

    row = {
        "room_id": str(room_id),
        "pair_revision_hash": revision,
        "status": "done",
        # Sanitised by construction: these are aspects between points, with
        # no birth date, time or coordinate anywhere in them.
        "evidence_json": aspects,
        "free_insights_json": insights,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    try:
        stored = service.table("astro_room_results").insert(row).execute().data[0]
    except APIError as exc:
        if exc.code != "23505":
            raise
        # Both members opened the room at once. Unique (room_id,
        # pair_revision_hash) means the other request's row is this one.
        stored = (
            service.table("astro_room_results").select("*")
            .eq("room_id", str(room_id)).eq("pair_revision_hash", revision)
            .limit(1).execute().data[0]
        )
    return _presented(room_id, stored, locale, _role_of(user_id, owner))


@router.get("/{room_id}/result", response_model=RoomResult)
def get_result(request: Request, room_id: UUID) -> RoomResult:
    user_id: str = request.state.user_id
    service = get_service_client()
    owner, invitee = _members(service, room_id, user_id)
    locale = request_locale(request)

    revision = pair_revision_hash(
        owner["profile_revision"], invitee["profile_revision"], locale
    )
    stored = (
        service.table("astro_room_results").select("*")
        .eq("room_id", str(room_id)).eq("pair_revision_hash", revision)
        .limit(1).execute().data
    )
    if not stored:
        # Either nothing has been computed yet or one of them edited their
        # birth data since. Both mean "ask for it", and POST is where the
        # work happens — a GET that computed would hide a cost behind a verb
        # that promises not to.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No reading for this pair yet"
        )
    return _presented(room_id, stored[0], locale, _role_of(user_id, owner))
