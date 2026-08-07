"""In-process rate limiting for endpoints backed by a third party.

Geocoding proxies Nominatim, whose usage policy applies to OUR user agent:
if the web tier lets anyone hammer it, Nominatim blocks the shared UA and
onboarding breaks in the iOS app too. The blast radius is what makes this
worth guarding, not the cost.

Deliberately in-process (no Redis): one API container today, and a limit
that resets on deploy is still far better than none. Move this to the
reverse proxy or a shared store when the API scales past one instance.
"""

import time
from collections import defaultdict, deque
from dataclasses import dataclass

from fastapi import HTTPException, Request, status


@dataclass(frozen=True)
class RateLimit:
    max_calls: int
    per_seconds: int


_hits: dict[str, deque[float]] = defaultdict(deque)


def _consume(key: str, limit: RateLimit, now: float) -> bool:
    window = _hits[key]
    cutoff = now - limit.per_seconds
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= limit.max_calls:
        return False
    window.append(now)
    return True


def enforce(request: Request, bucket: str, limit: RateLimit) -> None:
    """Counts the caller against `limit`, raising 429 when exhausted.

    Anonymous users share a per-IP bucket as well as their per-user one:
    a signed-up guest costs nothing to create, so a per-user limit alone
    would be trivially bypassed by signing up again.
    """
    now = time.monotonic()
    user_id = getattr(request.state, "user_id", None)
    claims = getattr(request.state, "jwt_claims", {}) or {}

    keys = [f"{bucket}:user:{user_id}"]
    if claims.get("is_anonymous"):
        client_ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip()
        keys.append(f"{bucket}:ip:{client_ip or (request.client.host if request.client else '?')}")

    for key in keys:
        if not _consume(key, limit, now):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests — please slow down",
            )


def reset() -> None:
    """Test helper: clears all windows."""
    _hits.clear()
