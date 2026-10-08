"""The free mutual reading, which is the one piece of generated-looking text
in the product that no model ever touches.

AC-09: deterministic, grounded in aspects that are actually there, no
percentage, and zero LLM calls billed.
"""

from app.astro import synastry_preview as preview


def aspect(p1: str, p2: str, type_: str, orb: float) -> dict:
    return {"p1": p1, "p2": p2, "type": type_, "orb": orb}


RICH = [
    aspect("venus", "mars", "trine", 2.0),
    aspect("mercury", "mercury", "sextile", 1.0),
    aspect("saturn", "moon", "square", 3.0),
    aspect("sun", "jupiter", "trine", 4.0),
]


def keyed(result: dict) -> dict[str, dict]:
    return {insight["key"]: insight for insight in result["free_insights"]}


def test_always_three_categories_in_the_contracted_order():
    # A reader opening the same room twice must not find the sections
    # rearranged, and a missing category would read as a bug rather than as
    # "nothing to say".
    for aspects in (RICH, []):
        result = preview.build_free_insights(
            aspects, a_time_known=True, b_time_known=True
        )
        assert [i["key"] for i in result["free_insights"]] == [
            "attraction",
            "communication",
            "differences",
        ]


def test_each_category_is_backed_by_aspects_that_fit_it():
    insights = keyed(preview.build_free_insights(RICH, a_time_known=True, b_time_known=True))
    assert {"venus", "mars"} == {
        insights["attraction"]["evidence"][0]["p1"],
        insights["attraction"]["evidence"][0]["p2"],
    }
    assert insights["communication"]["evidence"][0]["p1"] == "mercury"
    assert insights["differences"]["evidence"][0]["type"] == "square"


def test_a_category_with_nothing_to_say_says_so_instead_of_reaching():
    # The failure this prevents: filling an empty category with something
    # vague that reads like a reading. Empty evidence AND wording that admits
    # it, together — either alone is still misleading.
    only_friction = [aspect("saturn", "sun", "opposition", 2.0)]
    insights = keyed(
        preview.build_free_insights(only_friction, a_time_known=True, b_time_known=True)
    )
    assert insights["attraction"]["evidence"] == []
    assert "Chưa đủ góc chiếu" in insights["attraction"]["summary"]
    assert insights["differences"]["evidence"] != []


def test_nothing_in_the_output_is_a_score():
    # No percentage, no rating, no "soulmate". The spec forbids a quantified
    # verdict and the easiest way to break that is a stray number in a
    # template.
    import json

    rendered = json.dumps(
        preview.build_free_insights(RICH, a_time_known=True, b_time_known=True),
        ensure_ascii=False,
    )
    for banned in ("%", "soulmate", "tri kỷ", "định mệnh"):
        assert banned not in rendered.lower()


def test_the_same_input_always_produces_the_same_bytes():
    # Determinism is what lets a result be stored under a pair revision
    # rather than recomputed, so a reload cannot change the reading.
    import json

    first = json.dumps(
        preview.build_free_insights(RICH, a_time_known=True, b_time_known=True),
        sort_keys=True,
    )
    shuffled = [RICH[2], RICH[0], RICH[3], RICH[1]]
    second = json.dumps(
        preview.build_free_insights(shuffled, a_time_known=True, b_time_known=True),
        sort_keys=True,
    )
    assert first == second


def test_an_exact_orb_tie_still_orders_stably():
    tied = [
        aspect("venus", "mars", "trine", 2.0),
        aspect("mars", "venus", "trine", 2.0),
    ]
    one = preview.build_free_insights(tied, a_time_known=True, b_time_known=True)
    two = preview.build_free_insights(list(reversed(tied)), a_time_known=True, b_time_known=True)
    assert one == two


def test_a_tighter_orb_wins_inside_the_same_tier():
    loose = aspect("venus", "mars", "trine", 6.0)
    tight = aspect("mars", "venus", "sextile", 0.5)
    insights = keyed(
        preview.build_free_insights([loose, tight], a_time_known=True, b_time_known=True)
    )
    assert insights["attraction"]["evidence"][0] == tight


def test_a_stronger_rule_beats_a_tighter_orb():
    # Venus-Mars is the attraction contact. A tighter Sun-Venus trine is
    # still evidence, but it should not displace it.
    venus_mars = aspect("venus", "mars", "trine", 5.0)
    sun_venus = aspect("sun", "venus", "trine", 0.2)
    insights = keyed(
        preview.build_free_insights(
            [sun_venus, venus_mars], a_time_known=True, b_time_known=True
        )
    )
    assert insights["attraction"]["evidence"][0] == venus_mars


def test_an_unknown_birth_time_drops_that_persons_angles():
    # p1 is person A's point, p2 is person B's. With no birth time the
    # ephemeris still returns an ascendant — from a noon fallback — so an
    # aspect to it is evidence for nothing.
    aspects = [
        aspect("asc", "venus", "trine", 1.0),
        aspect("venus", "asc", "trine", 1.0),
        aspect("venus", "mars", "trine", 2.0),
    ]
    kept = preview.usable_aspects(aspects, a_time_known=False, b_time_known=True)
    assert kept == [aspects[1], aspects[2]]

    kept = preview.usable_aspects(aspects, a_time_known=True, b_time_known=False)
    assert kept == [aspects[0], aspects[2]]

    kept = preview.usable_aspects(aspects, a_time_known=False, b_time_known=False)
    assert kept == [aspects[2]]


def test_the_reliability_block_names_what_was_left_out():
    result = preview.build_free_insights(RICH, a_time_known=True, b_time_known=False)
    reliability = result["reliability"]
    assert reliability["person_a_birth_time_known"] is True
    assert reliability["person_b_birth_time_known"] is False
    assert reliability["excluded_points"] == ["person_b.asc", "person_b.mc"]
    # The Moon is kept — a noon fallback moves it by a few degrees, not out
    # of the reading — but the reader is told it is approximate.
    assert reliability["uncertain_points"] == ["person_b.moon"]


def test_a_complete_pair_excludes_nothing():
    result = preview.build_free_insights(RICH, a_time_known=True, b_time_known=True)
    assert result["reliability"]["excluded_points"] == []
    assert result["reliability"]["uncertain_points"] == []


def test_evidence_is_capped_so_one_category_cannot_become_a_list_dump():
    many = [aspect("venus", "mars", "trine", float(i)) for i in range(1, 9)]
    insights = keyed(preview.build_free_insights(many, a_time_known=True, b_time_known=True))
    assert len(insights["attraction"]["evidence"]) == preview.MAX_EVIDENCE_PER_CATEGORY


def test_every_category_has_both_wordings_in_every_locale():
    # A missing template would fall back to another category's text, which
    # reads as a reading about the wrong thing rather than as a bug.
    for locale in ("vi", "en"):
        for key, _rules in preview.CATEGORIES:
            assert preview.SUMMARIES[locale][key]["found"]
            assert preview.SUMMARIES[locale][key]["none"]
            assert preview.TITLES[locale][key]
    # An unknown locale falls back rather than raising.
    result = preview.build_free_insights(RICH, a_time_known=True, b_time_known=True, locale="fr")
    assert result["free_insights"][0]["title"] == preview.TITLES["vi"]["attraction"]


def test_every_insight_names_the_rules_that_produced_it():
    # Bumping TEMPLATE_VERSION is what makes stored previews recompute, so
    # the value has to travel with the reading.
    result = preview.build_free_insights(RICH, a_time_known=True, b_time_known=True)
    for insight in result["free_insights"]:
        assert insight["interpretation_source"] == preview.TEMPLATE_VERSION
