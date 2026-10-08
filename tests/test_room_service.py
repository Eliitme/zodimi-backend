"""Invite tokens and reading identity.

Both are small pure functions, and both are the kind of thing that looks
right and is not: a token that is predictable, or a revision hash that
collides between two different readings.
"""

import re

from app.astro import synastry_preview
from app.core.config import get_settings
from app.services import room_service


def with_secret(monkeypatch, value: str = "test-secret") -> None:
    """Pins both room settings.

    WEB_BASE_URL is blanked rather than left alone: settings read the ambient
    .env, so a developer with a local host configured turned "the link is
    just a path" into a failure that only happened on their machine. A test
    that wants a host sets one.
    """
    monkeypatch.setenv("ROOM_INVITE_SECRET", value)
    monkeypatch.setenv("WEB_BASE_URL", "")
    get_settings.cache_clear()


def test_a_token_is_long_random_and_url_safe(monkeypatch):
    with_secret(monkeypatch)
    tokens = {room_service.new_invite_token()[0] for _ in range(200)}
    assert len(tokens) == 200, "tokens repeated — not drawn from a CSPRNG"
    for token in tokens:
        # token_urlsafe(32) is 43 characters; anything that needs escaping
        # would break the link it is pasted into.
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", token), token


def test_the_stored_hash_is_not_the_token(monkeypatch):
    with_secret(monkeypatch)
    token, stored = room_service.new_invite_token()
    assert token not in stored
    assert len(stored) == 64  # sha256 hex


def test_hashing_is_stable_for_one_secret_and_changes_with_it(monkeypatch):
    with_secret(monkeypatch, "secret-one")
    first = room_service.hash_invite_token("abc")
    assert room_service.hash_invite_token("abc") == first

    # Rotating the secret is the emergency lever: every outstanding
    # invitation stops resolving, because no stored hash matches any more.
    with_secret(monkeypatch, "secret-two")
    assert room_service.hash_invite_token("abc") != first


def test_the_link_falls_back_to_a_path_when_no_host_is_configured(monkeypatch):
    with_secret(monkeypatch)
    monkeypatch.setenv("WEB_BASE_URL", "")
    get_settings.cache_clear()
    assert room_service.invite_url("tok") == "/m/tok"

    monkeypatch.setenv("WEB_BASE_URL", "https://example.test/")
    get_settings.cache_clear()
    # Trailing slash on the setting must not produce a double slash.
    assert room_service.invite_url("tok") == "https://example.test/m/tok"


def test_the_link_carries_the_token_and_nothing_else(monkeypatch):
    # AC-06: no user id, no room id, no name. Anything else in the URL ends
    # up in referrer headers, chat previews and server logs.
    with_secret(monkeypatch)
    token, _ = room_service.new_invite_token()
    assert room_service.invite_url(token) == f"/m/{token}"


def test_the_pair_revision_changes_when_either_person_edits_their_chart():
    base = room_service.pair_revision_hash(1, 1, "vi")
    assert room_service.pair_revision_hash(2, 1, "vi") != base
    assert room_service.pair_revision_hash(1, 2, "vi") != base
    # And is stable otherwise, which is what lets the reading be cached.
    assert room_service.pair_revision_hash(1, 1, "vi") == base


def test_the_pair_revision_is_not_symmetric():
    # A read of (owner=1, invitee=2) is not the same reading as
    # (owner=2, invitee=1): the aspects are directional.
    assert room_service.pair_revision_hash(1, 2, "vi") != room_service.pair_revision_hash(2, 1, "vi")


def test_locale_and_versions_are_part_of_the_identity():
    assert room_service.pair_revision_hash(1, 1, "vi") != room_service.pair_revision_hash(1, 1, "en")

    base = room_service.pair_revision_hash(1, 1, "vi")
    original = synastry_preview.TEMPLATE_VERSION
    try:
        # Bumping the template is how reworded insights reach existing
        # rooms; without this they would keep serving the old cached text.
        synastry_preview.TEMPLATE_VERSION = "rules-v2"
        assert room_service.pair_revision_hash(1, 1, "vi") != base
    finally:
        synastry_preview.TEMPLATE_VERSION = original


def test_an_unknown_revision_never_collides_with_a_real_one():
    # A null revision means "cannot prove which chart this was", which must
    # not be mistaken for any particular chart.
    unknown = room_service.pair_revision_hash(None, 1, "vi")
    assert unknown != room_service.pair_revision_hash(1, 1, "vi")
    assert unknown == room_service.pair_revision_hash(None, 1, "vi")


def test_the_revision_carries_nothing_it_was_computed_from():
    # It travels in API responses and logs, so it has to be opaque.
    value = room_service.pair_revision_hash(7, 9, "vi")
    assert value.startswith("sha256-")
    digest = value.removeprefix("sha256-")
    assert re.fullmatch(r"[0-9a-f]{64}", digest), "not an opaque digest"
