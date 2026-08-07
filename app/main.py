"""Stellune backend entrypoint.

Routing convention (enforced here, do not bypass):
- `public`: ONLY /health and, later, the RevenueCat webhook (own secret auth).
- `api`: everything else. Mounted with `require_user` so any router included
  here requires a verified Supabase JWT by default.
"""

from fastapi import APIRouter, Depends, FastAPI, Request

from app.api.auth_router import router as auth_router
from app.api.charts_router import router as charts_router
from app.api.geocode_router import router as geocode_router
from app.api.reports_router import router as reports_router
from app.core.auth import require_user

app = FastAPI(title="Stellune API", version="0.1.0")

public = APIRouter()
api = APIRouter(prefix="/v1", dependencies=[Depends(require_user)])
api.include_router(auth_router)
api.include_router(charts_router)
api.include_router(geocode_router)
api.include_router(reports_router)


@public.get("/health")
def health() -> dict:
    return {"status": "ok"}


@api.get("/me")
def me(request: Request) -> dict:
    """Smoke-test endpoint proving the default-auth convention works."""
    return {"user_id": request.state.user_id}


app.include_router(public)
app.include_router(api)
