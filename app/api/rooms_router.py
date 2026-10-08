"""Astro Rooms: create, list, invite, leave, close.

Everything here is owner- or member-scoped and goes through the service
role, because the two-sided consent rule can only be enforced where the
invitation token is — inside a request. No client role holds a grant on any
room table (migration 000013), so this module is the only way in.
"""

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from postgrest.exceptions import APIError

from app.core.rate_limit import RateLimit, enforce
from app.core.supabase_client import get_service_client
from app.schemas.room_schemas import (
    CONSENT_VERSION,
    CreateInvitationRequest,
    CreateRoomRequest,
    InvitationResponse,
    RoomSummary,
)
from app.services.room_service import invite_expiry, invite_url, new_invite_token

router = APIRouter(prefix="/rooms", tags=["rooms"])

# Creating a room is cheap for us and free for the caller, which is exactly
# the shape of thing that gets used to generate link spam.
CREATE_LIMIT = RateLimit(max_calls=10, per_seconds=60)
INVITE_LIMIT = RateLimit(max_calls=20, per_seconds=60)


def _membership(service, room_id: UUID, user_id: str) -> dict | None:
    rows = (
        service.table("astro_room_members").select("*")
        .eq("room_id", str(room_id)).eq("user_id", user_id)
        .is_("revoked_at", "null")
        .limit(1).execute().data
    )
    return rows[0] if rows else None


def _require_membership(service, room_id: UUID, user_id: str) -> dict:
    member = _membership(service, room_id, user_id)
    if member is None:
        # 404, not 403: a non-member must not be able to tell a room they
        # cannot see from one that does not exist.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Room not found"
        )
    return member


def _summary(service, room: dict, role: str) -> RoomSummary:
    members = (
        service.table("astro_room_members").select("consent_at, revoked_at")
        .eq("room_id", room["id"]).execute().data
    )
    live = [m for m in members if m["revoked_at"] is None]
    consented = [m for m in live if m["consent_at"] is not None]
    return RoomSummary(
        id=room["id"],
        topic=room["topic"],
        status=room["status"],
        role=role,
        member_count=len(live),
        both_consented=len(consented) == 2,
        created_at=room["created_at"],
    )


@router.post("", response_model=RoomSummary, status_code=status.HTTP_201_CREATED)
def create_room(request: Request, body: CreateRoomRequest) -> RoomSummary:
    user_id: str = request.state.user_id
    enforce(request, "room_create", CREATE_LIMIT)
    service = get_service_client()

    profile = (
        service.table("profiles").select("birth_date, profile_revision")
        .eq("user_id", user_id).limit(1).execute().data
    )
    if not profile or profile[0]["birth_date"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Complete your birth details before creating a room",
        )

    if body.client_request_id is not None:
        existing = (
            service.table("astro_rooms").select("*")
            .eq("owner_user_id", user_id)
            .eq("client_request_id", str(body.client_request_id))
            .limit(1).execute().data
        )
        if existing:
            # The retry of a create whose response was lost. Returning the
            # same room is the whole point of the key.
            return _summary(service, existing[0], "owner")

    try:
        room = service.table("astro_rooms").insert({
            "owner_user_id": user_id,
            "topic": body.topic,
            "client_request_id": str(body.client_request_id) if body.client_request_id else None,
        }).execute().data[0]
    except APIError as exc:
        if exc.code != "23505":
            raise
        # Two devices racing on the same key: the other one won.
        existing = (
            service.table("astro_rooms").select("*")
            .eq("owner_user_id", user_id)
            .eq("client_request_id", str(body.client_request_id))
            .limit(1).execute().data
        )
        if not existing:
            raise
        return _summary(service, existing[0], "owner")

    # The owner consents by creating. Written in the same breath so a room
    # can never exist without the membership that proves who owns it.
    service.table("astro_room_members").insert({
        "room_id": room["id"],
        "user_id": user_id,
        "role": "owner",
        "consent_at": datetime.now(UTC).isoformat(),
        "consent_version": CONSENT_VERSION,
        "profile_revision": profile[0]["profile_revision"],
    }).execute()

    return _summary(service, room, "owner")


