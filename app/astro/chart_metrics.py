"""Quantitative summaries derived from a computed natal chart.

These are pure arithmetic over data the chart already contains — no model
call, no extra cost, deterministic and instantly reproducible. That matters:
a number a reader can check, and a stated formula they can disagree with, is
worth more than another paragraph of prose asserting the same thing.

Every weighting choice here is a convention, not a fact. Astrologers disagree
about them, so the breakdown is returned alongside the totals and the weights
are constants a reader can be shown.
"""

from collections import Counter

ELEMENTS = {
    "aries": "fire", "leo": "fire", "sagittarius": "fire",
    "taurus": "earth", "virgo": "earth", "capricorn": "earth",
    "gemini": "air", "libra": "air", "aquarius": "air",
    "cancer": "water", "scorpio": "water", "pisces": "water",
}

MODALITIES = {
    "aries": "cardinal", "cancer": "cardinal", "libra": "cardinal", "capricorn": "cardinal",
    "taurus": "fixed", "leo": "fixed", "scorpio": "fixed", "aquarius": "fixed",
    "gemini": "mutable", "virgo": "mutable", "sagittarius": "mutable", "pisces": "mutable",
}

# Traditional rulerships: every sign has one, unlike modern rulerships where
# three signs gained an outer planet. Using the traditional set keeps the
# chart ruler defined for all twelve ascendants without a special case.
RULERS = {
    "aries": "mars", "taurus": "venus", "gemini": "mercury", "cancer": "moon",
    "leo": "sun", "virgo": "mercury", "libra": "venus", "scorpio": "mars",
    "sagittarius": "jupiter", "capricorn": "saturn", "aquarius": "saturn",
    "pisces": "jupiter",
}

# The lights and the ascendant describe the person; the outer planets move so
# slowly that everyone born the same year shares them, so they are counted but
# not allowed to dominate. Chiron and the nodes are points rather than
# planets and are left out of the balance entirely.
WEIGHTS = {
    "sun": 3.0, "moon": 3.0, "ascendant": 3.0,
    "mercury": 2.0, "venus": 2.0, "mars": 2.0,
    "midheaven": 1.5, "jupiter": 1.5, "saturn": 1.5,
    "uranus": 1.0, "neptune": 1.0, "pluto": 1.0,
}
EXCLUDED = ("chiron", "north_node", "south_node", "lilith")


def _percentages(totals: Counter[str], total: float) -> dict[str, int]:
    """Whole percentages that always sum to 100.

    Rounding each share on its own is what a reader notices: four elements
    rounded independently printed 36 + 29 + 27 + 9 = 101 beside a bar that
    was visibly full. Largest remainder gives the leftover points to the
    shares that lost the most to rounding, so the figures and the bar agree.

    Flooring first means the floors can only undershoot 100, so there is
    always a non-negative remainder to hand out and never one to claw back.
    Ties break on the group name, so the same chart always renders the same.
    """
    exact = {group: value / total * 100 for group, value in totals.items()}
    percent = {group: int(value) for group, value in exact.items()}
    leftover = 100 - sum(percent.values())
    ranked = sorted(exact, key=lambda group: (-(exact[group] - percent[group]), group))
    for group in ranked[:leftover]:
        percent[group] += 1
    return percent


def _weighted(signs_by_body: dict[str, str], table: dict[str, str]) -> dict:
    """Totals plus the per-body contributions that produced them."""
    totals: Counter[str] = Counter()
    contributions = []
    for body, sign in signs_by_body.items():
        weight = WEIGHTS.get(body)
        group = table.get(sign)
        if weight is None or group is None:
            continue
        totals[group] += weight
        contributions.append({"body": body, "sign": sign, "group": group, "weight": weight})

    total = sum(totals.values())
    return {
        "scores": {k: round(v, 1) for k, v in totals.items()},
        "percent": _percentages(totals, total) if total else {},
        "dominant": totals.most_common(1)[0][0] if totals else None,
        # Naming what is absent is often more telling than what dominates.
        "missing": [g for g in dict.fromkeys(table.values()) if g not in totals],
        "contributions": contributions,
    }


def _bodies(chart: dict) -> dict[str, str]:
    signs = {
        p["name"]: p["sign"] for p in chart["planets"] if p["name"] not in EXCLUDED
    }
    signs["ascendant"] = chart["angles"]["asc"]["sign"]
    signs["midheaven"] = chart["angles"]["mc"]["sign"]
    return signs


def _hemispheres(chart: dict) -> dict:
    """Which half of the sky the planets fall in.

    Houses 1-6 sit below the horizon, 7-12 above; 1-3 and 10-12 are the
    eastern half. Counting is unweighted here because the question is simply
    where the chart clusters.
    """
    houses = [p["house"] for p in chart["planets"] if p["name"] not in EXCLUDED]
    if not houses:
        return {}
    below = sum(1 for h in houses if 1 <= h <= 6)
    east = sum(1 for h in houses if h <= 3 or h >= 10)
    n = len(houses)
    quadrants = Counter(
        "q1" if h <= 3 else "q2" if h <= 6 else "q3" if h <= 9 else "q4" for h in houses
    )
    return {
        "below_horizon": below, "above_horizon": n - below,
        "east": east, "west": n - east,
        "vertical_dominant": "below" if below > n - below else "above" if below < n - below else None,
        "horizontal_dominant": "east" if east > n - east else "west" if east < n - east else None,
        "quadrants": {q: quadrants.get(q, 0) for q in ("q1", "q2", "q3", "q4")},
        "counted": n,
    }


def _chart_ruler(chart: dict) -> dict:
    """The ruler of the rising sign, and where it sits.

    Traditional astrology treats it as the chart's steering planet, so its
    house placement is a compact answer to "what is this life pointed at".
    """
    rising = chart["angles"]["asc"]["sign"]
    ruler = RULERS[rising]
    placement = next((p for p in chart["planets"] if p["name"] == ruler), None)
    return {
        "rising_sign": rising,
        "ruler": ruler,
        "sign": placement["sign"] if placement else None,
        "house": placement["house"] if placement else None,
    }


def _aspect_mix(chart: dict) -> dict:
    counts = Counter(a["type"] for a in chart.get("aspects", []))
    total = sum(counts.values())
    # Harmonious vs tense is the split readers actually ask about.
    easy = counts["trine"] + counts["sextile"]
    hard = counts["square"] + counts["opposition"]
    return {
        "counts": dict(counts),
        "total": total,
        "harmonious": easy,
        "challenging": hard,
        "dominant": counts.most_common(1)[0][0] if counts else None,
    }


def compute_metrics(chart: dict) -> dict:
    """Every quantitative summary for one chart.

    `time_unknown` charts get no house-dependent figures: the ascendant is a
    noon guess, so hemispheres, quadrants and the chart ruler's house would
    be presented as fact while resting on an assumption.
    """
    signs = _bodies(chart)
    metrics = {
        "elements": _weighted(signs, ELEMENTS),
        "modalities": _weighted(signs, MODALITIES),
        "aspects": _aspect_mix(chart),
        "weights": WEIGHTS,
        "excluded": list(EXCLUDED),
    }
    if not chart.get("time_unknown"):
        metrics["hemispheres"] = _hemispheres(chart)
        metrics["chart_ruler"] = _chart_ruler(chart)
    return metrics
