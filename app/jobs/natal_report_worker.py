"""Queue consumer for natal reports: queued → generating → done|failed.

- Claiming goes through the `claim_natal_report` RPC (SKIP LOCKED) so
  concurrent workers never double-process.
- Every section is generated separately; any failure re-queues the row with
  retry_count+1, and the third failure marks it `failed` (the app shows a
  retry UI for that state).
- Assembly guarantees the content contract (every section starts with
  "## ") regardless of what the model returns.

Run one pass:      python -m app.jobs.natal_report_worker
Run as a service:  python -m app.jobs.natal_report_worker --loop
"""

import logging
import sys
import time
from datetime import UTC, date, datetime
from datetime import time as dtime

from app.ai.llm_provider import get_provider
from app.ai.prompt_repository import render
from app.ai.safety_guardrails import system_rules
from app.astro.chart_calculator import calculate_natal_chart
from app.core.config import get_settings
from app.core.supabase_client import get_service_client
from app.jobs.job_heartbeat import record_failure, record_success
from app.push.apns_client import notify_report_ready

logger = logging.getLogger("stellune.jobs.report_worker")

JOB_NAME = "natal_report_worker"
MAX_RETRIES = 3
PROMPT_KEY = "natal_report_section"

# (slug, title_vi, title_en) — ~10 sections ≈ the "20-page" premium report.
REPORT_SECTIONS = [
    ("core_identity", "Bản chất cốt lõi", "Your core identity"),
    ("emotional_world", "Thế giới cảm xúc", "Your emotional world"),
    ("outer_persona", "Hình ảnh bên ngoài", "Your outer persona"),
    ("mind_communication", "Tư duy & giao tiếp", "Mind & communication"),
    ("love_relationships", "Tình yêu & các mối quan hệ", "Love & relationships"),
    ("career_vocation", "Sự nghiệp & thiên hướng", "Career & vocation"),
    ("strengths_gifts", "Điểm mạnh & tài năng", "Strengths & gifts"),
    ("challenges_growth", "Thử thách & bài học trưởng thành", "Challenges & growth"),
    ("life_purpose", "Hành trình & mục đích sống", "Life path & purpose"),
    ("year_ahead", "Năm sắp tới", "The year ahead"),
]


def _chart_summary(profile: dict) -> str:
    """Compact chart facts fed into every section prompt."""
    chart = calculate_natal_chart(
        birth_date=date.fromisoformat(profile["birth_date"]),
        birth_time=dtime.fromisoformat(profile["birth_time"]) if profile["birth_time"] else None,
        birth_time_unknown=profile["birth_time_unknown"],
        lat=profile["lat"],
        lon=profile["lon"],
        tz_of_birth=profile["tz_of_birth"],
    )
    lines = [
        f"{p['name']}: {p['sign']} {p['degree']:.1f} deg, house {p['house']}"
        + (" (retrograde)" if p["is_retrograde"] else "")
        for p in chart["planets"]
    ]
    lines.append(f"ascendant: {chart['angles']['asc']['sign']}")
    lines.append(f"midheaven: {chart['angles']['mc']['sign']}")
    if chart["time_unknown"]:
        lines.append("note: birth time unknown - houses and ascendant are approximate")
    return "\n".join(lines)


def _generate_content(profile: dict, locale: str, heartbeat=None) -> str:
    # Nobody is watching this run, so it takes the report-quality model.
    provider = get_provider(get_settings().openrouter_report_model or None)
    summary = _chart_summary(profile)
    parts = []
    for slug, title_vi, title_en in REPORT_SECTIONS:
        title = title_vi if locale == "vi" else title_en
        prompt = render(
            PROMPT_KEY, locale,
            section_title=title,
            section_slug=slug,
            chart_summary=summary,
            user_name=profile.get("name") or "",
        )
        body = provider.generate(system_rules(locale), prompt).strip()
        # Contract: sections start with "## " — strip any model-provided
        # heading and impose ours so paid sections never shift position.
        if body.startswith("## "):
            body = body.split("\n", 1)[1].strip() if "\n" in body else ""
        parts.append(f"## {title}\n\n{body}")
        if heartbeat:
            heartbeat()
    return "\n\n".join(parts)


def process_one() -> bool:
    """Claims and processes one report. Returns False when queue is empty."""
    service = get_service_client()
    claimed = service.rpc("claim_natal_report", {}).execute().data
    if not claimed:
        return False
    report = claimed[0]

    try:
        profile_rows = (
            service.table("profiles").select("*")
            .eq("user_id", report["user_id"]).limit(1).execute().data
        )
        if not profile_rows or profile_rows[0]["birth_date"] is None:
            raise ValueError("profile incomplete")
        profile = profile_rows[0]

        def heartbeat():
            # Bump the claim timestamp after every section: a slow-but-alive
            # job never trips the 15-minute watchdog TTL (one section is at
            # most ~3 min even with retries), so no duplicate generation.
            service.table("natal_reports").update({
                "generating_started_at": datetime.now(UTC).isoformat(),
            }).eq("id", report["id"]).eq("status", "generating").execute()

        content = _generate_content(profile, profile.get("locale") or "en", heartbeat)
        # status guard: if the watchdog re-queued this row (and possibly
        # another worker finished it), a stale worker must NOT clobber it.
        service.table("natal_reports").update({
            "status": "done",
            "content": content,
            "completed_at": datetime.now(UTC).isoformat(),
        }).eq("id", report["id"]).eq("status", "generating").execute()
        # BRD async mode: tell the user their report is ready instead of
        # making them poll the app. Never fails the job (silent no-op when
        # the user has no device or APNs is unconfigured).
        try:
            notify_report_ready(report["user_id"], profile.get("locale") or "en")
        except Exception as exc:
            logger.warning("report-ready push failed: %s", exc)
        logger.info("report %s done (%d sections)", report["id"], len(REPORT_SECTIONS))
        record_success(JOB_NAME)
        return True
    except Exception as exc:  # any failure re-queues; 3rd strike → failed
        retries = report["retry_count"] + 1
        new_status = "failed" if retries >= MAX_RETRIES else "queued"
        service.table("natal_reports").update({
            "status": new_status,
            "retry_count": retries,
        }).eq("id", report["id"]).eq("status", "generating").execute()
        logger.warning("report %s attempt %d failed → %s: %s",
                       report["id"], retries, new_status, exc)
        if new_status == "failed":
            record_failure(JOB_NAME, f"report {report['id']}: {exc}")
        return True


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    if "--loop" in sys.argv:
        logger.info("worker loop started")
        while True:
            if not process_one():
                time.sleep(3)
    else:
        while process_one():
            pass


if __name__ == "__main__":
    main()
