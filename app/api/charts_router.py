"""Natal chart endpoint. Auth comes from the parent `api` router (P1
convention) — do not add router-level auth here."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, Request, status
from kerykeion import KerykeionException

from app.astro.aspect_calculator import calculate_major_aspects
from app.astro.chart_calculator import build_subject, chart_from_subject
from app.astro.chart_metrics import compute_metrics
from app.core.rate_limit import RateLimit, enforce
from app.schemas.chart_schemas import BirthData, NatalChartResponse

router = APIRouter(prefix="/charts", tags=["charts"])

# Swiss Ephemeris is the most expensive thing this API does per request, and
# a guest account costs nothing to create — without a limit one script can
# saturate the single API container's CPU. The cost here is compute, not a
# third-party bill, so the ceiling can be generous: a real reader computes
# one chart per birth-data change (iOS caches the result on disk, the web
# recomputes only when the form changes), nowhere near thirty a minute.
CHART_LIMIT = RateLimit(max_calls=30, per_seconds=60)


@router.post("/natal", response_model=NatalChartResponse)
def natal_chart(request: Request, body: BirthData) -> NatalChartResponse:
    enforce(request, "chart", CHART_LIMIT)

    try:
        ZoneInfo(body.tz_of_birth)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown IANA timezone: {body.tz_of_birth}",
        )

    try:
        # One subject for the whole response: the chart and its aspects both
        # read from it, and building it twice meant computing the ephemeris
        # twice for a single request.
        subject = build_subject(
            body.birth_date, body.birth_time, body.birth_time_unknown,
            body.lat, body.lon, body.tz_of_birth,
        )
        chart = chart_from_subject(
            subject, bool(body.birth_time_unknown or body.birth_time is None)
        )
        chart["aspects"] = calculate_major_aspects(subject)
        # After aspects, not before: the aspect mix is part of the
        # metrics, and pure arithmetic besides — it rides along rather
        # than costing the client a second request.
        chart["metrics"] = compute_metrics(chart)
    except KerykeionException as exc:
        # Backstop for input the schema bounds can't anticipate — the
        # message is actionable ("ambiguous time", "no ephemeris data", ...).
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Chart computation rejected the input: {exc}",
        )
    return NatalChartResponse(**chart)
