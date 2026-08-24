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
5. TRANSFER revokes the account a subscription moved away from, so one
   purchase cannot leave two accounts permanently entitled.
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


def _parsed(stamp: str) -> datetime:
    """PostgREST timestamptz → aware datetime."""
    parsed = datetime.fromisoformat(stamp)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _revoke_transferred_from(service, payload: dict, event_id: str) -> bool:
    """Take premium away from the account a subscription moved OFF.

    Apple lets a subscription follow the buyer to a different app user, and
    RevenueCat reports that as one TRANSFER naming both sides. Treating it as
    informational left the losing account premium forever — one purchase,
    two permanent entitlements, which is a subscription-sharing loophole.

    Only the revoke half is applied here. The receiving account gets its
    entitlement from its own INITIAL_PURCHASE/RENEWAL event, verified the
    usual way; granting from a payload field would mean trusting the webhook
    body to hand out premium, which is the one thing this module refuses to do.

    Known gap: the caller drops the whole event when `app_user_id` matches no
    profile, so a transfer TO an account we have never seen leaves the losing
    side entitled. Closing it means letting TRANSFER past that check, which
    touches the idempotency insert — deliberately not done here.
    """
    event = payload.get("event") or {}
    losers = event.get("transferred_from") or []
    if not isinstance(losers, list):
        return False

    revoked = False
    for raw_id in losers:
        try:
            UUID(str(raw_id))
        except (ValueError, TypeError):
            continue  # anonymous RC id — never matched a profile anyway
        service.table("profiles").update({"is_premium": False}) \
            .eq("user_id", str(raw_id)).execute()
        revoked = True
    if revoked:
        logger.info("transfer %s revoked premium from %d account(s)", event_id, len(losers))
    return revoked


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

    if event_type == "TRANSFER":
        revoked = _revoke_transferred_from(service, payload, event_id)
        return {"status": "applied" if revoked else "stored"}

    if event_type not in PREMIUM_ON_EVENTS and event_type not in PREMIUM_OFF_EVENTS:
        return {"status": "stored"}  # informational event (TEST, ...)

    # Out-of-order guard: only the NEWEST event may change entitlement.
    newest = (
        service.table("iap_events").select("event_timestamp")
        .eq("user_id", app_user_id)
        .order("event_timestamp", desc=True).limit(1).execute().data
    )
    # Compared as datetimes, not strings: the two sides are produced by
    # different formatters (PostgREST vs datetime.isoformat) and a lexical
    # comparison only happens to work while they agree on fractional seconds
    # and offset spelling.
    if newest and _parsed(newest[0]["event_timestamp"]) > event_at:
        logger.info("stale event %s (%s) — entitlement unchanged", event_id, event_type)
        return {"status": "stale"}

    service.table("profiles").update(
        {"is_premium": event_type in PREMIUM_ON_EVENTS}
    ).eq("user_id", app_user_id).execute()
    return {"status": "applied"}
