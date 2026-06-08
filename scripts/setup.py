#!/usr/bin/env python3
"""Setup / preflight for /analyze-video.

Modes:
  setup.py --check      Silent preflight. Exit 0 if ready, 2 on required deps failure.
  setup.py --json       Machine-readable status for Claude to parse.
  setup.py              Installer. Auto-installs deps, scaffolds .env, marks SETUP_COMPLETE.

Design:
- Silent on success: --check exits 0 with no output when everything is ready
  so /analyze-video does not spam "setup is complete" on every turn.
- Idempotent: re-running the installer is safe, never clobbers existing keys
  and only appends missing ones.
- SETUP_COMPLETE=true in ~/.config/analyze-video/.env tells us required local
  dependencies have been set up. Whisper keys are optional.
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
# Writable per-user cache that build-docx.js falls back to (and installs into)
# when the skill directory is read-only. setup must check here too, otherwise it
# reports docx as "missing" even when the builder can resolve it fine.
CACHE_NODE_MODULES = Path.home() / ".cache" / "analyze-video" / "node_modules"


def _docx_roots() -> list[Path]:
    """Directories that may contain a resolvable `docx`, mirroring build-docx.js.

    Order matches the builder's resolveDocx(): DOCX_NODE_MODULES env, NODE_PATH
    entries, scripts/node_modules, then the per-user cache. Keeping these in sync
    is what stops setup from disagreeing with the actual builder.
    """
    roots: list[Path] = []
    env_modules = os.environ.get("DOCX_NODE_MODULES")
    if env_modules:
        roots.append(Path(env_modules))
    node_path = os.environ.get("NODE_PATH")
    if node_path:
        roots.extend(Path(p) for p in node_path.split(os.pathsep) if p)
    roots.append(SCRIPTS_DIR / "node_modules")
    roots.append(CACHE_NODE_MODULES)
    return roots


def _docx_available() -> bool:
    """True if the `docx` npm module is resolvable from any known location."""
    for root in _docx_roots():
        pkg = root / "docx" / "package.json"
        try:
            if pkg.exists():
                return True
        except OSError:
            continue
    # Final check mirroring build-docx.js's bare require('docx'): Node's default
    # resolution can find a docx installed in an ancestor node_modules that the
    # explicit roots above don't cover. Best-effort; ignore if node is absent.
    node = shutil.which("node")
    if node:
        try:
            result = subprocess.run(
                [node, "-e", "require.resolve('docx')"],
                capture_output=True,
                text=True,
                timeout=10,
                cwd=str(SCRIPTS_DIR),
            )
            if result.returncode == 0:
                return True
        except (OSError, subprocess.SubprocessError):
            pass
    return False
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
    return _resolve_tool(name)


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


try:
    from env_utils import read_env_key as _shared_read_env_key
except ImportError:
    _shared_read_env_key = None

try:
    from env_utils import resolve_tool as _resolve_tool
except ImportError:  # pragma: no cover
    def _resolve_tool(name: str) -> str | None:
        return shutil.which(name)

try:
    import cache_utils
except ImportError:  # pragma: no cover
    cache_utils = None


def _read_env_key(name: str) -> str | None:
    if _shared_read_env_key is not None:
        # Permission check still runs for the config file
        if CONFIG_FILE.exists():
            _check_file_permissions(CONFIG_FILE)
        return _shared_read_env_key(name)
    # Fallback: inline implementation
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


def _path_export_hint(tool: str) -> str | None:
    """If `tool` resolves only via a user-bin dir not on PATH, return an export hint.

    Auto-installed tools (pip --user / pipx) often land in ~/.local/bin or the
    Python userbase bin, which isn't on PATH in fresh sandboxes. Surfacing the
    exact export line is what keeps the very next bash call from failing.
    """
    resolved = _resolve_tool(tool)
    if not resolved:
        return None
    bindir = Path(resolved).resolve().parent
    path_dirs = []
    for p in os.environ.get("PATH", "").split(os.pathsep):
        if not p:
            continue
        try:
            path_dirs.append(Path(p).expanduser().resolve())
        except OSError:
            continue
    if bindir in path_dirs:
        return None
    return f'export PATH="$PATH:{bindir}"'


def _pip_install_user(pkg: str) -> tuple[bool, str]:
    """pip install --user <pkg>, retrying with --break-system-packages on PEP 668."""
    base = [sys.executable, "-m", "pip", "install", "--user", pkg]
    result = subprocess.run(base, capture_output=True, text=True)
    if result.returncode == 0:
        return True, f"installed {pkg} (pip --user)"
    combined = (result.stdout + result.stderr).lower()
    if "externally-managed" in combined or "break-system-packages" in combined:
        retry = [*base, "--break-system-packages"]
        result2 = subprocess.run(retry, capture_output=True, text=True)
        if result2.returncode == 0:
            return True, f"installed {pkg} (pip --user --break-system-packages)"
        return False, f"pip install {pkg} failed: {result2.stderr.strip()[:200]}"
    return False, f"pip install {pkg} failed: {result.stderr.strip()[:200]}"


def _install_ytdlp() -> tuple[bool, str]:
    """Install yt-dlp without sudo: prefer pipx, fall back to pip --user."""
    if _which("yt-dlp"):
        return True, "already installed"
    if shutil.which("pipx"):
        result = subprocess.run(
            ["pipx", "install", "yt-dlp"], capture_output=True, text=True
        )
        if result.returncode == 0:
            return True, "installed yt-dlp (pipx)"
    return _pip_install_user("yt-dlp")


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


def _npm_install_docx(prefix: Path) -> tuple[bool, str]:
    """Run `npm install docx` under `prefix` (creating it first).

    Installs into `<prefix>/node_modules/docx`. Returns (ok, message).
    """
    try:
        prefix.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return False, f"cannot create {prefix}: {exc}"
    pkg_json = prefix / "package.json"
    if not pkg_json.exists():
        try:
            pkg_json.write_text(
                '{\n  "name": "analyze-video-runtime",\n  "private": true,\n'
                '  "description": "Runtime npm deps for the analyze-video skill builder.",\n'
                '  "dependencies": {}\n}\n'
            )
        except OSError:
            pass
    result = subprocess.run(
        ["npm", "install", "--no-audit", "--no-fund", "--prefix", str(prefix), "docx@^9"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return False, f"npm install docx failed: {result.stderr.strip()[:300]}"
    return True, f"installed into {prefix / 'node_modules'}"


def _install_docx() -> tuple[bool, str]:
    """Install the npm `docx` package so `build-docx.js` can require it.

    Prefers the skill's own `scripts/` dir when it's writable, but falls back to
    the per-user cache (`~/.cache/analyze-video`) when the skill directory is
    mounted read-only. This mirrors build-docx.js's own resolution/install order
    and is what makes setup actually succeed in sandboxes instead of printing a
    deferral note and failing.
    """
    if _docx_available():
        return True, "already installed"
    if shutil.which("npm") is None:
        return False, "npm not available; install Node.js first"

    targets: list[Path] = []
    if os.access(SCRIPTS_DIR, os.W_OK):
        targets.append(SCRIPTS_DIR)
    # CACHE_NODE_MODULES is <cache>/node_modules; install with prefix <cache>.
    targets.append(CACHE_NODE_MODULES.parent)

    last_msg = "no writable install location found"
    for prefix in targets:
        ok, msg = _npm_install_docx(prefix)
        if ok:
            return True, msg
        last_msg = msg
    return False, last_msg


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
    clean = value.strip()
    if "\n" in clean or "\r" in clean:
        sys.stderr.write("key value must not contain newlines\n")
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
            new_lines.append(f"{key_name}={clean}")
            replaced = True
        else:
            new_lines.append(line)
    if not replaced:
        new_lines.append(f"{key_name}={clean}")
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

    docx_installed = _docx_available()

    if not missing and docx_installed and has_key:
        status = "ready"
    elif not missing and docx_installed:
        status = "ready_no_whisper_key"
    elif missing:
        status = "needs_install"
    else:
        status = "needs_docx"

    return {
        "status": status,
        "first_run": is_first_run(),
        "missing_binaries": missing,
        "whisper_backend": backend,
        "has_api_key": has_key,
        "docx_installed": docx_installed,
        "config_file": str(CONFIG_FILE),
        "download_cache_bytes": (
            cache_utils.dir_size(cache_utils.DOWNLOADS_DIR) if cache_utils is not None else 0
        ),
        "platform": platform.system(),
    }


def cmd_check() -> int:
    """Silent-on-success preflight.

    Exit 0 with no output when ready. Missing Whisper keys do not fail preflight:
    the skill can still analyze frames and videos with native captions.
    On required dependency failure, print one actionable line to stderr and
    return 2.
    """
    s = _status()
    if s["status"] in {"ready", "ready_no_whisper_key"}:
        return 0

    parts = []
    if s["missing_binaries"]:
        parts.append(f"missing binaries: {', '.join(s['missing_binaries'])}")
    if not s["docx_installed"]:
        parts.append("missing npm package: docx")
    installer = Path(__file__).resolve()
    sys.stderr.write(
        f"[analyze-video] setup incomplete ({'; '.join(parts)}). "
        f"Run: python3 {installer}\n"
    )
    sys.stderr.flush()

    return 2


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
            # User decision: auto-run the non-sudo install (yt-dlp via pipx/pip
            # --user). Only the packages that genuinely need root (ffmpeg,
            # node/npm) get printed as hints. This removes the macOS/Linux
            # inconsistency where Linux just echoed commands.
            if "yt-dlp" in missing:
                ok, msg = _install_ytdlp()
                print(f"[setup] yt-dlp: {msg}", file=sys.stderr)
                hint = _path_export_hint("yt-dlp")
                if hint:
                    print(
                        f"[setup] yt-dlp is not on PATH yet. Add it with:\n  {hint}",
                        file=sys.stderr,
                    )
            still_missing = _check_binaries()
            if still_missing:
                print(
                    "[setup] some dependencies need a system package manager "
                    "(likely sudo) on Linux:",
                    file=sys.stderr,
                )
                print("  " + _install_hint_linux(still_missing), file=sys.stderr)
                return 2
            installed_deps = True
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
    # the builder self-installs into the per-user cache on first run. But be
    # honest about it so the agent doesn't assume the Word-doc step is wired up.
    docx_ok, docx_msg = _install_docx()
    if docx_ok:
        print(f"[setup] docx npm module: {docx_msg}")
    else:
        print(f"[setup] docx npm module: skipped ({docx_msg})", file=sys.stderr)

    docx_ready = _docx_available()
    if not docx_ready:
        print(
            "[setup] NOTE: the `docx` module isn't installed yet. The Word-document "
            "step will try to install it into "
            f"{CACHE_NODE_MODULES} on first run. If that directory isn't writable, "
            "set NODE_PATH to a directory containing `docx` or run this setup from a "
            "writable checkout. Frames, captions, and transcripts work regardless.",
            file=sys.stderr,
        )

    # If any required tool resolves only via a user-local bin that isn't on PATH,
    # surface the exact export line. setup counts such tools as present (we invoke
    # ffmpeg/ffprobe/yt-dlp by absolute path), but the agent's own bare `node` /
    # `npm` calls and any new shell still need them on PATH.
    path_hints = []
    for binary in REQUIRED_BINARIES:
        hint = _path_export_hint(binary)
        if hint and hint not in path_hints:
            path_hints.append(hint)
    if path_hints:
        print(
            "[setup] NOTE: some tools are installed but not on PATH. "
            "Run before invoking the skill:",
            file=sys.stderr,
        )
        for hint in path_hints:
            print(f"  {hint}", file=sys.stderr)

    if has_key:
        _write_setup_complete()
        backend_note = "" if docx_ready else " (docx pending; see note above)"
        print(f"[setup] ready. whisper backend: {backend}{backend_note}")
        if installed_deps:
            print("[setup] installed dependencies; /analyze-video is fully set up.")
        return 0

    print("")
    _write_setup_complete()
    if docx_ready:
        print("[setup] ready for frames and native captions. Optional: add a Whisper API key.")
    else:
        print(
            "[setup] ready for frames and native captions (docx pending; see note above). "
            "Optional: add a Whisper API key."
        )
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
    return 0


def _fmt_bytes(n: int) -> str:
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024 or unit == "TB":
            return f"{val:.1f} {unit}" if unit != "B" else f"{int(val)} B"
        val /= 1024
    return f"{val:.1f} TB"


def cmd_clear_cache() -> int:
    """Delete all cached source-video downloads. Leaves the docx cache intact."""
    if cache_utils is None:
        print("[setup] cache utilities unavailable", file=sys.stderr)
        return 2
    result = cache_utils.clear_downloads()
    freed = _fmt_bytes(result.get("freed_bytes", 0))
    removed = result.get("removed", 0)
    failed = result.get("failed", 0)
    skipped = result.get("skipped", 0)
    print(f"[setup] cleared download cache: removed {removed} item(s), freed {freed}")
    if skipped:
        print(
            f"[setup] skipped {skipped} item(s) in use by a running analysis",
            file=sys.stderr,
        )
    if failed:
        print(f"[setup] {failed} item(s) could not be removed", file=sys.stderr)
        return 2
    return 0


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
        if arg == "--clear-cache":
            return cmd_clear_cache()
    return cmd_install()


if __name__ == "__main__":
    raise SystemExit(main())
