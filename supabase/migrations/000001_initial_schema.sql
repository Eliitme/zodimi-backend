-- Stellune initial schema.
-- RLS philosophy:
--   * owner tables: user reads own rows; content rows are written by the
--     backend (service role, which bypasses RLS and must check ownership in
--     application code).
--   * shared content (tarot_cards, sign_interpretations): read-only for
--     authenticated users, written only via service role / migrations.
--   * service-role-only tables: RLS enabled with NO policies.

-- ============================================================ profiles
create table public.profiles (
    user_id            uuid primary key references auth.users (id) on delete cascade,
    name               text,
    birth_date         date,
    birth_time         time,
    -- When true the birth time is unknown: rising sign cannot be computed
    -- reliably and clients must show a warning instead of a wrong value.
    birth_time_unknown boolean not null default false,
    birth_place_text   text,
    lat                double precision,
    lon                double precision,
    tz_of_birth        text,
    -- Locale for server-generated content before the device registers for
    -- push. Device timezone lives ONLY in `devices` (single source of truth
    -- for scheduling).
    locale             text not null default 'vi' check (locale in ('vi', 'en')),
    is_premium         boolean not null default false,
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now()
);

alter table public.profiles enable row level security;

create policy profiles_owner_select on public.profiles
    for select using ((select auth.uid()) = user_id);
create policy profiles_owner_insert on public.profiles
    for insert with check ((select auth.uid()) = user_id);
create policy profiles_owner_update on public.profiles
    for update using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);
-- No owner delete: account deletion goes through the backend (FR9) which
-- deletes auth.users first; profiles rows follow via cascade.

-- ============================================================ tarot_cards
create table public.tarot_cards (
    id                   smallint primary key check (id between 1 and 78),
    key                  text not null unique,
    name_vi              text not null,
    name_en              text not null,
    upright_meaning_vi   text not null,
    upright_meaning_en   text not null,
    reversed_meaning_vi  text not null,
    reversed_meaning_en  text not null,
    -- Static reading shown when the AI daily-content cache misses
    -- (degraded mode): a draw must NEVER block on a live LLM call.
    fallback_reading_vi  text not null,
    fallback_reading_en  text not null,
    image_ref            text not null
);

alter table public.tarot_cards enable row level security;

create policy tarot_cards_read on public.tarot_cards
    for select to authenticated using (true);

-- ==================================================== sign_interpretations
-- Static Big-Three texts for the free tier (12 signs x sun/moon/rising x
-- vi/en = 72 rows). Authored offline; runtime NEVER calls AI for these.
create table public.sign_interpretations (
    sign    text not null check (sign in (
        'aries','taurus','gemini','cancer','leo','virgo',
        'libra','scorpio','sagittarius','capricorn','aquarius','pisces')),
    point   text not null check (point in ('sun','moon','rising')),
    locale  text not null check (locale in ('vi','en')),
    content text not null,
    primary key (sign, point, locale)
);

alter table public.sign_interpretations enable row level security;

create policy sign_interpretations_read on public.sign_interpretations
    for select to authenticated using (true);

-- ============================================================ tarot_draws
create table public.tarot_draws (
    id          bigint generated always as identity primary key,
    user_id     uuid not null references auth.users (id) on delete cascade,
    card_id     smallint not null references public.tarot_cards (id),
    is_reversed boolean not null default false,
    drawn_on    date not null,
    locale      text not null check (locale in ('vi','en')),
    created_at  timestamptz not null default now(),
    -- Free quota: one draw per user per day, enforced at the DB level.
    unique (user_id, drawn_on)
);

alter table public.tarot_draws enable row level security;

create policy tarot_draws_owner_select on public.tarot_draws
    for select using ((select auth.uid()) = user_id);
-- Draws are created by the backend (draw logic + quota checks server-side).

-- ============================================================ tarot_qa_log
-- One row per premium Q&A question; enforces the 20-questions/day quota.
create table public.tarot_qa_log (
    id         bigint generated always as identity primary key,
    user_id    uuid not null references auth.users (id) on delete cascade,
    asked_on   date not null,
    created_at timestamptz not null default now()
);

create index tarot_qa_log_quota_idx on public.tarot_qa_log (user_id, asked_on);

alter table public.tarot_qa_log enable row level security;

create policy tarot_qa_log_owner_select on public.tarot_qa_log
    for select using ((select auth.uid()) = user_id);
-- Rows are written by the backend when answering a question.

-- ========================================================= ai_content_cache
-- SHARED content only (e.g. daily tarot reading per card+locale). Never
-- stores per-user content, so it contains no PII and survives user deletion.
create table public.ai_content_cache (
    id             bigint generated always as identity primary key,
    content_type   text not null,
    cache_key      text not null,
    locale         text not null check (locale in ('vi','en')),
    content        text not null,
    model          text not null,
    prompt_version integer not null,
    created_at     timestamptz not null default now(),
    unique (content_type, cache_key, locale)
);

