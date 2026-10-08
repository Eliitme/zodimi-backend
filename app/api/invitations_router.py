"""Looking at an invitation, and accepting or declining it.

Mounted under `/v1`, so a caller needs a verified JWT — an anonymous one is
fine and is what the web bootstraps before it resolves a link. The spec
offers a public preview route as an alternative; this is the other branch of
that choice, taken because `main.py` enforces exactly one exception to
default auth and a growth-loop endpoint that consumes tokens is not a good
second one. The cost is one anonymous sign-in before the link resolves,
which the web already does for every other call.

Every refusal here is deliberately uninformative. "No such token", "that
room was deleted" and "that link was already used by someone else" all have
to look the same to someone sweeping tokens, or the endpoint becomes a way
to find out which rooms exist.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Path, Request, status

from app.core.rate_limit import RateLimit, enforce
from app.core.supabase_client import get_service_client
from app.schemas.room_schemas import AcceptInvitationRequest, InvitationPreview
from app.services.room_service import MAX_INVITE_ATTEMPTS, hash_invite_token

router = APIRouter(prefix="/invitations", tags=["rooms"])

# Tighter than the room endpoints: this is the one path where the caller
# supplies a secret, so it is the one worth guessing at.
TOKEN_LIMIT = RateLimit(max_calls=10, per_seconds=60)

#: Token length is fixed by `secrets.token_urlsafe(32)`. Rejecting anything
#: else before touching the database turns a malformed sweep into no query
#: at all.
TOKEN_PATTERN = "^[A-Za-z0-9_-]{40,50}$"

NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND, detail="This invitation is not available"
)


def _lookup(service, token: str) -> dict:
    rows = (
        service.table("astro_room_invitations").select("*")
        .eq("token_hash", hash_invite_token(token)).limit(1).execute().data
    )
    if not rows:
        raise NOT_FOUND
    invitation = rows[0]
    if invitation["attempt_count"] >= MAX_INVITE_ATTEMPTS:
        # A link that has been looked at twenty times is being probed, not
        # opened. Stop answering rather than keep confirming it exists.
        raise NOT_FOUND
    return invitation


@router.get("/{token}/preview", response_model=InvitationPreview)
def preview_invitation(
    request: Request, token: str = Path(pattern=TOKEN_PATTERN)
) -> InvitationPreview:
    enforce(request, "invite_preview", TOKEN_LIMIT)
    service = get_service_client()
    invitation = _lookup(service, token)
    service.table("astro_room_invitations").update(
        {"attempt_count": invitation["attempt_count"] + 1}
    ).eq("id", invitation["id"]).execute()

    room = (
        service.table("astro_rooms").select("topic, status")
        .eq("id", invitation["room_id"]).limit(1).execute().data
    )
    if not room:
        raise NOT_FOUND

    if invitation["revoked_at"] is not None:
        state = "revoked"
    elif invitation["accepted_at"] is not None:
        state = "claimed"
    elif datetime.fromisoformat(invitation["expires_at"]) <= datetime.now(UTC):
        state = "expired"
    elif room[0]["status"] in ("revoked", "closed"):
        state = "revoked"
    else:
        state = "open"

    # Topic and state. No host name, no alias, no room id, nothing about the
    # person who sent it — enough to decide whether to go on, and nothing
    # that would cost them anything if the link were forwarded.
    return InvitationPreview(
        topic=room[0]["topic"], status=state, expires_at=invitation["expires_at"]
    )


@router.post("/{token}/accept")
def accept_invitation(
    request: Request,
    body: AcceptInvitationRequest,
    token: str = Path(pattern=TOKEN_PATTERN),
) -> dict:
    user_id: str = request.state.user_id
    enforce(request, "invite_accept", TOKEN_LIMIT)
    if not body.consent:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Consent is required to join a room",
        )

    service = get_service_client()
    # Only for the neutral-404 and attempt-cap checks; the RPC below does the
    # real validation, under a lock, because by the time we got here the
    # state could already have changed.
    _lookup(service, token)

    # The invitee's birth data goes into THEIR OWN profile. The room keeps a
    # revision number and nothing else, so the person who invited them never
    # receives their date, time or place.
    if body.birth_data is not None:
        service.table("profiles").upsert({
            "user_id": user_id,
            "birth_date": body.birth_data.birth_date.isoformat(),
            "birth_time": body.birth_data.birth_time.isoformat()
            if body.birth_data.birth_time else None,
            "birth_time_unknown": body.birth_data.birth_time_unknown,
            "lat": body.birth_data.lat,
            "lon": body.birth_data.lon,
            "tz_of_birth": body.birth_data.tz_of_birth,
        }, on_conflict="user_id").execute()

    profile = (
        service.table("profiles").select("birth_date, profile_revision")
        .eq("user_id", user_id).limit(1).execute().data
    )
    if not profile or profile[0]["birth_date"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Birth details are needed to join a room",
        )

    # One call, one lock, one decision — see the function's own comment.
    result = service.rpc("accept_room_invitation", {
        "p_token_hash": hash_invite_token(token),
        "p_user_id": user_id,
        "p_consent_version": body.consent_version,
        "p_profile_revision": profile[0]["profile_revision"],
    }).execute().data

    outcome = result[0] if result else {"code": "invalid", "room_id": None}
    code = outcome["code"]
    if code == "ok":
        return {"room_id": outcome["room_id"], "status": "joined"}

    # Mapped one by one rather than collapsed: "this expired" and "someone
    # else got there first" lead to different next steps for the reader.
    raise {
        "expired": HTTPException(status.HTTP_410_GONE, "This invitation has expired"),
        "revoked": HTTPException(status.HTTP_410_GONE, "This invitation was withdrawn"),
        "already_claimed": HTTPException(
            status.HTTP_409_CONFLICT, "Someone has already joined this room"
        ),
        "capacity": HTTPException(
            status.HTTP_409_CONFLICT, "Someone has already joined this room"
        ),
        "room_closed": HTTPException(status.HTTP_410_GONE, "This room is closed"),
        "owner_cannot_accept": HTTPException(
            status.HTTP_409_CONFLICT, "You cannot join a room you created"
        ),
    }.get(code, NOT_FOUND)


@router.post("/{token}/decline")
def decline_invitation(
    request: Request, token: str = Path(pattern=TOKEN_PATTERN)
) -> dict:
    """Declining writes nothing about the person declining.

    No membership row, no profile, no record that this account saw the link.
    The invitation is revoked so the owner learns it will not be used, and
    that is the whole trace. AC-07.
    """
    enforce(request, "invite_decline", TOKEN_LIMIT)
    service = get_service_client()
    invitation = _lookup(service, token)
    if invitation["accepted_at"] is None and invitation["revoked_at"] is None:
        service.table("astro_room_invitations").update(
            {"revoked_at": datetime.now(UTC).isoformat()}
        ).eq("id", invitation["id"]).execute()
    return {"status": "declined"}
