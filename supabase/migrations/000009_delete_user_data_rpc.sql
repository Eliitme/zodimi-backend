-- Account deletion (FR9): removes every user-scoped row in ONE transaction,
-- driven by the same `user_scoped_tables` catalog the guest-merge uses — so
-- a table added for a new feature can never be deleted by one flow and
-- missed by the other.
--
-- iap_events is NOT in that catalog (it is service-role billing history, not
-- user-scoped app data) but it does hold a user_id, so it is cleared here
-- explicitly. ai_content_cache holds only shared, non-personal content and
-- is deliberately left alone.
create function public.delete_user_data(target_id uuid)
returns void
language plpgsql
security definer
set search_path = public
as $$
declare
    entry record;
begin
    for entry in select table_name, user_column from user_scoped_tables loop
        if to_regclass('public.' || entry.table_name) is null then
            continue;
        end if;
        execute format('delete from public.%I where %I = $1',
                       entry.table_name, entry.user_column) using target_id;
    end loop;
    delete from iap_events where user_id = target_id;
end;
$$;

revoke execute on function public.delete_user_data(uuid) from public, anon, authenticated;
grant execute on function public.delete_user_data(uuid) to service_role;
