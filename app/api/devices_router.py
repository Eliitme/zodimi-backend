"""Device registration for push (FR8).

`devices` is the single source of truth for push scheduling timezones —
the app re-registers whenever the token, timezone, or locale changes, so a
travelling user gets their 8am notification in the CURRENT timezone.
"""

from fastapi import APIRouter, Request, status
from pydantic import BaseModel, Field

from app.core.supabase_client import get_service_client

router = APIRouter(prefix="/devices", tags=["devices"])


class DeviceRegistration(BaseModel):
    apns_token: str = Field(min_length=32, max_length=200)
    device_timezone: str          # IANA zone, e.g. "Asia/Ho_Chi_Minh"
    locale: str = Field(pattern="^(vi|en)$")


class DeviceResponse(BaseModel):
    registered: bool


@router.post("", response_model=DeviceResponse, status_code=status.HTTP_201_CREATED)
def register_device(request: Request, body: DeviceRegistration) -> DeviceResponse:
    user_id: str = request.state.user_id
    # Upsert on the token: reinstalling or switching accounts on one device
    # must re-point the token, never create a duplicate row.
    get_service_client().table("devices").upsert({
        "user_id": user_id,
        "apns_token": body.apns_token,
        "device_timezone": body.device_timezone,
        "locale": body.locale,
        "updated_at": "now()",
    }, on_conflict="apns_token").execute()
    return DeviceResponse(registered=True)


@router.delete("/{apns_token}", response_model=DeviceResponse)
def unregister_device(request: Request, apns_token: str) -> DeviceResponse:
    """Called when the user turns notifications off."""
    user_id: str = request.state.user_id
    get_service_client().table("devices").delete() \
        .eq("apns_token", apns_token).eq("user_id", user_id).execute()
    return DeviceResponse(registered=False)
