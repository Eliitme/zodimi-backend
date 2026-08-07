"""Daily tarot draw (free, 1/day) and premium Q&A (SSE streaming).

Cost rules enforced here:
- draw NEVER calls the LLM — reading comes from the pregen cache, or the
  static per-card fallback when the cache misses (degraded mode).
- ask is premium-only, capped at 20 questions/day (logged BEFORE the LLM
  call so a crash still counts the attempt).
"""

import random

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import StreamingResponse
from postgrest.exceptions import APIError
from pydantic import BaseModel, Field

from app.ai import content_cache
from app.ai.llm_provider import LLMUnavailableError, get_provider
from app.ai.prompt_repository import render
from app.ai.safety_guardrails import (
    append_disclaimer,
    crisis_response,
    detect_self_harm,
    system_rules,
)
from app.core.entitlements import (
    QA_DAILY_LIMIT,
    is_premium,
    qa_questions_used_today,
    utc_today,
)
from app.core.supabase_client import get_service_client
from app.jobs.daily_tarot_pregen_job import CONTENT_TYPE, cache_key

router = APIRouter(prefix="/tarot", tags=["tarot"])


class DrawResponse(BaseModel):
    card_key: str
    card_name: str
    image_ref: str
    is_reversed: bool
    drawn_on: str
    reading: str
    already_drawn_today: bool


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


def _locale(request: Request) -> str:
    lang = (request.headers.get("Accept-Language") or "en")[:2].lower()
    return "vi" if lang == "vi" else "en"


def _reading_for(card: dict, is_reversed: bool, day: str, locale: str) -> str:
    cached = content_cache.get(CONTENT_TYPE, cache_key(card["key"], is_reversed, day), locale)
    # Degraded mode: static fallback, never a synchronous LLM call.
    text = cached or card[f"fallback_reading_{locale}"]
    return append_disclaimer(text, locale)


@router.post("/draw", response_model=DrawResponse)
def draw(request: Request) -> DrawResponse:
    user_id: str = request.state.user_id
    locale = _locale(request)
    day = utc_today()
    service = get_service_client()

    card_id = random.randint(1, 78)
    is_reversed = random.random() < 0.5
    already = False
    try:
        row = service.table("tarot_draws").insert({
            "user_id": user_id, "card_id": card_id,
            "is_reversed": is_reversed, "drawn_on": day, "locale": locale,
        }).execute().data[0]
    except APIError as exc:
        if exc.code != "23505":
            raise
        # Free quota = the unique(user_id, drawn_on) constraint: today's
        # card is returned again instead of an error ("come back tomorrow"
        # copy lives client-side).
        already = True
        row = (
            service.table("tarot_draws").select("*")
            .eq("user_id", user_id).eq("drawn_on", day).limit(1).execute().data[0]
        )

    card = (
        service.table("tarot_cards").select("*")
        .eq("id", row["card_id"]).limit(1).execute().data[0]
    )
    return DrawResponse(
        card_key=card["key"],
        card_name=card[f"name_{locale}"],
        image_ref=card["image_ref"],
        is_reversed=row["is_reversed"],
        drawn_on=row["drawn_on"],
        reading=_reading_for(card, row["is_reversed"], row["drawn_on"], locale),
        already_drawn_today=already,
    )


@router.post("/ask")
def ask(request: Request, body: AskRequest) -> StreamingResponse:
    user_id: str = request.state.user_id
    locale = _locale(request)

    if not is_premium(user_id):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tarot Q&A is a premium feature",
        )

    def sse(text_iter) -> StreamingResponse:
        def events():
            for chunk in text_iter:
                # SSE framing: a newline inside a chunk would end the event
                # early and the remainder would be DROPPED by the client —
                # emit one `data:` line per text line instead (the client
                # rejoins them with \n per the SSE spec).
                yield "".join(f"data: {line}\n" for line in chunk.split("\n")) + "\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(events(), media_type="text/event-stream")

    # Crisis pre-filter: never sent to the model, never charged to quota.
    if detect_self_harm(body.question):
        return sse([crisis_response(locale)])

    # Charge quota by INSERTING FIRST, then counting — two concurrent
    # requests at the limit both insert, but the count check catches the
    # overrun (and a crashed LLM call still costs the attempt).
    service = get_service_client()
    service.table("tarot_qa_log").insert({
        "user_id": user_id, "asked_on": utc_today(),
    }).execute()
    if qa_questions_used_today(user_id) > QA_DAILY_LIMIT:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Daily limit of {QA_DAILY_LIMIT} questions reached",
        )

    prompt = render("tarot_qa", locale, question=body.question)

    def answer_stream():
        try:
            yield from get_provider().stream(system_rules(locale), prompt)
        except LLMUnavailableError:
            yield {
                "vi": "Xin lỗi, hệ thống đang bận. Vui lòng thử lại sau ít phút.",
                "en": "Sorry, the system is busy. Please try again in a few minutes.",
            }[locale]
        yield append_disclaimer("", locale)

    return sse(answer_stream())
