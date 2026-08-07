-- Heartbeat per background job. The VPS crontab has no dashboard: a job
-- that dies silently is only detectable by this table — the release phase
-- adds a check that alerts when last_success_at is older than expected.
create table public.job_runs (
    job_name        text primary key,
    last_success_at timestamptz,
    last_error      text,
    last_error_at   timestamptz
);

alter table public.job_runs enable row level security;
-- Service-role-only: no policies.
grant all on public.job_runs to service_role;
-- Same hygiene as 000001 (that revoke predates this table).
revoke truncate, trigger, references on public.job_runs from anon, authenticated;

-- Atomically claims the oldest queued natal report for a worker.
-- SKIP LOCKED makes concurrent workers (or worker + watchdog re-queue)
-- safe without duplicate processing.
create function public.claim_natal_report()
returns setof public.natal_reports
language sql
security definer
set search_path = public
as $$
    update natal_reports
       set status = 'generating', generating_started_at = now()
     where id = (
        select id from natal_reports
         where status = 'queued'
         order by requested_at
         for update skip locked
         limit 1
     )
    returning *;
$$;

revoke execute on function public.claim_natal_report() from public, anon, authenticated;
grant execute on function public.claim_natal_report() to service_role;
