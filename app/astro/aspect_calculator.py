"""Major-aspect extraction from a kerykeion subject.

Only the five classical (Ptolemaic) aspects are part of the API contract;
kerykeion's default orb settings are used as-is.
"""

from kerykeion import AstrologicalSubject, NatalAspects

MAJOR_ASPECTS = {"conjunction", "opposition", "trine", "square", "sextile"}

# Contract point names (lowercase) keyed by kerykeion's aspect point names.
POINT_NAMES = {
    "Sun": "sun", "Moon": "moon", "Mercury": "mercury", "Venus": "venus",
    "Mars": "mars", "Jupiter": "jupiter", "Saturn": "saturn",
    "Uranus": "uranus", "Neptune": "neptune", "Pluto": "pluto",
    "Chiron": "chiron", "True_Node": "north_node",
    "Ascendant": "asc", "Medium_Coeli": "mc",
}


def _to_contract(raw_aspects) -> list[dict]:
    aspects = []
    for a in raw_aspects:
        if a.aspect not in MAJOR_ASPECTS:
            continue
        p1 = POINT_NAMES.get(a.p1_name)
        p2 = POINT_NAMES.get(a.p2_name)
        if p1 is None or p2 is None:
            continue  # point outside the contract (e.g. south node, lilith)
        aspects.append({
            "p1": p1,
            "p2": p2,
            "type": a.aspect,
            "orb": round(a.orbit, 2),
        })
    return aspects


def calculate_major_aspects(subject: AstrologicalSubject) -> list[dict]:
    """Return the subject's major natal aspects in contract shape."""
    return _to_contract(NatalAspects(subject).relevant_aspects)


def calculate_synastry_aspects(
    subject_a: AstrologicalSubject, subject_b: AstrologicalSubject
) -> list[dict]:
    """Major INTER-chart aspects (p1 = person A's point, p2 = person B's)."""
    from kerykeion import SynastryAspects

    return _to_contract(SynastryAspects(subject_a, subject_b).relevant_aspects)
