"""Synastry (relationship compatibility) — premium-only, since every result
costs an LLM interpretation call.

The caller's own chart comes from their PROFILE (server-side); the request
carries only the partner's birth data. Results are per-user scoped rows —
deleting the account deletes them (both tables are in `user_scoped_tables`).
"""

from datetime import UTC, date, datetime
from datetime import time as dtime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel, Field

from app.ai.llm_provider import LLMUnavailableError, get_provider
from app.ai.prompt_repository import render
from app.ai.safety_guardrails import append_disclaimer, system_rules
from app.ai.section_format import normalise_sections
from app.astro.aspect_calculator import calculate_synastry_aspects
from app.astro.chart_calculator import build_subject
from app.core.entitlements import is_premium
from app.core.locale import request_locale
from app.core.supabase_client import get_service_client
from app.schemas.chart_schemas import Aspect, BirthData

router = APIRouter(prefix="/synastry", tags=["synastry"])

# Every reading is an LLM call — same cost-gate convention as reports/Q&A.
DAILY_SYNASTRY_CAP = 5

# Synastry used to answer with one undivided block. Readers skim for the
# question they actually came with — "do we argue well?" — and a wall of
# prose makes them hunt for it. These are the pair's own dynamics, not one
# person's relational style across contexts.
#
# All five come back in ONE model call: this endpoint answers synchronously,
# and five sequential calls would take a paid request past a minute.
SYNASTRY_SECTIONS = [
    ("overview", "Tổng quan", "Overview"),
    ("attraction", "Sức hút & hoà hợp", "Attraction & chemistry"),
    ("communication", "Giao tiếp & thấu hiểu", "Communication & understanding"),
    ("friction", "Xung đột & cách hoà giải", "Friction & repair"),
    ("long_term", "Đường dài", "The long run"),
]


def _section_titles(locale: str) -> list[str]:
    return [vi if locale == "vi" else en for _, vi, en in SYNASTRY_SECTIONS]


class PartnerInput(BirthData):
    name: str | None = Field(default=None, max_length=100)


class SynastryResult(BaseModel):
    id: UUID
    partner_name: str | None
    inter_aspects: list[Aspect]
    interpretation: str
    created_at: str
    # True when the owner edited their birth data after this reading was
    # written, so it describes a chart the account no longer has. Additive:
    # older clients that ignore it behave exactly as before.
    is_stale: bool = False


def _subject_from(row: dict):
    return build_subject(
        birth_date=date.fromisoformat(str(row["birth_date"])),
        birth_time=dtime.fromisoformat(row["birth_time"]) if row["birth_time"] else None,
        birth_time_unknown=row["birth_time_unknown"],
        lat=row["lat"],
        lon=row["lon"],
        tz_of_birth=row["tz_of_birth"],
    )


