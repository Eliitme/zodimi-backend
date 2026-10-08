-- Accepting an invitation, as one indivisible step.
--
-- The check and the write cannot be two round trips. Share a link to two
-- devices and both pass "is this invitation unclaimed?" before either writes,
-- and the room ends up with two invitees, or one person's consent recorded
-- against the other's membership. The PostgREST client has no transaction to
-- wrap that in, so the whole decision lives here and the row lock does the
-- serialising.
--
-- Returns a code rather than raising. A caller has to tell "expired" from
-- "already claimed" from "you cannot accept your own invitation" to say
-- anything useful, and parsing that back out of an exception message is a
-- contract made of error strings.

create function public.accept_room_invitation(
    p_token_hash      text,
    p_user_id         uuid,
    p_consent_version text,
    p_profile_revision integer
)
returns table (code text, room_id uuid)
language plpgsql
security definer
set search_path = public
as $$
declare
    inv  public.astro_room_invitations;
    room public.astro_rooms;
begin
    -- for update: the lock, and therefore the whole point of this function.
    -- Concurrent accepts of the same token queue here; the second one wakes
    -- up seeing accepted_at already set.
    select * into inv
    from public.astro_room_invitations
    where token_hash = p_token_hash
    for update;

    if not found then
        -- Neutral: a wrong token and a token for a room that was deleted
        -- must look identical, or the endpoint becomes an existence oracle.
        return query select 'invalid'::text, null::uuid;
        return;
    end if;

    update public.astro_room_invitations
    set attempt_count = attempt_count + 1
    where id = inv.id;

    if inv.revoked_at is not null then
        return query select 'revoked'::text, null::uuid;
        return;
    end if;

    if inv.accepted_at is not null then
        -- A retry after a dropped response is not a second claim. Same
        -- person, same outcome, no error — otherwise a flaky connection
        -- locks someone out of the room they just joined.
        if inv.accepted_by = p_user_id then
            return query select 'ok'::text, inv.room_id;
        else
            return query select 'already_claimed'::text, null::uuid;
        end if;
        return;
    end if;

    if inv.expires_at <= now() then
        return query select 'expired'::text, null::uuid;
        return;
    end if;

    select * into room from public.astro_rooms where id = inv.room_id;
    if not found or room.status in ('revoked', 'closed') then
        return query select 'room_closed'::text, null::uuid;
        return;
    end if;

    if room.owner_user_id = p_user_id then
        -- A room is two people. Accepting your own invitation would also
        -- trip unique (room_id, user_id), but a named code lets the app say
        -- why instead of showing a database error.
        return query select 'owner_cannot_accept'::text, null::uuid;
        return;
    end if;

    begin
        insert into public.astro_room_members
            (room_id, user_id, role, consent_at, consent_version, profile_revision)
        values
            (inv.room_id, p_user_id, 'invitee', now(), p_consent_version, p_profile_revision);
    exception when unique_violation then
        -- Belt and braces: the lock above already serialises this path.
        return query select 'capacity'::text, null::uuid;
        return;
    end;

    update public.astro_room_invitations
    set accepted_at = now(), accepted_by = p_user_id
    where id = inv.id;

    update public.astro_rooms
    set status = 'ready', updated_at = now()
    where id = inv.room_id;

    return query select 'ok'::text, inv.room_id;
end;
$$;

revoke execute on function
    public.accept_room_invitation(text, uuid, text, integer)
    from public, anon, authenticated;
grant execute on function
    public.accept_room_invitation(text, uuid, text, integer)
    to service_role;
