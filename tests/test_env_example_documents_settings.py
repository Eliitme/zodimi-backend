"""`.env.example` is the only place a deployer learns what to configure.

A setting added to Settings but not to the example ships invisible: the
default silently applies in production and the symptom surfaces somewhere
far away. That already happened once with the OpenRouter keys, which reached
the example but not a working .env, leaving the app on the deterministic
fake while looking configured.

The real .env is gitignored, so this can only check the example — which is
the file that has to be right for anyone deploying.
"""

import re
from pathlib import Path

from app.core.config import Settings

EXAMPLE = Path(__file__).parent.parent / ".env.example"

# Settings fields that are deliberately absent from the example.
NOT_DEPLOYER_FACING: set[str] = set()


def documented_keys() -> set[str]:
    return {
        m.group(1)
        for line in EXAMPLE.read_text().splitlines()
        if (m := re.match(r"^([A-Z][A-Z0-9_]*)=", line.strip()))
    }


def test_every_setting_appears_in_env_example():
    expected = {f.upper() for f in Settings.model_fields} - NOT_DEPLOYER_FACING
    missing = expected - documented_keys()
    assert not missing, f"settings missing from .env.example: {sorted(missing)}"


def test_env_example_has_no_unknown_keys():
    # Catches a renamed setting whose old name lingers in the example and
    # would be set by a deployer to no effect.
    known = {f.upper() for f in Settings.model_fields}
    unknown = documented_keys() - known
    assert not unknown, f".env.example documents unknown keys: {sorted(unknown)}"


def test_example_ships_no_real_secrets():
    # Placeholder values only: this file is committed, and to a public repo.
    for line in EXAMPLE.read_text().splitlines():
        match = re.match(r"^([A-Z][A-Z0-9_]*)=(.*)$", line.strip())
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if not any(s in key for s in ("KEY", "SECRET", "TOKEN", "PASSWORD")):
            continue
        assert not value, f"{key} carries a value in .env.example"
