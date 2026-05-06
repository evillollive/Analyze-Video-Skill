#!/usr/bin/env python3
"""Setup / preflight for /analyze-video.

Modes:
  setup.py --check      Silent preflight. Exit 0 if ready, 2/3/4 on failure.
  setup.py --json       Machine-readable status for Claude to parse.
  setup.py              Installer. Auto-installs deps, scaffolds .env, marks SETUP_COMPLETE.

Design:
- Silent on success: --check exits 0 with no output when everything is ready
  so /analyze-video does not spam "setup is complete" on every turn.
- Idempotent: re-running the installer is safe, never clobbers existing keys
  and only appends missing ones.
- SETUP_COMPLETE=true in ~/.config/analyze-video/.env tells us the user has
  been through a successful installer run at least once.
- Never sudo. On macOS, auto-install via brew. Elsewhere, print exact commands.
- Never write an API key to disk automatically, only scaffold placeholders.
"""
from __future__ import annotations

import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path


REQUIRED_BINARIES = ["ffmpeg", "ffprobe", "yt-dlp", "node", "npm"]
CONFIG_DIR = Path.home() / ".config" / "analyze-video"
CONFIG_FILE = CONFIG_DIR / ".env"
SCRIPTS_DIR = Path(__file__).resolve().parent
DOCX_NODE_MODULES = SCRIPTS_DIR / "node_modules" / "docx"
ENV_TEMPLATE = """# /analyze-video API configuration
#
# Whisper transcription fallback, used only when yt-dlp cannot get captions
# (or when you point /analyze-video at a local file with no subtitles).
#
# Groq is preferred: it runs whisper-large-v3 at a fraction of OpenAI's price
# and is faster in practice. OpenAI is the compatible fallback.
#
# Get a Groq key:    https://console.groq.com/keys
# Get an OpenAI key: https://platform.openai.com/api-keys
#
# Leave both blank to disable Whisper. /analyze-video will still work, but
# videos without native captions will come back frames-only.

GROQ_API_KEY=
OPENAI_API_KEY=
"""


def _which(name: str) -> str | None:
    return shutil.which(name)


def _check_binaries() -> list[str]:
    return [b for b in REQUIRED_BINARIES if not _which(b)]


def _check_file_permissions(path: Path) -> None:
    """Warn to stderr if a secrets file is world/group readable."""
    try:
        mode = path.stat().st_mode
        if mode & 0o044:
            sys.stderr.write(
                f"[analyze-video] WARNING: {path} is readable by other users. "
                f"Run: chmod 600 {path}\n"
            )
            sys.stderr.flush()
    except OSError:
        pass


def _read_env_key(name: str) -> str | None:
    value = os.environ.get(name)
    if value and value.strip():
        return value.strip()
    if not CONFIG_FILE.exists():
        return None
    _check_file_permissions(CONFIG_FILE)
    try:
        for line in CONFIG_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, raw = line.partition("=")
            if key.strip() != name:
                continue
            raw = raw.strip()
            if len(raw) >= 2 and raw[0] in ('"', "'") and raw[-1] == raw[0]:
                raw = raw[1:-1]
            return raw or None
    except OSError:
        return None
    return None


def _have_api_key() -> tuple[bool, str | None]:
    if _read_env_key("GROQ_API_KEY"):
        return True, "groq"
    if _read_env_key("OPENAI_API_KEY"):
        return True, "openai"
    return False, None


def is_first_run() -> bool:
    """True if the installer has not completed successfully yet."""
    return _read_env_key("SETUP_COMPLETE") != "true"


def _scaffold_env() -> bool:
    """Create ~/.config/analyze-video/.env with placeholders if missing."""
    if CONFIG_FILE.exists():
        return False
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(ENV_TEMPLATE)
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass
    return True


