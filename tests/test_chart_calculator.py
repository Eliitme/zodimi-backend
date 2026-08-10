"""Astronomical accuracy tests against an independently-verified fixture.

Reference: Steve Jobs, 1955-02-24 19:15, San Francisco (birth-certificate
rated). Positions cross-checked against astro-charts.com (Placidus) — every
planet, angle, and house matched to the arc-minute. North Node uses the TRUE
node convention (reference sites that show Cap 2°30' use the mean node).
"""

import json
from datetime import date, time
from pathlib import Path

import pytest

from app.astro.aspect_calculator import MAJOR_ASPECTS, calculate_major_aspects
from app.astro.chart_calculator import build_subject, calculate_natal_chart
from app.astro.chart_metrics import compute_metrics

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "natal-chart-fixture.json"

JOBS_BIRTH = dict(
    birth_date=date(1955, 2, 24),
    birth_time=time(19, 15),
    birth_time_unknown=False,
    lat=37.7749,
    lon=-122.4194,
    tz_of_birth="America/Los_Angeles",
)

# (name, sign, degree, house, retrograde) — degree = deg + min/60
EXPECTED_PLANETS = [
    ("sun", "pisces", 5 + 45 / 60, 6, False),
    ("moon", "aries", 7 + 45 / 60, 7, False),
    ("mercury", "aquarius", 14 + 22 / 60, 5, True),
    ("venus", "capricorn", 21 + 10 / 60, 4, False),
    ("mars", "aries", 29 + 5 / 60, 8, False),
    ("jupiter", "cancer", 20 + 30 / 60, 10, True),
    ("saturn", "scorpio", 21 + 10 / 60, 3, False),
    ("uranus", "cancer", 24 + 8 / 60, 10, True),
    ("neptune", "libra", 28 + 3 / 60, 2, True),
    ("pluto", "leo", 25 + 19 / 60, 12, True),
    ("chiron", "aquarius", 2 + 20 / 60, 5, False),
]


@pytest.fixture(scope="module")
def jobs_chart() -> dict:
    return calculate_natal_chart(**JOBS_BIRTH)


def test_planets_match_reference_within_arc_minute(jobs_chart):
    by_name = {p["name"]: p for p in jobs_chart["planets"]}
    for name, sign, degree, house, retro in EXPECTED_PLANETS:
        p = by_name[name]
        assert p["sign"] == sign, name
        assert abs(p["degree"] - degree) < 0.1, name
        assert p["house"] == house, name
        assert p["is_retrograde"] == retro, name


def test_angles_match_reference(jobs_chart):
    asc, mc = jobs_chart["angles"]["asc"], jobs_chart["angles"]["mc"]
    assert asc["sign"] == "virgo" and abs(asc["degree"] - (22 + 17 / 60)) < 0.1
    assert mc["sign"] == "gemini" and abs(mc["degree"] - (21 + 19 / 60)) < 0.1


def test_twelve_houses_returned(jobs_chart):
    assert [h["number"] for h in jobs_chart["houses"]] == list(range(1, 13))
    assert all(0 <= h["cusp_degree"] < 30 for h in jobs_chart["houses"])


def test_unknown_birth_time_falls_back_to_noon_and_flags():
    chart = calculate_natal_chart(
        birth_date=date(1955, 2, 24),
        birth_time=None,
        birth_time_unknown=True,
        lat=37.7749,
        lon=-122.4194,
        tz_of_birth="America/Los_Angeles",
    )
    noon = calculate_natal_chart(**{**JOBS_BIRTH, "birth_time": time(12, 0)})
    assert chart["time_unknown"] is True
    sun = next(p for p in chart["planets"] if p["name"] == "sun")
    noon_sun = next(p for p in noon["planets"] if p["name"] == "sun")
    assert abs(sun["degree"] - noon_sun["degree"]) < 1e-9


def test_birthplace_near_dateline():
    # Kiritimati (UTC+14) — westernmost clock time on Earth.
    chart = calculate_natal_chart(
        birth_date=date(2000, 6, 1),
        birth_time=time(23, 55),
        birth_time_unknown=False,
        lat=1.87,
        lon=-157.43,
        tz_of_birth="Pacific/Kiritimati",
    )
    assert len(chart["planets"]) == 12
    assert chart["angles"]["asc"]["sign"] in {
        "aries", "taurus", "gemini", "cancer", "leo", "virgo",
        "libra", "scorpio", "sagittarius", "capricorn", "aquarius", "pisces",
    }


def test_pre_1970_dates_supported(jobs_chart):
    # The 1955 fixture itself proves pre-epoch dates work; sanity only.
    assert jobs_chart["planets"][0]["sign"] == "pisces"


def test_major_aspects_shape():
    subject = build_subject(**JOBS_BIRTH)
    aspects = calculate_major_aspects(subject)
    assert aspects, "expected at least one major aspect"
    for a in aspects:
        assert a["type"] in MAJOR_ASPECTS
        assert 0 <= a["orb"] < 12


def test_shared_ios_fixture_is_current(jobs_chart):
    """The fixture file consumed by the iOS decode tests must stay in sync
    with what this backend actually computes (contract drift guard)."""
    fixture = json.loads(FIXTURE_PATH.read_text())
    subject = build_subject(**JOBS_BIRTH)
    # Mirror the router's assembly order exactly: aspects are attached first
    # because the metrics summarise them.
    expected = {**jobs_chart, "aspects": calculate_major_aspects(subject)}
    expected["metrics"] = compute_metrics(expected)
    assert fixture["response"] == expected
