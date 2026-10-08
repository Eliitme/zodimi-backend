-- Astro Rooms: two people, two consents, one shared reading.
--
-- Schema only. The API lands separately; nothing here is reachable by a
-- client, by design (see the grants at the bottom).
--
-- The hard part of this feature is not the tables, it is that a room holds
-- data belonging to TWO accounts. Every existing user-scoped table answers
-- "whose row is this?" with one column. A room cannot, so the merge and
-- deletion flows — both driven by `user_scoped_tables` — need to be reasoned
-- through rather than handed another row in the catalog.

-- =============================================================== rooms

create table public.astro_rooms (
    id                uuid primary key default gen_random_uuid(),
    owner_user_id     uuid not null references auth.users (id) on delete cascade,
    -- Topic changes the copy around the reading, never the computation. A
    -- check rather than an enum: adding a fourth topic should be a one-line
    -- migration, not a type rewrite.
    topic             text not null check (topic in ('romance', 'friendship', 'understanding')),
    status            text not null default 'pending'
                      check (status in ('pending', 'ready', 'revoked', 'closed')),
    -- Idempotency for create: a double tap on a slow connection must not
    -- leave two rooms and two invite links. Null allowed so a caller that
    -- does not send one is not blocked, but then it gets no protection.
    client_request_id uuid,
    created_at        timestamptz not null default now(),
    updated_at        timestamptz not null default now(),
    unique (owner_user_id, client_request_id)
);

create index astro_rooms_owner_idx on public.astro_rooms (owner_user_id, created_at desc);

-- ============================================================= members

create table public.astro_room_members (
    id               uuid primary key default gen_random_uuid(),
    room_id          uuid not null references public.astro_rooms (id) on delete cascade,
    user_id          uuid not null references auth.users (id) on delete cascade,
    role             text not null check (role in ('owner', 'invitee')),
    -- Null until the person has actively agreed. The invitee's row is only
    -- written at the moment they accept, so a declined invitation leaves no
    -- trace of them in the room at all; the owner consents by creating it.
    consent_at       timestamptz,
    consent_version  text,
    -- Which revision of their birth data they consented with. A later edit
    -- makes the shared reading describe a chart they no longer have, the
    -- same way it does for a natal report.
    profile_revision integer,
    revoked_at       timestamptz,
    joined_at        timestamptz not null default now(),
    unique (room_id, user_id)
);

-- Two people, not three. One owner and at most one invitee per room, which
-- together with the unique above caps a room at two rows — so "max 2
-- participants" is a constraint rather than a rule the API has to remember.
-- It also makes the owner-is-not-the-invitee case impossible: the same user
-- cannot hold both rows.
create unique index astro_room_members_one_owner on public.astro_room_members (room_id)
    where role = 'owner';
create unique index astro_room_members_one_invitee on public.astro_room_members (room_id)
    where role = 'invitee';

create index astro_room_members_user_idx on public.astro_room_members (user_id);

-- ========================================================= invitations

create table public.astro_room_invitations (
    id            uuid primary key default gen_random_uuid(),
    room_id       uuid not null references public.astro_rooms (id) on delete cascade,
    -- The HASH of a 32-byte random token, never the token. The token is
    -- shown to its creator once and is not recoverable from here, so a dump
    -- of this table does not let anyone join anything.
    token_hash    text not null unique,
    expires_at    timestamptz not null,
    revoked_at    timestamptz,
    accepted_at   timestamptz,
    -- set null, not cascade: a deleted invitee must not delete the record
    -- that the invitation was used, which is what the owner's "accepted"
    -- state is derived from.
    accepted_by   uuid references auth.users (id) on delete set null,
    -- Lets the API rate-limit per invitation as well as per caller, so a
    -- token cannot be probed from a rotating set of anonymous sessions.
    attempt_count integer not null default 0,
    created_at    timestamptz not null default now()
);

-- One live invitation per room: reissuing has to revoke the previous token,
-- not leave two working links to the same room.
create unique index astro_room_invitations_one_live on public.astro_room_invitations (room_id)
    where revoked_at is null and accepted_at is null;

-- ============================================================= results

create table public.astro_room_results (
    id                 uuid primary key default gen_random_uuid(),
    room_id            uuid not null references public.astro_rooms (id) on delete cascade,
    -- Hash over both members' profile revisions plus the algorithm and
    -- template versions. Two readings for the same pair of charts are the
    -- same reading; edit either birth date and this changes, so the old
    -- result is kept rather than overwritten.
    pair_revision_hash text not null,
    status             text not null default 'queued'
                       check (status in ('queued', 'processing', 'done', 'failed')),
    -- The aspects the free insights were derived from: sanitised, no birth
    -- dates, times or coordinates. Stored so the evidence and the words can
    -- never drift apart.
    evidence_json      jsonb,
    free_insights_json jsonb,
    -- Paid text. Service role only, like natal_reports.content.
    premium_content    text,
    failed_code        text,
    generated_at       timestamptz,
    created_at         timestamptz not null default now(),
    unique (room_id, pair_revision_hash)
);

create index astro_room_results_room_idx on public.astro_room_results (room_id, created_at desc);

-- ==================================================== access predicate

