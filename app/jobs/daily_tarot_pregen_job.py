"""Pre-generates tomorrow's daily tarot readings into the shared cache:
78 cards x upright/reversed x vi/en. Idempotent — existing cache entries are
kept (first write wins), so re-runs after a partial failure only fill gaps.

Run via cron at 00:00 UTC:
    docker compose exec api python -m app.jobs.daily_tarot_pregen_job
"""

import logging
import sys
from datetime import UTC, datetime, timedelta

from app.ai import content_cache
from app.ai.llm_provider import GEMINI_MODEL, LLMUnavailableError, get_provider
from app.ai.prompt_repository import render
from app.ai.safety_guardrails import system_rules
from app.core.supabase_client import get_service_client
from app.jobs.job_heartbeat import record_failure, record_success

logger = logging.getLogger("stellune.jobs.pregen")

JOB_NAME = "daily_tarot_pregen"
CONTENT_TYPE = "daily_tarot"
PROMPT_KEY = "daily_tarot_reading"
PROMPT_VERSION = 1


def cache_key(card_key: str, reversed_: bool, day: str) -> str:
    orientation = "reversed" if reversed_ else "upright"
    return f"{card_key}:{orientation}:{day}"


def run(target_day: str | None = None) -> int:
    """Generates missing entries for `target_day` (default: tomorrow UTC).
    Returns the number of entries generated."""
    day = target_day or (datetime.now(UTC).date() + timedelta(days=1)).isoformat()
    provider = get_provider()
    cards = (
        get_service_client().table("tarot_cards")
        .select("key, name_vi, name_en, upright_meaning_vi, upright_meaning_en, "
                "reversed_meaning_vi, reversed_meaning_en")
        .execute().data
    )

    generated, failures = 0, 0
    for card in cards:
        for reversed_ in (False, True):
            for locale in ("vi", "en"):
                key = cache_key(card["key"], reversed_, day)
                if content_cache.get(CONTENT_TYPE, key, locale) is not None:
                    continue
                meaning_col = ("reversed" if reversed_ else "upright") + f"_meaning_{locale}"
                try:
                    prompt = render(
                        PROMPT_KEY, locale,
                        card_name=card[f"name_{locale}"],
                        orientation="reversed" if reversed_ else "upright",
                        meaning=card[meaning_col],
                        day=day,
                    )
                    content = provider.generate(system_rules(locale), prompt)
                    content_cache.put(CONTENT_TYPE, key, locale, content,
                                      model=GEMINI_MODEL, prompt_version=PROMPT_VERSION)
                    generated += 1
                except LLMUnavailableError as exc:
                    failures += 1
                    logger.warning("pregen miss %s/%s: %s", key, locale, exc)

    if failures:
        # Partial failure is safe (draws fall back to static readings) but
        # must be visible — the heartbeat carries the alert.
        record_failure(JOB_NAME, f"{failures} entries failed for {day}")
    else:
        record_success(JOB_NAME)
    logger.info("pregen %s: generated=%d failed=%d", day, generated, failures)
    return generated


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(0 if run() >= 0 else 1)
