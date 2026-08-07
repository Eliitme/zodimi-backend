-- Synastry: partner birth profiles (deliberately created in THIS phase, not
-- the initial schema — synastry is the first feature to cut if the schedule
-- slips, and its schema lives in one migration so cutting it is clean).

create table public.partner_profiles (
    id                 uuid primary key default gen_random_uuid(),
    user_id            uuid not null references auth.users (id) on delete cascade,
    name               text,
    birth_date         date not null,
    birth_time         time,
    birth_time_unknown boolean not null default false,
    lat                double precision not null,
    lon                double precision not null,
    tz_of_birth        text not null,
    created_at         timestamptz not null default now()
);

create index partner_profiles_user_idx on public.partner_profiles (user_id);

alter table public.partner_profiles enable row level security;

create policy partner_profiles_owner_select on public.partner_profiles
    for select using ((select auth.uid()) = user_id);
-- Writes go through the backend (synastry endpoint) via service role.
grant select on public.partner_profiles to authenticated;
-- The blanket service_role grant in the initial schema only covered tables
-- existing at that time — new tables need their own.
grant all on public.partner_profiles to service_role;
revoke truncate, trigger, references on public.partner_profiles from anon, authenticated;

-- The FK deferred since the initial schema (partner_profiles did not exist).
alter table public.synastry_results
    add constraint synastry_results_partner_fk
    foreign key (partner_profile_id) references public.partner_profiles (id)
    on delete cascade;

-- Both tables join the single source of truth for guest-merge + deletion.
insert into public.user_scoped_tables (table_name, user_column) values
    ('partner_profiles', 'user_id'),
    ('synastry_results', 'owner_user_id');

-- The catalog itself was only reachable via the security-definer merge RPC
-- until now; the deletion flow reads it directly with the service role.
grant select on public.user_scoped_tables to service_role;
