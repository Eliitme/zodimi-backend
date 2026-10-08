"""A reading belongs to the chart it was written for.

The bug this closes: `GET /v1/synastry/{id}` recomputed the inter-aspects
from the caller's CURRENT profile and returned them beside interpretation
text written for the old one. Edit your birth time and the aspects move while
the prose stays, so the page shows evidence that contradicts the reading
printed above it. Natal reports had the same gap, unseen because nothing
rendered their inputs.
"""

import app.api.reports_router as reports

ROW = {
    "id": "11111111-1111-1111-1111-111111111111",
    "status": "done",
    "content": "## Phần 1\ntext",
    "requested_at": "2026-01-01T00:00:00Z",
    "completed_at": "2026-01-01T00:05:00Z",
}


def test_a_report_written_from_the_current_chart_is_not_stale():
    assert reports._is_stale({**ROW, "profile_revision": 4}, 4) is False


def test_a_report_written_before_a_birth_data_edit_is_stale():
    # The revision only moves when the chart inputs move, so a difference
    # here means the prose describes a chart the account no longer has.
    assert reports._is_stale({**ROW, "profile_revision": 3}, 4) is True


def test_an_unknown_revision_is_stale_rather_than_assumed_current():
    # Rows written before reports carried a revision cannot prove they match,
    # and "unknown" presented as "current" is the exact claim that must not
    # be made. Same for a profile that has since been cleared.
    assert reports._is_stale({**ROW, "profile_revision": None}, 4) is True
    assert reports._is_stale({**ROW}, 4) is True
    assert reports._is_stale({**ROW, "profile_revision": 4}, None) is True


def test_the_response_carries_the_flag(monkeypatch):
    monkeypatch.setattr(reports, "is_premium", lambda _user_id: True)
    fresh = reports._presented({**ROW, "profile_revision": 2}, "user-a", "vi", 2)
    edited = reports._presented({**ROW, "profile_revision": 1}, "user-a", "vi", 2)
    assert fresh.is_stale is False
    assert edited.is_stale is True
    # Staleness must not disturb what the reader is shown; it is a label on
    # the same content, not a second gate on it.
    assert fresh.content == edited.content
