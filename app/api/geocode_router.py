"""Birthplace geocoding endpoint. Auth via parent `api` router (P1)."""

from fastapi import APIRouter, HTTPException, Request, status
from geopy.exc import GeopyError

from app.astro.geocoding_service import geocode_place
from app.core.rate_limit import RateLimit, enforce
from app.schemas.chart_schemas import GeocodeRequest, GeocodeResponse

router = APIRouter(prefix="/geocode", tags=["geocode"])

# Onboarding needs a handful of searches; anything beyond this is a script.
# The cache key is the normalized text, so random strings bypass it — which
# is exactly the traffic that would get our Nominatim user agent blocked.
GEOCODE_LIMIT = RateLimit(max_calls=20, per_seconds=60)


@router.post("", response_model=GeocodeResponse)
def geocode(request: Request, body: GeocodeRequest) -> GeocodeResponse:
    enforce(request, "geocode", GEOCODE_LIMIT)
    try:
        results = geocode_place(body.place_text)
    except GeopyError:
        # Upstream Nominatim outage/throttle — tell the client to retry,
        # never degrade into guessing coordinates.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Geocoding service temporarily unavailable",
        )
    return GeocodeResponse(results=list(results))
