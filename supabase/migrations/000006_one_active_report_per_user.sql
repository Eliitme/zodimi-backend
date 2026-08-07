-- At most ONE active (queued/generating) natal report per user, enforced at
-- the DB level: two concurrent requests (two devices on one account, or
-- rows re-pointed by a guest merge) must never yield duplicate worker jobs.
-- The API catches 23505 and returns the winning row.
create unique index natal_reports_one_active_per_user
    on public.natal_reports (user_id)
    where status in ('queued', 'generating');
