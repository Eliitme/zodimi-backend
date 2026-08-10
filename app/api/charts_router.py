"""Natal chart endpoint. Auth comes from the parent `api` router (P1
convention) — do not add router-level auth here."""

from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException, status
from kerykeion import KerykeionException

from app.astro.aspect_calculator import calculate_major_aspects
from app.astro.chart_calculator import build_subject, calculate_natal_chart
from app.astro.chart_metrics import compute_metrics
from app.schemas.chart_schemas import BirthData, NatalChartResponse

router = APIRouter(prefix="/charts", tags=["charts"])


@router.post("/natal", response_model=NatalChartResponse)
def natal_chart(body: BirthData) -> NatalChartResponse:
    try:
        ZoneInfo(body.tz_of_birth)
    except (ZoneInfoNotFoundError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown IANA timezone: {body.tz_of_birth}",
        )

    try:
        chart = calculate_natal_chart(
            birth_date=body.birth_date,
            birth_time=body.birth_time,
            birth_time_unknown=body.birth_time_unknown,
            lat=body.lat,
            lon=body.lon,
            tz_of_birth=body.tz_of_birth,
        )
        # Aspects need the kerykeion subject; rebuild is cheap and keeps the
        # calculator modules independent of each other.
        subject = build_subject(
            body.birth_date, body.birth_time, body.birth_time_unknown,
            body.lat, body.lon, body.tz_of_birth,
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
