#!/usr/bin/env python3
"""The single place the skill talks to yt-dlp.

Every yt-dlp invocation in the skill builds its command from these helpers and
runs it through :func:`run`, so a change in how yt-dlp is found, configured,
updated or replaced only touches this file.

It also owns the update policy. YouTube changes its player often, and yt-dlp
usually ships a fix within days, so when an extraction fails in a way an update
can plausibly fix, :func:`with_update_retry` updates yt-dlp (stable first, then
nightly) and retries once per channel. Set ``ANALYZE_VIDEO_NO_YTDLP_UPDATE=1``
to turn that off.
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from env_utils import resolve_tool as _resolve_tool
except ImportError:  # pragma: no cover
    def _resolve_tool(name: str) -> str | None:
        return shutil.which(name)


MISSING_MESSAGE = (
    "yt-dlp is not installed. Run scripts/setup.py or install with: "
    "pipx install 'yt-dlp[default]' or pip install --user 'yt-dlp[default]'"
)

LOCAL_FILE_FALLBACK = (
    "If this keeps failing, download the video yourself (browser, app, or any "
    "other tool), then re-run with the local file path instead of the URL."
)

NO_UPDATE_ENV = "ANALYZE_VIDEO_NO_YTDLP_UPDATE"

# yt-dlp versions are dates (YYYY.MM.DD). YouTube breaks extraction every few
# weeks, so a copy older than this is likely missing fixes.
STALE_AFTER_DAYS = 45

UPDATE_TIMEOUT_SECONDS = 300

# Output that points at yt-dlp's own extraction code being out of date. An
# update can plausibly fix these.
UPDATE_TRIGGERS: tuple[str, ...] = (
    "unable to extract",
    "signature extraction failed",
    "nsig extraction failed",
    "n challenge",
    "challenge solving failed",
    "please report this issue",
    "requested format is not available",
    "precondition check failed",
    "only images are available",
    "some formats may be missing",
    "sabr",
    "despite higher-resolution formats",
    "unsupported url",
)

# Failures caused by the video or the account rather than by yt-dlp. Updating
# never fixes these, so it is skipped to save the user a slow, useless retry.
NOT_FIXABLE_BY_UPDATE: tuple[str, ...] = (
    "sign in to confirm",
    "not a bot",
    "members-only",
    "members only",
    "private video",
    "video unavailable",
    "geo-restricted",
    "not available in your country",
    "age-restricted",
    "http error 429",
    "too many requests",
)

_VERSION_RE = re.compile(r"(\d{4})\.(\d{1,2})\.(\d{1,2})")
_version_cache: dict[str, str | None] = {}
_update_attempts: set[str] = set()


# --------------------------------------------------------------------------
# Finding and running yt-dlp
# --------------------------------------------------------------------------

def find() -> str | None:
    """Path to the yt-dlp executable, including user-bin dirs off PATH."""
    return _resolve_tool("yt-dlp")


def require() -> str:
    """Path to yt-dlp, or exit with install instructions."""
    path = find()
    if path is None:
        raise SystemExit(MISSING_MESSAGE)
    return path


def runtime_args() -> list[str]:
    """Select yt-dlp's supported JavaScript runtime and EJS challenge scripts."""
    deno = _resolve_tool("deno")
    if deno:
        return ["--js-runtimes", f"deno:{deno}", "--remote-components", "ejs:github"]
    node = _resolve_tool("node")
    if node:
        return ["--js-runtimes", f"node:{node}", "--remote-components", "ejs:github"]
    return []


def js_runtime_name() -> str | None:
    """'deno', 'node', or None, matching what :func:`runtime_args` would pick."""
    args = runtime_args()
    if not args:
        return None
    return args[1].split(":", 1)[0]


def client_args(player_client: str | None) -> list[str]:
    """Pin a YouTube player client for the whole invocation."""
    if not player_client:
        return []
    return ["--extractor-args", f"youtube:player-client={player_client}"]


def auth_args(cookie_path: Path | str | None, cookies_from_browser: str | None) -> list[str]:
    args: list[str] = []
    if cookie_path:
        args += ["--cookies", str(cookie_path)]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    return args


