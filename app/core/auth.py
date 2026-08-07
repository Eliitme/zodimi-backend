"""Shared auth dependency — every router MUST require a verified Supabase JWT.

Usage: include routers under the authenticated parent router in `app.main`
(which applies `Depends(require_user)` by default). The ONLY allowed
exceptions are `/health` and the RevenueCat webhook (authenticated by its own
shared secret, added in the monetization phase).
"""

import jwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWKClient

from app.core.config import get_settings

_bearer = HTTPBearer(auto_error=False)
_jwks_client: PyJWKClient | None = None


def _get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        url = f"{get_settings().supabase_url}/auth/v1/.well-known/jwks.json"
        _jwks_client = PyJWKClient(url, cache_keys=True)
    return _jwks_client


def decode_token(token: str) -> dict:
    """Verify a Supabase JWT and return its claims. Public because the
    guest-merge endpoint also verifies a second (guest) token."""
    settings = get_settings()
    if settings.supabase_jwt_secret:
        # Local dev / legacy projects sign with a shared HS256 secret.
        return jwt.decode(
            token,
            settings.supabase_jwt_secret,
            algorithms=["HS256"],
            audience="authenticated",
        )
    # Cloud projects with asymmetric signing keys publish a JWKS endpoint.
    signing_key = _get_jwks_client().get_signing_key_from_jwt(token)
    return jwt.decode(
        token,
        signing_key.key,
        algorithms=["ES256", "RS256"],
        audience="authenticated",
    )


def require_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> str:
    """Verify the Supabase JWT and return the caller's user id (sub claim)."""
    if credentials is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
        )
    try:
        claims = decode_token(credentials.credentials)
    except jwt.PyJWKClientConnectionError:
        # Auth-server outage is not the caller's fault — a 401 here would
        # make clients wrongly sign the user out.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Auth keys temporarily unavailable",
        )
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    user_id = claims.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token has no subject",
        )
    # Stash for handlers/middleware that need the raw token or claims.
    request.state.user_id = user_id
    request.state.jwt_claims = claims
    request.state.jwt = credentials.credentials
    return user_id
