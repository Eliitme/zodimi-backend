-- Least privilege has to be asserted, not assumed.
--
-- Supabase cloud ships `alter default privileges in schema public grant all
-- on tables to anon, authenticated` as part of its bootstrap. The local CLI
-- stack does not. Every earlier migration only ever GRANTed, never REVOKEd,
-- so the same SQL produced least privilege locally and a wide-open schema on
-- cloud: `authenticated` held DELETE/INSERT/SELECT/UPDATE on all fourteen
-- public tables.
--
-- Verified on a fresh cloud project before this migration: an ordinary
-- anonymous user could POST {"is_premium": true} to /rest/v1/profiles and
-- read it back as true — free premium for anyone who opens the network tab.
-- Row-level security still filtered rows on service-role-only tables, which
-- is why the damage stopped short of prompt_templates and iap_events, but
-- RLS was the only thing left standing.
--
-- The local RLS suite passed throughout, because locally the grants were
-- right. Only running scripts/verify-rls.sh against the cloud project
-- exposed it (5 of 13 failed).
--
-- So: revoke everything from the client roles, re-assert the minimal set,
-- and stop future tables inheriting the open default.

-- ============================================================ hard reset
revoke all on all tables in schema public from anon, authenticated;
revoke all on all sequences in schema public from anon, authenticated;

-- Future tables and sequences must not arrive pre-opened. Supabase owns
-- some of these defaults as `supabase_admin`, which a migration cannot
-- alter; revoking the postgres-owned ones is what is available to us and is
-- what covers objects created by our own migrations.
do $$
begin
    alter default privileges in schema public
        revoke all on tables from anon, authenticated;
    alter default privileges in schema public
        revoke all on sequences from anon, authenticated;
exception when insufficient_privilege then
    raise notice 'default privileges left unchanged (owned by another role)';
end $$;

-- ================================================ minimal re-grant: reads
-- Shared reference content.
grant select on public.tarot_cards to authenticated;
grant select on public.sign_interpretations to authenticated;

-- Own rows, filtered by RLS. Writes to these go through the API, not
-- PostgREST, so select is all a client needs.
grant select on public.tarot_draws to authenticated;
grant select on public.tarot_qa_log to authenticated;
grant select on public.natal_reports to authenticated;
grant select on public.synastry_results to authenticated;
grant select on public.partner_profiles to authenticated;

-- ============================================== minimal re-grant: writes
-- profiles: column-scoped on purpose. is_premium is absent from both the
-- insert and the update list and must stay absent — it is written only by
-- the backend service role from verified RevenueCat events. A table-wide
-- grant here is the exact bug this migration exists to close.
-- user_id is updatable because PostgREST's upsert emits ON CONFLICT DO
-- UPDATE over every payload column; the profiles_owner_update WITH CHECK
-- still prevents re-pointing a row at another user.
grant select on public.profiles to authenticated;
grant insert (user_id, name, birth_date, birth_time, birth_time_unknown,
              birth_place_text, lat, lon, tz_of_birth, locale),
      update (user_id, name, birth_date, birth_time, birth_time_unknown,
              birth_place_text, lat, lon, tz_of_birth, locale)
    on public.profiles to authenticated;

-- devices: the client registers and updates its own push token directly.
grant select, insert, update, delete on public.devices to authenticated;

-- ================================================================ backend
-- The service role keeps full access; it is never exposed to clients.
grant all on all tables in schema public to service_role;
grant usage, select on all sequences in schema public to service_role;

-- anon (no session at all) gets nothing: every client path authenticates
-- first, anonymously or otherwise, and therefore arrives as `authenticated`.
