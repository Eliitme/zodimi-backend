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

    # AI provider (used from Phase 3 onward). OpenRouter wins when both are
    # set — it reaches many models behind one key, which is how models get
    # compared on real Vietnamese output rather than on benchmarks.
    openrouter_api_key: str = ""
    # Defaults to the same model the direct Gemini path used, so switching to
    # OpenRouter changes the route without silently changing cost or writing
    # style. Swap this to A/B another model; no code change needed.
    openrouter_model: str = "google/gemini-2.5-flash"
    # The natal report runs in a background worker, so a slower but
    # better-writing model costs the reader nothing. Empty falls back to
    # openrouter_model.
    openrouter_report_model: str = ""
    gemini_api_key: str = ""

    # RevenueCat (used from Phase 7 onward)
    revenuecat_webhook_secret: str = ""
    revenuecat_api_key: str = ""

    # Serves the browser test console at /devtools and enables the /v1/dev
    # helper endpoints. MUST stay false in production — those helpers grant
    # premium on demand.
    dev_tools: bool = False

    # Origins allowed to call the API from a browser (the web app). Comma
    # separated. Deliberately NOT "*": a wildcard would let any site drive a
    # signed-in user's account.
    #
    # Empty by default ON PURPOSE. A localhost default would silently ship to
    # production and break the real web app with a CORS error that shows up
    # only in the browser console — better to fail loudly at startup.
    web_origins: str = ""

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.web_origins.split(",") if o.strip()]

    # Astro Rooms. The HMAC key for invite-token hashes: a database dump
    # alone must not be enough to recognise a token. Rotating it invalidates
    # every outstanding invitation, which is the intended emergency lever.
    room_invite_secret: str = ""
    # Canonical origin of the web app, used to build invite links. Empty
    # means links come back as a path and the client prepends its own
    # origin — a link to the wrong domain is worse than no host at all, and
    # the host is an owner decision that is not made yet.
    web_base_url: str = ""

    # APNs token-based auth (.p8). Empty in dev → push calls become no-ops.
    apns_key_path: str = ""      # path to AuthKey_XXXXXXXXXX.p8 on the host
    apns_key_id: str = ""
    apns_team_id: str = ""
    apns_bundle_id: str = "com.zodimi.app"
    apns_use_sandbox: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()
