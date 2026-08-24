"""APNs sender (HTTP/2, token-based .p8 auth — no Firebase, iOS only).

Without APNs credentials configured, sends become no-ops rather than
raising: a missing push must never fail the flow that triggered it (a report
still completes, a cron run still finishes).

Callers get the APNs *reason* back so permanently-dead tokens can be reaped
— retrying an `Unregistered` token forever grows `devices` without bound.
"""

import asyncio
import logging
from dataclasses import dataclass

from app.core.config import get_settings

logger = logging.getLogger("stellune.push")

# Reasons that mean the token will NEVER work again (Apple's APNs docs).
DEAD_TOKEN_REASONS = {"Unregistered", "BadDeviceToken", "DeviceTokenNotForTopic"}

# Copy per locale for each notification kind.
DAILY_CARD = {
    "vi": ("Lá bài hôm nay đang đợi bạn", "Mở Stellune để xem thông điệp của ngày mới."),
    "en": ("Your card for today is waiting", "Open Stellune to see today's message."),
}
REPORT_READY = {
    "vi": ("Báo cáo của bạn đã sẵn sàng", "Bản đồ sao chuyên sâu của bạn đã được chuẩn bị xong."),
    "en": ("Your report is ready", "Your in-depth natal reading has finished."),
}


@dataclass(frozen=True)
class PushResult:
    delivered: bool
    reason: str | None = None

    @property
    def token_is_dead(self) -> bool:
        return self.reason in DEAD_TOKEN_REASONS


def is_configured() -> bool:
    settings = get_settings()
    return bool(settings.apns_key_path and settings.apns_key_id and settings.apns_team_id)


def _build_client():
    from aioapns import APNs

    settings = get_settings()
    return APNs(
        key=settings.apns_key_path,
        key_id=settings.apns_key_id,
        team_id=settings.apns_team_id,
        topic=settings.apns_bundle_id,
        use_sandbox=settings.apns_use_sandbox,
    )


async def _send_with(client, token: str, title: str, body: str) -> PushResult:
    from aioapns import NotificationRequest, PushType

    request = NotificationRequest(
        device_token=token,
        message={"aps": {"alert": {"title": title, "body": body}, "sound": "default"}},
        push_type=PushType.ALERT,
    )
    try:
        result = await client.send_notification(request)
        if result.is_successful:
            return PushResult(delivered=True)
        logger.warning("APNs rejected a token: %s", result.description)
        return PushResult(delivered=False, reason=result.description)
    except Exception as exc:
        logger.warning("APNs send failed: %s", exc)
        return PushResult(delivered=False)


async def send_batch(messages: list[tuple[str, str, str]]) -> list[PushResult]:
    """`messages` = [(token, title, body)]. ONE client (one TLS handshake and
    one signed JWT) for the whole batch — per-message clients would cost
    minutes of handshakes at a few hundred devices."""
    if not messages:
        return []
    if not is_configured():
        logger.info("APNs not configured — skipping %d push(es)", len(messages))
        return [PushResult(delivered=False) for _ in messages]

    client = _build_client()
    semaphore = asyncio.Semaphore(20)

    async def one(message: tuple[str, str, str]) -> PushResult:
        async with semaphore:
            return await _send_with(client, *message)

    try:
        return await asyncio.gather(*(one(m) for m in messages))
    finally:
        # The client owns an HTTP/2 connection pool. Cron jobs exit and take
        # it with them, but the report worker runs `--loop` forever and calls
        # this once per finished report — an unclosed pool each time leaks a
        # TLS connection for the life of the container. The pool cannot be
        # kept between calls either: it is bound to the event loop that
        # `send_batch_sync` creates and discards.
        try:
            client.pool.close()
        except Exception as exc:  # never let cleanup fail a delivered push
            logger.warning("APNs pool close failed: %s", exc)


def send_batch_sync(messages: list[tuple[str, str, str]]) -> list[PushResult]:
    """Blocking wrapper for the sync worker/cron code paths."""
    return asyncio.run(send_batch(messages))


def notify_report_ready(user_id: str, locale: str) -> None:
    """Fire-and-forget push for a finished natal report (BRD async mode).
    Silent when the user has no registered device."""
    from app.core.supabase_client import get_service_client

    service = get_service_client()
    devices = (
        service.table("devices").select("id, apns_token, locale")
        .eq("user_id", user_id).execute().data
    )
    if not devices:
        return
    messages = []
    for device in devices:
        title, body = REPORT_READY.get(device.get("locale") or locale, REPORT_READY["en"])
        messages.append((device["apns_token"], title, body))

    for device, result in zip(devices, send_batch_sync(messages), strict=True):
        if result.token_is_dead:
            service.table("devices").delete().eq("id", device["id"]).execute()
