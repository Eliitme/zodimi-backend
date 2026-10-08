# Zodimi Backend

FastAPI backend for **Zodimi** — astrology & tarot iOS app (natal charts, daily tarot, AI-generated readings).

Licensed under **AGPL-3.0** (uses `pyswisseph`, added in the ephemeris phase). The iOS app lives in a separate private repository.

## Stack

- FastAPI + uvicorn (Python 3.12, managed with [uv](https://docs.astral.sh/uv/))
- Supabase — Postgres + RLS, Auth (Sign in with Apple + anonymous guests)
- Gemini (AI content pipeline), RevenueCat (IAP) — later phases

## Local development

```bash
# 1. Start the local Supabase stack (Docker required)
supabase start          # prints anon key, service key, JWT secret

# 2. Configure env
cp .env.example .env    # paste the values printed by `supabase start`

# 3. Run the API
docker compose up       # or: uv run uvicorn app.main:app --reload

curl http://localhost:8000/health   # → {"status":"ok"}
```

## Browser API console (local only)

Set `DEV_TOOLS=true` in `.env`, restart, then open **http://localhost:8000/devtools/**.

Every endpoint is reachable from one page: guest sign-in, profile + geocode,
natal chart, report request/poll, tarot draw, SSE Q&A streaming, synastry,
device registration, account deletion. Each call is logged with its raw
response — 403 (premium gate) and 429 (quota) are the interesting cases, so
they are displayed rather than swallowed.

Dev-only helpers on the same page: grant/revoke premium, reset today's
quotas, delete your reports. **They only exist while `DEV_TOOLS=true`** —
with the flag off (the default, and what `.env.example` ships) the console
and every `/v1/dev/*` route return 404. `tests/test_devtools_gating.py`
pins that boundary.

## Auth convention (do not bypass)

Every router is included under the `api` router in `app/main.py`, which applies
the `require_user` dependency (verified Supabase JWT) **by default**. The only
public endpoints allowed: `/health` and the RevenueCat webhook (authenticated
by its own shared secret).

Service-role DB access bypasses RLS — code using `get_service_client()` must
check ownership explicitly.

## Tests

```bash
uv run pytest
```

## Cloud project checklist (not covered by local config)

When the real Supabase project is created, mirror these settings in the
dashboard — `supabase/config.toml` only applies to the local stack:

- [ ] Enable **anonymous sign-ins** + rate limit (local: 10/hour/IP)
- [ ] Enable **Sign in with Apple** provider (requires Apple Developer account)

## Secrets

All secrets come from environment variables (`app/core/config.py`). This repo
is public: never commit `.env`, keys, or prompt template contents (prompts
live in the `prompt_templates` table, service-role-only).
