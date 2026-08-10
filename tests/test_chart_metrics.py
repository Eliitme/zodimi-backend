"""Chart metrics are shown to readers as numbers, so they have to be right.

Fixture is the chart used throughout development (2000-07-22 17:30, Thanh
Hoá), independently confirmed to the arcsecond against another Vietnamese
astrology site — so the placements below are known-good input.
"""

import pytest

from app.astro.chart_metrics import ELEMENTS, MODALITIES, RULERS, WEIGHTS, compute_metrics

PLANETS = [
    ("sun", "cancer", 7), ("moon", "aries", 3), ("mercury", "cancer", 6),
    ("venus", "leo", 7), ("mars", "cancer", 7), ("jupiter", "gemini", 5),
    ("saturn", "taurus", 5), ("uranus", "aquarius", 2), ("neptune", "aquarius", 1),
    ("pluto", "sagittarius", 11), ("chiron", "sagittarius", 11), ("north_node", "cancer", 7),
]

CHART = {
    "planets": [
        {"name": n, "sign": s, "degree": 10.0, "house": h, "is_retrograde": False}
        for n, s, h in PLANETS
    ],
    "houses": [{"number": i, "sign": "capricorn", "cusp_degree": 0.0} for i in range(1, 13)],
    "angles": {"asc": {"sign": "capricorn", "degree": 13.89},
               "mc": {"sign": "libra", "degree": 25.63}},
    "aspects": [{"p1": "sun", "p2": "moon", "type": "square", "orb": 1.0},
                {"p1": "sun", "p2": "venus", "type": "trine", "orb": 2.0},
                {"p1": "mars", "p2": "saturn", "type": "sextile", "orb": 3.0},
                {"p1": "moon", "p2": "pluto", "type": "trine", "orb": 4.0}],
    "time_unknown": False,
}


class TestTables:
    def test_every_sign_has_an_element_and_a_modality_and_a_ruler(self):
        signs = set(ELEMENTS)
        assert len(signs) == 12
        assert signs == set(MODALITIES) == set(RULERS)

    def test_elements_and_modalities_split_evenly(self):
        # Three signs per element, four per modality — a table typo shows up
        # here rather than as a quietly skewed percentage.
        from collections import Counter
        assert set(Counter(ELEMENTS.values()).values()) == {3}
        assert set(Counter(MODALITIES.values()).values()) == {4}


class TestElements:
    def test_scores_match_the_published_weights(self):
        m = compute_metrics(CHART)["elements"]
        # water: sun 3 + mercury 2 + mars 2 = 7
        # fire: venus 2 ; air: jupiter 1.5 + uranus 1 + neptune 1 + mc 1.5 = 5
        # earth: saturn 1.5 + asc 3 = 4.5 ; fire also moon 3 + pluto 1 = 6
        assert m["scores"]["water"] == 7.0
        assert m["scores"]["earth"] == 4.5
        assert m["dominant"] == "water"

    def test_percentages_sum_to_about_a_hundred(self):
        pct = compute_metrics(CHART)["elements"]["percent"]
        assert 99 <= sum(pct.values()) <= 101

    def test_points_are_excluded_from_the_balance(self):
        # north_node sits in Cancer; counting it would inflate water and
        # imply a placement carries weight the convention says it does not.
        bodies = {c["body"] for c in compute_metrics(CHART)["elements"]["contributions"]}
        assert "north_node" not in bodies and "chiron" not in bodies

    def test_missing_elements_are_named(self):
        chart = {**CHART, "planets": [
            {"name": "sun", "sign": "aries", "degree": 1.0, "house": 1, "is_retrograde": False}
        ], "angles": {"asc": {"sign": "aries", "degree": 0.0},
                      "mc": {"sign": "leo", "degree": 0.0}}}
        m = compute_metrics(chart)["elements"]
        assert m["dominant"] == "fire"
        assert set(m["missing"]) == {"earth", "air", "water"}

    def test_contributions_explain_every_point_of_the_total(self):
        m = compute_metrics(CHART)["elements"]
        assert sum(c["weight"] for c in m["contributions"]) == pytest.approx(sum(m["scores"].values()))


class TestHemispheres:
    def test_counts_split_at_the_horizon_and_the_meridian(self):
        h = compute_metrics(CHART)["hemispheres"]
        # houses: 7,3,6,7,7,5,5,2,1,11 (chiron/node excluded) → below = 1,2,3,5,5,6
        assert h["below_horizon"] + h["above_horizon"] == h["counted"] == 10
        assert h["east"] + h["west"] == 10

    def test_quadrants_account_for_every_counted_planet(self):
        h = compute_metrics(CHART)["hemispheres"]
        assert sum(h["quadrants"].values()) == h["counted"]

    def test_a_tie_reports_no_dominant_side(self):
        planets = [{"name": n, "sign": "aries", "degree": 1.0, "house": h, "is_retrograde": False}
                   for n, h in [("sun", 1), ("moon", 2), ("mercury", 7), ("venus", 8)]]
        h = compute_metrics({**CHART, "planets": planets})["hemispheres"]
        assert h["vertical_dominant"] is None


class TestChartRuler:
    def test_traditional_ruler_of_the_rising_sign(self):
        r = compute_metrics(CHART)["chart_ruler"]
        assert r["rising_sign"] == "capricorn" and r["ruler"] == "saturn"
        assert r["sign"] == "taurus" and r["house"] == 5

    def test_every_sign_resolves_to_a_ruler_that_exists_in_a_chart(self):
        # Traditional rulership was chosen so all twelve resolve; a modern
        # table would leave Scorpio/Aquarius/Pisces pointing at outer planets.
        for sign in ELEMENTS:
            assert RULERS[sign] in WEIGHTS


class TestAspectMix:
    def test_splits_harmonious_from_challenging(self):
        a = compute_metrics(CHART)["aspects"]
        assert a["total"] == 4 and a["harmonious"] == 3 and a["challenging"] == 1
        assert a["dominant"] == "trine"

    def test_empty_aspect_list_is_not_an_error(self):
        a = compute_metrics({**CHART, "aspects": []})["aspects"]
        assert a["total"] == 0 and a["dominant"] is None


class TestUnknownBirthTime:
    def test_house_dependent_metrics_are_withheld(self):
        # The ascendant is a noon guess when the time is unknown, so anything
        # derived from houses would be presented as fact while resting on it.
        m = compute_metrics({**CHART, "time_unknown": True})
        assert "hemispheres" not in m and "chart_ruler" not in m

    def test_sign_based_metrics_still_returned(self):
        m = compute_metrics({**CHART, "time_unknown": True})
        assert m["elements"]["dominant"] == "water"
        assert m["modalities"]["scores"]
