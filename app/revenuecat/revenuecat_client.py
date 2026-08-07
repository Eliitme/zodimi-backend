"""Minimal RevenueCat REST wrapper — the ONLY trusted source for a
subscriber's entitlement state. Client-supplied purchase claims are never
believed; `/v1/iap/sync` calls this instead.
"""

from datetime import UTC, datetime

import httpx

from app.core.config import get_settings

API_BASE = "https://api.revenuecat.com/v1"
ENTITLEMENT = "premium"


class RevenueCatNotConfiguredError(RuntimeError):
    """REVENUECAT_API_KEY is empty (account not created yet)."""


def subscriber_has_premium(app_user_id: str) -> bool:
    """GET /subscribers/{id} with the SECRET api key and check whether the
    `premium` entitlement is currently active."""
    settings = get_settings()
    if not settings.revenuecat_api_key:
        raise RevenueCatNotConfiguredError

    response = httpx.get(
        f"{API_BASE}/subscribers/{app_user_id}",
        headers={"Authorization": f"Bearer {settings.revenuecat_api_key}"},
        timeout=10,
    )
    response.raise_for_status()
    entitlements = response.json().get("subscriber", {}).get("entitlements", {})
    premium = entitlements.get(ENTITLEMENT)
    if not premium:
        return False
    expires = premium.get("expires_date")
    if expires is None:
        return True  # lifetime / non-expiring
    return datetime.fromisoformat(expires.replace("Z", "+00:00")) > datetime.now(UTC)
