"""The free mutual reading: three grounded observations, no model involved.

Two things this must never do, because both are easy and both are lies.

It must not call an LLM. The free preview is the first thing two people see
and it is offered to everyone, so a model call here is an unbounded bill
attached to an unauthenticated growth loop. Everything below is arithmetic
over aspects the ephemeris already computed.

It must not invent certainty. There is no percentage, no "soulmate", no
verdict. Where the aspects say nothing about a category, the category says
so rather than reaching for something vague that sounds like a reading.

Summaries are fixed strings. The planets involved travel as structured
evidence and the clients render their own names for them, which keeps one
set of display names per platform instead of a third one here, and removes
any path by which a name could be interpolated into prose unchecked.
"""

from dataclasses import dataclass

# Bumped when a rule, an order or a template changes. It is part of the pair
# revision hash, so a bump makes stored previews recompute rather than
# silently mixing old text with new rules.
TEMPLATE_VERSION = "rules-v1"

# Points that only exist once a birth time pins them down. With an unknown
# time the ephemeris still returns them, computed from a noon fallback, so
# every aspect touching one is discarded rather than quietly used.
TIME_DEPENDENT_POINTS = frozenset({"asc", "mc"})

# The Moon travels about 13° a day, so a noon fallback leaves it up to ~6.5°
# from where it was. That is small enough to keep — the aspect is usually
# still real — and large enough that the reader has to be told.
TIME_SENSITIVE_POINTS = frozenset({"moon"})

HARMONIOUS = frozenset({"trine", "sextile"})
CHALLENGING = frozenset({"square", "opposition"})
CONJUNCTION = frozenset({"conjunction"})


@dataclass(frozen=True)
class Rule:
    """One way a category can be evidenced.

    `tier` orders rules within a category: a Venus-Mars contact says more
    about attraction than a Sun-Jupiter trine does, so it is picked first
    even when the trine is tighter.
    """

    points: frozenset[str]
    aspects: frozenset[str]
    tier: int
    #: Both points must come from this set, rather than one of each.
    require_both: bool = True


# Ordered exactly as the spec names them: attraction, communication,
# differences. The order is part of the contract — a reader who opens the
# same room twice must not find the sections rearranged.
CATEGORIES: list[tuple[str, list[Rule]]] = [
    (
        "attraction",
        [
            Rule(frozenset({"venus", "mars"}), HARMONIOUS | CONJUNCTION, tier=0),
            Rule(frozenset({"sun", "moon"}), HARMONIOUS | CONJUNCTION, tier=1),
            Rule(frozenset({"venus", "sun", "moon"}), HARMONIOUS | CONJUNCTION, tier=2),
        ],
    ),
    (
        "communication",
        [
            Rule(frozenset({"mercury"}), HARMONIOUS | CONJUNCTION, tier=0, require_both=True),
            Rule(frozenset({"mercury", "moon"}), HARMONIOUS | CONJUNCTION, tier=1),
            Rule(frozenset({"mercury", "jupiter"}), HARMONIOUS | CONJUNCTION, tier=2),
        ],
    ),
    (
        "differences",
        [
            Rule(frozenset({"saturn", "sun", "moon"}), CHALLENGING, tier=0),
            Rule(frozenset({"mars", "venus"}), CHALLENGING, tier=1),
            Rule(
                frozenset({"sun", "moon", "mercury", "venus", "mars"}),
                CHALLENGING,
                tier=2,
            ),
        ],
    ),
]

MAX_EVIDENCE_PER_CATEGORY = 3

# Static, per locale. Nothing is interpolated: the planets are in `evidence`
# and the client names them. The wording stays an invitation to talk rather
# than a statement about the relationship — "three ways in", not a verdict.
SUMMARIES: dict[str, dict[str, dict[str, str]]] = {
    "vi": {
        "attraction": {
            "found": "Có những góc chiếu cho thấy hai bạn dễ bị cuốn vào nhau. "
            "Đây là chỗ để bắt đầu một cuộc trò chuyện, không phải kết luận.",
            "none": "Chưa đủ góc chiếu rõ ràng để nói về khía cạnh này.",
        },
        "communication": {
            "found": "Cách hai bạn trao đổi và hiểu nhau có vài điểm đáng để ý.",
            "none": "Chưa đủ góc chiếu rõ ràng để nói về khía cạnh này.",
        },
        "differences": {
            "found": "Có vài chỗ hai bạn nhìn khác nhau. Khác biệt không phải điều xấu "
            "— biết trước thì dễ nói chuyện hơn.",
            "none": "Chưa đủ góc chiếu rõ ràng để nói về khía cạnh này.",
        },
    },
    "en": {
        "attraction": {
            "found": "Some contacts here suggest the two of you notice each other. "
            "A place to start a conversation, not a conclusion.",
            "none": "Not enough clear contacts to say anything about this one.",
        },
        "communication": {
            "found": "There is something worth noticing in how the two of you talk "
            "and follow each other.",
            "none": "Not enough clear contacts to say anything about this one.",
        },
        "differences": {
            "found": "A few places where the two of you see things differently. "
            "Difference is not a problem — knowing about it early just makes it "
            "easier to talk about.",
            "none": "Not enough clear contacts to say anything about this one.",
        },
    },
}