-- A room's reading is shared material: it exists because two people agreed
-- to it, so it stays readable only while both agreements stand.
--
-- Written once, here, because the same question is asked by the RLS policies
-- below and by the API. It covers withdrawal and account deletion with one
-- rule instead of two: revoking sets `revoked_at`, deleting removes the row,
-- and either way the count stops being two. No trigger has to notice.
-- Membership, answered WITHOUT re-entering the policies on the membership
-- table. A policy on `astro_room_members` that selects from
-- `astro_room_members` is infinite recursion (42P17), and because
-- `astro_rooms`' policy reads the same table the error spreads to every room
-- query. Security definer runs as the owner and so skips RLS, which is the
-- standard way out and the reason this is a function rather than an inline
-- EXISTS.
-- Takes no user: it answers "am I in this room?", read from the JWT, so a
-- caller cannot probe whether somebody ELSE is in a room they happen to know
-- the id of.
create function public.astro_room_is_member(p_room_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select exists (
        select 1 from public.astro_room_members m
        where m.room_id = p_room_id
          and m.user_id = (select auth.uid())
          and m.revoked_at is null
    );
$$;

-- A policy is evaluated as the CALLING role, so `authenticated` has to be
-- able to execute this or every room query fails "permission denied for
-- function" the moment a select grant appears. Revoking from public and
-- re-granting narrowly is the difference between a locked door and a wall
-- with no door at all.
revoke execute on function public.astro_room_is_member(uuid) from public, anon;
grant execute on function public.astro_room_is_member(uuid) to authenticated, service_role;

create function public.astro_room_has_both_consents(p_room_id uuid)
returns boolean
language sql
stable
security definer
set search_path = public
as $$
    select count(*) = 2
    from public.astro_room_members m
    where m.room_id = p_room_id
      and m.consent_at is not null
      and m.revoked_at is null;
$$;

-- Same reason, and it gives nothing away: the answer is about a room id,
-- which is a v4 uuid nobody can guess, and anyone who legitimately holds one
-- is a member who already knows whether the other person consented.
revoke execute on function public.astro_room_has_both_consents(uuid) from public, anon;
grant execute on function public.astro_room_has_both_consents(uuid) to authenticated, service_role;

-- ================================================================= RLS

alter table public.astro_rooms enable row level security;
alter table public.astro_room_members enable row level security;
alter table public.astro_room_invitations enable row level security;
alter table public.astro_room_results enable row level security;

-- Defence in depth only: no client role is granted SELECT on any of these
-- four tables (see the grants below), so none of these policies is reachable
-- today. They are written anyway so that the ownership rule does not have to
-- be re-derived from scratch the day a direct read is ever wanted — the same
-- reasoning migration 000011 used when it revoked reads but kept policies.

create policy astro_rooms_member_select on public.astro_rooms
    for select using (
        public.astro_room_is_member(astro_rooms.id)
    );

-- A member sees the membership rows of rooms they are in. The table carries
-- no birth data — consent state and a revision number — so this exposes who
-- agreed, not anything about them.
create policy astro_room_members_peer_select on public.astro_room_members
    for select using (
        public.astro_room_is_member(astro_room_members.room_id)
    );

-- Invitations hold the token hash and are never client-readable, by anyone,
-- including the owner. The owner gets the token once, from the API response
-- that created it.
create policy astro_room_invitations_service_only on public.astro_room_invitations
    for select using (false);

create policy astro_room_results_member_select on public.astro_room_results
    for select using (
        public.astro_room_has_both_consents(astro_room_results.room_id)
        and public.astro_room_is_member(astro_room_results.room_id)
    );

-- ============================================================== grants

-- Nothing for clients. Rooms are created, joined and read through the API,
-- which is the only place the two-sided consent rule can be enforced with
-- the invitation token in hand. A `select` grant here would hand
-- premium_content and the evidence to anyone holding the anon key — the
-- exact leak migration 000011 closed for natal_reports.
grant all on public.astro_rooms to service_role;
grant all on public.astro_room_members to service_role;
grant all on public.astro_room_invitations to service_role;
grant all on public.astro_room_results to service_role;
revoke truncate, trigger, references
    on public.astro_rooms, public.astro_room_members,
       public.astro_room_invitations, public.astro_room_results
    from anon, authenticated;

-- ================================================ merge and deletion

-- Only the two tables that answer "whose row is this?" with one column join
-- the catalog. The other two are room-scoped and ride along on the room's
-- own foreign key.
--
-- `astro_rooms` by its owner: a guest who created rooms keeps them after
-- signing in, and deleting an owner deletes their rooms, which cascades to
-- the membership, the invitation and the shared result. That is the intended
-- outcome rather than a side effect: leaving a room alive after its owner is
-- gone would leave the invitee's consented material in a room nobody owns.
--
-- `astro_room_members` by the member, deduped on room_id. Without the
-- dedupe, a guest invitee merging into an account that is already in the
-- same room hits unique (room_id, user_id) and the whole merge transaction
-- fails. With it, the destination account's membership wins and the guest
-- row is dropped — which leaves the room one consent short, and
-- `astro_room_has_both_consents` stops returning true. No special case.
insert into public.user_scoped_tables (table_name, user_column, dedupe_columns) values
    ('astro_rooms', 'owner_user_id', '{}'),
    ('astro_room_members', 'user_id', '{room_id}');
