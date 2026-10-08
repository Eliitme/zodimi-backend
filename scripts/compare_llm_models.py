"""Generate the same report sections through several OpenRouter models.

Which model to ship is a judgement about Vietnamese prose — warmth, natural
phrasing, whether it reads translated — and that cannot be settled from a
benchmark. This runs the REAL production path (same prompt templates, same
safety rules, same section contract) so what you read is what users get.

Output goes to one markdown file per model plus a side-by-side index, with
elapsed time and length per model. Token counts and spend per call land in
the OpenRouter dashboard, which is also where per-model pricing lives.

    export OPENROUTER_API_KEY=...
    uv run python scripts/compare_llm_models.py
    uv run python scripts/compare_llm_models.py --models anthropic/claude-sonnet-4.5,openai/gpt-5
    uv run python scripts/compare_llm_models.py --sections core_identity,love_relationships

Costs real money: every model × every section is one API call.
"""

import argparse
import sys
import time
from datetime import date
from datetime import time as dtime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.ai.llm_provider import LLMUnavailableError, OpenRouterProvider  # noqa: E402
from app.ai.prompt_repository import render  # noqa: E402
from app.ai.safety_guardrails import system_rules  # noqa: E402
from app.astro.chart_calculator import calculate_natal_chart  # noqa: E402
from app.core.config import get_settings  # noqa: E402
from app.jobs.natal_report_worker import PROMPT_KEY, REPORT_SECTIONS  # noqa: E402

DEFAULT_MODELS = [
    "google/gemini-2.5-flash",
    "anthropic/claude-sonnet-4.5",
    "openai/gpt-5-mini",
    "qwen/qwen3-max",
]

# One real chart, so every model describes the same person and the prose can
# be compared directly rather than across different subjects.
SAMPLE_BIRTH = {
    "birth_date": "2000-07-22",
    "birth_time": "17:30",
    "birth_time_unknown": False,
    "lat": 19.8,
    "lon": 105.7767,
    "tz_of_birth": "Asia/Ho_Chi_Minh",
    "name": "",
}


def chart_summary() -> str:
    chart = calculate_natal_chart(
        birth_date=date.fromisoformat(SAMPLE_BIRTH["birth_date"]),
        birth_time=dtime.fromisoformat(SAMPLE_BIRTH["birth_time"]),
        birth_time_unknown=False,
        lat=SAMPLE_BIRTH["lat"],
        lon=SAMPLE_BIRTH["lon"],
        tz_of_birth=SAMPLE_BIRTH["tz_of_birth"],
    )
    lines = [
        f"{p['name']}: {p['sign']} {p['degree']:.1f} deg, house {p['house']}"
        + (" (retrograde)" if p["is_retrograde"] else "")
        for p in chart["planets"]
    ]
    lines.append(f"ascendant: {chart['angles']['asc']['sign']}")
    lines.append(f"midheaven: {chart['angles']['mc']['sign']}")
    return "\n".join(lines)


def run_model(model: str, sections, summary: str, locale: str, out_dir: Path) -> dict:
    provider = OpenRouterProvider(get_settings().openrouter_api_key, model)
    parts, started = [], time.monotonic()

    for slug, title_vi, title_en in sections:
        title = title_vi if locale == "vi" else title_en
        prompt = render(
            PROMPT_KEY, locale,
            section_title=title, section_slug=slug,
            chart_summary=summary, user_name="",
        )
        try:
            body = provider.generate(system_rules(locale), prompt).strip()
        except LLMUnavailableError as exc:
            body = f"_[thất bại: {exc}]_"
        # Same heading normalisation the worker applies, so the comparison
        # reflects shipped output rather than raw model output.
        if body.startswith("## "):
            body = body.split("\n", 1)[1].strip() if "\n" in body else ""
        parts.append(f"## {title}\n\n{body}")
        print(f"    {slug}: {len(body)} ký tự")

    elapsed = time.monotonic() - started
    content = "\n\n".join(parts)
    path = out_dir / f"{model.replace('/', '__')}.md"
    path.write_text(f"# {model}\n\n_{elapsed:.1f}s · {len(content)} ký tự_\n\n{content}\n")
    return {"model": model, "elapsed": elapsed, "chars": len(content), "path": path}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(DEFAULT_MODELS))
    parser.add_argument(
        "--sections", default="core_identity,love_relationships",
        help="section slugs, or 'all' for the full ten-section report",
    )
    parser.add_argument("--locale", default="vi")
    parser.add_argument("--out", default="/tmp/zodimi-llm-compare")
    args = parser.parse_args()

    if not get_settings().openrouter_api_key:
        print("OPENROUTER_API_KEY chưa đặt — không có gì để so sánh.", file=sys.stderr)
        return 1

    wanted = args.sections.strip()
    sections = (
        REPORT_SECTIONS if wanted == "all"
        else [s for s in REPORT_SECTIONS if s[0] in wanted.split(",")]
    )
    if not sections:
        print(f"không khớp section nào: {wanted}", file=sys.stderr)
        return 1

    models = [m.strip() for m in args.models.split(",") if m.strip()]
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = chart_summary()

    print(f"{len(models)} model × {len(sections)} section = {len(models) * len(sections)} lời gọi\n")
    results = []
    for model in models:
        print(f"  {model}")
        try:
            results.append(run_model(model, sections, summary, args.locale, out_dir))
        except Exception as exc:  # a bad model slug must not kill the run
            print(f"    bỏ qua: {exc}")

    index = ["# So sánh model", "", "| Model | Thời gian | Độ dài | File |", "|---|---|---|---|"]
    index += [
        f"| {r['model']} | {r['elapsed']:.1f}s | {r['chars']} | `{r['path'].name}` |"
        for r in results
    ]
    (out_dir / "index.md").write_text("\n".join(index) + "\n")

    print(f"\nĐọc kết quả: {out_dir}")
    for r in results:
        print(f"  {r['elapsed']:6.1f}s  {r['chars']:6d} ký tự  {r['model']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
