-- Room lifecycle invariants, against the LOCAL Supabase stack.
--   psql "$DB_URL" -v ON_ERROR_STOP=1 -f scripts/verify-room-lifecycle.sql
--
-- These rules live in constraints, a predicate function and the two
-- catalog-driven RPCs — none of which the Python suite exercises, because it
-- stubs the database. A room holds two accounts' material, so the ways it can
-- go wrong are all at this level: a third member, two live invites, a merge
-- that collides on a unique index, a deletion that leaves one person's
-- consented reading readable by the other.
--
-- Everything runs in one transaction and rolls back, so the stack is left
-- exactly as it was found.

begin;

\set QUIET on
\set ON_ERROR_STOP on

create or replace function pg_temp.expect(label text, actual anyelement, wanted anyelement)
returns void language plpgsql as $$
begin
    if actual is distinct from wanted then
        raise exception 'FAIL: % (expected %, got %)', label, wanted, actual;
    end if;
    raise notice '  ok: %', label;
end;
$$;

/* Runs a statement that must fail with a given SQLSTATE. */
create or replace function pg_temp.expect_rejected(label text, stmt text, sqlstate_wanted text)
returns void language plpgsql as $$
begin
    begin
        execute stmt;
    exception when others then
        if sqlstate = sqlstate_wanted then
            raise notice '  ok: %', label;
            return;
        end if;
        raise exception 'FAIL: % (expected SQLSTATE %, got % — %)',
            label, sqlstate_wanted, sqlstate, sqlerrm;
    end;
    raise exception 'FAIL: % (statement was accepted)', label;
end;
$$;

create or replace function pg_temp.mkuser(tag text)
returns uuid language plpgsql as $$
declare id uuid := gen_random_uuid();
begin
    insert into auth.users (id, instance_id, aud, role, email,
                            encrypted_password, created_at, updated_at)
    values (id, '00000000-0000-0000-0000-000000000000', 'authenticated',
            'authenticated', tag || '-' || id || '@example.test', 'x', now(), now());
    return id;
end;
$$;

create or replace function pg_temp.mkroom(owner_id uuid)
returns uuid language plpgsql as $$
declare room uuid;
begin
    insert into public.astro_rooms (owner_user_id, topic)
    values (owner_id, 'friendship') returning id into room;
    -- The owner consents by creating the room.
    insert into public.astro_room_members (room_id, user_id, role, consent_at, consent_version)
    values (room, owner_id, 'owner', now(), 'v1');
    return room;
end;
$$;

do $$
declare
    a uuid; b uuid; c uuid; room uuid;
