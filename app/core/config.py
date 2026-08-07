"""Application settings — all secrets come from environment variables.

This repo is public (AGPL-3.0): never hardcode keys here. Copy `.env.example`
to `.env` for local development; on the VPS the `.env` file lives outside git.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Supabase
    supabase_url: str = "http://127.0.0.1:54321"
    supabase_anon_key: str = ""
    supabase_service_key: str = ""
    # ONLY for legacy projects signing JWTs with a shared HS256 secret. Leave
    # empty otherwise — auth then verifies via the JWKS endpoint (local CLI
    # stack and new cloud projects sign with asymmetric keys).
    supabase_jwt_secret: str = ""

    # AI provider (used from Phase 3 onward)
    gemini_api_key: str = ""

    # RevenueCat (used from Phase 7 onward)
    revenuecat_webhook_secret: str = ""
    revenuecat_api_key: str = ""

    # Serves the browser test console at /devtools and enables the /v1/dev
    # helper endpoints. MUST stay false in production — those helpers grant
    # premium on demand.
    dev_tools: bool = False

    # APNs token-based auth (.p8). Empty in dev → push calls become no-ops.
    apns_key_path: str = ""      # path to AuthKey_XXXXXXXXXX.p8 on the host
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = "com.stellune.app"
    apns_use_sandbox: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
