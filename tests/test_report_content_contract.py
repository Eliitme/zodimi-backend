"""Worker content assembly must honour the iOS section contract:
content starts with '## ', exactly one heading per section, and headings
are OURS (model-provided headings are stripped) so paid sections never
shift into the free preview.
"""


import pytest

import app.jobs.natal_report_worker as worker


@pytest.fixture(autouse=True)
def offline_prompts(monkeypatch):
    monkeypatch.setattr(
        worker, "render",
        lambda key, locale, **kw: f"section {kw['section_slug']} for chart",
    )


PROFILE = {
    "name": "Test",
    "birth_date": "1955-02-24",
    "birth_time": "19:15:00",
    "birth_time_unknown": False,
    "lat": 37.7749,
    "lon": -122.4194,
    "tz_of_birth": "America/Los_Angeles",
    "locale": "en",
}


def test_content_starts_with_heading_and_has_all_sections():
    content = worker._generate_content(PROFILE, "en")
    assert content.startswith("## "), "content must begin with a section heading"
    assert content.count("\n## ") + 1 == len(worker.REPORT_SECTIONS)
    for _, _, title_en in worker.REPORT_SECTIONS:
        assert f"## {title_en}" in content


def test_model_provided_heading_is_stripped(monkeypatch):
    # FakeProvider outputs '## Sample heading\n\n...' — assembly must strip
    # it and impose the canonical section title instead.
    content = worker._generate_content(PROFILE, "vi")
    assert "## Sample heading" not in content
    for _, title_vi, _ in worker.REPORT_SECTIONS:
        assert f"## {title_vi}" in content


def test_ten_sections_defined():
    assert len(worker.REPORT_SECTIONS) == 10
    slugs = [s for s, _, _ in worker.REPORT_SECTIONS]
    assert len(set(slugs)) == 10
