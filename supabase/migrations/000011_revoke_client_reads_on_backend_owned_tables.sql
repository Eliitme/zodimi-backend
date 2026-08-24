-- Paid content must not be readable around its own gate.
--
-- `natal_reports.content` and `synastry_results.content` hold generated text
-- that the API withholds from a reader whose entitlement has lapsed
-- (reports_router._gate_content trims the report to its free preview). That
-- gate lives on the API path only. A `select` grant on the table hands the
-- same rows to any client holding the anon key and its own JWT — both of
-- which ship inside the web bundle and the iOS app — so the gate stopped at
-- the front door while the side door stayed open. A subscriber who cancels
-- keeps the full report via /rest/v1/natal_reports?select=content.
--
-- The remaining three tables are revoked on least-privilege grounds rather
-- than to close a leak: no client reads them. Verified by grepping both
-- clients for direct PostgREST access — web touches only
-- sign_interpretations and profiles (src/lib/api-client.ts,
-- src/lib/profile-sync.ts), iOS only the same two (InterpretationService,
-- ProfileService). Everything else already goes through the API.
--
-- Untouched on purpose: profiles, tarot_cards, sign_interpretations (clients
-- read these directly), and the write grants on profiles + devices.

revoke select on public.natal_reports from authenticated;
revoke select on public.synastry_results from authenticated;
revoke select on public.partner_profiles from authenticated;
revoke select on public.tarot_draws from authenticated;
revoke select on public.tarot_qa_log from authenticated;

-- The RLS policies stay in place. They are now unreachable for these tables,
-- but dropping them would mean re-deriving the ownership rule from scratch if
-- a client ever needs direct reads again — and a policy with no grant behind
-- it costs nothing.
