#!/usr/bin/env python3
"""/analyze-video pipeline (one video).

Downloads the video, extracts auto-scaled frames, fetches a transcript
(captions first, Whisper API as fallback), and tiles all frames into a
single contact sheet.

Writes everything to --out-dir, including:
  - download/video.<ext>      : the source video
  - frames/frame_NNNN.jpg     : extracted frames (chronological)
  - contact_sheet.jpg         : tiled overview, one image, row-major
  - audio.mp3                 : extracted audio (only if Whisper was used)
  - manifest.json             : structured pipeline output for the skill
  - report.md                 : human-readable summary

The skill's SKILL.md handles multi-video orchestration, frame selection,
analysis prose, and the .docx build. This script is one-video-at-a-time.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from download import download, is_url  # noqa: E402
from frames import (  # noqa: E402
    MAX_FPS,
    auto_fps,
    auto_fps_focus,
    extract,
    format_time,
    get_metadata,
    make_contact_sheet,
    parse_time,
)
from transcribe import filter_range, format_transcript, parse_vtt  # noqa: E402
from whisper import load_api_key, transcribe_video  # noqa: E402


def _aspect_ratio_label(width: int | None, height: int | None) -> str | None:
    if not width or not height or height == 0:
        return None
    ratio = width / height
    if 1.7 <= ratio <= 1.85:
        return "16:9"
    if 1.3 <= ratio <= 1.4:
        return "4:3"
    if 0.5 <= ratio <= 0.6:
        return "9:16"
    if 1.0 <= ratio <= 1.05:
        return "1:1"
    return f"{round(ratio, 2)}:1"


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="analyze-video",
        description=(
            "Process one video for the analyze-video skill: download, frames, "
            "transcript, contact sheet, and a structured manifest."
        ),
    )
    ap.add_argument("--source", required=True, help="Video URL or local file path")
    ap.add_argument(
        "--out-dir",
        required=True,
        help="Output directory (typically a per-video subfolder of session outputs)",
    )
    ap.add_argument("--max-frames", type=int, default=80, help="Cap on frame count (default 80, hard max 100)")
    ap.add_argument("--resolution", type=int, default=512, help="Frame width in pixels (default 512)")
    ap.add_argument("--fps", type=float, default=None, help="Override auto-fps (clamped to 2 fps)")
    ap.add_argument("--start", type=str, default=None, help="Range start (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument("--end", type=str, default=None, help="Range end (SS, MM:SS, or HH:MM:SS)")
    ap.add_argument(
        "--no-whisper",
        action="store_true",
        help="Disable Whisper fallback. Frames-only if no captions.",
    )
    ap.add_argument(
        "--whisper",
        choices=["groq", "openai"],
        default=None,
        help="Force a specific Whisper backend. Default: prefer Groq, fall back to OpenAI.",
    )
    ap.add_argument(
        "--no-contact-sheet",
        action="store_true",
        help="Skip contact sheet generation (saves a few seconds)",
    )
    ap.add_argument(
        "--contact-sheet-cols",
        type=int,
        default=8,
        help="Tiles per row in the contact sheet (default 8)",
    )
    ap.add_argument(
        "--contact-sheet-tile-width",
        type=int,
        default=200,
        help="Width in px for each tile in the contact sheet (default 200)",
    )
    args = ap.parse_args()

    max_frames = min(args.max_frames, 100)

    work = Path(args.out_dir).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    print(f"[analyze-video] working dir: {work}", file=sys.stderr)

    print(
        "[analyze-video] downloading via yt-dlp..."
        if is_url(args.source)
        else "[analyze-video] using local file...",
        file=sys.stderr,
    )
    dl = download(args.source, work / "download")
    video_path = dl["video_path"]

    meta = get_metadata(video_path)
    full_duration = meta["duration_seconds"]

    start_sec = parse_time(args.start)
    end_sec = parse_time(args.end)

    if start_sec is not None and start_sec < 0:
        raise SystemExit("--start must be non-negative")
    if end_sec is not None and start_sec is not None and end_sec <= start_sec:
        raise SystemExit("--end must be greater than --start")
    if full_duration > 0 and start_sec is not None and start_sec >= full_duration:
        raise SystemExit(f"--start {start_sec:.1f}s is past end of video ({full_duration:.1f}s)")

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)
    focused = start_sec is not None or end_sec is not None

    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=max_frames)
    else:
        fps, target = auto_fps(effective_duration, max_frames=max_frames)
    if args.fps is not None:
        fps = min(args.fps, MAX_FPS)
        target = max(1, int(round(fps * effective_duration)))

    scope = (
        f"{format_time(effective_start)}-{format_time(effective_end)} ({effective_duration:.1f}s)"
        if focused
        else f"full {effective_duration:.1f}s"
    )
    print(
        f"[analyze-video] extracting ~{target} frames at {fps:.3f} fps over {scope}...",
        file=sys.stderr,
    )

    frames = extract(
        video_path,
        work / "frames",
        fps=fps,
        resolution=args.resolution,
        max_frames=max_frames,
        start_seconds=start_sec,
        end_seconds=end_sec,
    )

    # Transcript: try captions first, then Whisper as fallback
    transcript_segments: list[dict] = []
    transcript_text: str | None = None
    transcript_source: str | None = None
    if dl.get("subtitle_path"):
        try:
            all_segments = parse_vtt(dl["subtitle_path"])
            transcript_segments = (
                filter_range(all_segments, start_sec, end_sec) if focused else all_segments
            )
            transcript_text = format_transcript(transcript_segments)
            transcript_source = "captions"
        except Exception as exc:
            print(f"[analyze-video] subtitle parse failed: {exc}", file=sys.stderr)

    if not transcript_segments and not args.no_whisper:
        backend, api_key = load_api_key(args.whisper)
        if backend and api_key:
            try:
                all_segments, used_backend = transcribe_video(
                    video_path,
                    work / "audio.mp3",
                    backend=backend,
                    api_key=api_key,
                )
                transcript_segments = (
                    filter_range(all_segments, start_sec, end_sec) if focused else all_segments
                )
                transcript_text = format_transcript(transcript_segments)
                transcript_source = f"whisper ({used_backend})"
            except SystemExit as exc:
                print(f"[analyze-video] whisper fallback failed: {exc}", file=sys.stderr)
        else:
            hint = (
                f"--whisper {args.whisper} was set but the matching API key is missing"
                if args.whisper
                else "no subtitles and no Whisper API key found"
            )
            setup_py = SCRIPT_DIR / "setup.py"
            print(
                f"[analyze-video] {hint}, run `python3 {setup_py}` to enable the "
                "Whisper fallback",
                file=sys.stderr,
            )

    # Contact sheet
    contact_sheet_path: Path | None = None
    if not args.no_contact_sheet and frames:
        print(
            f"[analyze-video] building contact sheet ({len(frames)} tiles, "
            f"{args.contact_sheet_cols} cols, {args.contact_sheet_tile_width}px wide)...",
            file=sys.stderr,
        )
        contact_sheet_path = make_contact_sheet(
            work / "frames",
            work / "contact_sheet.jpg",
            cols=args.contact_sheet_cols,
            tile_width=args.contact_sheet_tile_width,
        )

    info = dl.get("info") or {}
    aspect = _aspect_ratio_label(meta.get("width"), meta.get("height"))
    long_video_warning = (not focused) and full_duration > 600

    # Build manifest (structured output the skill consumes)
    manifest = {
        "schema_version": 1,
        "source": args.source,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "url": info.get("url"),
        "duration_seconds": round(full_duration, 2),
        "duration_formatted": format_time(full_duration),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "aspect_ratio": aspect,
        "codec": meta.get("codec"),
        "has_audio": meta.get("has_audio"),
        "focus_range": (
            {
                "start_seconds": round(effective_start, 2),
                "end_seconds": round(effective_end, 2),
                "start_formatted": format_time(effective_start),
                "end_formatted": format_time(effective_end),
            }
            if focused
            else None
        ),
        "extraction": {
            "fps": round(fps, 3),
            "target_frames": target,
            "frame_count": len(frames),
            "frame_resolution_width": args.resolution,
            "max_frames_cap": max_frames,
        },
        "frames": [
            {
                "index": f["index"],
                "timestamp_seconds": f["timestamp_seconds"],
                "timestamp_formatted": format_time(f["timestamp_seconds"]),
                "path": str(Path(f["path"]).relative_to(work)),
                "absolute_path": f["path"],
            }
            for f in frames
        ],
        "transcript": {
            "source": transcript_source,
            "segment_count": len(transcript_segments),
            "segments": transcript_segments,
            "formatted": transcript_text,
        },
        "contact_sheet": {
            "path": (
                str(contact_sheet_path.relative_to(work))
                if contact_sheet_path
                else None
            ),
            "absolute_path": str(contact_sheet_path) if contact_sheet_path else None,
            "layout": {
                "cols": args.contact_sheet_cols,
                "tile_width_px": args.contact_sheet_tile_width,
                "order": "row-major chronological",
            } if contact_sheet_path else None,
        },
        "long_video_warning": long_video_warning,
        "out_dir": str(work),
    }

    manifest_path = work / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    # Human-readable report (kept compact; the manifest is the structured copy)
    report_lines: list[str] = []
    report_lines.append("# analyze-video: pipeline report")
    report_lines.append("")
    report_lines.append(f"- **Source:** {args.source}")
    if info.get("title"):
        report_lines.append(f"- **Title:** {info['title']}")
    if info.get("uploader"):
        report_lines.append(f"- **Uploader:** {info['uploader']}")
    report_lines.append(
        f"- **Duration:** {format_time(full_duration)} ({full_duration:.1f}s)"
    )
    if focused:
        report_lines.append(
            f"- **Focus range:** {format_time(effective_start)} to "
            f"{format_time(effective_end)} ({effective_duration:.1f}s)"
        )
    if meta.get("width") and meta.get("height"):
        report_lines.append(
            f"- **Resolution:** {meta['width']}x{meta['height']} "
            f"({meta.get('codec') or 'unknown codec'}{', ' + aspect if aspect else ''})"
        )
    mode = "focused" if focused else "full"
    report_lines.append(
        f"- **Frames:** {len(frames)} @ {fps:.3f} fps, {mode} mode "
        f"(budget {target}, max {max_frames})"
    )
    report_lines.append(f"- **Frame size:** {args.resolution}px wide")
    if transcript_segments:
        in_range = " in range" if focused else ""
        report_lines.append(
            f"- **Transcript:** {len(transcript_segments)} segments{in_range} "
            f"(via {transcript_source or 'captions'})"
        )
    else:
        report_lines.append("- **Transcript:** none available")
    if contact_sheet_path:
        report_lines.append(f"- **Contact sheet:** `{contact_sheet_path.name}`")
    report_lines.append(f"- **Manifest:** `{manifest_path.name}`")
    report_lines.append("")

    if long_video_warning:
        mins = int(full_duration // 60)
        report_lines.append(
            f"> **Warning:** This is a {mins}-minute video. Frame coverage is "
            "sparse at this length. Accuracy degrades on anything over 10 minutes. "
            "For better results, re-run with `--start HH:MM:SS --end HH:MM:SS` to "
            "zoom into a specific section."
        )
        report_lines.append("")

    if not transcript_segments:
        setup_py = SCRIPT_DIR / "setup.py"
        report_lines.append(
            "_No transcript available. Captions were missing and the Whisper "
            "fallback was unavailable (no API key set, or `--no-whisper` was used). "
            f"Run `python3 {setup_py}` to enable Whisper, then re-run._"
        )
        report_lines.append("")

    (work / "report.md").write_text("\n".join(report_lines))

    # Final stdout: just the manifest path so the skill can find it
    print(str(manifest_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
