"""API contract for chart + geocode endpoints.

These models are mirrored by the iOS decode models and by the shared fixture
`tests/fixtures/natal-chart-fixture.json` — any change here must update the
fixture in BOTH repos (backend + iOS).
"""

from datetime import date, time

from pydantic import BaseModel, Field


class BirthData(BaseModel):
    # Bounds = coverage of the bundled Chiron ephemeris file (seas_18.se1,
    # 1800–2399); outside it the calculation errors. Also catches typo years.
    birth_date: date = Field(ge=date(1800, 1, 1), le=date(2399, 12, 31))
    birth_time: time | None = None
    birth_time_unknown: bool = False
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    tz_of_birth: str  # IANA zone name, e.g. "Asia/Ho_Chi_Minh"


class PlanetPosition(BaseModel):
    name: str  # sun..pluto, chiron, north_node
    sign: str  # full lowercase, e.g. "capricorn"
    degree: float  # position within sign, 0-30
    house: int = Field(ge=1, le=12)
    is_retrograde: bool


class HouseCusp(BaseModel):
    number: int = Field(ge=1, le=12)
    sign: str
    cusp_degree: float


class Aspect(BaseModel):
    p1: str
    p2: str
    type: str  # conjunction | opposition | trine | square | sextile
    orb: float


class AnglePosition(BaseModel):
    sign: str
    degree: float


class Angles(BaseModel):
    asc: AnglePosition
    mc: AnglePosition


class NatalChartResponse(BaseModel):
    planets: list[PlanetPosition]
    houses: list[HouseCusp]
    aspects: list[Aspect]
    angles: Angles
    # When true the birth time was unknown (noon fallback): Asc/MC and house
    # placements are unreliable and clients MUST show a warning.
    time_unknown: bool


class GeocodeRequest(BaseModel):
    place_text: str = Field(min_length=1, max_length=200)


class GeocodeResult(BaseModel):
    lat: float
    lon: float
    tz: str
    display_name: str


class GeocodeResponse(BaseModel):
    # Empty list = not found; client lets the user retry or enter manually.
    results: list[GeocodeResult]


# --- Synastry (contract defined here; endpoint ships with monetization) ---


class SynastryRequest(BaseModel):
    profile_a: BirthData
    profile_b: BirthData


class SynastryResponse(BaseModel):
    inter_aspects: list[Aspect]
