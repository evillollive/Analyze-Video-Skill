#!/usr/bin/env python3
"""/analyze-video pipeline (one video, possibly chunked).

Downloads the video, fetches a transcript (captions first, Whisper API as
fallback), and processes the video in one or more chunks. Each chunk gets its
own frame extraction, contact sheet, and transcript slice.

Auto-chunking activates for unfocused videos longer than 12 minutes (split
into 10-minute chunks with 5-second overlap). User-specified --start/--end
ranges bypass chunking. Short videos process as a single chunk.

Writes everything under --out-dir, including:
  - download/video.<ext>                     : the source video
  - audio.mp3                                : extracted audio (only if Whisper used)
  - chunks/chunk_N/frames/frame_NNNN.jpg     : extracted frames per chunk
  - chunks/chunk_N/contact_sheet.jpg         : tiled overview per chunk
  - manifest.json                            : structured pipeline output (schema_version 3)
  - transcript.txt                           : human-readable transcript (only if one exists)
  - report.md                                : human-readable summary

The skill's SKILL.md handles multi-video orchestration, frame selection,
analysis prose, and the .docx build. This script is one-video-at-a-time.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent.resolve()
sys.path.insert(0, str(SCRIPT_DIR))

from download import download, is_url  # noqa: E402
import cache_utils  # noqa: E402
from frames import (  # noqa: E402
    HARD_MAX_FRAMES,
    MAX_FPS,
    auto_fps,
    auto_fps_focus,
    auto_tile_width,
    compute_chunks,
    extract,
    format_time,
    get_metadata,
    make_contact_sheet,
    parse_time,
    should_chunk,
)
from transcribe import detect_trailing_promo, filter_range, parse_vtt  # noqa: E402
from whisper import load_all_api_keys, load_api_key, transcribe_video  # noqa: E402


# Soft-warn the skill when chunking produces this many or more contact sheets,
# because the preview cost (one Read per chunk) becomes substantial.
PREVIEW_COST_WARNING_CHUNKS = 5

# Shared, per-user cache for downloaded source videos. Keying by URL means the
# full download happens once and any later run (including a focused --start/--end
# rerun in a different out-dir) reuses it instead of re-downloading. We always
# fetch the whole video, so full_duration and absolute timestamps stay correct.
# cache_utils owns the canonical location and the eviction/clear logic.
DOWNLOAD_CACHE_DIR = cache_utils.DOWNLOADS_DIR


def _download_dir(source: str, work: Path, *, no_cache: bool) -> Path:
    """Where the source should be downloaded/resolved.

    For URLs (unless --no-download-cache), this is a stable per-URL folder in the
    shared cache so reruns reuse the download. For local files the path is
    irrelevant (resolve_local ignores it), and --no-download-cache forces the
    per-run out-dir for callers who want self-contained output.
    """
    if no_cache or not is_url(source):
        return work / "download"
    key = hashlib.sha256(source.encode("utf-8")).hexdigest()[:16]
    return DOWNLOAD_CACHE_DIR / key


def _write_status(work: Path, stage: str, **extra) -> None:
    """Write a small status.json so a run killed mid-pipeline still shows where
    it got to (download / transcribe / extracting chunk i of N / complete)."""
    try:
        (work / "status.json").write_text(
            json.dumps({"stage": stage, "updated_at": time.time(), **extra}, indent=2)
        )
    except OSError:
        pass


def _write_partial_manifest(
    work: Path,
    *,
    info: dict,
    full_duration: float,
    chunk_count: int,
    processed_chunks: list[dict],
    transcript_source: str | None,
    segment_count: int,
) -> None:
    """Persist a partial manifest as chunks complete, so a timeout leaves usable
    progress on disk instead of nothing."""
    try:
        (work / "manifest_partial.json").write_text(
            json.dumps(
                {
                    "schema_version": 3,
                    "partial": True,
                    "status": "in_progress",
                    "title": info.get("title"),
                    "duration_seconds": round(full_duration, 2),
                    "chunk_count": chunk_count,
                    "chunks_completed": len(processed_chunks),
                    "transcript_source": transcript_source,
                    "transcript_segment_count": segment_count,
                    "chunks": processed_chunks,
                    "out_dir": str(work),
                },
                indent=2,
            )
        )
    except OSError:
        pass


def _slugify(text: str, *, max_len: int = 60) -> str:
    """Lowercase, hyphen-separated slug safe for filenames ('' if no usable chars)."""
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text or "").strip("-").lower()
    if len(slug) > max_len:
        slug = slug[:max_len].rstrip("-")
    return slug


def _suggested_docx_name(title: str | None, source: str) -> str:
    """Output filename derived from the video title, always ending in '-analysis.docx'.

    Falls back to the source's basename (URL tail or local file stem) when the
    title is missing or has no slug-safe characters.
    """
    base = _slugify(title or "")
    if not base:
        stem = Path(source.split("?")[0].rstrip("/")).stem
        base = _slugify(stem) or "video"
    return f"{base}-analysis.docx"


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


def _docx_image_dimensions(width: int | None, height: int | None, aspect: str | None) -> dict:
    """Pick docx image dimensions in points. Width is generally pinned at 480
    so the image fits the body text column on US Letter at 1in margins."""
    if aspect == "16:9":
        return {"width": 480, "height": 270}
    if aspect == "4:3":
        return {"width": 480, "height": 360}
    if aspect == "9:16":
        return {"width": 240, "height": 427}
    if aspect == "1:1":
        return {"width": 360, "height": 360}
    if width and height:
        return {"width": 480, "height": int(round(480 * height / width))}
    return {"width": 480, "height": 270}


def _write_transcript_file(work: Path, segments: list[dict]) -> Path | None:
    """Write a human-readable transcript.txt (one '[time] text' line per segment).

    Returns the path, or None when there are no segments. Always written when a
    transcript exists so that keeping it after cleanup just means not deleting it.
    """
    if not segments:
        return None
    lines = []
    for seg in segments:
        text = (seg.get("text") or "").strip()
        if not text:
            continue
        lines.append(f"[{format_time(seg.get('start') or 0.0)}] {text}")
    if not lines:
        return None
    path = work / "transcript.txt"
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _focused_audio_path(work: Path, start_seconds: float, end_seconds: float) -> Path:
    start = int(round(start_seconds))
    end = int(round(end_seconds))
    return work / f"audio_{start}_{end}.mp3"


def _process_chunk(
    *,
    chunk_index: int,
    chunk_count: int,
    chunk_start: float,
    chunk_end: float,
    is_focus: bool,
    video_path: str,
    work: Path,
    args,
    max_frames: int,
    full_transcript_segments: list[dict],
) -> dict:
    """Extract frames + contact sheet + transcript slice indices for one chunk."""
    chunk_dir = work / "chunks" / f"chunk_{chunk_index}"
    chunk_frames_dir = chunk_dir / "frames"
    chunk_sheet_path = chunk_dir / "contact_sheet.jpg"
    chunk_duration = max(0.0, chunk_end - chunk_start)

    if is_focus:
        chunk_fps, chunk_target = auto_fps_focus(chunk_duration, max_frames=max_frames)
    else:
        chunk_fps, chunk_target = auto_fps(chunk_duration, max_frames=max_frames)
    if args.fps is not None:
        chunk_fps = min(args.fps, MAX_FPS)
        chunk_target = max(1, int(round(chunk_fps * chunk_duration)))

    print(
        f"[analyze-video] chunk {chunk_index}/{chunk_count}: "
        f"~{chunk_target} frames at {chunk_fps:.3f} fps over "
        f"{format_time(chunk_start)} to {format_time(chunk_end)} "
        f"({chunk_duration:.1f}s)...",
        file=sys.stderr,
    )

    chunk_frames, chunk_frames_dir = extract(
        video_path,
        chunk_frames_dir,
        fps=chunk_fps,
        resolution=args.resolution,
        max_frames=max_frames,
        start_seconds=chunk_start,
        end_seconds=chunk_end,
        force=getattr(args, "force", False),
    )

    chunk_sheet: Path | None = None
    if not args.no_contact_sheet and chunk_frames:
        # Auto-pick tile width based on frame count unless the user overrode it.
        tile_width = (
            args.contact_sheet_tile_width
            if args.contact_sheet_tile_width is not None
            else auto_tile_width(len(chunk_frames))
        )
        chunk_sheet = make_contact_sheet(
            chunk_frames_dir,
            chunk_sheet_path,
            cols=args.contact_sheet_cols,
            tile_width=tile_width,
        )

    # Per-chunk transcript = indices into the top-level segments array (no
    # duplicated text). The agent (or select_frames.py) can slice on demand.
    if full_transcript_segments:
        chunk_segments = filter_range(full_transcript_segments, chunk_start, chunk_end)
        seg_count = len(chunk_segments)
        if seg_count:
            first = chunk_segments[0]
            last = chunk_segments[-1]
            # Use id() comparison to find exact segment objects, avoiding
            # list.index() which matches by value and breaks on duplicates.
            start_idx = next(
                (i for i, s in enumerate(full_transcript_segments) if s is first), None
            )
            end_idx = next(
                (i for i, s in enumerate(full_transcript_segments) if s is last), None
            )
        else:
            start_idx, end_idx = None, None
    else:
        seg_count = 0
        start_idx, end_idx = None, None

    return {
        "index": chunk_index,
        "is_focus": is_focus,
        "start_seconds": round(chunk_start, 2),
        "end_seconds": round(chunk_end, 2),
        "duration_seconds": round(chunk_duration, 2),
        "start_formatted": format_time(chunk_start),
        "end_formatted": format_time(chunk_end),
        "frame_count": len(chunk_frames),
        "frames": [
            {
                "index": f["index"],
                "timestamp_seconds": f["timestamp_seconds"],
                "timestamp_formatted": format_time(f["timestamp_seconds"]),
                "absolute_path": f["path"],
            }
            for f in chunk_frames
        ],
        "contact_sheet": (
            {
                "absolute_path": str(chunk_sheet),
                "cols": args.contact_sheet_cols,
                "rows": (len(chunk_frames) + args.contact_sheet_cols - 1)
                // args.contact_sheet_cols,
            }
            if chunk_sheet
            else None
        ),
        "transcript_slice": {
            "segment_count": seg_count,
            "start_index": start_idx,
            "end_index": end_idx,
        },
    }


def main() -> int:
    ap = argparse.ArgumentParser(
        prog="analyze-video",
        description=(
            "Process one video for the analyze-video skill: download, frames, "
            "transcript, contact sheet, optional auto-chunking for long videos, "
            "and a structured manifest."
        ),
    )
    ap.add_argument("--source", required=True, help="Video URL or local file path")
    ap.add_argument(
        "--out-dir",
        required=True,
        help="Output directory (typically a per-video subfolder of session outputs)",
    )
    ap.add_argument(
        "--max-frames",
        type=int,
        default=100,
        help=f"Cap on frame count per video/chunk (default 100, hard max {HARD_MAX_FRAMES})",
    )
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
        "--cookies",
        default=None,
        help=(
            "Path to a user-provided yt-dlp cookies.txt file for videos that require "
            "an authenticated session."
        ),
    )
    ap.add_argument(
        "--cookies-from-browser",
        default=None,
        metavar="BROWSER",
        help=(
            "Ask yt-dlp to read cookies from the user's local browser (for example: "
            "chrome, firefox, safari). Use only with explicit user authorization."
        ),
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
        default=None,
        help="Width in px for each tile in the contact sheet (default: auto-pick by frame count)",
    )
    ap.add_argument(
        "--no-chunking",
        action="store_true",
        help=(
            "Disable auto-chunking even for long videos. "
            "By default, unfocused videos > 12 min split into 10-min chunks."
        ),
    )
    ap.add_argument(
        "--quick",
        action="store_true",
        help=(
            "Quick mode: lower frame budget (max 40/chunk) and skip contact-sheet "
            "preview step (manifest sets quick_mode=true so the skill knows to "
            "select frames directly from the manifest without reading sheets)."
        ),
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help=(
            "Ignore any resumable progress: re-download the video and re-extract "
            "all frames even if cached output from a previous run is present."
        ),
    )
    ap.add_argument(
        "--trim-static-outro",
        action="store_true",
        help=(
            "If a repetitive promo/outro card is detected at the end, exclude it "
            "from frame extraction. Off by default; detection is always reported "
            "in the manifest as a hint regardless of this flag."
        ),
    )
    ap.add_argument(
        "--no-download-cache",
        action="store_true",
        help=(
            "Download the source into this run's out-dir instead of the shared "
            "per-user cache. By default a URL is downloaded once into "
            "~/.cache/analyze-video/downloads/<url-hash>/ and reused across runs "
            "(so focused reruns don't re-download), keeping full timestamps intact."
        ),
    )
    args = ap.parse_args()

    if args.quick:
        # Quick mode: trim the frame budget. The sheet itself is still produced
        # as a fallback, but the manifest signals the skill to skip the preview.
        args.max_frames = min(args.max_frames, 40)
    if args.cookies and args.cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")

    max_frames = min(args.max_frames, HARD_MAX_FRAMES)

    work = Path(args.out_dir).expanduser().resolve()
    work.mkdir(parents=True, exist_ok=True)
    print(f"[analyze-video] working dir: {work}", file=sys.stderr)
    _write_status(work, "starting", source=args.source)

    # 1. Download
    print(
        "[analyze-video] downloading via yt-dlp..."
        if is_url(args.source)
        else "[analyze-video] using local file...",
        file=sys.stderr,
    )
    _write_status(work, "downloading")
    download_dir = _download_dir(args.source, work, no_cache=args.no_download_cache)
    cache_entry = download_dir if (is_url(args.source) and not args.no_download_cache) else None
    if cache_entry is not None:
        print(
            f"[analyze-video] download cache: {download_dir} "
            f"(reused across runs; --force to refresh, --no-download-cache to opt out)",
            file=sys.stderr,
        )
        # Lease + recency before fetching so concurrent runs and the pruner
        # won't evict the entry this run depends on.
        cache_utils.begin_use(cache_entry)
    dl = download(
        args.source,
        download_dir,
        cookies=args.cookies,
        cookies_from_browser=args.cookies_from_browser,
        force=args.force,
    )
    video_path = dl["video_path"]
    _write_status(work, "downloaded")

    # 2. Probe metadata
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

    focused = start_sec is not None or end_sec is not None
    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)

    # 3. Transcript: try captions first, then Whisper. If --whisper not specified,
    #    try preferred backend and auto-fall-back to the other one if the first
    #    fails (rate limits, transient errors). We fetch the full-video
    #    transcript once and slice it per chunk later.
    full_transcript_segments: list[dict] = []
    transcript_source: str | None = None
    _write_status(work, "transcribing")
    if dl.get("subtitle_path"):
        try:
            full_transcript_segments = parse_vtt(dl["subtitle_path"])
            transcript_source = "captions"
        except Exception as exc:
            print(f"[analyze-video] subtitle parse failed: {exc}", file=sys.stderr)

    if not full_transcript_segments and not args.no_whisper:
        if args.whisper:
            # User pinned a specific backend; honor that, no fallback.
            backend, api_key = load_api_key(args.whisper)
            attempts = [(backend, api_key)] if backend and api_key else []
        else:
            # Auto-fallback: try Groq first, then OpenAI (or whichever keys exist).
            available = load_all_api_keys()
            attempts = [
                (b, available[b])
                for b in ("groq", "openai")
                if b in available
            ]

        if not attempts:
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
        else:
            for i, (backend, api_key) in enumerate(attempts):
                try:
                    audio_out = (
                        _focused_audio_path(work, effective_start, effective_end)
                        if focused
                        else work / "audio.mp3"
                    )
                    full_transcript_segments, used_backend = transcribe_video(
                        video_path,
                        audio_out,
                        backend=backend,
                        api_key=api_key,
                        start_seconds=effective_start if focused else None,
                        end_seconds=effective_end if focused else None,
                    )
                    transcript_source = f"whisper ({used_backend})"
                    break
                except SystemExit as exc:
                    next_attempt = attempts[i + 1] if i + 1 < len(attempts) else None
                    if next_attempt:
                        print(
                            f"[analyze-video] whisper backend '{backend}' failed: {exc}; "
                            f"falling back to '{next_attempt[0]}'",
                            file=sys.stderr,
                        )
                    else:
                        print(
                            f"[analyze-video] whisper failed (no more backends): {exc}",
                            file=sys.stderr,
                        )

    # 4. Decide chunking strategy.
    #    Detect a repetitive promo/outro at the very end (advisory). When
    #    --trim-static-outro is set on an unfocused video, exclude that block
    #    from the content window so frame extraction skips the ad cards.
    trailing_promo: dict | None = None
    content_end = effective_end
    if not focused:
        promo = detect_trailing_promo(full_transcript_segments, full_duration)
        if promo:
            suggested_end = promo["start_seconds"]
            trimmed = False
            # Only trim if a healthy amount of real content remains, and only for
            # high-confidence detections, so a quiet legit ending isn't dropped.
            min_keep = max(10.0, 0.4 * full_duration)
            can_trim = (
                args.trim_static_outro
                and promo.get("confidence") == "high"
                and min_keep <= suggested_end < content_end
            )
            if can_trim:
                content_end = suggested_end
                trimmed = True
                print(
                    f"[analyze-video] trimming trailing promo/outro: dropping "
                    f"{format_time(suggested_end)} to {format_time(full_duration)} "
                    f"({promo['reason']})",
                    file=sys.stderr,
                )
            else:
                low = " (low confidence)" if promo.get("confidence") == "low" else ""
                print(
                    f"[analyze-video] heads up{low}: {promo['reason']} starting at "
                    f"{format_time(suggested_end)}. Re-run with "
                    f"--end {format_time(suggested_end)} (or --trim-static-outro) "
                    f"to skip it.",
                    file=sys.stderr,
                )
            trailing_promo = {
                **promo,
                "start_formatted": format_time(promo["start_seconds"]),
                "end_formatted": format_time(promo["end_seconds"]),
                "suggested_end_seconds": round(suggested_end, 2),
                "suggested_end_formatted": format_time(suggested_end),
                "trimmed": trimmed,
            }

    _write_status(work, "transcript_ready", transcript_source=transcript_source)

    if focused:
        # Focus mode bypasses chunking. Single chunk = the focus range.
        chunk_ranges = [(effective_start, effective_end)]
        is_focus_chunk = True
        chunked = False
    elif args.no_chunking or not should_chunk(content_end, focused=False):
        # Single-chunk path (short video or chunking disabled)
        chunk_ranges = [(0.0, content_end)]
        is_focus_chunk = False
        chunked = False
    else:
        # Auto-chunked (over the content window, which may exclude a trimmed outro)
        chunk_ranges = compute_chunks(content_end)
        is_focus_chunk = False
        chunked = True

    if chunked:
        print(
            f"[analyze-video] auto-chunking: {len(chunk_ranges)} chunks "
            f"of ~{int(chunk_ranges[0][1] - chunk_ranges[0][0])}s each",
            file=sys.stderr,
        )
        print(
            "[analyze-video] if this run is interrupted (e.g. a timeout), just "
            "re-run the exact same command: completed chunks are reused and only "
            "the unfinished one is redone (check status.json for progress).",
            file=sys.stderr,
        )

    # 5. Process each chunk
    info = dl.get("info") or {}
    resume_hint = "Interrupted? Re-run the same command to resume from here."
    processed_chunks: list[dict] = []
    _write_status(
        work,
        "extracting",
        chunk_count=len(chunk_ranges),
        chunks_completed=0,
        resume_hint=resume_hint,
    )
    for i, (cs, ce) in enumerate(chunk_ranges):
        # Mark the in-flight chunk *before* extracting it, so a process killed
        # mid-chunk leaves a status that names exactly where it stopped.
        _write_status(
            work,
            "extracting",
            chunk_count=len(chunk_ranges),
            chunks_completed=len(processed_chunks),
            current_chunk=i + 1,
            resume_hint=resume_hint,
        )
        chunk = _process_chunk(
            chunk_index=i + 1,
            chunk_count=len(chunk_ranges),
            chunk_start=cs,
            chunk_end=ce,
            is_focus=is_focus_chunk,
            video_path=video_path,
            work=work,
            args=args,
            max_frames=max_frames,
            full_transcript_segments=full_transcript_segments,
        )
        processed_chunks.append(chunk)
        _write_status(
            work,
            "extracting",
            chunk_count=len(chunk_ranges),
            chunks_completed=len(processed_chunks),
            resume_hint=resume_hint,
        )
        _write_partial_manifest(
            work,
            info=info,
            full_duration=full_duration,
            chunk_count=len(chunk_ranges),
            processed_chunks=processed_chunks,
            transcript_source=transcript_source,
            segment_count=len(full_transcript_segments),
        )

    aspect = _aspect_ratio_label(meta.get("width"), meta.get("height"))
    docx_dim = _docx_image_dimensions(meta.get("width"), meta.get("height"), aspect)

    total_frames = sum(c["frame_count"] for c in processed_chunks)
    preview_cost_warning = chunked and len(processed_chunks) >= PREVIEW_COST_WARNING_CHUNKS

    # Standalone, human-readable transcript next to the manifest. Written whenever
    # a transcript exists so the skill can offer it both as a doc appendix and as
    # a kept file after cleanup.
    transcript_file = _write_transcript_file(work, full_transcript_segments)

    # 6. Build full manifest (schema_version 3). Top-level segments are the
    #    canonical transcript; per-chunk transcript is just index pointers.
    common_fields = {
        "schema_version": 3,
        "source": args.source,
        "title": info.get("title"),
        "uploader": info.get("uploader"),
        "url": info.get("url"),
        "duration_seconds": round(full_duration, 2),
        "duration_formatted": format_time(full_duration),
        "width": meta.get("width"),
        "height": meta.get("height"),
        "aspect_ratio": aspect,
        "docx_image_dimensions": docx_dim,
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
        "chunked": chunked,
        "chunk_count": len(processed_chunks),
        "total_frame_count": total_frames,
        "preview_cost_warning": preview_cost_warning,
        "quick_mode": bool(args.quick),
        "trailing_promo": trailing_promo,
        "transcript_source": transcript_source,
        "transcript_segment_count": len(full_transcript_segments),
        "transcript_path": str(transcript_file) if transcript_file else None,
        "out_dir": str(work),
        "suggested_docx_name": _suggested_docx_name(info.get("title"), args.source),
    }

    manifest = {
        **common_fields,
        "chunks": processed_chunks,
        "transcript_segments": full_transcript_segments,
    }

    manifest_path = work / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    # 6b. Lightweight manifest: same shape minus transcript_segments. The skill
    #     reads this by default; only fall back to manifest.json when raw
    #     transcript text is needed (direct quotes, boundary refinement).
    manifest_lite = {
        **common_fields,
        "chunks": processed_chunks,
        "manifest_path": str(manifest_path),
    }
    (work / "manifest_lite.json").write_text(json.dumps(manifest_lite, indent=2))

    # 7. Human-readable report
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
    if chunked:
        report_lines.append(
            f"- **Chunks:** {len(processed_chunks)} (auto-chunked, "
            f"10-min windows with 5s overlap)"
        )
    elif focused:
        report_lines.append("- **Mode:** focused on user-specified range")
    else:
        report_lines.append("- **Mode:** single-pass (under 12-minute chunking threshold)")
    report_lines.append(
        f"- **Total frames:** {total_frames} across {len(processed_chunks)} chunk"
        f"{'s' if len(processed_chunks) != 1 else ''}"
    )
    if full_transcript_segments:
        report_lines.append(
            f"- **Transcript:** {len(full_transcript_segments)} segments "
            f"(via {transcript_source or 'captions'})"
        )
    else:
        report_lines.append("- **Transcript:** none available")
    report_lines.append(f"- **Manifest:** `{manifest_path.name}`")
    report_lines.append("")

    if preview_cost_warning:
        report_lines.append(
            f"> **Heads up:** This video chunked into {len(processed_chunks)} "
            f"sections. Reading every contact sheet to preview the whole video "
            f"will cost ~{len(processed_chunks) * 7}-{len(processed_chunks) * 12}k "
            "tokens before any frames are selected. If the user has a specific "
            "section in mind, re-running with `--start HH:MM:SS --end HH:MM:SS` "
            "is much cheaper."
        )
        report_lines.append("")

    if trailing_promo:
        if trailing_promo["trimmed"]:
            report_lines.append(
                f"> **Trimmed trailing promo/outro:** excluded "
                f"{trailing_promo['suggested_end_formatted']} to "
                f"{trailing_promo['end_formatted']} from frame extraction "
                f"({trailing_promo['reason']})."
            )
        else:
            report_lines.append(
                f"> **Possible promo/outro detected:** {trailing_promo['reason']} "
                f"starting at {trailing_promo['suggested_end_formatted']}. Re-run "
                f"with `--end {trailing_promo['suggested_end_formatted']}` or "
                f"`--trim-static-outro` to skip it."
            )
        report_lines.append("")

    if not full_transcript_segments:
        setup_py = SCRIPT_DIR / "setup.py"
        report_lines.append(
            "_No transcript available. Captions were missing and the Whisper "
            "fallback was unavailable (no API key set, or `--no-whisper` was used). "
            f"Run `python3 {setup_py}` to enable Whisper, then re-run._"
        )
        report_lines.append("")

    (work / "report.md").write_text("\n".join(report_lines))

    # Pipeline finished: mark complete and drop the partial manifest so consumers
    # use the authoritative manifest.json.
    _write_status(work, "complete", manifest_path=str(manifest_path))
    partial = work / "manifest_partial.json"
    if partial.exists():
        try:
            partial.unlink()
        except OSError:
            pass

    # Release this run's cache lease and evict old/oversized cached downloads so
    # the shared cache doesn't grow without bound. The just-used entry is
    # protected from eviction. Housekeeping runs even for local/no-cache sources.
    if cache_entry is not None:
        cache_utils.end_use(cache_entry)
    cache_utils.prune_downloads(protect=cache_entry)

    # Final stdout: the lite manifest path (skill reads this; full manifest
    # path is recorded inside it under "manifest_path" if needed).
    print(str(work / "manifest_lite.json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
