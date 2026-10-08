"""The parts of Astro Rooms that are decisions rather than routing.

Kept out of the routers so the two things worth testing in isolation — what
an invite token is, and what makes two readings the same reading — do not
need an HTTP request to exercise.
"""

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

# Imported as a MODULE, not as the value: binding the constant here would
# snapshot it at import, and bumping the template version would then stop
# changing the pair revision — which is the one thing a bump is for.
from app.astro import synastry_preview
from app.core.config import get_settings

# Bumped when the aspect calculation itself changes — a different ephemeris
# or a different orb set makes the same two charts a different reading, even
# with the same rules on top.
ALGORITHM_VERSION = "synastry-v1"

#: 32 bytes, per the spec. Long enough that guessing is not a strategy and
#: the rate limiter only has to stop the noise.
TOKEN_BYTES = 32

DEFAULT_TTL_DAYS = 7

#: How many times one invitation may be looked at before it stops answering.
#: The per-caller rate limit stops a fast sweep; this stops a slow one from a
#: rotating set of anonymous sessions, which is cheap to create.
MAX_INVITE_ATTEMPTS = 20


def new_invite_token() -> tuple[str, str]:
    """A fresh token and the hash to store. The token is returned exactly
    once, to its creator; it is not recoverable from the stored hash."""
    token = secrets.token_urlsafe(TOKEN_BYTES)
    return token, hash_invite_token(token)


def hash_invite_token(token: str) -> str:
    """Keyed, not plain SHA-256.

    A bare digest of a random 32-byte token is already impractical to
    reverse, but the key means a leaked database dump is not enough on its
    own: an attacker would also need the server secret before they could
    recognise a token they had seen elsewhere, or build a lookup table if the
    token space were ever narrowed.
    """
    secret = get_settings().room_invite_secret.encode()
    return hmac.new(secret, token.encode(), hashlib.sha256).hexdigest()


def invite_expiry(days: int = DEFAULT_TTL_DAYS) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


def invite_url(token: str) -> str:
    """Where the link points.

    Returns the path alone when no canonical host is configured, rather than
    inventing one: a link to the wrong domain is worse than a client having
    to prepend its own origin, and the host is an owner decision that is not
    made yet.
    """
    base = get_settings().web_base_url.rstrip("/")
    return f"{base}/m/{token}" if base else f"/m/{token}"


def pair_revision_hash(
    a_revision: int | None, b_revision: int | None, locale: str
) -> str:
    """Identity of a reading: which two charts, read how.

    Everything that can change the output is in here, and nothing else is —
    no user ids, no names, no birth data, because this value travels in API
    responses and logs. Either side editing their birth data moves their
    revision and therefore this hash, so the old reading is kept rather than
    overwritten, and the new one is computed rather than served stale.

    A null revision means "unknown", which must not collide with any real
    revision: a row written before revisions existed cannot be assumed
    current.
    """
    parts = [
        str(a_revision) if a_revision is not None else "unknown",
        str(b_revision) if b_revision is not None else "unknown",
        locale,
        ALGORITHM_VERSION,
        synastry_preview.TEMPLATE_VERSION,
    ]
    digest = hashlib.sha256("|".join(parts).encode()).hexdigest()
    return f"sha256-{digest}"