@router.post("", response_model=SynastryResult, status_code=status.HTTP_201_CREATED)
def create_synastry(request: Request, partner: PartnerInput) -> SynastryResult:
    user_id: str = request.state.user_id
    if not is_premium(user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Synastry is a premium feature",
        )

    service = get_service_client()
    created_today = (
        service.table("synastry_results").select("id", count="exact")
        .eq("owner_user_id", user_id)
        .gte("created_at", datetime.now(UTC).date().isoformat() + "T00:00:00Z")
        .execute().count or 0
    )
    if created_today >= DAILY_SYNASTRY_CAP:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Daily compatibility limit reached — please try again tomorrow",
        )

    me = (
        service.table("profiles").select("*").eq("user_id", user_id)
        .limit(1).execute().data
    )
    if not me or me[0]["birth_date"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Complete your own birth details first",
        )
    profile = me[0]
    # Accept-Language, not profiles.locale: the same account may read on a
    # Vietnamese phone and an English one, and every other generated-text
    # endpoint already follows the header (see core.locale). Keying this one
    # off the stored preference gave a reader English tarot and Vietnamese
    # synastry in the same session.
    locale = request_locale(request)

    aspects = calculate_synastry_aspects(
        _subject_from(profile),
        build_subject(
            birth_date=partner.birth_date,
            birth_time=partner.birth_time,
            birth_time_unknown=partner.birth_time_unknown,
            lat=partner.lat, lon=partner.lon, tz_of_birth=partner.tz_of_birth,
        ),
    )

    # Written before the model call, not after: an insert that fails once the
    # reading exists has already spent the money and has nowhere to put the
    # result. A partner row orphaned by a failed reading is harmless — the
    # table is in `user_scoped_tables`, so deleting the account still clears it.
    partner_row = service.table("partner_profiles").insert({
        "user_id": user_id,
        "name": partner.name,
        "birth_date": partner.birth_date.isoformat(),
        "birth_time": partner.birth_time.isoformat() if partner.birth_time else None,
        "birth_time_unknown": partner.birth_time_unknown,
        "lat": partner.lat, "lon": partner.lon, "tz_of_birth": partner.tz_of_birth,
    }).execute().data[0]

    aspect_lines = "\n".join(f"{a['p1']} {a['type']} {a['p2']} (orb {a['orb']})" for a in aspects)
    try:
        titles = _section_titles(locale)
        prompt = render(
            "synastry", locale,
            partner_name=partner.name or "",
            aspects=aspect_lines,
            sections="\n".join(f"## {t}" for t in titles),
        )
        raw = get_provider().generate(system_rules(locale), prompt)
        # Impose our headings so clients can split on them reliably.
        interpretation = append_disclaimer(normalise_sections(raw, titles), locale)
    except LLMUnavailableError:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Reading service is busy — please try again shortly",
        )

    result = service.table("synastry_results").insert({
        "owner_user_id": user_id,
        "partner_profile_id": partner_row["id"],
        "content": interpretation,
        # Both recorded so the reading stays tied to the chart it was written
        # for. The aspects especially: deriving them again at read time is
        # what let the evidence drift away from the prose above it.
        "owner_profile_revision": profile["profile_revision"],
        "inter_aspects": aspects,
    }).execute().data[0]

    return SynastryResult(
        id=result["id"],
        partner_name=partner.name,
        inter_aspects=[Aspect(**a) for a in aspects],
        interpretation=interpretation,
        created_at=result["created_at"],
    )


@router.get("/{result_id}", response_model=SynastryResult)
def get_synastry(request: Request, result_id: UUID) -> SynastryResult:
    user_id: str = request.state.user_id
    rows = (
        get_service_client()
        .table("synastry_results")
        .select("*, partner_profiles(name, birth_date, birth_time, birth_time_unknown, lat, lon, tz_of_birth)")
        .eq("id", str(result_id)).eq("owner_user_id", user_id)  # ownership scope
        .limit(1).execute().data
    )
    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Result not found")
    row = rows[0]
    partner = row["partner_profiles"]

    me_rows = (
        get_service_client().table("profiles").select("*")
        .eq("user_id", user_id).limit(1).execute().data
    )
    current_revision = me_rows[0]["profile_revision"] if me_rows else None
    stored_revision = row.get("owner_profile_revision")
    is_stale = (
        stored_revision is None
        or current_revision is None
        or stored_revision != current_revision
    )

    # The aspects the reading was WRITTEN against, not the ones the owner's
    # chart would produce today. Recomputing them here is what made an edited
    # birth time move the evidence while the prose above it stayed put, so
    # the page argued with itself. Rows from before this was stored fall back
    # to recomputation and are reported stale, because that is the one case
    # where the two genuinely cannot be reconciled.
    stored_aspects = row.get("inter_aspects")
    if stored_aspects is not None:
        aspects = stored_aspects
    else:
        if not me_rows or me_rows[0]["birth_date"] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Complete your own birth details first",
            )
        aspects = calculate_synastry_aspects(_subject_from(me_rows[0]), _subject_from(partner))
        is_stale = True

    return SynastryResult(
        id=row["id"],
        partner_name=partner["name"],
        inter_aspects=[Aspect(**a) for a in aspects],
        interpretation=row["content"],
        created_at=row["created_at"],
        is_stale=is_stale,
    )
