#!/usr/bin/env python3
"""Validate build-docx spec file paths before invoking build-docx.js.

Checks that referenced frame/contact-sheet/transcript paths are absolute and
exist on disk. This prevents silent drift when a spec is built from stale
context rather than the current manifest/select_frames output.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def _load_spec(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"could not read spec JSON: {exc}")


def _iter_references(spec: dict) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    videos = spec.get("videos") or []
    for vi, video in enumerate(videos):
        sections = video.get("sections") or []
        for si, section in enumerate(sections):
            frames = section.get("frames") or []
            for fi, frame in enumerate(frames):
                path = frame.get("path")
                if path:
                    refs.append((f"videos[{vi}].sections[{si}].frames[{fi}].path", str(path)))

    for ci, sheet in enumerate(spec.get("appendix_contact_sheets") or []):
        path = sheet.get("path")
        if path:
            refs.append((f"appendix_contact_sheets[{ci}].path", str(path)))

    for ti, transcript in enumerate(spec.get("appendix_transcript") or []):
        path = transcript.get("path")
        if path:
            refs.append((f"appendix_transcript[{ti}].path", str(path)))

    return refs


def validate_spec_paths(spec_path: Path) -> int:
    spec = _load_spec(spec_path)
    refs = _iter_references(spec)
    issues: list[str] = []

    for label, raw_path in refs:
        p = Path(raw_path).expanduser()
        if not p.is_absolute():
            issues.append(f"{label}: path is not absolute: {raw_path}")
            continue
        if not p.exists():
            issues.append(f"{label}: file not found: {raw_path}")
            continue
        if not p.is_file():
            issues.append(f"{label}: not a file: {raw_path}")

    if issues:
        print("[validate-spec] path validation failed:", file=sys.stderr)
        for issue in issues:
            print(f"  - {issue}", file=sys.stderr)
        return 1

    print(f"[validate-spec] ok: checked {len(refs)} path(s)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="validate_spec_paths",
        description="Validate referenced file paths in an analyze-video spec JSON.",
    )
    ap.add_argument("--spec", required=True, help="Absolute path to spec.json")
    args = ap.parse_args()
    return validate_spec_paths(Path(args.spec))


if __name__ == "__main__":
    raise SystemExit(main())
