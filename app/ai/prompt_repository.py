"""Prompt templates live in the DB (`prompt_templates`, service-role-only) —
NEVER in this public repo. Loaded once per (key, locale) and cached for the
process lifetime — rolling out a template edit requires BOTH bumping
`version`/`is_active` in the DB and restarting the api + worker containers.
"""

from functools import lru_cache

from app.core.supabase_client import get_service_client


class PromptMissingError(RuntimeError):
    """The requested template is not seeded — a deploy-time mistake, not a
    user error. Callers must fail their job rather than improvise a prompt."""


@lru_cache(maxsize=64)
def get_template(key: str, locale: str) -> str:
    rows = (
        get_service_client()
        .table("prompt_templates")
        .select("template, version")
        .eq("key", key).eq("locale", locale).eq("is_active", True)
        .order("version", desc=True).limit(1)
        .execute().data
    )
    if not rows:
        raise PromptMissingError(f"prompt_templates missing active ({key}, {locale})")
    return rows[0]["template"]


def render(key: str, locale: str, **variables: str) -> str:
    """`{placeholder}` substitution via str.format — templates are trusted
    content authored by us, not user input."""
    return get_template(key, locale).format(**variables)