begin
raise notice '== shape: a room holds exactly two people ==';
    a := pg_temp.mkuser('a'); b := pg_temp.mkuser('b'); c := pg_temp.mkuser('c');
    room := pg_temp.mkroom(a);

    insert into public.astro_room_members (room_id, user_id, role, consent_at, consent_version)
    values (room, b, 'invitee', now(), 'v1');

    -- 23505 unique_violation, from the partial index on role = 'invitee'.
    perform pg_temp.expect_rejected(
        'a third person cannot join',
        format('insert into public.astro_room_members (room_id, user_id, role)
                values (%L, %L, ''invitee'')', room, c),
        '23505');

    perform pg_temp.expect_rejected(
        'the owner cannot also be the invitee',
        format('insert into public.astro_room_members (room_id, user_id, role)
                values (%L, %L, ''invitee'')', room, a),
        '23505');

    perform pg_temp.expect_rejected(
        'a room cannot have two owners',
        format('insert into public.astro_room_members (room_id, user_id, role)
                values (%L, %L, ''owner'')', room, c),
        '23505');

raise notice '== invitations: one live link at a time ==';
    insert into public.astro_room_invitations (room_id, token_hash, expires_at)
    values (room, 'hash-1', now() + interval '7 days');

    perform pg_temp.expect_rejected(
        'a second live invitation is refused',
        format('insert into public.astro_room_invitations (room_id, token_hash, expires_at)
                values (%L, ''hash-2'', now() + interval ''7 days'')', room),
        '23505');

    -- Revoking the first one frees the slot, which is how reissue works.
    update public.astro_room_invitations set revoked_at = now() where room_id = room;
    insert into public.astro_room_invitations (room_id, token_hash, expires_at)
    values (room, 'hash-2', now() + interval '7 days');
    perform pg_temp.expect(
        'revoking frees the slot for a reissue',
        (select count(*)::int from public.astro_room_invitations where room_id = room), 2);

raise notice '== consent: the reading lives only while both agreements stand ==';
    perform pg_temp.expect('both consented', public.astro_room_has_both_consents(room), true);

    update public.astro_room_members set revoked_at = now()
    where room_id = room and user_id = b;
    perform pg_temp.expect(
        'withdrawal closes it', public.astro_room_has_both_consents(room), false);

    update public.astro_room_members set revoked_at = null
    where room_id = room and user_id = b;
    perform pg_temp.expect(
        'and re-consenting reopens it', public.astro_room_has_both_consents(room), true);

    -- An invitee who declined has a row written only on acceptance, so the
    -- modelled equivalent is a member with no consent timestamp.
    update public.astro_room_members set consent_at = null
    where room_id = room and user_id = b;
    perform pg_temp.expect(
        'an unconsented member does not count',
        public.astro_room_has_both_consents(room), false);
end $$;

do $$
declare
    a uuid; b uuid; guest uuid; room uuid; result_id uuid;
begin
raise notice '== merge: a guest invitee signing into a fresh account ==';
    a := pg_temp.mkuser('a'); guest := pg_temp.mkuser('guest'); b := pg_temp.mkuser('b');
    room := pg_temp.mkroom(a);
    insert into public.astro_room_members (room_id, user_id, role, consent_at, consent_version)
    values (room, guest, 'invitee', now(), 'v1');

    perform public.merge_user_data(guest, b);

    perform pg_temp.expect(
        'the membership moves to the new account',
        (select user_id from public.astro_room_members where room_id = room and role = 'invitee'),
        b);
    perform pg_temp.expect(
        'and the room is still shareable',
        public.astro_room_has_both_consents(room), true);

raise notice '== merge: a guest invitee signing into an account already in the room ==';
    -- The collision case. Without dedupe_columns on room_id this hits
    -- unique (room_id, user_id) and the whole merge transaction fails, which
    -- would mean a reader could not sign in at all.
    guest := pg_temp.mkuser('guest2');
    update public.astro_room_members set user_id = guest
    where room_id = room and role = 'invitee';

    insert into public.astro_room_results (room_id, pair_revision_hash, status, premium_content)
    values (room, 'hash-a', 'done', 'paid text') returning id into result_id;

    -- `a` owns the room and already holds its owner row.
    perform public.merge_user_data(guest, a);

    perform pg_temp.expect(
        'the colliding guest row is dropped rather than failing the merge',
        (select count(*)::int from public.astro_room_members where room_id = room), 1);
    perform pg_temp.expect(
        'and the shared reading closes, because only one consent is left',
        public.astro_room_has_both_consents(room), false);
    perform pg_temp.expect(
        'the result row itself is kept for the retention policy to decide',
        (select count(*)::int from public.astro_room_results where id = result_id), 1);

raise notice '== merge: an owner signing in keeps their rooms ==';
    a := pg_temp.mkuser('a2'); b := pg_temp.mkuser('b2');
    room := pg_temp.mkroom(a);
    perform public.merge_user_data(a, b);
    perform pg_temp.expect(
        'ownership moves with the account',
        (select owner_user_id from public.astro_rooms where id = room), b);
    perform pg_temp.expect(
        'and so does the owner membership',
        (select user_id from public.astro_room_members where room_id = room and role = 'owner'),
        b);
end $$;

do $$
declare
    a uuid; b uuid; room uuid;
begin
raise notice '== deletion: the owner leaves ==';
    a := pg_temp.mkuser('a3'); b := pg_temp.mkuser('b3');
    room := pg_temp.mkroom(a);
    insert into public.astro_room_members (room_id, user_id, role, consent_at, consent_version)
    values (room, b, 'invitee', now(), 'v1');
    insert into public.astro_room_invitations (room_id, token_hash, expires_at)
    values (room, 'hash-del-1', now() + interval '7 days');
    insert into public.astro_room_results (room_id, pair_revision_hash, status, premium_content)
    values (room, 'hash-b', 'done', 'paid text');

    perform public.delete_user_data(a);

    -- Everything goes: leaving the room alive would leave the invitee's
    -- consented material in a room with no owner.
    perform pg_temp.expect('the room is gone',
        (select count(*)::int from public.astro_rooms where id = room), 0);
    perform pg_temp.expect('both memberships are gone',
        (select count(*)::int from public.astro_room_members where room_id = room), 0);
    perform pg_temp.expect('the invitation is gone',
        (select count(*)::int from public.astro_room_invitations where room_id = room), 0);
    perform pg_temp.expect('the shared reading is gone',
        (select count(*)::int from public.astro_room_results where room_id = room), 0);

raise notice '== deletion: the invitee leaves ==';
    a := pg_temp.mkuser('a4'); b := pg_temp.mkuser('b4');
    room := pg_temp.mkroom(a);
    insert into public.astro_room_members (room_id, user_id, role, consent_at, consent_version)
    values (room, b, 'invitee', now(), 'v1');
    insert into public.astro_room_results (room_id, pair_revision_hash, status, premium_content)
    values (room, 'hash-c', 'done', 'paid text');

    perform public.delete_user_data(b);

    perform pg_temp.expect('their membership is gone',
        (select count(*)::int from public.astro_room_members
          where room_id = room and user_id = b), 0);
    perform pg_temp.expect('the owner keeps their own room',
        (select count(*)::int from public.astro_rooms where id = room), 1);
    -- The point of the whole exercise: the owner must not keep reading a
    -- reading that exists only because the other person agreed to it.
    perform pg_temp.expect('but the shared reading is closed to them too',
        public.astro_room_has_both_consents(room), false);
end $$;

do $$
declare
    a uuid; b uuid; room uuid;
begin
raise notice '== deletion: dropping the auth user directly ==';
    -- The RPC is the supported path, but a row removed straight from
    -- auth.users (an admin action, or Supabase''s own account deletion) must
    -- not leave orphans behind either.
    a := pg_temp.mkuser('a5'); b := pg_temp.mkuser('b5');
    room := pg_temp.mkroom(a);
    insert into public.astro_room_members (room_id, user_id, role, consent_at, consent_version)
    values (room, b, 'invitee', now(), 'v1');
    insert into public.astro_room_invitations (room_id, token_hash, expires_at, accepted_at, accepted_by)
    values (room, 'hash-del-2', now() + interval '7 days', now(), b);

    delete from auth.users where id = b;
    perform pg_temp.expect('the invitee membership cascades away',
        (select count(*)::int from public.astro_room_members where room_id = room), 1);
    perform pg_temp.expect(
        'the invitation survives with no acceptor, so the owner still sees it was used',
        (select accepted_at is not null and accepted_by is null
         from public.astro_room_invitations where room_id = room), true);

    delete from auth.users where id = a;
    perform pg_temp.expect('deleting the owner takes the room with it',
        (select count(*)::int from public.astro_rooms where id = room), 0);
end $$;

-- ======================================== the policies, actually exercised
--
-- No client role is granted SELECT on these tables, so the RLS policies
-- never run in production. That makes them easy to get wrong and never find
-- out: the first version recursed (a policy on astro_room_members selecting
-- from astro_room_members) and every room query would have failed 42P17 the
-- day a grant was added. Granting inside this transaction runs them for
-- real, as `authenticated`, with auth.uid() impersonated the way Supabase
-- sets it.
do $$
declare
    a uuid; b uuid; outsider uuid; room uuid;
begin
raise notice '== policies: reachable, non-recursive, and correct ==';
    a := pg_temp.mkuser('pol-a'); b := pg_temp.mkuser('pol-b');
    outsider := pg_temp.mkuser('pol-x');
    room := pg_temp.mkroom(a);
    insert into public.astro_room_members (room_id, user_id, role, consent_at, consent_version)
    values (room, b, 'invitee', now(), 'v1');
    insert into public.astro_room_results (room_id, pair_revision_hash, status, premium_content)
    values (room, 'hash-pol', 'done', 'paid text');

    grant select on public.astro_rooms, public.astro_room_members,
                    public.astro_room_invitations, public.astro_room_results
        to authenticated;

    set local role authenticated;

    perform set_config('request.jwt.claims', json_build_object('sub', a)::text, true);
    perform pg_temp.expect('the owner sees their room',
        (select count(*)::int from public.astro_rooms where id = room), 1);
    perform pg_temp.expect('and both membership rows',
        (select count(*)::int from public.astro_room_members where room_id = room), 2);
    perform pg_temp.expect('and the shared reading',
        (select count(*)::int from public.astro_room_results where room_id = room), 1);

    perform set_config('request.jwt.claims', json_build_object('sub', outsider)::text, true);
    perform pg_temp.expect('a stranger sees no room',
        (select count(*)::int from public.astro_rooms where id = room), 0);
    perform pg_temp.expect('no members',
        (select count(*)::int from public.astro_room_members where room_id = room), 0);
    perform pg_temp.expect('and no reading',
        (select count(*)::int from public.astro_room_results where room_id = room), 0);

    reset role;
    update public.astro_room_members set revoked_at = now()
    where room_id = room and user_id = b;
    set local role authenticated;

    perform set_config('request.jwt.claims', json_build_object('sub', a)::text, true);
    -- The point of D8: one person withdrawing closes the shared reading for
    -- the other as well, not just for themselves.
    perform pg_temp.expect('after the invitee withdraws the owner loses the reading',
        (select count(*)::int from public.astro_room_results where room_id = room), 0);
    perform pg_temp.expect('but still sees the room itself',
        (select count(*)::int from public.astro_rooms where id = room), 1);

    perform set_config('request.jwt.claims', json_build_object('sub', b)::text, true);
    perform pg_temp.expect('and the withdrawn invitee sees neither',
        (select count(*)::int from public.astro_rooms where id = room)
        + (select count(*)::int from public.astro_room_results where room_id = room), 0);

    -- Invitations are never client-readable, not even by the room's owner:
    -- the token hash lives there.
    perform set_config('request.jwt.claims', json_build_object('sub', a)::text, true);
    perform pg_temp.expect('nobody reads invitations',
        (select count(*)::int from public.astro_room_invitations where room_id = room), 0);

    reset role;
end $$;

\echo '  all room lifecycle checks passed'

rollback;
