"""Client-initiated entitlement sync (right after a purchase, or app start).

The request body is EMPTY by design: the server asks RevenueCat directly and
believes only that answer. Trusting a client-supplied receipt/claim would
hand out free premium to anyone forging a payload.
"""

import logging

import httpx
from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.core.supabase_client import get_service_client
from app.revenuecat.revenuecat_client import (
    RevenueCatNotConfiguredError,
    subscriber_has_premium,
)

logger = logging.getLogger("stellune.iap")

router = APIRouter(prefix="/iap", tags=["iap"])


class SyncResponse(BaseModel):
    is_premium: bool


@router.post("/sync", response_model=SyncResponse)
def sync_entitlement(request: Request) -> SyncResponse:
    user_id: str = request.state.user_id
    try:
        premium = subscriber_has_premium(user_id)
    except RevenueCatNotConfiguredError:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Purchases are not configured yet")
    except httpx.HTTPError as exc:
        logger.warning("revenuecat sync failed for a user: %s", exc)
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Could not verify purchases — try again")

    get_service_client().table("profiles").update(
        {"is_premium": premium}
    ).eq("user_id", user_id).execute()
    return SyncResponse(is_premium=premium)
