"""Read optional local OpenAI settings without executing the .env file."""

from __future__ import annotations

import os
import shlex
from pathlib import Path


LOCAL_ENV = Path(__file__).resolve().parent / ".env"
ALLOWED_SETTINGS = frozenset({"OPENAI_API_KEY", "OPENAI_MODEL"})


def project_setting(name: str, default: str | None = None) -> str | None:
    """Prefer the shell environment, then an ignored project-local .env file."""
    if name not in ALLOWED_SETTINGS:
        raise ValueError(f"Unsupported setting: {name}")

    shell_value = os.environ.get(name)
    if shell_value:
        return shell_value
    if not LOCAL_ENV.is_file():
        return default

    for raw_line in LOCAL_ENV.read_text(encoding="utf-8-sig").splitlines():
        try:
            tokens = shlex.split(raw_line, comments=True)
        except ValueError:
            continue
        if len(tokens) != 1 or not tokens[0].startswith(f"{name}="):
            continue
        return tokens[0].split("=", 1)[1] or default
    return default
