from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: Path = Path(".env")) -> dict[str, str]:
    """Load a simple `.env` file into process environment variables.

    Beacon only needs straightforward `KEY=value` lines right now, so this
    small parser avoids adding a dependency just to read local secrets. Existing
    OS environment variables win over file values, which lets scheduled runs or
    CI override local settings without editing `.env`.
    """

    loaded_values: dict[str, str] = {}
    if not path.exists():
        return loaded_values

    for raw_line in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip().lstrip("\ufeff")
        value = _clean_env_value(value)
        if not key:
            continue

        loaded_values[key] = value
        os.environ.setdefault(key, value)

    return loaded_values


def _clean_env_value(value: str) -> str:
    """Trim whitespace and matching quotes around a `.env` value."""

    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value