@router.get("", response_model=list[RoomSummary])
def list_rooms(request: Request) -> list[RoomSummary]:
    user_id: str = request.state.user_id
    service = get_service_client()
    memberships = (
        service.table("astro_room_members").select("room_id, role")
        .eq("user_id", user_id).is_("revoked_at", "null").execute().data
    )
    if not memberships:
        return []
    roles = {m["room_id"]: m["role"] for m in memberships}
    rooms = (
        service.table("astro_rooms").select("*")
        .in_("id", list(roles)).order("created_at", desc=True).execute().data
    )
    return [_summary(service, room, roles[room["id"]]) for room in rooms]


@router.get("/{room_id}", response_model=RoomSummary)
def get_room(request: Request, room_id: UUID) -> RoomSummary:
    service = get_service_client()
    member = _require_membership(service, room_id, request.state.user_id)
    room = (
        service.table("astro_rooms").select("*")
        .eq("id", str(room_id)).limit(1).execute().data
    )
    if not room:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Room not found")
    return _summary(service, room[0], member["role"])


@router.post(
    "/{room_id}/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_invitation(
    request: Request, room_id: UUID, body: CreateInvitationRequest
) -> InvitationResponse:
    user_id: str = request.state.user_id
    enforce(request, "room_invite", INVITE_LIMIT)
    service = get_service_client()
    member = _require_membership(service, room_id, user_id)
    if member["role"] != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the room owner can invite"
        )

    room = (
        service.table("astro_rooms").select("status")
        .eq("id", str(room_id)).limit(1).execute().data
    )
    if not room or room[0]["status"] in ("revoked", "closed"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="This room is closed"
        )
    if room[0]["status"] == "ready":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Someone has already joined this room"
        )

    # Reissuing supersedes: two working links to one room is a second way in
    # that nobody is tracking.
    service.table("astro_room_invitations").update(
        {"revoked_at": datetime.now(UTC).isoformat()}
    ).eq("room_id", str(room_id)).is_("revoked_at", "null").is_(
        "accepted_at", "null"
    ).execute()

    token, token_hash = new_invite_token()
    expires_at = invite_expiry(body.expires_in_days)
    service.table("astro_room_invitations").insert({
        "room_id": str(room_id),
        "token_hash": token_hash,
        "expires_at": expires_at.isoformat(),
    }).execute()

    # The only time the token exists outside the sharer's hands.
    return InvitationResponse(invite_url=invite_url(token), expires_at=expires_at.isoformat())


@router.post("/{room_id}/invitations/revoke")
def revoke_invitation(request: Request, room_id: UUID) -> dict:
    user_id: str = request.state.user_id
    service = get_service_client()
    member = _require_membership(service, room_id, user_id)
    if member["role"] != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the room owner can revoke"
        )
    service.table("astro_room_invitations").update(
        {"revoked_at": datetime.now(UTC).isoformat()}
    ).eq("room_id", str(room_id)).is_("revoked_at", "null").is_(
        "accepted_at", "null"
    ).execute()
    return {"revoked": True}


@router.delete("/{room_id}/members/me")
def leave_room(request: Request, room_id: UUID) -> dict:
    """Withdraw consent.

    The membership row stays with `revoked_at` set rather than being
    deleted: the room has to be able to say it was left, and
    `astro_room_has_both_consents` stops returning true either way. The
    shared reading closes for BOTH people — it only existed because both
    agreed.
    """
    user_id: str = request.state.user_id
    service = get_service_client()
    _require_membership(service, room_id, user_id)
    now = datetime.now(UTC).isoformat()
    service.table("astro_room_members").update({"revoked_at": now}).eq(
        "room_id", str(room_id)
    ).eq("user_id", user_id).execute()
    service.table("astro_rooms").update(
        {"status": "revoked", "updated_at": now}
    ).eq("id", str(room_id)).execute()
    return {"status": "revoked"}


@router.post("/{room_id}/close")
def close_room(request: Request, room_id: UUID) -> dict:
    user_id: str = request.state.user_id
    service = get_service_client()
    member = _require_membership(service, room_id, user_id)
    if member["role"] != "owner":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Only the room owner can close"
        )
    now = datetime.now(UTC).isoformat()
    service.table("astro_room_invitations").update({"revoked_at": now}).eq(
        "room_id", str(room_id)
    ).is_("revoked_at", "null").is_("accepted_at", "null").execute()
    service.table("astro_rooms").update(
        {"status": "closed", "updated_at": now}
    ).eq("id", str(room_id)).execute()
    return {"status": "closed"}
