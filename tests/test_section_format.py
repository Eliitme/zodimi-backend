"""Clients split sectioned readings on "\\n## ", so heading fidelity is a
contract, not cosmetics: a renamed or missing heading silently merges two
sections into one on the screen.

Synastry asks for all its sections in a single call (it answers a
synchronous request), so unlike the natal report it cannot impose headings
per call — normalise_sections is what closes that gap.
"""

from app.ai.section_format import normalise_sections, split_sections

TITLES = ["Tổng quan", "Sức hút & hoà hợp", "Giao tiếp & thấu hiểu"]


def body_of(text: str, title: str) -> str:
    return dict((h, b) for h, b in split_sections(text))[title]


class TestSplit:
    def test_pairs_headings_with_their_bodies(self):
        out = split_sections("## Một\n\nA.\n\n## Hai\n\nB.")
        assert out == [("Một", "A."), ("Hai", "B.")]

    def test_preamble_before_the_first_heading_is_dropped(self):
        # The prompt forbids a lead-in; if one arrives it must not become the
        # first section's body.
        out = split_sections("Chào bạn!\n\n## Một\n\nA.")
        assert out == [("Một", "A.")]

    def test_no_headings_yields_nothing(self):
        assert split_sections("Chỉ là một đoạn văn.") == []


class TestNormalise:
    def test_model_headings_are_replaced_by_ours(self):
        raw = "## Khái quát\n\nA.\n\n## Hoá học\n\nB.\n\n## Nói chuyện\n\nC."
        out = normalise_sections(raw, TITLES)
        assert [h for h, _ in split_sections(out)] == TITLES

    def test_bodies_survive_replacement_in_order(self):
        raw = "## X\n\nA.\n\n## Y\n\nB.\n\n## Z\n\nC."
        out = normalise_sections(raw, TITLES)
        assert body_of(out, "Tổng quan") == "A."
        assert body_of(out, "Giao tiếp & thấu hiểu") == "C."

    def test_output_starts_with_a_heading(self):
        # iOS and web both assume content begins with "## ".
        raw = "## X\n\nA.\n\n## Y\n\nB.\n\n## Z\n\nC."
        assert normalise_sections(raw, TITLES).startswith("## ")

    def test_heading_depth_is_normalised(self):
        # A model that answers in ### must not produce sections the client
        # cannot see.
        raw = "### X\n\nA.\n\n### Y\n\nB.\n\n### Z\n\nC."
        assert normalise_sections(raw, TITLES).count("\n## ") == len(TITLES) - 1

    def test_wrong_section_count_returns_the_text_untouched(self):
        # A paid reader gets imperfect headings rather than an error; the
        # mismatch is logged so the prompt can be fixed.
        raw = "## Chỉ một phần\n\nA."
        assert normalise_sections(raw, TITLES) == raw

    def test_unsectioned_answer_is_still_returned(self):
        raw = "Một đoạn văn không có tiêu đề nào."
        assert normalise_sections(raw, TITLES) == raw

    def test_multi_paragraph_bodies_are_preserved(self):
        raw = "## X\n\nĐoạn một.\n\nĐoạn hai.\n\n## Y\n\nB.\n\n## Z\n\nC."
        assert body_of(normalise_sections(raw, TITLES), "Tổng quan") == "Đoạn một.\n\nĐoạn hai."