alter table public.ai_content_cache enable row level security;
-- Service-role-only: no policies.

-- ============================================================ natal_reports
create table public.natal_reports (
    id                    uuid primary key default gen_random_uuid(),
    user_id               uuid not null references auth.users (id) on delete cascade,
    status                text not null default 'queued'
                          check (status in ('queued','generating','done','failed')),
    -- Report content lives HERE (per-user scoped), not in ai_content_cache:
    -- deleting the user deletes their generated content.
    content               text,
    retry_count           integer not null default 0,
    -- Set when a worker picks the report up; a stale-watchdog requeues
    -- reports stuck in 'generating' past a TTL (worker killed mid-job).
    generating_started_at timestamptz,
    requested_at          timestamptz not null default now(),
    completed_at          timestamptz
);

create index natal_reports_user_idx on public.natal_reports (user_id);
create index natal_reports_status_idx on public.natal_reports (status);

alter table public.natal_reports enable row level security;

create policy natal_reports_owner_select on public.natal_reports
    for select using ((select auth.uid()) = user_id);
-- Created/updated by the backend worker (service role + ownership checks).

-- ========================================================== synastry_results
create table public.synastry_results (
    id                 uuid primary key default gen_random_uuid(),
    owner_user_id      uuid not null references auth.users (id) on delete cascade,
    -- FK to partner_profiles is added in the synastry-phase migration,
    -- where that table is created.
    partner_profile_id uuid not null,
    content            text not null,
    created_at         timestamptz not null default now()
);

create index synastry_results_owner_idx on public.synastry_results (owner_user_id);

alter table public.synastry_results enable row level security;

create policy synastry_results_owner_select on public.synastry_results
    for select using ((select auth.uid()) = owner_user_id);

-- ========================================================== prompt_templates
-- Kept in the DB (not in this public repo) so prompts stay private.
create table public.prompt_templates (
    key       text not null,
    locale    text not null check (locale in ('vi','en')),
    version   integer not null,
    template  text not null,
    is_active boolean not null default false,
    primary key (key, locale, version)
);

alter table public.prompt_templates enable row level security;
-- Service-role-only: no policies.

-- ================================================================ devices
create table public.devices (
    id              uuid primary key default gen_random_uuid(),
    user_id         uuid not null references auth.users (id) on delete cascade,
    apns_token      text not null unique,
    -- Single source of truth for push scheduling timezones.
    device_timezone text not null,
    locale          text not null check (locale in ('vi','en')),
    last_push_date  date,
    updated_at      timestamptz not null default now()
);

create index devices_user_idx on public.devices (user_id);

alter table public.devices enable row level security;

create policy devices_owner_all on public.devices
    for all using ((select auth.uid()) = user_id)
    with check ((select auth.uid()) = user_id);

-- ============================================================== iap_events
create table public.iap_events (
    id                  bigint generated always as identity primary key,
    -- Nullable: a webhook can arrive with an app_user_id that does not map
    -- to a known user (e.g. account deleted between purchase and event).
    -- The raw event is still stored for audit/idempotency.
    user_id             uuid references auth.users (id) on delete cascade,
    -- DB-level idempotency safety net for RevenueCat webhook retries.
    revenuecat_event_id text not null unique,
    revenuecat_event    jsonb not null,
    -- Event time from RevenueCat: compared before overwriting is_premium so
    -- an out-of-order older event never downgrades a newer state.
    event_timestamp     timestamptz not null,
    received_at         timestamptz not null default now()
);

create index iap_events_user_idx on public.iap_events (user_id);

alter table public.iap_events enable row level security;
-- Service-role-only: no policies.

-- ======================================================== table privileges
-- Explicit minimal grants: RLS filters rows, grants filter verbs. Tables
-- without grants to `authenticated` (ai_content_cache, prompt_templates,
-- iap_events) are reachable only through the service role.
-- profiles: column-scoped writes — is_premium is NEVER client-writable
-- (RLS only isolates rows; a table-wide grant would let a user update
-- is_premium on their own row). Premium state is written exclusively by the
-- backend service role from verified RevenueCat events.
grant select on public.profiles to authenticated;
grant insert (user_id, name, birth_date, birth_time, birth_time_unknown,
              birth_place_text, lat, lon, tz_of_birth, locale),
      update (name, birth_date, birth_time, birth_time_unknown,
              birth_place_text, lat, lon, tz_of_birth, locale)
    on public.profiles to authenticated;
grant select on public.tarot_cards to authenticated;
grant select on public.sign_interpretations to authenticated;
grant select on public.tarot_draws to authenticated;
grant select on public.tarot_qa_log to authenticated;
grant select on public.natal_reports to authenticated;
grant select on public.synastry_results to authenticated;
grant select, insert, update, delete on public.devices to authenticated;

grant all on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;

-- Hygiene: strip default privileges PostgREST never needs from client roles.
revoke truncate, trigger, references on all tables in schema public
    from anon, authenticated;
