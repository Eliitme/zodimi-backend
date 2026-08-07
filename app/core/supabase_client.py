"""Supabase client factories.

Two access modes:
- service client: batch jobs / workers. BYPASSES RLS — every query made with
  it must check ownership explicitly in application code.
- user client: per-request, carries the caller's JWT so RLS applies.
"""

from functools import lru_cache

from app.core.config import get_settings
from supabase import Client, ClientOptions, create_client


@lru_cache
def get_service_client() -> Client:
    settings = get_settings()
    return create_client(settings.supabase_url, settings.supabase_service_key)


def get_user_client(user_jwt: str) -> Client:
    settings = get_settings()
    return create_client(
        settings.supabase_url,
        settings.supabase_anon_key,
        options=ClientOptions(headers={"Authorization": f"Bearer {user_jwt}"}),
    )
