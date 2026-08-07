-- Two fixes from Phase-4 review, both reproduced against the live stack:
--
-- 1. Profile upsert via PostgREST returned 403: ON CONFLICT DO UPDATE lists
--    every payload column in SET, and Postgres checks UPDATE privilege on
--    all of them even when no conflict occurs — user_id was not granted.
--    Granting it is safe: profiles_owner_update's WITH CHECK
--    (auth.uid() = user_id) still makes re-pointing a row to another user
--    impossible.
grant update (user_id) on public.profiles to authenticated;

-- 2. merge_user_data blew up when the DESTINATION user already had rows
--    that collide with the guest's (most common real trigger: reinstall →
--    guest → sign in to an EXISTING Apple account → fallback merge).
--    Policy: the permanent (destination) account's data wins; colliding
--    guest rows are dropped, non-colliding rows move over.
alter table public.user_scoped_tables
    add column one_row_per_user boolean not null default false,
    -- Columns (besides the user column) that form a per-user uniqueness
    -- rule, e.g. tarot_draws unique(user_id, drawn_on) → '{drawn_on}'.
    add column dedupe_columns text[] not null default '{}';

update public.user_scoped_tables set one_row_per_user = true where table_name = 'profiles';
update public.user_scoped_tables set dedupe_columns = '{drawn_on}' where table_name = 'tarot_draws';

create or replace function public.merge_user_data(old_id uuid, new_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    entry record;
begin
    if old_id = new_id then
        raise exception 'old_id and new_id must differ';
    end if;
    for entry in
        select table_name, user_column, one_row_per_user, dedupe_columns
        from user_scoped_tables
    loop
        if to_regclass('public.' || entry.table_name) is null then
            continue;
        end if;

        if entry.one_row_per_user then
            -- Destination wins: drop the guest row when one already exists.
            execute format(
                'delete from public.%1$I where %2$I = $2
                   and exists (select 1 from public.%1$I where %2$I = $1)',
                entry.table_name, entry.user_column
            ) using new_id, old_id;
        elsif array_length(entry.dedupe_columns, 1) > 0 then
            -- Drop guest rows that collide on the per-user unique columns.
            execute format(
                'delete from public.%1$I t where t.%2$I = $2
                   and exists (select 1 from public.%1$I n
                                where n.%2$I = $1 and (%3$s))',
                entry.table_name, entry.user_column,
                (select string_agg(format('n.%1$I = t.%1$I', c), ' and ')
                 from unnest(entry.dedupe_columns) as c)
            ) using new_id, old_id;
        end if;

        execute format(
            'update public.%I set %I = $1 where %I = $2',
            entry.table_name, entry.user_column, entry.user_column
        ) using new_id, old_id;
    end loop;
end;
$$;

-- create or replace preserves grants, but re-assert to be explicit.
revoke execute on function public.merge_user_data(uuid, uuid) from public, anon, authenticated;
grant execute on function public.merge_user_data(uuid, uuid) to service_role;
