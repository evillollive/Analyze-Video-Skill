#!/usr/bin/env python3
"""Shared .env file reader for the analyze-video skill.

Provides a single implementation of .env parsing used by both setup.py and
whisper.py so the logic doesn't drift out of sync.
"""
from __future__ import annotations

import os
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "analyze-video"
CONFIG_FILE = CONFIG_DIR / ".env"


def read_dotenv_key(path: Path, name: str) -> str | None:
    """Read a single key from a .env-style file. Returns None if not found."""
    if not path.exists():
        return None
    try:
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != name:
                continue
            value = value.strip()
            if len(value) >= 2 and value[0] in ('"', "'") and value[-1] == value[0]:
                value = value[1:-1]
            return value or None
    except OSError:
        return None
    return None


def read_env_key(name: str) -> str | None:
    """Read a key from the environment, then fall back to .env files.

    Checks (in order): environment variable, CONFIG_FILE, cwd/.env.
    """
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    for candidate in [CONFIG_FILE, Path.cwd() / ".env"]:
        result = read_dotenv_key(candidate, name)
        if result:
            return result
    return None
