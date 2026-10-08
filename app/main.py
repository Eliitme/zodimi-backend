"""Stellune backend entrypoint.

Routing convention (enforced here, do not bypass):
- `public`: ONLY /health and, later, the RevenueCat webhook (own secret auth).
- `api`: everything else. Mounted with `require_user` so any router included
  here requires a verified Supabase JWT by default.
"""

import logging

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware

from app.api.account_router import router as account_router
from app.api.auth_router import router as auth_router
from app.api.charts_router import router as charts_router
from app.api.devices_router import router as devices_router
from app.api.geocode_router import router as geocode_router
from app.api.iap_sync_router import router as iap_sync_router
from app.api.iap_webhook_router import router as iap_webhook_router
from app.api.invitations_router import router as invitations_router
from app.api.reports_router import router as reports_router
from app.api.room_results_router import router as room_results_router
from app.api.rooms_router import router as rooms_router
from app.api.synastry_router import router as synastry_router
from app.api.tarot_router import router as tarot_router
from app.core.auth import require_user
from app.core.config import get_settings

logger = logging.getLogger("stellune.api")

app = FastAPI(title="Stellune API", version="0.1.0")

# The web app runs on its own origin; the iOS app is unaffected (native
# clients do not enforce CORS). Explicit origins only — see config.
_origins = get_settings().allowed_origins
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        # Auth travels in the Authorization header, not cookies.
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept-Language"],
    )
else:
    logger.warning(
        "WEB_ORIGINS is empty — browser clients will be blocked by CORS. "
        "Set it to the web app's origin(s) in this environment's .env."
    )

public = APIRouter()
api = APIRouter(prefix="/v1", dependencies=[Depends(require_user)])
api.include_router(account_router)
api.include_router(auth_router)
api.include_router(charts_router)
api.include_router(devices_router)

if get_settings().dev_tools:
    # Browser test console — never mounted in production (the /v1/dev
    # helpers grant premium on demand).
    from fastapi.staticfiles import StaticFiles

    from app.api.devtools_router import router as devtools_router

    api.include_router(devtools_router)
    app.mount(
        "/devtools",
        StaticFiles(directory="devtools", html=True),
        name="devtools",
    )
api.include_router(geocode_router)
api.include_router(iap_sync_router)
api.include_router(invitations_router)
api.include_router(reports_router)
# Two routers share the /rooms prefix: one owns the room itself, the other
# the reading inside it. Split by lifetime, not by URL — a room outlives any
# particular reading of it.
api.include_router(rooms_router)
api.include_router(room_results_router)
api.include_router(synastry_router)
api.include_router(tarot_router)


@public.get("/health")
def health() -> dict:
    return {"status": "ok"}


@public.get("/v1/dev/config")
def dev_config() -> dict:
    """Lets the test console bootstrap itself (Supabase URL + anon key) so
    those values live in ONE place. Public because the console needs it
    before signing in; both values are already shipped in the iOS app."""
    settings = get_settings()
    if not settings.dev_tools:
        raise HTTPException(status_code=404, detail="Not found")
    return {
        # host.docker.internal is how the CONTAINER reaches the host stack;
        # a browser has to use localhost or the request never resolves.
        "supabase_url": settings.supabase_url.replace("host.docker.internal", "127.0.0.1"),
        "supabase_anon_key": settings.supabase_anon_key,
    }


# The RevenueCat webhook is the ONE allowlisted exception to default JWT
# auth — it authenticates with its own shared secret (see its module doc).
public.include_router(iap_webhook_router, prefix="/v1")


@api.get("/me")
def me(request: Request) -> dict:
    """Smoke-test endpoint proving the default-auth convention works."""
    return {"user_id": request.state.user_id}


app.include_router(public)
app.include_router(api)
