#!/usr/bin/env python3
"""Shared .env file reader for the analyze-video skill.

Provides a single implementation of .env parsing used by both setup.py and
whisper.py so the logic doesn't drift out of sync.
"""
from __future__ import annotations

import os
import shutil
import site
import sysconfig
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "analyze-video"
CONFIG_FILE = CONFIG_DIR / ".env"


def _user_bin_dirs() -> list[Path]:
    """Common per-user executable dirs that may hold pip/pipx-installed tools.

    `pip install --user` and `pipx` land binaries in locations that are often
    missing from a non-interactive shell's PATH (e.g. ~/.local/bin), which is why
    tools like yt-dlp can be installed yet invisible to a bare name lookup.
    """
    dirs: list[Path] = []
    try:
        userbase = Path(site.getuserbase())
        dirs.append(userbase / "bin")
        # On Windows, `pip install --user` puts executables under Scripts/.
        dirs.append(userbase / "Scripts")
    except Exception:
        pass
    dirs.append(Path.home() / ".local" / "bin")
    dirs.append(Path.home() / "bin")
    if os.name == "nt":
        dirs.extend(_windows_tool_dirs())
    seen: set[Path] = set()
    ordered: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            ordered.append(d)
    return ordered


def _windows_tool_dirs() -> list[Path]:
    """Where Windows installers put tools that a running shell cannot see yet.

    A tool installed a moment ago (by winget, the Node.js installer, pip or the
    Deno installer) is added to PATH only for terminals opened afterwards, so
    these locations are checked directly.
    """
    dirs: list[Path] = []
    try:
        # The real `pip install --user` scripts dir, under the user's AppData folder.
        dirs.append(Path(sysconfig.get_path("scripts", "nt_user")))
    except Exception:
        pass
    local = os.environ.get("LOCALAPPDATA")
    if local:
        dirs.append(Path(local) / "Microsoft" / "WinGet" / "Links")
    for var in ("ProgramFiles", "ProgramW6432"):
        base = os.environ.get(var)
        if base:
            dirs.append(Path(base) / "nodejs")
    dirs.append(Path.home() / ".deno" / "bin")
    return dirs


def _windows_winget_package_tool(name: str) -> str | None:
    """Find `name` inside a winget "portable" package folder.

    Without permission to create links, winget leaves the program in its own
    folder under WinGet/Packages and only edits PATH for future terminals.
    """
    local = os.environ.get("LOCALAPPDATA")
    if not local:
        return None
    packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
    try:
        if not packages.is_dir():
            return None
        for pattern in (f"*/{name}.exe", f"*/*/{name}.exe", f"*/*/bin/{name}.exe"):
            for cand in sorted(packages.glob(pattern)):
                if cand.is_file():
                    return str(cand)
    except OSError:
        return None
    return None


def _candidate_names(name: str) -> list[str]:
    """File names to try for a tool: on Windows, executables carry an extension."""
    if os.name != "nt" or Path(name).suffix:
        return [name]
    return [name + ext for ext in (".exe", ".cmd", ".bat")] + [name]


def resolve_tool(name: str) -> str | None:
    """Find an executable on PATH, falling back to common user-local bin dirs.

    Returns an absolute path string, or None if not found. Using the resolved
    absolute path (rather than the bare name) lets the pipeline run even when a
    user-installed tool's directory isn't on PATH.
    """
    found = shutil.which(name)
    if found:
        try:
            return str(Path(found).resolve())
        except OSError:
            return found
    for d in _user_bin_dirs():
        for candidate_name in _candidate_names(name):
            cand = d / candidate_name
            try:
                if cand.is_file() and os.access(cand, os.X_OK):
                    return str(cand)
            except OSError:
                continue
    if os.name == "nt":
        return _windows_winget_package_tool(name)
    return None


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
