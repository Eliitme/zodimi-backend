-- A generated reading belongs to the chart it was written for.
--
-- Today nothing records which chart that was. `GET /v1/synastry/{id}`
-- recomputes the inter-aspects from the caller's CURRENT profile and returns
-- them beside interpretation text written for the old one: edit your birth
-- time and the aspects move while the prose stays, so the page shows a
-- reading that contradicts the evidence printed under it. The same hole is
-- latent in natal reports, which are generated from a profile snapshot the
-- row never names.
--
-- Two halves to the fix:
--   1. `profiles.profile_revision` — a counter that moves only when the
--      birth data moves, so "is this reading still about this chart?" has an
--      answer.
--   2. Each reading records the revision it was generated from, and synastry
--      additionally stores the aspects it was written against instead of
--      deriving them again at read time.

-- =========================================================== 1. revision

alter table public.profiles
    add column profile_revision integer not null default 1;

-- Decided by the database, never by the caller. `authenticated` holds a
-- column-scoped UPDATE grant on profiles (000010) and this column is absent
-- from it, but the trigger is what makes that non-negotiable: without it a
-- client with a wider grant — or a backend bug — could pin the revision and
-- make a stale reading look current, which is precisely the claim the
-- column exists to make.
create or replace function public.bump_profile_revision()
returns trigger
language plpgsql
set search_path = public
as $$
begin
    if tg_op = 'INSERT' then
        new.profile_revision := 1;
        return new;
    end if;

    -- Only the inputs to the chart count. A rename, a locale switch or a
    -- premium flag flipping does not invalidate anything already written,
    -- and bumping on those would mark every reading stale the first time
    -- someone edited their display name.
    if new.birth_date is distinct from old.birth_date
       or new.birth_time is distinct from old.birth_time
       or new.birth_time_unknown is distinct from old.birth_time_unknown
       or new.lat is distinct from old.lat
       or new.lon is distinct from old.lon
       or new.tz_of_birth is distinct from old.tz_of_birth
    then
        new.profile_revision := old.profile_revision + 1;
    else
        new.profile_revision := old.profile_revision;
    end if;
    return new;
end;
$$;

create trigger profiles_bump_revision
    before insert or update on public.profiles
    for each row execute function public.bump_profile_revision();

-- =================================================== 2. reading snapshots

alter table public.natal_reports
    -- Null on rows written before this migration: their revision is unknown,
    -- which is not the same as "current", and the API says so rather than
    -- guessing.
    add column profile_revision integer;

alter table public.synastry_results
    add column owner_profile_revision integer,
    -- The aspects the interpretation was written against. Stored rather than
    -- recomputed so the text and the evidence under it can never drift apart,
    -- however often the owner edits their birth data afterwards.
    add column inter_aspects jsonb;

-- Guest merge note: `merge_user_data` re-points profiles by UPDATE, so the
-- revision rides along with the row and readings stay current. When the
-- destination account already HAS a profile the guest row is dropped instead,
-- and readings moved over from the guest keep their old revision — they end
-- up marked as belonging to an earlier chart, which is exactly what they are:
-- the account's birth data is now the destination's.