TITLES: dict[str, dict[str, str]] = {
    "vi": {
        "attraction": "Sức hút",
        "communication": "Giao tiếp",
        "differences": "Khác biệt",
    },
    "en": {
        "attraction": "Attraction",
        "communication": "Communication",
        "differences": "Differences",
    },
}


def _excluded_points(a_time_known: bool, b_time_known: bool) -> list[str]:
    excluded = []
    if not a_time_known:
        excluded += [f"person_a.{point}" for point in sorted(TIME_DEPENDENT_POINTS)]
    if not b_time_known:
        excluded += [f"person_b.{point}" for point in sorted(TIME_DEPENDENT_POINTS)]
    return excluded


def _uncertain_points(a_time_known: bool, b_time_known: bool) -> list[str]:
    uncertain = []
    if not a_time_known:
        uncertain += [f"person_a.{point}" for point in sorted(TIME_SENSITIVE_POINTS)]
    if not b_time_known:
        uncertain += [f"person_b.{point}" for point in sorted(TIME_SENSITIVE_POINTS)]
    return uncertain


def usable_aspects(
    aspects: list[dict], a_time_known: bool, b_time_known: bool
) -> list[dict]:
    """Drops every aspect that rests on a birth time nobody supplied.

    `p1` is person A's point and `p2` is person B's (see
    `calculate_synastry_aspects`), so which side's time is missing decides
    which half of each pair is suspect.
    """
    kept = []
    for aspect in aspects:
        if not a_time_known and aspect["p1"] in TIME_DEPENDENT_POINTS:
            continue
        if not b_time_known and aspect["p2"] in TIME_DEPENDENT_POINTS:
            continue
        kept.append(aspect)
    return kept


def _matches(rule: Rule, aspect: dict) -> bool:
    if aspect["type"] not in rule.aspects:
        return False
    if rule.require_both:
        return aspect["p1"] in rule.points and aspect["p2"] in rule.points
    return aspect["p1"] in rule.points or aspect["p2"] in rule.points


def _evidence_for(rules: list[Rule], aspects: list[dict]) -> list[dict]:
    """The aspects that evidence one category, best first.

    Best means lowest tier, then tightest orb, then a stable alphabetical
    tiebreak — an exact-orb tie between two aspects must not reorder between
    two requests, or the same room would read differently on a refresh.
    """
    scored: list[tuple[int, float, str, str, dict]] = []
    for aspect in aspects:
        tiers = [rule.tier for rule in rules if _matches(rule, aspect)]
        if not tiers:
            continue
        scored.append((min(tiers), aspect["orb"], aspect["p1"], aspect["p2"], aspect))
    scored.sort(key=lambda row: row[:4])
    return [aspect for *_, aspect in scored[:MAX_EVIDENCE_PER_CATEGORY]]


def build_free_insights(
    aspects: list[dict],
    *,
    a_time_known: bool,
    b_time_known: bool,
    locale: str = "vi",
) -> dict:
    """The whole free preview: three categories, their evidence, and what
    was left out of the calculation and why.

    Deterministic by construction — same aspects and same flags in, same
    bytes out — which is what lets the result be cached under a pair
    revision instead of recomputed per request.
    """
    texts = SUMMARIES.get(locale, SUMMARIES["vi"])
    titles = TITLES.get(locale, TITLES["vi"])
    usable = usable_aspects(aspects, a_time_known, b_time_known)

    insights = []
    for key, rules in CATEGORIES:
        evidence = _evidence_for(rules, usable)
        insights.append({
            "key": key,
            "title": titles[key],
            "summary": texts[key]["found" if evidence else "none"],
            "evidence": evidence,
            "interpretation_source": TEMPLATE_VERSION,
        })

    return {
        "reliability": {
            "person_a_birth_time_known": a_time_known,
            "person_b_birth_time_known": b_time_known,
            "excluded_points": _excluded_points(a_time_known, b_time_known),
            # Kept in the reading but not to be read as exact.
            "uncertain_points": _uncertain_points(a_time_known, b_time_known),
        },
        "free_insights": insights,
    }
