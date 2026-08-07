"""RevenueCat webhook — the ONE endpoint allowlisted from `require_user()`
(mounted on the public router): it authenticates with its own shared secret,
not a user JWT.

Defense layers, in order:
1. Constant-time secret compare (Authorization header).
2. `app_user_id` must match an existing profile — otherwise log + drop
   (200 so RevenueCat stops retrying junk).
3. DB-level idempotency: unique index on `revenuecat_event_id` — a redelivered
   event inserts nothing and must NOT re-apply entitlement changes.
4. Timestamp ordering: RevenueCat does not guarantee delivery order — an
   EXPIRATION older than an already-processed RENEWAL must never revoke a
   paying user's entitlement.
"""

import hmac
import logging
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from postgrest.exceptions import APIError

from app.core.config import get_settings
from app.core.supabase_client import get_service_client

logger = logging.getLogger("stellune.iap")

router = APIRouter(prefix="/iap", tags=["iap"])

PREMIUM_ON_EVENTS = {
    "INITIAL_PURCHASE", "RENEWAL", "UNCANCELLATION",
    "PRODUCT_CHANGE", "NON_RENEWING_PURCHASE",
}
# CANCELLATION only turns off auto-renew — access lasts until EXPIRATION.
PREMIUM_OFF_EVENTS = {"EXPIRATION"}


@router.post("/revenuecat-webhook")
async def revenuecat_webhook(request: Request) -> dict:
    settings = get_settings()
    if not settings.revenuecat_webhook_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Webhook not configured")
    provided = request.headers.get("Authorization", "")
    if not hmac.compare_digest(provided, settings.revenuecat_webhook_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Bad webhook credentials")

    payload = await request.json()
    event = payload.get("event") or {}
    event_id = event.get("id")
    event_type = event.get("type", "")
    app_user_id = event.get("app_user_id")
    timestamp_ms = event.get("event_timestamp_ms")
    if not event_id or not app_user_id or timestamp_ms is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Malformed event")
    event_at = datetime.fromtimestamp(timestamp_ms / 1000, tz=UTC)

    # RevenueCat can send non-UUID ids (e.g. "$RCAnonymousID:..." before the
    # app aliases the user) — those can never match a profile; drop them
    # instead of letting the uuid cast 500 and trigger an RC retry storm.
    try:
        UUID(app_user_id)
    except (ValueError, TypeError):
        logger.warning("webhook with non-uuid app_user_id — dropped (event %s)", event_id)
        return {"status": "dropped"}

    service = get_service_client()
    known_user = (
        service.table("profiles").select("user_id")
        .eq("user_id", app_user_id).limit(1).execute().data
    )
    if not known_user:
        logger.warning("webhook for unknown app_user_id — dropped (event %s)", event_id)
        return {"status": "dropped"}

    try:
        service.table("iap_events").insert({
            "user_id": app_user_id,
            "revenuecat_event_id": event_id,
            "revenuecat_event": payload,
            "event_timestamp": event_at.isoformat(),
        }).execute()
    except APIError as exc:
        if exc.code != "23505":
            raise
        # Redelivery of an event we already processed: acknowledge, change nothing.
        return {"status": "duplicate"}

    if event_type not in PREMIUM_ON_EVENTS and event_type not in PREMIUM_OFF_EVENTS:
        return {"status": "stored"}  # informational event (TEST, TRANSFER, ...)

    # Out-of-order guard: only the NEWEST event may change entitlement.
    newest = (
        service.table("iap_events").select("event_timestamp")
        .eq("user_id", app_user_id)
        .order("event_timestamp", desc=True).limit(1).execute().data
    )
    if newest and newest[0]["event_timestamp"] > event_at.isoformat():
        logger.info("stale event %s (%s) — entitlement unchanged", event_id, event_type)
        return {"status": "stale"}

    service.table("profiles").update(
        {"is_premium": event_type in PREMIUM_ON_EVENTS}
    ).eq("user_id", app_user_id).execute()
    return {"status": "applied"}
