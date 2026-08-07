-- Guest→permanent account merge (fallback path when identity linking fails)
-- and the shared catalog of user-scoped tables.
--
-- `user_scoped_tables` is the SINGLE source of truth consumed by BOTH the
-- guest-merge here and the account-deletion flow (FR9): when a new
-- user-scoped table is added (e.g. synastry in the monetization phase),
-- add one row here instead of editing two functions.

create table public.user_scoped_tables (
    table_name  text primary key,
    -- Column holding the owning user id (profiles/tarot_draws use user_id,
    -- synastry_results uses owner_user_id).
    user_column text not null default 'user_id'
);

alter table public.user_scoped_tables enable row level security;
-- Service-role-only: no policies.

insert into public.user_scoped_tables (table_name, user_column) values
    ('profiles', 'user_id'),
    ('tarot_draws', 'user_id'),
    ('tarot_qa_log', 'user_id'),
    ('natal_reports', 'user_id'),
    ('devices', 'user_id');
-- synastry_results / partner_profiles are appended by the phase that
-- creates them (synastry may be cut — nothing here may reference it).

-- Re-points every user-scoped row from old_id to new_id in ONE transaction.
-- The caller (backend, service role) verifies both JWTs BEFORE calling and
-- deletes the anonymous auth user only AFTER this commits — so a client
-- killed mid-flow leaves either all data on the old user or all on the new,
-- never a half-merged state. Idempotent: re-running with the same old_id
-- just moves zero rows.
create function public.merge_user_data(old_id uuid, new_id uuid)
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
    for entry in select table_name, user_column from user_scoped_tables loop
        -- Skip tables that do not exist yet (catalog may run ahead of a
        -- feature phase that was cut or reordered).
        if to_regclass('public.' || entry.table_name) is null then
            continue;
        end if;
        execute format(
            'update public.%I set %I = $1 where %I = $2',
            entry.table_name, entry.user_column, entry.user_column
        ) using new_id, old_id;
    end loop;
end;
$$;

-- Callable only via service role (backend); nothing granted to clients.
-- (revoking PUBLIC also removes the default grant, so service_role needs
-- an explicit one.)
revoke execute on function public.merge_user_data(uuid, uuid) from public, anon, authenticated;
grant execute on function public.merge_user_data(uuid, uuid) to service_role;
