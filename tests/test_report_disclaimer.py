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
