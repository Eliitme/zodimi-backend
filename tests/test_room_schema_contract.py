"""Room-schema invariants that must not be edited away by accident.

The behaviour itself is verified against a real database by
`scripts/verify-room-lifecycle.sql` and `scripts/verify-rls.sh`. Neither runs
in CI, because neither the unit suite nor the build has a Postgres. This file
is the part that does run everywhere: it reads the migration and fails on the
specific edits that would reopen a hole, so a careless `grant select` is
caught by `uv run pytest` rather than by a reader of production data.

Migration 000011 exists because exactly that grant was once in place on the
paid-content tables. A room holds two people's material and a paid reading,
so the same mistake costs more here.
"""

import re
from pathlib import Path

MIGRATIONS = Path(__file__).parent.parent / "supabase" / "migrations"
ROOM_SQL = (MIGRATIONS / "000013_astro_rooms.sql").read_text()

ROOM_TABLES = [
    "astro_rooms",
    "astro_room_members",
    "astro_room_invitations",
    "astro_room_results",
]


def test_no_client_role_is_granted_anything_on_a_room_table():
    # The two-sided consent rule needs the invitation token to enforce, and
    # the token only ever exists inside an API request. A direct grant hands
    # premium_content and the evidence to anyone holding the anon key.
    for statement in re.findall(r"^grant\s+[^;]+;", ROOM_SQL, re.MULTILINE | re.IGNORECASE):
        if not any(table in statement for table in ROOM_TABLES):
            continue
        assert "service_role" in statement, statement
        for role in ("authenticated", "anon"):
            assert role not in statement, f"{role} granted on a room table: {statement}"


def test_every_room_table_has_row_level_security_enabled():
    # Defence in depth behind the missing grant. Without this, adding a grant
    # later would expose every row rather than the caller's own.
    for table in ROOM_TABLES:
        assert f"alter table public.{table} enable row level security;" in ROOM_SQL, table


def test_the_membership_policy_does_not_select_its_own_table():
    # A policy on astro_room_members that reads astro_room_members recurses
    # (42P17) and takes every room query down with it, including the rooms
    # table whose own policy reads membership. The security-definer helper
    # exists to break that cycle; inlining the EXISTS again would restore it.
    policy = re.search(
        r"create policy astro_room_members_peer_select.*?;", ROOM_SQL, re.S
    )
    assert policy, "membership policy missing"
    assert "astro_room_is_member" in policy.group(0)
    assert "from public.astro_room_members" not in policy.group(0)


def test_the_policy_helpers_are_executable_by_the_role_that_runs_policies():
    # A policy is evaluated as the CALLING role. Revoking from public without
    # re-granting leaves "permission denied for function" in place of every
    # room read — a wall with no door, which passes a review that only looks
    # for things being locked.
    for helper in ("astro_room_is_member", "astro_room_has_both_consents"):
        grant = re.search(
            rf"^grant execute on function public\.{helper}\([^)]*\) to ([^;]+);",
            ROOM_SQL, re.MULTILINE,
        )
        assert grant, f"{helper} is never granted to anyone"
        assert "authenticated" in grant.group(1), helper


def test_membership_identifies_the_caller_from_the_token():
    # Taking a user id as an argument would let anyone holding a room id ask
    # whether some OTHER account is in it.
    signature = re.search(
        r"create function public\.astro_room_is_member\(([^)]*)\)", ROOM_SQL
    )
    assert signature, "membership helper missing"
    assert signature.group(1).strip() == "p_room_id uuid"
    assert "auth.uid()" in ROOM_SQL.split("astro_room_is_member")[1]


def test_both_user_scoped_room_tables_join_the_merge_and_deletion_catalog():
    # One catalog drives guest-merge AND account deletion, so a table missing
    # from it is a table that survives a deletion request.
    catalog = re.search(
        r"insert into public\.user_scoped_tables[^;]+;", ROOM_SQL, re.S
    )
    assert catalog, "room tables never registered"
    assert "'astro_rooms', 'owner_user_id'" in catalog.group(0)
    assert "'astro_room_members', 'user_id'" in catalog.group(0)
    # Without the room_id dedupe, a guest invitee signing into an account
    # that is already in the same room hits unique (room_id, user_id) and the
    # whole merge transaction fails — they cannot sign in at all.
    assert "{room_id}" in catalog.group(0)


def test_a_room_cannot_hold_more_than_two_people():
    # One owner plus at most one invitee, enforced by two partial unique
    # indexes rather than by the API remembering to count.
    for role in ("owner", "invitee"):
        assert re.search(
            rf"create unique index astro_room_members_one_{role}"
            rf"\s+on public\.astro_room_members \(room_id\)\s+where role = '{role}';",
            ROOM_SQL,
        ), role
    assert "unique (room_id, user_id)" in ROOM_SQL


def test_only_one_invitation_can_be_live_per_room():
    # Reissuing has to revoke the old token, not leave two working links.
    assert re.search(
        r"create unique index astro_room_invitations_one_live\s+"
        r"on public\.astro_room_invitations \(room_id\)\s+"
        r"where revoked_at is null and accepted_at is null;",
        ROOM_SQL,
    )


def test_the_invitation_stores_a_hash_and_not_the_token():
    # A dump of this table must not let anyone join anything.
    assert "token_hash    text not null unique" in ROOM_SQL
    assert not re.search(r"^\s+token\s+text", ROOM_SQL, re.MULTILINE)


def test_a_deleted_invitee_does_not_erase_the_record_that_the_invite_was_used():
    # on delete set null, not cascade: the owner's "accepted" state is
    # derived from this row.
    assert "accepted_by   uuid references auth.users (id) on delete set null" in ROOM_SQL


def test_the_migration_ordinal_is_not_reused():
    ordinals = [path.name.split("_")[0] for path in MIGRATIONS.glob("*.sql")]
    assert len(ordinals) == len(set(ordinals)), sorted(ordinals)
