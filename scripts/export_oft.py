#!/usr/bin/env python3
"""Export an analyze-video spec as an Open Filmmaker Toolkit project."""

import argparse
import json
import math
import os
import sys
from pathlib import Path


def _frame_record(frame: dict, spec_dir: Path, project_dir: Path) -> dict:
    if not isinstance(frame, dict):
        raise ValueError("each frame must be an object")
    path = frame.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError("each frame must have a non-empty path")

    frame_path = Path(path)
    if not frame_path.is_absolute():
        frame_path = spec_dir / frame_path
    if not frame_path.is_file():
        raise ValueError(f"frame file does not exist: {frame_path}")

    record = {
        "relativePath": os.path.relpath(
            frame_path.resolve(), project_dir.resolve()
        ).replace(os.sep, "/")
    }
    timestamp = frame.get("timestamp_formatted")
    if timestamp is not None and not isinstance(timestamp, str):
        raise ValueError("frame timestamp_formatted must be a string")
    if isinstance(timestamp, str) and timestamp:
        record["timecode"] = timestamp
    timestamp_seconds = frame.get("timestamp_seconds")
    if timestamp_seconds is not None and (
        not isinstance(timestamp_seconds, (int, float))
        or isinstance(timestamp_seconds, bool)
        or not math.isfinite(timestamp_seconds)
    ):
        raise ValueError("frame timestamp_seconds must be a finite number")
    if timestamp_seconds is not None:
        record["timestampSeconds"] = timestamp_seconds
    caption = frame.get("caption")
    if caption is not None and not isinstance(caption, str):
        raise ValueError("frame caption must be a string")
    if isinstance(caption, str):
        record["caption"] = caption
    return record


def _analysis_notes(sections: list[dict]) -> str:
    notes = []
    for section in sections:
        heading = section["heading"]
        time_range = section["timeRange"]
        label = heading
        if time_range and time_range != heading:
            label = f"{heading} ({time_range})" if heading else time_range
        lines = [label] if label else []
        if section["body"]:
            lines.append(section["body"])
        for frame in section["frames"]:
            details = []
            if frame.get("timecode"):
                details.append(frame["timecode"])
            if frame.get("caption"):
                details.append(frame["caption"])
            if details:
                lines.append(f"- {' - '.join(details)}")
        if lines:
            notes.append("\n".join(lines))
    return "\n\n".join(notes)


def build_project(spec: dict, spec_dir: Path, output_path: Path) -> dict:
    """Convert a build-docx spec into a schema-compatible project file."""
    videos = spec.get("videos")
    if not isinstance(videos, list) or not videos:
        raise ValueError("spec must contain at least one video")

    assets = []
    for video_index, video in enumerate(videos, start=1):
        if not isinstance(video, dict):
            raise ValueError(f"videos[{video_index - 1}] must be an object")
        title = video.get("title")
        if not isinstance(title, str) or not title.strip():
            title = f"Video {video_index}"
        sections = video.get("sections", [])
        if sections is None:
            sections = []
        if not isinstance(sections, list):
            raise ValueError(f"videos[{video_index - 1}].sections must be an array")

        exported_sections = []
        for section_index, section in enumerate(sections):
            if not isinstance(section, dict):
                raise ValueError(
                    f"videos[{video_index - 1}].sections[{section_index}] "
                    "must be an object"
                )
            heading = section.get("heading", "")
            if not isinstance(heading, str):
                raise ValueError(
                    f"videos[{video_index - 1}].sections[{section_index}].heading "
                    "must be a string"
                )
            body = section.get("body", "")
            if not isinstance(body, str):
                raise ValueError(
                    f"videos[{video_index - 1}].sections[{section_index}].body "
                    "must be a string"
                )
            frames = section.get("frames", [])
            if frames is None:
                frames = []
            if not isinstance(frames, list):
                raise ValueError(
                    f"videos[{video_index - 1}].sections[{section_index}].frames "
                    "must be an array"
                )
            time_range = section.get("time_range", heading)
            if time_range is None or time_range == "":
                time_range = heading
            if not isinstance(time_range, str):
                raise ValueError(
                    f"videos[{video_index - 1}].sections[{section_index}].time_range "
                    "must be a string"
                )
            exported_sections.append(
                {
                    "heading": heading,
                    "timeRange": time_range,
                    "body": body,
                    "frames": [
                        _frame_record(frame, spec_dir, output_path.parent)
                        for frame in frames
                    ],
                }
            )

        asset = {
            "id": f"analysis-video-{video_index}",
            "name": title,
            "type": "video",
            "analysis": {"sections": exported_sections},
        }
        notes = _analysis_notes(exported_sections)
        if notes:
            asset["notes"] = notes
        source = video.get("source")
        if source is not None and not isinstance(source, str):
            raise ValueError(f"videos[{video_index - 1}].source must be a string")
        if isinstance(source, str) and source:
            asset["source"] = source
        assets.append(asset)

    project_title = spec.get("title")
    if not isinstance(project_title, str) or not project_title.strip():
        project_title = assets[0]["name"]

    return {
        "format": "open-filmmaker-toolkit/project",
        "formatVersion": 1,
        "project": {"title": project_title},
        "thirdPartyElements": [],
        "assets": assets,
    }


def export_oft(spec_path: Path, output_path: Path) -> None:
    """Write an OFT project without overwriting an existing file."""
    if output_path.exists():
        raise FileExistsError(f"output already exists: {output_path}")
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    if not isinstance(spec, dict):
        raise ValueError("spec must be a JSON object")
    project = build_project(spec, spec_path.parent, output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("x", encoding="utf-8") as output:
        output.write(json.dumps(project, indent=2, ensure_ascii=False) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", required=True, type=Path, help="Analysis spec JSON")
    parser.add_argument("--out", required=True, type=Path, help="Output .oft.json path")
    args = parser.parse_args()
    try:
        export_oft(args.spec, args.out)
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
