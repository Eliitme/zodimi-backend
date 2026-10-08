"""In-process rate limiting for endpoints that cost more than a DB read.

Two reasons an endpoint lands here:

- It proxies a third party whose usage policy applies to OUR identity.
  Geocoding proxies Nominatim: if the web tier lets anyone hammer it,
  Nominatim blocks the shared user agent and onboarding breaks in the iOS
  app too. The blast radius is what makes this worth guarding, not the cost.
- It burns real CPU. Chart computation runs Swiss Ephemeris, which is the
  most expensive thing this API does per request.

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


#: How much more an IP may do than a single account.
#:
#: Anonymous callers are counted twice — once per account, once per address —
#: because a guest account costs nothing to create, so a per-account limit
#: alone is bypassed by signing up again. But an address is not a person: a
#: school, an office or a phone network is one address for hundreds of
#: readers, and holding that to one reader's ceiling locks all of them out
#: together. The per-account limit is still the one that stops a single
#: abuser; this one only has to stop a flood.
IP_BUCKET_MULTIPLIER = 10


_hits: dict[str, deque[float]] = defaultdict(deque)

# Without a sweep this dict grows one entry per (bucket, caller) for the life
# of the process — anonymous callers alone would make that unbounded. The
# retention is far longer than any window in use, so a key is only dropped
# once it cannot possibly still be limiting anyone.
_SWEEP_EVERY_CALLS = 1000
_MAX_WINDOW_SECONDS = 3600
_calls_since_sweep = 0


def _consume(key: str, limit: RateLimit, now: float) -> bool:
    window = _hits[key]
    cutoff = now - limit.per_seconds
    while window and window[0] < cutoff:
        window.popleft()
    if len(window) >= limit.max_calls:
        return False
    window.append(now)
    return True


def _sweep(now: float) -> None:
    cutoff = now - _MAX_WINDOW_SECONDS
    stale = [key for key, window in _hits.items() if not window or window[-1] < cutoff]
    for key in stale:
        del _hits[key]


def _client_ip(request: Request) -> str:
    """The caller's address as the reverse proxy saw it.

    The LAST X-Forwarded-For hop, not the first. Caddy appends the peer
    address to whatever the client already sent, so the first entry is
    attacker-controlled text and the last is the one Caddy wrote itself —
    reading the first turned the IP bucket into "one bucket per header
    value", which is no limit at all.

    This assumes exactly ONE trusted proxy in front of the API (see
    deploy/Caddyfile.example, which also pins trusted_proxies so Caddy
    replaces rather than extends an untrusted header). Adding a CDN or a
    second proxy means counting back from the end by that many hops.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    last_hop = forwarded.split(",")[-1].strip() if forwarded else ""
    if last_hop:
        return last_hop
    return request.client.host if request.client else "?"


def enforce(request: Request, bucket: str, limit: RateLimit) -> None:
    """Counts the caller against `limit`, raising 429 when exhausted.

    Anonymous users share a per-IP bucket as well as their per-user one:
    a signed-up guest costs nothing to create, so a per-user limit alone
    would be trivially bypassed by signing up again. The shared bucket is
    `IP_BUCKET_MULTIPLIER` times larger, because an address is many people.
    """
    global _calls_since_sweep

    now = time.monotonic()
    _calls_since_sweep += 1
    if _calls_since_sweep >= _SWEEP_EVERY_CALLS:
        _calls_since_sweep = 0
        _sweep(now)

    user_id = getattr(request.state, "user_id", None)
    claims = getattr(request.state, "jwt_claims", {}) or {}

    checks = [(f"{bucket}:user:{user_id}", limit)]
    if claims.get("is_anonymous"):
        checks.append((
            f"{bucket}:ip:{_client_ip(request)}",
            RateLimit(
                max_calls=limit.max_calls * IP_BUCKET_MULTIPLIER,
                per_seconds=limit.per_seconds,
            ),
        ))

    for key, applicable in checks:
        if not _consume(key, applicable, now):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many requests — please slow down",
            )


def reset() -> None:
    """Test helper: clears all windows."""
    global _calls_since_sweep

    _hits.clear()
    _calls_since_sweep = 0
