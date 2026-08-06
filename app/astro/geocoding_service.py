"""Birthplace geocoding: free-text place → candidate list with IANA timezone.

Uses Nominatim (OpenStreetMap) via geopy + timezonefinder — no API key.
Contract rule: ALWAYS return a list for the user to pick from, and an empty
list when nothing matches. Never silently guess a single result — a wrong
birthplace produces a silently-wrong chart with no way to detect it later.

Nominatim allows ~1 req/s; geocoding only happens during onboarding and
results are cached per normalized place text, so this stays within limits.
"""

from functools import lru_cache

from geopy.geocoders import Nominatim
from timezonefinder import TimezoneFinder

MAX_RESULTS = 5

# Nominatim median response is often >1s — geopy's 1s default timeout would
# turn routine slowness into spurious 503s during onboarding.
_geolocator = Nominatim(user_agent="stellune-backend", timeout=5)
_tz_finder: TimezoneFinder | None = None


def _get_tz_finder() -> TimezoneFinder:
    # Lazy singleton: TimezoneFinder loads ~40MB of polygon data on init.
    global _tz_finder
    if _tz_finder is None:
        _tz_finder = TimezoneFinder()
    return _tz_finder


def geocode_place(place_text: str) -> tuple[dict, ...]:
    """Free-text place → up to MAX_RESULTS candidates ({lat, lon, tz,
    display_name}), empty tuple when not found. Cached per normalized text.

    Historical timezone offsets (e.g. Saigon pre-1975) are NOT resolved here:
    the IANA zone name is stable, and chart computation applies the correct
    offset for the birth date via zoneinfo.
    """
    # Normalize BEFORE the cache boundary so "Hanoi" / " hanoi " / "HANOI"
    # share one entry (and one Nominatim call).
    normalized = " ".join(place_text.split()).lower()
    return _geocode_normalized(normalized)


@lru_cache(maxsize=1024)
def _geocode_normalized(normalized: str) -> tuple[dict, ...]:
    locations = _geolocator.geocode(
        normalized, exactly_one=False, limit=MAX_RESULTS, language="en"
    )
    if not locations:
        return ()

    tz_finder = _get_tz_finder()
    results = []
    for loc in locations:
        tz = tz_finder.timezone_at(lat=loc.latitude, lng=loc.longitude)
        if tz is None:
            continue  # open-ocean or unresolvable point — useless as birthplace
        results.append({
            "lat": round(loc.latitude, 6),
            "lon": round(loc.longitude, 6),
            "tz": tz,
            "display_name": loc.address,
        })
    return tuple(results)
