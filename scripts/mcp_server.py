"""Minimal MCP server exposing analyze-video's scripts as tools.

Each tool shells out to the existing CLI so behavior matches the skill exactly.
Requires the optional `mcp` package (v1 API): pip install "mcp<2"
Run: python3 scripts/mcp_server.py   (stdio transport)
"""

import json
import subprocess
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
IMAGE_SUFFIXES = {".jpg": "jpeg", ".jpeg": "jpeg", ".png": "png"}


def _run(script: str, args: list, timeout: int = 3600) -> dict:
    cmd = [sys.executable, str(SCRIPTS / script), *args]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"{script} timed out after {timeout}s"}
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip()[-4000:],
    }


def prepare_args(source: str, out_dir=None, quick=False, max_frames=None, start=None, end=None) -> list:
    args = ["--source", source]
    if out_dir:
        args += ["--out-dir", out_dir]
    if quick:
        args.append("--quick")
    if max_frames:
        args += ["--max-frames", str(max_frames)]
    if start:
        args += ["--start", start]
    if end:
        args += ["--end", end]
    return args


def captions_args(source: str, out_dir: str, langs=None, formats=None, list_only=False) -> list:
    args = ["--source", source, "--out-dir", out_dir]
    if langs:
        args += ["--langs", langs]
    if formats:
        args += ["--format", formats]
    if list_only:
        args.append("--list")
    return args


def analyze_prepare(source, out_dir=None, quick=False, max_frames=None, start=None, end=None) -> dict:
    result = _run("process.py", prepare_args(source, out_dir, quick, max_frames, start, end))
    if result["ok"] and result["stdout"]:
        manifest = Path(result["stdout"].splitlines()[-1])
        result["manifest_lite"] = str(manifest)
        if manifest.is_file():
            try:
                result["manifest"] = json.loads(manifest.read_text())
            except ValueError:
                pass
    return result


def get_captions(source, out_dir, langs=None, formats=None, list_only=False) -> dict:
    return _run("captions.py", captions_args(source, out_dir, langs, formats, list_only), timeout=600)


def validate_spec(spec_path: str) -> dict:
    paths = _run("validate_spec_paths.py", ["--spec", spec_path], timeout=120)
    quality = _run("lint_spec_quality.py", ["--spec", spec_path], timeout=120)
    return {"ok": paths["ok"] and quality["ok"], "paths": paths, "quality": quality}


def read_image(path: str) -> tuple:
    p = Path(path)
    fmt = IMAGE_SUFFIXES.get(p.suffix.lower())
    if not p.is_absolute() or not p.is_file() or fmt is None:
        raise ValueError(f"Expected an absolute path to an existing .jpg/.png file: {path}")
    return p.read_bytes(), fmt


def build_server():
    try:
        from mcp.server.fastmcp import FastMCP, Image
    except ImportError as exc:
        raise SystemExit("The MCP server needs the 'mcp' package (v1 API): pip install 'mcp<2'") from exc

    server = FastMCP("analyze-video")

    @server.tool()
    def prepare(
        source: str,
        out_dir: str = "",
        quick: bool = False,
        max_frames: int = 0,
        start: str = "",
        end: str = "",
    ) -> dict:
        """Download a video (URL or local path), extract frames, transcript and contact sheets.
        Returns the manifest (paths to frames and contact sheets) for the next steps."""
        return analyze_prepare(source, out_dir or None, quick, max_frames or None, start or None, end or None)

    @server.tool()
    def captions(source: str, out_dir: str, langs: str = "", formats: str = "", list_only: bool = False) -> dict:
        """Download captions (srt, vtt, txt) for a video URL without building a report."""
        return get_captions(source, out_dir, langs or None, formats or None, list_only)

    @server.tool()
    def validate(spec_path: str) -> dict:
        """Validate a docx spec's file paths and alt-text quality before building."""
        return validate_spec(spec_path)

    @server.tool()
    def get_image(path: str) -> Image:
        """Return a frame or contact sheet (absolute .jpg/.png path from the manifest) as an image."""
        data, fmt = read_image(path)
        return Image(data=data, format=fmt)

    return server


if __name__ == "__main__":
    build_server().run()
