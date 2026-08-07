"""Account deletion (FR9).

Order matters: the AUTH USER is deleted FIRST, then the data rows. If the
second step fails, the account can no longer sign in and the orphaned rows
are recoverable by a sweep (user_id absent from auth.users). The reverse
order would leave a user who believes their account is gone while it is
still fully usable — the worse failure.

Which tables get cleared is driven by the `user_scoped_tables` catalog (see
the delete RPC), the same source the guest-merge uses.
"""

import logging

from fastapi import APIRouter, Request, status
from pydantic import BaseModel
from supabase_auth.errors import AuthApiError

from app.core.supabase_client import get_service_client

logger = logging.getLogger("stellune.account")

router = APIRouter(prefix="/account", tags=["account"])


class DeleteAccountResponse(BaseModel):
    deleted: bool


@router.delete("", response_model=DeleteAccountResponse, status_code=status.HTTP_200_OK)
def delete_account(request: Request) -> DeleteAccountResponse:
    user_id: str = request.state.user_id
    service = get_service_client()

    try:
        service.auth.admin.delete_user(user_id)
    except AuthApiError as exc:
        if exc.status != 404:
            raise
        # Already gone (retry of a partially-completed deletion) — continue
        # so the data rows still get cleared.
        logger.info("auth user already deleted; clearing data rows")

    service.rpc("delete_user_data", {"target_id": user_id}).execute()
    return DeleteAccountResponse(deleted=True)