def run(cmd: list[str], *, timeout: float | None = None) -> subprocess.CompletedProcess:
    """Run a yt-dlp command and capture its output."""
    if timeout is None:
        return subprocess.run(cmd, capture_output=True, text=True)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def echo(result: subprocess.CompletedProcess, *, stdout: bool = True, stderr: bool = True) -> None:
    """Forward captured yt-dlp output to our stderr so the user sees progress."""
    streams = []
    if stdout:
        streams.append(result.stdout)
    if stderr:
        streams.append(result.stderr)
    for text in streams:
        if text:
            print(text, file=sys.stderr, end="" if text.endswith("\n") else "\n")


def output_of(result: subprocess.CompletedProcess | None) -> str:
    if result is None:
        return ""
    return (result.stdout or "") + "\n" + (result.stderr or "")


# --------------------------------------------------------------------------
# Version and staleness
# --------------------------------------------------------------------------

def version(ytdlp: str | None = None, *, refresh: bool = False) -> str | None:
    """yt-dlp's version string (``YYYY.MM.DD`` or a nightly build), or None."""
    path = ytdlp or find()
    if not path:
        return None
    if not refresh and path in _version_cache:
        return _version_cache[path]
    try:
        result = run([path, "--version"], timeout=60)
        value = (result.stdout or "").strip().splitlines()[0].strip() if result.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, IndexError):
        value = None
    _version_cache[path] = value or None
    return _version_cache[path]


def version_from_info(info: dict | None) -> str | None:
    """The yt-dlp version recorded in an info.json / ``-J`` payload."""
    if not isinstance(info, dict):
        return None
    meta = info.get("_version")
    if isinstance(meta, dict) and meta.get("version"):
        return str(meta["version"])
    return None


def version_date(value: str | None) -> _dt.date | None:
    if not value:
        return None
    match = _VERSION_RE.search(value)
    if not match:
        return None
    try:
        return _dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def age_days(value: str | None, *, today: _dt.date | None = None) -> int | None:
    built = version_date(value)
    if built is None:
        return None
    return ((today or _dt.date.today()) - built).days


def staleness_warning(value: str | None, *, today: _dt.date | None = None) -> str | None:
    """A one-line warning when the installed yt-dlp is old enough to matter."""
    days = age_days(value, today=today)
    if days is None or days <= STALE_AFTER_DAYS:
        return None
    return (
        f"yt-dlp {value} is {days} days old; YouTube changes often, so downloads may "
        "fail or come back low quality. Update it with: python3 scripts/setup.py --update-ytdlp"
    )


# --------------------------------------------------------------------------
# Updating
# --------------------------------------------------------------------------

def auto_update_enabled() -> bool:
    return os.environ.get(NO_UPDATE_ENV, "").strip().lower() not in {"1", "true", "yes", "on"}


def update_may_help(output: str) -> bool:
    """True when yt-dlp's output suggests an update could fix the failure."""
    text = (output or "").lower()
    if any(marker in text for marker in NOT_FIXABLE_BY_UPDATE):
        return False
    return any(marker in text for marker in UPDATE_TRIGGERS)


def _script_interpreter(path: Path) -> str | None:
    """The Python interpreter named in a pip-installed console script's shebang."""
    try:
        with path.open("rb") as handle:
            first = handle.readline(512)
    except OSError:
        return None
    if not first.startswith(b"#!"):
        return None
    line = first[2:].decode("utf-8", "replace").strip()
    if "python" not in line:
        return None
    parts = line.split()
    if Path(parts[0]).name == "env" and len(parts) > 1:
        return shutil.which(parts[1]) or parts[1]
    return parts[0]


def install_method(ytdlp: str) -> str:
    """How this yt-dlp was installed: pipx, pip, brew, binary, or unknown."""
    path = Path(ytdlp)
    try:
        resolved = path.resolve()
    except OSError:
        resolved = path
    text = str(resolved).lower()
    if "pipx" in text:
        return "pipx"
    if "cellar" in text or "homebrew" in text or "linuxbrew" in text:
        return "brew"
    if _script_interpreter(resolved):
        return "pip"
    if resolved.is_file():
        return "binary"
    return "unknown"


