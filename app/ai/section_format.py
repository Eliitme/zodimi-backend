"""Turn a model's sectioned answer into the section contract clients expect.

The natal report gets one call per section, so its headings can simply be
imposed. Synastry cannot: it answers a synchronous request, and five
sequential calls would put a paid endpoint at twenty-five seconds on the
fast model and past a minute on the slow one. So it asks for every section
in one response — which means the headings arrive as the model wrote them,
and have to be normalised here.

Clients split on "\\n## ", so a missing or renamed heading silently merges
two sections into one. Re-imposing our own titles in order removes that
whole class of failure.
"""

import logging
import re

logger = logging.getLogger("stellune.ai.sections")

_HEADING = re.compile(r"^#{1,6}\s*(.+?)\s*$", re.MULTILINE)


def split_sections(text: str) -> list[tuple[str, str]]:
    """(heading, body) pairs. Text before the first heading is discarded —
    it is preamble the prompt asked the model not to write."""
    matches = list(_HEADING.finditer(text))
    out = []
    for index, m in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        out.append((m.group(1), text[m.end():end].strip()))
    return out


def normalise_sections(text: str, titles: list[str]) -> str:
    """Rebuild `text` under exactly `titles`, in order.

    When the model returns the expected number of sections its wording is
    replaced by ours and nothing else changes. When it does not, the text is
    returned as-is rather than dropped: a reader has paid for this, and
    imperfect headings beat an error page. The mismatch is logged because it
    means the prompt needs work.
    """
    sections = split_sections(text)
    if len(sections) != len(titles):
        logger.warning(
            "expected %d sections, model returned %d (%s)",
            len(titles), len(sections), [h for h, _ in sections],
        )
        return text.strip()
    return "\n\n".join(
        f"## {title}\n\n{body}" for title, (_, body) in zip(titles, sections, strict=True)
    ).strip()
