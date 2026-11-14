#!/usr/bin/env python3
"""Quality linter for analyze-video spec.json content."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


TIMECODE_RE = re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?\b")


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"could not read spec JSON: {exc}")


def lint_spec(spec: dict) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []

    videos = spec.get("videos") or []
    if not videos:
        errors.append("spec.videos must be non-empty")
        return errors, warnings

    for vi, video in enumerate(videos):
        sections = video.get("sections") or []
        if not sections:
            errors.append(f"videos[{vi}] has no sections")
            continue
        if not any((section.get("frames") or []) for section in sections):
            errors.append(f"videos[{vi}] has no frames in any section")
        for si, section in enumerate(sections):
            heading = str(section.get("heading") or "")
            body = str(section.get("body") or "").strip()
            frames = section.get("frames") or []
            # Summary and closing sections (a lens's "Key points", "What to
            # borrow", "Assessment") cover the whole video and carry no frames.
            text_only = section.get("text_only") is True
            if not heading:
                errors.append(f"videos[{vi}].sections[{si}] missing heading")
            elif not text_only and TIMECODE_RE.search(heading) is None:
                warnings.append(f"videos[{vi}].sections[{si}] heading has no timestamp")
            if len(body) < 40:
                warnings.append(f"videos[{vi}].sections[{si}] body is very short")
            if not frames and not text_only:
                errors.append(
                    f"videos[{vi}].sections[{si}] has no frames "
                    '(set "text_only": true for a summary or closing section)'
                )
            for fi, frame in enumerate(frames):
                caption = str((frame or {}).get("caption") or "").strip()
                if len(caption) < 8:
                    warnings.append(
                        f"videos[{vi}].sections[{si}].frames[{fi}] caption is very short"
                    )
    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="lint_spec_quality",
        description="Lint content quality and structure in analyze-video spec JSON.",
    )
    ap.add_argument("--spec", required=True, help="Absolute path to spec.json")
    args = ap.parse_args()
    spec = _load(Path(args.spec))
    errors, warnings = lint_spec(spec)
    for w in warnings:
        print(f"[spec-lint] warning: {w}")
    if errors:
        for e in errors:
            print(f"[spec-lint] error: {e}")
        return 1
    print(f"[spec-lint] ok: {len(warnings)} warning(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
