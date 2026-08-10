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


class BalanceContribution(BaseModel):
    """One body's share of a balance, so a client can show the working."""
    body: str
    sign: str
    group: str
    weight: float


class Balance(BaseModel):
    """Weighted distribution across elements or modalities."""
    scores: dict[str, float]
    percent: dict[str, int]
    dominant: str | None
    # Elements or modalities with no bodies at all — often the more telling
    # half of the reading.
    missing: list[str]
    contributions: list[BalanceContribution]


class Hemispheres(BaseModel):
    below_horizon: int
    above_horizon: int
    east: int
    west: int
    vertical_dominant: str | None  # below | above | null when tied
    horizontal_dominant: str | None  # east | west | null when tied
    quadrants: dict[str, int]  # q1..q4
    counted: int


class ChartRuler(BaseModel):
    rising_sign: str
    ruler: str
    sign: str | None
    house: int | None


class AspectMix(BaseModel):
    counts: dict[str, int]
    total: int
    harmonious: int
    challenging: int
    dominant: str | None


class ChartMetrics(BaseModel):
    """Arithmetic over the chart — no model call, fully reproducible.

    Weights are part of the payload on purpose: the split is a convention
    astrologers disagree about, so a client can show how a number was
    reached instead of asking readers to take it on trust.
    """
    elements: Balance
    modalities: Balance
    aspects: AspectMix
    weights: dict[str, float]
    excluded: list[str]
    # Absent when the birth time is unknown: houses rest on a noon guess, so
    # anything derived from them would be presented as fact on an assumption.
    hemispheres: Hemispheres | None = None
    chart_ruler: ChartRuler | None = None


class NatalChartResponse(BaseModel):
    planets: list[PlanetPosition]
    houses: list[HouseCusp]
    aspects: list[Aspect]
    angles: Angles
    # When true the birth time was unknown (noon fallback): Asc/MC and house
    # placements are unreliable and clients MUST show a warning.
    time_unknown: bool
    metrics: ChartMetrics


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
