"""Natal chart computation on top of kerykeion (Swiss Ephemeris).

Results are deterministic for a given birth data, so responses can be cached
forever by the caller. Sign/point identifiers use full lowercase names to
match the `sign_interpretations` table and the iOS decode models.
"""

from datetime import date, time

from kerykeion import AstrologicalSubject

# kerykeion abbreviates signs ("Ari") — the API contract uses full names.
SIGN_NAMES = {
    "Ari": "aries", "Tau": "taurus", "Gem": "gemini", "Can": "cancer",
    "Leo": "leo", "Vir": "virgo", "Lib": "libra", "Sco": "scorpio",
    "Sag": "sagittarius", "Cap": "capricorn", "Aqu": "aquarius", "Pis": "pisces",
}

HOUSE_NUMBERS = {
    "First_House": 1, "Second_House": 2, "Third_House": 3, "Fourth_House": 4,
    "Fifth_House": 5, "Sixth_House": 6, "Seventh_House": 7, "Eighth_House": 8,
    "Ninth_House": 9, "Tenth_House": 10, "Eleventh_House": 11, "Twelfth_House": 12,
}

# Points exposed in the contract: 10 planets + Chiron + true North Node.
PLANET_ATTRS = [
    ("sun", "sun"), ("moon", "moon"), ("mercury", "mercury"),
    ("venus", "venus"), ("mars", "mars"), ("jupiter", "jupiter"),
    ("saturn", "saturn"), ("uranus", "uranus"), ("neptune", "neptune"),
    ("pluto", "pluto"), ("chiron", "chiron"),
    # True Node (not mean): matches astro.com's default, and kerykeion 5.12
    # only computes `true_node` in its default point set (`mean_node` is None).
    ("true_node", "north_node"),
]

HOUSE_ATTRS = [
    "first_house", "second_house", "third_house", "fourth_house",
    "fifth_house", "sixth_house", "seventh_house", "eighth_house",
    "ninth_house", "tenth_house", "eleventh_house", "twelfth_house",
]


def build_subject(
    birth_date: date,
    birth_time: time | None,
    birth_time_unknown: bool,
    lat: float,
    lon: float,
    tz_of_birth: str,
) -> AstrologicalSubject:
    """Create a kerykeion subject. Unknown birth time falls back to noon —
    planet signs stay reliable, but Asc/MC and house placements do not
    (callers must surface `time_unknown` so clients can warn the user)."""
    if birth_time_unknown or birth_time is None:
        birth_time = time(12, 0)
    return AstrologicalSubject(
        name="subject",
        year=birth_date.year,
        month=birth_date.month,
        day=birth_date.day,
        hour=birth_time.hour,
        minute=birth_time.minute,
        lat=lat,
        lng=lon,
        tz_str=tz_of_birth,
        city="birthplace",  # display-only; online=False means no lookup
        online=False,
        # DST convention: clock times that occur twice (fall-back hour)
        # resolve to STANDARD time; times in the spring-forward gap resolve
        # instead of raising. Verified no effect on unambiguous times.
        is_dst=False,
    )


def calculate_natal_chart(
    birth_date: date,
    birth_time: time | None,
    birth_time_unknown: bool,
    lat: float,
    lon: float,
    tz_of_birth: str,
) -> dict:
    """Birth data → chart dict matching the NatalChartResponse contract
    (aspects are added by the router via aspect_calculator)."""
    subject = build_subject(
        birth_date, birth_time, birth_time_unknown, lat, lon, tz_of_birth
    )

    planets = []
    for attr, contract_name in PLANET_ATTRS:
        p = getattr(subject, attr)
        planets.append({
            "name": contract_name,
            "sign": SIGN_NAMES[p.sign],
            "degree": round(p.position, 4),
            "house": HOUSE_NUMBERS[p.house],
            "is_retrograde": bool(p.retrograde),
        })

    houses = []
    for i, attr in enumerate(HOUSE_ATTRS, start=1):
        h = getattr(subject, attr)
        houses.append({
            "number": i,
            "sign": SIGN_NAMES[h.sign],
            "cusp_degree": round(h.position, 4),
        })

    chart = {
        "planets": planets,
        "houses": houses,
        "angles": {
            "asc": {
                "sign": SIGN_NAMES[subject.ascendant.sign],
                "degree": round(subject.ascendant.position, 4),
            },
            "mc": {
                "sign": SIGN_NAMES[subject.medium_coeli.sign],
                "degree": round(subject.medium_coeli.position, 4),
            },
            # Exactly opposite asc/mc, but returned rather than derived on
            # the client: two clients each doing +180° with a wraparound is
            # two chances to get it wrong, and the ephemeris already has them.
            "dsc": {
                "sign": SIGN_NAMES[subject.descendant.sign],
                "degree": round(subject.descendant.position, 4),
            },
            "ic": {
                "sign": SIGN_NAMES[subject.imum_coeli.sign],
                "degree": round(subject.imum_coeli.position, 4),
            },
        },
        # A missing birth time is treated as unknown even if the flag says
        # otherwise — the chart was computed with the noon fallback, so
        # Asc/MC/houses must never be presented as reliable.
        "time_unknown": bool(birth_time_unknown or birth_time is None),
    }
    return chart
