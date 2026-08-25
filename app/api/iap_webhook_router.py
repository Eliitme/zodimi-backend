"""RevenueCat webhook — the ONE endpoint allowlisted from `require_user()`
(mounted on the public router): it authenticates with its own shared secret,
not a user JWT.

Defense layers, in order:
1. Constant-time secret compare (Authorization header).
2. `app_user_id` is resolved to a known profile or to NULL. An event for
   an id we cannot place is still stored (audit + idempotency) but can
   never move an entitlement.
3. DB-level idempotency: unique index on `revenuecat_event_id` — a redelivered
   event inserts nothing and must NOT re-apply entitlement changes.
4. Timestamp ordering: RevenueCat does not guarantee delivery order — an
   EXPIRATION older than an already-processed RENEWAL must never revoke a
   paying user's entitlement.
5. TRANSFER revokes the account a subscription moved away from, so one
   purchase cannot leave two accounts permanently entitled. It is handled
   independently of `app_user_id`, because the account being revoked is
   named in the payload rather than being the event's own subject.
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


def _as_uuid(value) -> str | None:
    """RevenueCat ids are only sometimes ours. Anything that is not a UUID is
    an RC-side id (e.g. "$RCAnonymousID:...") that can never name a profile."""
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        return None


def _newest_event_at(service, user_id: str) -> datetime | None:
    """When we last saw an event for this account, or None."""
    rows = (
        service.table("iap_events").select("event_timestamp")
        .eq("user_id", user_id)
        .order("event_timestamp", desc=True).limit(1).execute().data
    )
    return _parsed(rows[0]["event_timestamp"]) if rows else None


def _revoke_transferred_from(service, payload: dict, event_id: str,
                             event_at: datetime) -> bool:
    """Take premium away from the account a subscription moved OFF.

    Apple lets a subscription follow the buyer to a different app user, and
    RevenueCat reports that as one TRANSFER naming both sides. Treating it as
    informational left the losing account premium forever — one purchase,
    two permanent entitlements, which is a subscription-sharing loophole.

    Only the revoke half is applied here. The receiving account gets its
    entitlement from its own INITIAL_PURCHASE/RENEWAL event, verified the
    usual way; granting from a payload field would mean trusting the webhook
    body to hand out premium, which is the one thing this module refuses to do.

    Each revoke carries its own ordering guard, against the LOSER's timeline
    rather than the event's subject. Delivery order is not guaranteed, and
    someone who transfers a subscription away and then buys their own would
    otherwise be stripped of the new one by the late-arriving transfer.
    """
    event = payload.get("event") or {}
    losers = event.get("transferred_from") or []
    if not isinstance(losers, list):
        return False

    revoked = 0
    for raw_id in losers:
        loser_id = _as_uuid(raw_id)
        if loser_id is None:
            continue
        newest = _newest_event_at(service, loser_id)
        if newest is not None and newest > event_at:
            logger.info("stale transfer %s — %s keeps its entitlement", event_id, loser_id)
            continue
        service.table("profiles").update({"is_premium": False}) \
            .eq("user_id", loser_id).execute()
        revoked += 1
    if revoked:
        logger.info("transfer %s revoked premium from %d account(s)", event_id, revoked)
    return revoked > 0


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

    service = get_service_client()
    # Resolve the event's subject to one of our users, or to NULL. This used
    # to be two drop-outs — non-UUID id, then unknown profile — which also
    # discarded TRANSFERs naming an account we DO know, because a transfer is
    # addressed to its recipient and the recipient may be a stranger. The
    # column is nullable precisely so an unplaceable event can still be
    # stored for audit and idempotency.
    owner_user_id = _as_uuid(app_user_id)
    if owner_user_id is not None:
        known = (
            service.table("profiles").select("user_id")
            .eq("user_id", owner_user_id).limit(1).execute().data
        )
        if not known:
            owner_user_id = None
    if owner_user_id is None:
        logger.info("webhook subject %s is not one of our users (event %s)",
                    app_user_id, event_id)

    try:
        service.table("iap_events").insert({
            "user_id": owner_user_id,
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
        # Deliberately before the owner check: the account losing the
        # subscription is named in the payload, so this works even when the
        # account gaining it is a stranger to us.
        revoked = _revoke_transferred_from(service, payload, event_id, event_at)
        return {"status": "applied" if revoked else "stored"}

    if event_type not in PREMIUM_ON_EVENTS and event_type not in PREMIUM_OFF_EVENTS:
        return {"status": "stored"}  # informational event (TEST, ...)

    if owner_user_id is None:
        # Stored above for audit; there is no account here to move.
        return {"status": "dropped"}

    # Out-of-order guard: only the NEWEST event may change entitlement.
    # Compared as datetimes, not strings: the two sides are produced by
    # different formatters (PostgREST vs datetime.isoformat) and a lexical
    # comparison only happens to work while they agree on fractional seconds
    # and offset spelling.
    newest = _newest_event_at(service, owner_user_id)
    if newest is not None and newest > event_at:
        logger.info("stale event %s (%s) — entitlement unchanged", event_id, event_type)
        return {"status": "stale"}

    service.table("profiles").update(
        {"is_premium": event_type in PREMIUM_ON_EVENTS}
    ).eq("user_id", owner_user_id).execute()
    return {"status": "applied"}
