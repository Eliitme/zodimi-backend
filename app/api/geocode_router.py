"""Birthplace geocoding endpoint. Auth via parent `api` router (P1)."""

from fastapi import APIRouter, HTTPException, status
from geopy.exc import GeopyError

from app.astro.geocoding_service import geocode_place
from app.schemas.chart_schemas import GeocodeRequest, GeocodeResponse

router = APIRouter(prefix="/geocode", tags=["geocode"])


@router.post("", response_model=GeocodeResponse)
def geocode(body: GeocodeRequest) -> GeocodeResponse:
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
