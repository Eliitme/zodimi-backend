"""Seeds `prompt_templates` from prompts/templates.local.json (private,
gitignored) — falls back to templates.example.json so local dev works out
of the box. Existing (key, locale, version) rows are upserted; the seeded
version is marked active.

Run: uv run python scripts/seed_prompt_templates.py
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.supabase_client import get_service_client  # noqa: E402

PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def main() -> None:
    source = PROMPTS_DIR / "templates.local.json"
    if not source.exists():
        source = PROMPTS_DIR / "templates.example.json"
        print("NOTE: using EXAMPLE templates — create templates.local.json for production prompts")

    templates = json.loads(source.read_text())["templates"]
    service = get_service_client()
    for entry in templates:
        service.table("prompt_templates").upsert({
            "key": entry["key"],
            "locale": entry["locale"],
            "version": entry["version"],
            "template": entry["template"],
            "is_active": True,
        }).execute()
    print(f"seeded {len(templates)} templates from {source.name}")


if __name__ == "__main__":
    main()
