"""Shared-content cache backed by `ai_content_cache` (never per-user data).

Request paths call `get()` ONLY — on a miss they fall back to static
content (degraded mode). Generation happens exclusively in batch jobs via
`put()`; a user request must never wait on an LLM call for cached content.
"""

import logging

from postgrest.exceptions import APIError

from app.core.supabase_client import get_service_client

logger = logging.getLogger("stellune.cache")


def get(content_type: str, cache_key: str, locale: str) -> str | None:
    rows = (
        get_service_client()
        .table("ai_content_cache")
        .select("content")
        .eq("content_type", content_type).eq("cache_key", cache_key).eq("locale", locale)
        .limit(1).execute().data
    )
    return rows[0]["content"] if rows else None


def put(content_type: str, cache_key: str, locale: str, content: str,
        model: str, prompt_version: int) -> None:
    try:
        get_service_client().table("ai_content_cache").insert({
            "content_type": content_type,
            "cache_key": cache_key,
            "locale": locale,
            "content": content,
            "model": model,
            "prompt_version": prompt_version,
        }).execute()
    except APIError as exc:
        if exc.code != "23505":
            raise
        # Another job run already cached this entry — first write wins.
        logger.info("cache entry already exists: %s/%s/%s", content_type, cache_key, locale)
