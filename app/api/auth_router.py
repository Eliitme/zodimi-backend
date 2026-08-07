"""Guest-account merge (fallback path of FR2).

Used by the iOS app when linking the anonymous identity to Apple fails and
sign-in produced a NEW user: the client sends its old guest JWT here (while
authenticated as the new user) and the backend re-points all user-scoped
rows in one server-side transaction, then deletes the guest auth user.

Safety: the transaction commits atomically on the server — a client killed
mid-flow can simply retry; there is no half-merged state. The guest user is
deleted only AFTER the merge commits (idempotent on retry).
"""

import jwt as pyjwt
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel
from supabase_auth.errors import AuthApiError

from app.core.auth import decode_token
from app.core.supabase_client import get_service_client

router = APIRouter(prefix="/auth", tags=["auth"])


class MergeGuestRequest(BaseModel):
    guest_jwt: str


class MergeGuestResponse(BaseModel):
    merged: bool
    user_id: str


@router.post("/merge-guest", response_model=MergeGuestResponse)
def merge_guest(request: Request, body: MergeGuestRequest) -> MergeGuestResponse:
    new_user_id: str = request.state.user_id

    try:
        guest_claims = decode_token(body.guest_jwt)
    except pyjwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Guest token invalid or expired",
        )

    old_user_id = guest_claims.get("sub")
    if not old_user_id or old_user_id == new_user_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Guest token must belong to a different user",
        )
    # Only ANONYMOUS accounts may be merged away — otherwise a stolen JWT
    # of a permanent account could be drained into the attacker's account.
    if not guest_claims.get("is_anonymous", False):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Only guest (anonymous) accounts can be merged",
        )

    service = get_service_client()
    service.rpc(
        "merge_user_data", {"old_id": old_user_id, "new_id": new_user_id}
    ).execute()
    # Delete AFTER the merge transaction committed. If this fails the guest
    # user lingers with zero rows — harmless, and a retry cleans it up.
    try:
        service.auth.admin.delete_user(old_user_id)
    except AuthApiError as exc:
        if exc.status != 404:
            raise
        # Already deleted by a previous attempt — the retry is a no-op.

    return MergeGuestResponse(merged=True, user_id=new_user_id)
