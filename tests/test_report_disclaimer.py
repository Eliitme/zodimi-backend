"""FR11 requires the entertainment disclaimer on every user-facing output.

The report was the one path that never got it: tarot and synastry call
append_disclaimer, the worker did not, and a 17k-character reading about
career and relationships is the last place to omit it. It is applied on read
rather than at generation because the free tier truncates the tail away.
"""

import app.api.reports_router as reports
from app.ai.safety_guardrails import DISCLAIMER

FULL = "\n\n".join(f"## Phần {i}\n\nNội dung {i}." for i in range(1, 11))


def test_premium_reader_gets_the_disclaimer():
    row = reports._with_disclaimer({"content": FULL, "status": "done"}, "vi")
    assert row["content"].endswith(DISCLAIMER["vi"])


def test_free_reader_gets_it_too_after_truncation():
    # The gate cuts to the first sections; appending at generation time would
    # have put the disclaimer in the part that gets cut.
    gated = {"content": "\n\n".join(FULL.split("\n\n")[:6]), "status": "done"}
    assert reports._with_disclaimer(gated, "vi")["content"].endswith(DISCLAIMER["vi"])


def test_not_applied_twice():
    once = reports._with_disclaimer({"content": FULL, "status": "done"}, "vi")
    twice = reports._with_disclaimer(once, "vi")
    assert twice["content"].count(DISCLAIMER["vi"]) == 1


def test_pending_report_without_content_is_untouched():
    row = {"content": None, "status": "generating"}
    assert reports._with_disclaimer(row, "vi") == row


def test_every_response_path_goes_through_the_same_presenter(monkeypatch):
    """POST used to return the stored row raw while both GETs wrapped it, so
    a reader who tapped "create" on an already-finished report got the full
    text with no disclaimer. Routing all four returns through `_presented` is
    what makes that impossible to reintroduce on one path only."""
    monkeypatch.setattr(reports, "is_premium", lambda _user_id: True)
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": "done",
        "content": FULL,
        "requested_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:05:00Z",
    }
    assert reports._presented(row, "user-a", "vi", 1).content.endswith(DISCLAIMER["vi"])


def test_presenter_gates_before_it_appends(monkeypatch):
    """Order matters: the disclaimer has to land after truncation, otherwise
    the free tier loses exactly the notice it most needs."""
    monkeypatch.setattr(reports, "is_premium", lambda _user_id: False)
    row = {
        "id": "11111111-1111-1111-1111-111111111111",
        "status": "done",
        "content": FULL,
        "requested_at": "2026-01-01T00:00:00Z",
        "completed_at": "2026-01-01T00:05:00Z",
    }
    presented = reports._presented(row, "user-a", "vi", 1)
    assert presented.content.endswith(DISCLAIMER["vi"])
    assert presented.content.count("## Phần") == reports.FREE_SECTION_COUNT
    assert presented.locked_section_titles == [f"Phần {i}" for i in range(4, 11)]