def update_commands(ytdlp: str, channel: str) -> list[list[str]]:
    """Commands that update this yt-dlp to ``channel`` ('stable' or 'nightly').

    Returns alternatives to try in order; an empty list means this install
    method can't be updated to that channel automatically.
    """
    nightly = channel == "nightly"
    method = install_method(ytdlp)
    if method == "pipx":
        pipx = shutil.which("pipx") or "pipx"
        if nightly:
            return [[pipx, "upgrade", "--pip-args=--pre", "yt-dlp"]]
        return [[pipx, "upgrade", "yt-dlp"]]
    if method == "brew":
        return [] if nightly else [[shutil.which("brew") or "brew", "upgrade", "yt-dlp"]]
    if method == "pip":
        interpreter = _script_interpreter(Path(ytdlp).resolve()) or sys.executable
        base = [interpreter, "-m", "pip", "install", "--upgrade"]
        if nightly:
            base.append("--pre")
        user_base = Path.home() / ".local"
        try:
            is_user = Path(ytdlp).resolve().is_relative_to(user_base.resolve())
        except (OSError, ValueError):
            is_user = False
        if is_user:
            base.append("--user")
        base.append("yt-dlp[default]")
        # PEP 668 systems refuse user installs without this flag.
        return [base, [*base, "--break-system-packages"]] if is_user else [base]
    if method == "binary":
        return [[ytdlp, "--update-to", "nightly" if nightly else "stable"]]
    return []


def try_update(channel: str = "stable") -> tuple[bool, str]:
    """Update yt-dlp once per process per channel. Returns (changed, message).

    ``changed`` is True only when the installed version is different afterward,
    so callers never retry against the same copy that just failed.
    """
    if not auto_update_enabled():
        return False, f"automatic yt-dlp updates are off ({NO_UPDATE_ENV} is set)"
    if channel in _update_attempts:
        return False, f"already tried updating yt-dlp to {channel} in this run"
    _update_attempts.add(channel)
    ytdlp = find()
    if not ytdlp:
        return False, "yt-dlp is not installed"
    commands = update_commands(ytdlp, channel)
    if not commands:
        return False, f"can't update this yt-dlp install ({install_method(ytdlp)}) to {channel} automatically"
    before = version(ytdlp)
    print(f"[yt-dlp] updating yt-dlp {before or '(unknown version)'} to the latest {channel} build...", file=sys.stderr)
    last_error = ""
    for cmd in commands:
        try:
            result = run(cmd, timeout=UPDATE_TIMEOUT_SECONDS)
        except (OSError, subprocess.SubprocessError) as exc:
            last_error = str(exc)
            continue
        if result.returncode == 0:
            break
        last_error = (result.stderr or result.stdout or "").strip()[-300:]
    after = version(find() or ytdlp, refresh=True)
    if after and after != before:
        message = f"updated yt-dlp {before or '?'} -> {after}"
        print(f"[yt-dlp] {message}; retrying", file=sys.stderr)
        return True, message
    message = f"yt-dlp is still {after or before or 'unknown'} after trying {channel}"
    if last_error:
        message += f" ({last_error})"
    print(f"[yt-dlp] {message}", file=sys.stderr)
    return False, message


T = TypeVar("T")


def with_update_retry(
    attempt: Callable[[], T],
    *,
    failed: Callable[[T], bool],
    output: Callable[[T], str],
) -> T:
    """Run ``attempt``; on a failure an update could fix, update and retry.

    Tries the latest stable yt-dlp first, then nightly, re-running ``attempt``
    after each update that actually changed the installed version.
    """
    outcome = attempt()
    for channel in ("stable", "nightly"):
        if not failed(outcome) or not update_may_help(output(outcome)):
            return outcome
        changed, _ = try_update(channel)
        if changed:
            outcome = attempt()
    return outcome


def _reset_for_tests() -> None:
    _version_cache.clear()
    _update_attempts.clear()