def _write_setup_complete() -> None:
    """Idempotently append SETUP_COMPLETE=true to .env.

    Used only after a fully successful install (deps + key). Future sessions
    detect this marker to skip wizard-style UI and stay silent.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    existing = ""
    if CONFIG_FILE.exists():
        existing = CONFIG_FILE.read_text()
        for line in existing.splitlines():
            if line.strip().startswith("SETUP_COMPLETE="):
                return
        if existing and not existing.endswith("\n"):
            existing += "\n"
        CONFIG_FILE.write_text(existing + "SETUP_COMPLETE=true\n")
    else:
        CONFIG_FILE.write_text(ENV_TEMPLATE + "\nSETUP_COMPLETE=true\n")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass


def _brew_pkg(missing: list[str]) -> list[str]:
    pkgs: list[str] = []
    for bin_name in missing:
        if bin_name in ("ffmpeg", "ffprobe"):
            if "ffmpeg" not in pkgs:
                pkgs.append("ffmpeg")
        elif bin_name == "yt-dlp":
            if "yt-dlp" not in pkgs:
                pkgs.append("yt-dlp")
        elif bin_name in ("node", "npm"):
            if "node" not in pkgs:
                pkgs.append("node")
        else:
            pkgs.append(bin_name)
    return pkgs


def _install_macos(missing: list[str]) -> tuple[bool, str]:
    if _which("brew") is None:
        return False, (
            "Homebrew is not installed. Install it from https://brew.sh, then re-run setup. "
            "Or install manually: `brew install " + " ".join(_brew_pkg(missing)) + "`"
        )
    pkgs = _brew_pkg(missing)
    if not pkgs:
        return True, "nothing to install"
    cmd = ["brew", "install", *pkgs]
    print(f"[setup] running: {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd)
    if result.returncode != 0:
        return False, f"brew install failed with exit code {result.returncode}"
    return True, f"installed via brew: {', '.join(pkgs)}"


def _install_hint_linux(missing: list[str]) -> str:
    pkgs = _brew_pkg(missing)
    hints = []
    if "ffmpeg" in pkgs:
        hints.append("apt: `sudo apt install ffmpeg` or dnf: `sudo dnf install ffmpeg`")
    if "yt-dlp" in pkgs:
        hints.append("`pipx install yt-dlp` (recommended) or `pip install --user yt-dlp`")
    if "node" in pkgs:
        hints.append("apt: `sudo apt install nodejs npm` or use nvm (https://github.com/nvm-sh/nvm)")
    return "\n  ".join(hints) if hints else "nothing to install"


def _install_hint_windows(missing: list[str]) -> str:
    pkgs = _brew_pkg(missing)
    hints = []
    if "ffmpeg" in pkgs:
        hints.append("winget: `winget install Gyan.FFmpeg`")
    if "yt-dlp" in pkgs:
        hints.append("winget: `winget install yt-dlp.yt-dlp` or pip: `pip install --user yt-dlp`")
    if "node" in pkgs:
        hints.append("winget: `winget install OpenJS.NodeJS`")
    return "\n  ".join(hints) if hints else "nothing to install"


def _install_docx() -> tuple[bool, str]:
    """Install the npm `docx` package once into scripts/node_modules.

    The build script (`scripts/build-docx.js`) requires('docx'), and Node
    resolves modules from any `node_modules/` next to the script. Installing
    once here avoids the per-session `npm init && npm install` dance.
    """
    if DOCX_NODE_MODULES.exists():
        return True, "already installed"
    if shutil.which("npm") is None:
        return False, "npm not available; install Node.js first"
    SCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
    pkg_json = SCRIPTS_DIR / "package.json"
    if not pkg_json.exists():
        pkg_json.write_text(
            '{\n  "name": "analyze-video-runtime",\n  "private": true,\n'
            '  "description": "Runtime npm deps for the analyze-video skill builder.",\n'
            '  "dependencies": {}\n}\n'
        )
    print("[setup] installing npm `docx` into scripts/node_modules ...", file=sys.stderr)
    result = subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund", "--prefix", str(SCRIPTS_DIR), "docx"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, f"npm install docx failed: {result.stderr.strip()[:300]}"
    return True, "installed"


def _set_key(backend: str, value: str) -> int:
    """Write/replace a single API key in the .env file at mode 0600."""
    backend = backend.lower()
    key_name = {"groq": "GROQ_API_KEY", "openai": "OPENAI_API_KEY"}.get(backend)
    if not key_name:
        sys.stderr.write(f"unknown backend: {backend} (expected 'groq' or 'openai')\n")
        return 2
    if not value or not value.strip():
        sys.stderr.write("empty key value\n")
        return 2

    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_FILE.exists():
        CONFIG_FILE.write_text(ENV_TEMPLATE)

    lines = CONFIG_FILE.read_text().splitlines()
    new_lines: list[str] = []
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key_name}=") or stripped.startswith(f"{key_name} ="):
            new_lines.append(f"{key_name}={value.strip()}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{key_name}={value.strip()}")
    CONFIG_FILE.write_text("\n".join(new_lines) + "\n")
    try:
        CONFIG_FILE.chmod(0o600)
    except OSError:
        pass
    print(f"[setup] wrote {key_name} to {CONFIG_FILE}", file=sys.stderr)
    return 0


def _status() -> dict:
    """Structured preflight snapshot."""
    missing = _check_binaries()
    has_key, backend = _have_api_key()

    if not missing and has_key:
        status = "ready"
    elif missing and not has_key:
        status = "needs_install_and_key"
    elif missing:
        status = "needs_install"
    else:
        status = "needs_key"

    return {
        "status": status,
        "first_run": is_first_run(),
        "missing_binaries": missing,
        "whisper_backend": backend,
        "has_api_key": has_key,
        "docx_installed": DOCX_NODE_MODULES.exists(),
        "config_file": str(CONFIG_FILE),
        "platform": platform.system(),
    }


def cmd_check() -> int:
    """Silent-on-success preflight.

    Exit 0 with no output when ready. On failure, print one actionable line
    to stderr and return:
      2 -> binaries missing
      3 -> API key missing
      4 -> both missing
    """
    s = _status()
    if s["status"] == "ready":
        return 0

    parts = []
    if s["missing_binaries"]:
        parts.append(f"missing binaries: {', '.join(s['missing_binaries'])}")
    if not s["has_api_key"]:
        parts.append("no Whisper API key (GROQ_API_KEY or OPENAI_API_KEY)")
    installer = Path(__file__).resolve()
    sys.stderr.write(
        f"[analyze-video] setup incomplete ({'; '.join(parts)}). "
        f"Run: python3 {installer}\n"
    )
    sys.stderr.flush()

    if s["missing_binaries"] and not s["has_api_key"]:
        return 4
    if s["missing_binaries"]:
        return 2
    return 3


def cmd_json() -> int:
    json.dump(_status(), sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_install() -> int:
    missing = _check_binaries()
    installed_deps = False
    if missing:
        system = platform.system()
        if system == "Darwin":
            ok, msg = _install_macos(missing)
            print(f"[setup] {msg}", file=sys.stderr)
            if not ok:
                return 2
            still_missing = _check_binaries()
            if still_missing:
                print(
                    f"[setup] still missing after install: {', '.join(still_missing)}",
                    file=sys.stderr,
                )
                return 2
            installed_deps = True
        elif system == "Linux":
            print("[setup] dependencies missing on Linux, please install:", file=sys.stderr)
            print("  " + _install_hint_linux(missing), file=sys.stderr)
            return 2
        elif system == "Windows":
            print("[setup] dependencies missing on Windows, please install:", file=sys.stderr)
            print("  " + _install_hint_windows(missing), file=sys.stderr)
            return 2
        else:
            print(
                f"[setup] unsupported platform ({system}) for auto-install. Install manually:",
                file=sys.stderr,
            )
            print(f"  missing: {', '.join(missing)}", file=sys.stderr)
            return 2

    created = _scaffold_env()
    if created:
        print(f"[setup] created config: {CONFIG_FILE}")
    else:
        print(f"[setup] config exists: {CONFIG_FILE}")

    has_key, backend = _have_api_key()

    # Always try to install the docx node module too. Failure is non-fatal:
    # the per-session fallback in SKILL.md still works.
    docx_ok, docx_msg = _install_docx()
    if docx_ok:
        print(f"[setup] docx npm module: {docx_msg}")
    else:
        print(f"[setup] docx npm module: skipped ({docx_msg})", file=sys.stderr)

    if has_key:
        _write_setup_complete()
        print(f"[setup] ready. whisper backend: {backend}")
        if installed_deps:
            print("[setup] installed dependencies; /analyze-video is fully set up.")
        return 0

    print("")
    print("[setup] one step left: add a Whisper API key.")
    print("")
    print("  Easiest: re-run with the key, e.g.")
    print(f"    python3 {Path(__file__).resolve()} --set-key groq sk-...")
    print("")
    print("  Or edit the file directly:")
    print(f"    {CONFIG_FILE}")
    print("    GROQ_API_KEY=...    (preferred, cheaper, faster; get one at console.groq.com/keys)")
    print("    OPENAI_API_KEY=...  (fallback; get one at platform.openai.com/api-keys)")
    print("")
    print("  Without a key, /analyze-video still works but videos without")
    print("  captions come back frames-only.")
    return 3


def main() -> int:
    if len(sys.argv) > 1:
        arg = sys.argv[1]
        if arg == "--check":
            return cmd_check()
        if arg == "--json":
            return cmd_json()
        if arg == "--set-key":
            if len(sys.argv) < 4:
                sys.stderr.write("usage: setup.py --set-key <groq|openai> <KEY>\n")
                return 2
            rc = _set_key(sys.argv[2], sys.argv[3])
            if rc == 0:
                # Check if this completes setup; mark complete if so.
                if not _check_binaries() and _have_api_key()[0]:
                    _write_setup_complete()
            return rc
        if arg == "--install-docx":
            ok, msg = _install_docx()
            print(f"[setup] {msg}", file=sys.stderr)
            return 0 if ok else 2
    return cmd_install()


if __name__ == "__main__":
    raise SystemExit(main())
