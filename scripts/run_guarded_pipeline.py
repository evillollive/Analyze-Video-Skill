#!/usr/bin/env python3
"""Guarded entrypoint for /analyze-video.

Runs preflight on the active host, executes processing, enforces frame-count
intent, and can validate/lint/build a docx spec in one command sequence.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
PROCESS = SCRIPT_DIR / "process.py"
SETUP = SCRIPT_DIR / "setup.py"
SELECT = SCRIPT_DIR / "select_frames.py"
VALIDATE_SPEC = SCRIPT_DIR / "validate_spec_paths.py"
LINT_SPEC = SCRIPT_DIR / "lint_spec_quality.py"
BUILD_DOCX = SCRIPT_DIR / "build-docx.js"


def _run(cmd: list[str], *, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.stdout:
        print(result.stdout, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, end="" if result.stderr.endswith("\n") else "\n", file=sys.stderr)
    if check and result.returncode != 0:
        raise SystemExit(result.returncode)
    return result


def _write_selection_proof(out_dir: Path, frame_count: int, selected: list[dict]) -> Path:
    proof = out_dir / "selection_proof.json"
    payload = {
        "written_at": time.time(),
        "frame_count": frame_count,
        "selected_paths": [s.get("absolute_path") for s in selected if s.get("absolute_path")],
    }
    proof.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return proof


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="run_guarded_pipeline",
        description="Guarded wrapper around setup/process/select/build for analyze-video.",
    )
    ap.add_argument("--source", required=True, help="Video URL or local file path")
    ap.add_argument("--out-dir", required=True, help="Per-video output directory")
    ap.add_argument("--source-url", default=None, help="Original URL when --source is local")
    ap.add_argument("--frames", type=int, default=None, help="Frames to select after processing")
    ap.add_argument(
        "--auto-frame-consent",
        action="store_true",
        help="Explicitly allow automatic frame-count choice when --frames is omitted.",
    )
    ap.add_argument("--spec", default=None, help="Optional spec.json to validate/lint/build")
    ap.add_argument("--runner-timeout-seconds", type=int, default=None)
    ap.add_argument("--expected-duration-minutes", type=float, default=None)
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--start", default=None)
    ap.add_argument("--end", default=None)
    args = ap.parse_args()

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.frames is None and not args.auto_frame_consent:
        raise SystemExit(
            "frame count missing. Pass --frames <N> or explicitly allow auto choice with "
            "--auto-frame-consent."
        )

    _run([sys.executable, str(SETUP), "--check"], check=True)

    process_cmd = [
        sys.executable,
        str(PROCESS),
        "--source",
        args.source,
        "--out-dir",
        str(out_dir),
    ]
    if args.source_url:
        process_cmd += ["--source-url", args.source_url]
    if args.runner_timeout_seconds:
        process_cmd += ["--runner-timeout-seconds", str(args.runner_timeout_seconds)]
    if args.expected_duration_minutes is not None:
        process_cmd += ["--expected-duration-minutes", str(args.expected_duration_minutes)]
    if args.quick:
        process_cmd.append("--quick")
    if args.start:
        process_cmd += ["--start", args.start]
    if args.end:
        process_cmd += ["--end", args.end]
    _run(process_cmd, check=True)

    frame_count = args.frames
    if frame_count is None:
        frame_count = 20
        print("[guarded] frame count delegated; defaulting to 20", file=sys.stderr)

    lite = out_dir / "manifest_lite.json"
    select = _run(
        [sys.executable, str(SELECT), str(lite), str(frame_count)],
        check=True,
    )
    selected = json.loads(select.stdout or "[]")
    proof = _write_selection_proof(out_dir, frame_count, selected)
    print(f"[guarded] wrote selection proof: {proof}")

    if args.spec:
        spec = str(Path(args.spec).expanduser().resolve())
        _run([sys.executable, str(VALIDATE_SPEC), "--spec", spec], check=True)
        _run([sys.executable, str(LINT_SPEC), "--spec", spec], check=True)
        _run(["node", str(BUILD_DOCX), "--spec", spec], check=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
