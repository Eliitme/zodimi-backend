"""Rate limiting on the third-party-backed endpoint.

The web tier made /v1/geocode reachable by anyone who can create a guest
account, and Nominatim's policy applies to OUR user agent — an abusive
caller would get the shared UA blocked and break iOS onboarding too.
"""

import pytest
from fastapi import HTTPException, Request

from app.core.rate_limit import RateLimit, enforce, reset


def fake_request(user_id: str, anonymous: bool = False, ip: str = "1.2.3.4") -> Request:
    scope = {
        "type": "http",
        "headers": [(b"x-forwarded-for", ip.encode())],
        "client": (ip, 1234),
    }
    request = Request(scope)
    request.state.user_id = user_id
    request.state.jwt_claims = {"is_anonymous": anonymous}
    return request


@pytest.fixture(autouse=True)
def clean_windows():
    reset()
    yield
    reset()


LIMIT = RateLimit(max_calls=3, per_seconds=60)


def test_allows_calls_up_to_the_limit():
    request = fake_request("user-a")
    for _ in range(3):
        enforce(request, "geocode", LIMIT)


def test_rejects_the_call_past_the_limit():
    request = fake_request("user-a")
    for _ in range(3):
        enforce(request, "geocode", LIMIT)
    with pytest.raises(HTTPException) as caught:
        enforce(request, "geocode", LIMIT)
    assert caught.value.status_code == 429


def test_users_have_separate_budgets():
    for _ in range(3):
        enforce(fake_request("user-a"), "geocode", LIMIT)
    enforce(fake_request("user-b"), "geocode", LIMIT)  # must not raise


def test_anonymous_users_also_share_a_per_ip_budget():
    """Guest accounts are free to create, so a per-user limit alone would be
    bypassed by signing up again from the same machine."""
    for index in range(3):
        enforce(fake_request(f"guest-{index}", anonymous=True), "geocode", LIMIT)
    with pytest.raises(HTTPException) as caught:
        enforce(fake_request("guest-fresh", anonymous=True), "geocode", LIMIT)
    assert caught.value.status_code == 429


def test_different_ips_are_independent():
    for index in range(3):
        enforce(fake_request(f"g-{index}", anonymous=True, ip="9.9.9.9"), "geocode", LIMIT)
    enforce(fake_request("g-other", anonymous=True, ip="8.8.8.8"), "geocode", LIMIT)


def test_signed_in_users_are_not_limited_by_ip():
    """A household or office behind one NAT must not throttle each other."""
    for index in range(3):
        enforce(fake_request(f"real-{index}", anonymous=False), "geocode", LIMIT)
    enforce(fake_request("real-next", anonymous=False), "geocode", LIMIT)


def test_buckets_do_not_leak_across_endpoints():
    request = fake_request("user-a")
    for _ in range(3):
        enforce(request, "geocode", LIMIT)
    enforce(request, "other-endpoint", LIMIT)  # separate budget
