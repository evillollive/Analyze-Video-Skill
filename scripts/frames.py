#!/usr/bin/env python3
"""Probe video metadata, extract frames, and tile them into a contact sheet.

Auto-fps targets a frame budget, not a fixed rate. Token cost scales with frame
count, so budget-by-duration keeps short videos dense and long videos capped.
When a user-specified range is passed, focused-mode budgets denser (they are
zooming in for detail).

Contact sheet: a single tiled image of all extracted frames. One Read call
gives the agent visual coverage of the entire video for ~5-10k tokens instead of
the 50-80k it would cost to Read every frame individually.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from env_utils import resolve_tool as _resolve_tool
except ImportError:  # pragma: no cover
    def _resolve_tool(name: str) -> str | None:
        return shutil.which(name)


def _require_tool(name: str) -> str:
    """Resolve a required executable (PATH or user-local bins) or exit clearly."""
    path = _resolve_tool(name)
    if path is None:
        raise SystemExit(
            f"{name} is not installed. Install with: brew install ffmpeg (macOS) "
            "or your system package manager."
        )
    return path


MAX_FPS = 2.0
HARD_MAX_FRAMES = 120  # was 100, bumped for the auto-chunking redesign

# Keyframe-only sampling. Decoding a chunk is the most expensive step in the
# pipeline: ffmpeg decodes every frame of the range just to keep the handful the
# fps filter lets through. Telling the decoder to skip non-keyframes removes
# nearly all of that work.
#
# The catch is that each sampled frame then snaps back to the nearest preceding
# keyframe, so this is only safe when keyframes are denser than the sampling
# interval. If they are not, the fps filter duplicates a single keyframe across
# many outputs and the contact sheet collapses to a handful of distinct images.
# KEYFRAME_SAMPLING_SAFETY is the required margin: take at most one frame per
# this many keyframes, which also bounds how stale a frame can be at half the
# sampling interval.
KEYFRAME_SAMPLING_SAFETY = 2.0

# Only this much of the video is inspected to measure the keyframe interval.
# Web video uses a fixed GOP, so a bounded sample is representative, and reading
# packet headers for the whole file would give back the time this is saving.
KEYFRAME_PROBE_SECONDS = 120.0

# Auto-chunking thresholds. Videos longer than CHUNK_THRESHOLD_SECONDS get split
# into CHUNK_DURATION_SECONDS-long chunks with CHUNK_OVERLAP_SECONDS overlap.
# Each chunk gets its own frame extraction, contact sheet, and transcript slice.
CHUNK_THRESHOLD_SECONDS = 12 * 60   # 720s, kicks in just above one chunk size
CHUNK_DURATION_SECONDS = 10 * 60    # 600s per chunk
CHUNK_OVERLAP_SECONDS = 5            # so transitions on chunk boundaries aren't lost

# Intra-chunk parallel extraction. A single ffmpeg decode does not scale across
# all cores, so one long range is split into contiguous sub-ranges that are
# decoded concurrently. Splitting only pays off when each worker still has real
# work to do, hence the floors below.
MIN_FRAMES_PER_SPLIT_JOB = 8
MIN_SPLIT_DURATION_SECONDS = 60.0


def should_chunk(duration_seconds: float, focused: bool) -> bool:
    """Auto-chunking activates for unfocused videos over the threshold.

    A user-specified --start/--end range bypasses chunking; they already chose
    a section.
    """
    return (not focused) and duration_seconds > CHUNK_THRESHOLD_SECONDS


def compute_chunks(duration_seconds: float) -> list[tuple[float, float]]:
    """Compute (start, end) tuples for each chunk in the video.

    Returns a single (0, duration) tuple for short videos (no chunking).
    For long videos, returns N chunks of CHUNK_DURATION_SECONDS each, with
    CHUNK_OVERLAP_SECONDS overlap between consecutive chunks. A trailing
    chunk shorter than 30s is absorbed into the previous one.
    """
    if duration_seconds <= CHUNK_THRESHOLD_SECONDS:
        return [(0.0, duration_seconds)]

    chunks: list[tuple[float, float]] = []
    start = 0.0
    while start < duration_seconds:
        end = min(start + CHUNK_DURATION_SECONDS, duration_seconds)
        chunks.append((start, end))
        if end >= duration_seconds:
            break
        start = end - CHUNK_OVERLAP_SECONDS

    # Absorb a short trailing chunk (under 60s) into the previous one to avoid
    # awkward tiny tail chunks. The previous chunk ends up slightly longer
    # than CHUNK_DURATION_SECONDS, which is fine.
    if len(chunks) >= 2 and (chunks[-1][1] - chunks[-1][0]) < 60:
        chunks[-2] = (chunks[-2][0], chunks[-1][1])
        chunks.pop()

    return chunks


def _clamp_fps(fps: float, duration_seconds: float, max_frames: int) -> tuple[float, int]:
    fps = min(fps, MAX_FPS)
    target = min(max_frames, max(1, int(round(fps * duration_seconds))))
    return fps, target


def parse_time(value: str | float | int | None) -> float | None:
    """Parse SS, MM:SS, or HH:MM:SS (with optional .ms) into seconds."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).strip()
    if not s:
        return None
    parts = s.split(":")
    try:
        if len(parts) == 1:
            return float(parts[0])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
    except ValueError:
        pass
    raise SystemExit(f"Cannot parse time value: {value!r} (expected SS, MM:SS, or HH:MM:SS)")


def format_time(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, sec = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{sec:02d}"
    return f"{minutes:02d}:{sec:02d}"


def get_metadata(video_path: str) -> dict:
    ffprobe = _require_tool("ffprobe")

    # Ask only for the fields below instead of every stream/format entry: on files
    # with many streams (multi-audio, subtitles, attached art) the full dump
    # carries tags, dispositions, and side data that are parsed only to be
    # discarded. `codec_type` is kept so audio can still be detected. Stream
    # ordering and selection are unchanged, so results match the full dump.
    result = subprocess.run(
        [
            ffprobe,
            "-v", "quiet",
            "-print_format", "json",
            "-show_entries",
            "format=duration,size:stream=codec_type,codec_name,width,height,duration",
            video_path,
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise SystemExit(f"ffprobe failed: {result.stderr.strip()}")

    data = json.loads(result.stdout or "{}")
    streams = data.get("streams", [])
    fmt = data.get("format", {})
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = float(fmt.get("duration") or video_stream.get("duration") or 0)
    return {
        "duration_seconds": duration,
        "width": video_stream.get("width"),
        "height": video_stream.get("height"),
        "codec": video_stream.get("codec_name"),
        "size_bytes": int(fmt.get("size") or 0),
        "has_audio": audio_stream is not None,
    }


def probe_keyframe_interval(
    video_path: str,
    *,
    sample_seconds: float = KEYFRAME_PROBE_SECONDS,
) -> float | None:
    """Median seconds between keyframes, measured over a bounded packet sample.

    Reads packet headers only (no decoding) for the first ``sample_seconds`` of
    video, so this costs a fraction of a second even on long files.

    Returns ``None`` when the interval cannot be established (ffprobe missing or
    failing, or fewer than two keyframes in the sample). Callers treat ``None``
    as "do not risk keyframe-only sampling" and fall back to a full decode.
    """
    ffprobe = _resolve_tool("ffprobe")
    if ffprobe is None:
        return None
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v", "quiet",
                "-select_streams", "v:0",
                "-show_entries", "packet=pts_time,flags",
                "-of", "csv=p=0",
                "-read_intervals", f"%+{sample_seconds:.3f}",
                video_path,
            ],
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None

    times: list[float] = []
    for line in result.stdout.splitlines():
        pts, _, flags = line.partition(",")
        if "K" not in flags:  # ffprobe marks keyframe packets with a K flag
            continue
        try:
            times.append(float(pts))
        except ValueError:
            continue  # pts_time can be N/A
    if len(times) < 2:
        return None

    times.sort()
    deltas = sorted(b - a for a, b in zip(times, times[1:]) if b > a)
    if not deltas:
        return None
    # Median, so one irregular gap (a scene-cut keyframe, or the tail of the
    # sampled window) cannot skew the estimate.
    mid = len(deltas) // 2
    if len(deltas) % 2:
        return deltas[mid]
    return (deltas[mid - 1] + deltas[mid]) / 2.0


def can_sample_keyframes_only(fps: float, keyframe_interval: float | None) -> bool:
    """True when sampling at ``fps`` is sparse enough for keyframe-only decoding.

    Requires the gap between sampled frames to cover at least
    ``KEYFRAME_SAMPLING_SAFETY`` keyframes, which guarantees every output frame
    lands on a distinct keyframe instead of the fps filter repeating one.
    """
    if not keyframe_interval or keyframe_interval <= 0 or fps <= 0:
        return False
    return (1.0 / fps) >= KEYFRAME_SAMPLING_SAFETY * keyframe_interval


def auto_tile_width(frame_count: int) -> int:
    """Pick a contact-sheet tile width that keeps the sheet ~5-7k image tokens.

    More frames per sheet -> smaller tiles so the overall image stays compact.
    """
    if frame_count <= 24:
        return 256
    if frame_count <= 60:
        return 200
    return 160


def auto_fps(duration_seconds: float, max_frames: int = 120) -> tuple[float, int]:
    """Pick fps that targets a sensible frame budget for full-video scans.

    Used for full-video runs and for each individual chunk (chunks are <= 600s
    so they fall in the <=10 min bracket, getting up to 100 frames each).
    """
    if duration_seconds <= 0:
        return 1.0, 1

    if duration_seconds <= 30:
        target = min(max_frames, max(12, int(round(duration_seconds))))
    elif duration_seconds <= 60:
        target = min(max_frames, 40)
    elif duration_seconds <= 180:  # 3 min
        target = min(max_frames, 60)
    elif duration_seconds <= 600:  # 10 min (also: standard chunk size)
        target = min(max_frames, 100)
    else:
        target = max_frames  # 10-12 min unchunked path; > 12 min triggers chunking

    return _clamp_fps(target / duration_seconds, duration_seconds, max_frames)


def auto_fps_focus(duration_seconds: float, max_frames: int = 120) -> tuple[float, int]:
    """Denser budget for user-specified ranges (they are zooming in for detail)."""
    if duration_seconds <= 0:
        return min(MAX_FPS, 2.0), 2

    if duration_seconds <= 5:
        target = min(max_frames, max(10, int(round(duration_seconds * 6))))
    elif duration_seconds <= 15:
        target = min(max_frames, max(30, int(round(duration_seconds * 4))))
    elif duration_seconds <= 30:
        target = min(max_frames, 60)
    elif duration_seconds <= 60:
        target = min(max_frames, 80)
    elif duration_seconds <= 180:
        target = max_frames
    else:
        target = max_frames

    return _clamp_fps(target / duration_seconds, duration_seconds, max_frames)


def _extract_signature(
    video_path: str,
    fps: float,
    resolution: int,
    max_frames: int,
    start_seconds: float | None,
    end_seconds: float | None,
    keyframes_only: bool = False,
) -> dict:
    """Fingerprint the inputs that determine a chunk's extracted frames.

    Used to decide whether an existing frames/ directory can be reused (resume)
    or must be re-extracted. Includes the source file's size + mtime so a swapped
    video invalidates stale frames.
    """
    try:
        st = os.stat(video_path)
        video_sig = f"{st.st_size}:{st.st_mtime_ns}"
    except OSError:
        video_sig = "missing"
    signature = {
        "video": str(video_path),
        "video_sig": video_sig,
        "fps": round(fps, 6),
        "resolution": resolution,
        "max_frames": max_frames,
        "start_seconds": None if start_seconds is None else round(start_seconds, 3),
        "end_seconds": None if end_seconds is None else round(end_seconds, 3),
    }
    # Only recorded when enabled, so a full-decode extraction keeps the exact
    # signature (and cache directory) it had before keyframe sampling existed.
    # The two modes still differ, so their frames can never be mixed up.
    if keyframes_only:
        signature["keyframes_only"] = True
    return signature


def _signature_key(signature: dict) -> str:
    """Short, stable directory name derived from an extraction signature.

    Each distinct set of extraction inputs (video, fps, range, etc.) maps to its
    own frames subdirectory. This is what lets a re-run after a timeout resume
    safely: identical inputs reuse (or overwrite) the same directory, while any
    change in inputs lands in a *fresh* directory. Stale frames left by a prior
    run with different inputs can never pollute the current run, and we never
    have to delete cross-session files (some sandboxes forbid that and crash).
    """
    blob = json.dumps(signature, sort_keys=True).encode("utf-8")
    return "fr_" + hashlib.sha1(blob).hexdigest()[:12]


def _list_frames(frames_dir: Path) -> list[Path]:
    """Sorted frame files in a directory ([] if it doesn't exist)."""
    if not frames_dir.exists():
        return []
    return sorted(frames_dir.glob("frame_*.jpg"))


def _frames_from_dir(
    out_dir: Path,
    fps: float,
    start_seconds: float | None,
    frame_files: list[Path] | None = None,
) -> list[dict]:
    offset = start_seconds or 0.0
    frames = _list_frames(out_dir) if frame_files is None else frame_files
    return [
        {
            "index": i + 1,
            "timestamp_seconds": round(offset + (i / fps if fps > 0 else 0.0), 2),
            "path": str(p),
        }
        for i, p in enumerate(frames)
    ]


def plan_split(
    fps: float,
    target_frames: int | None,
    start_seconds: float | None,
    end_seconds: float | None,
    jobs: int,
) -> list[tuple[float, float, int, int]] | None:
    """Partition one extraction into fps-grid-aligned, contiguous sub-ranges.

    Returns ``[(start, end, start_number, frame_count), ...]`` or ``None`` when
    splitting is not worth it (or not provably safe).

    The frame at global index ``i`` always sits at ``start + i / fps``, which is
    exactly the timestamp the manifest records. Each sub-range therefore begins
    on that same grid and is capped at its own frame count, so running the
    sub-ranges concurrently reproduces the single-pass frame set exactly.
    """
    if jobs < 2 or fps <= 0 or not target_frames or target_frames < 2 * MIN_FRAMES_PER_SPLIT_JOB:
        return None
    if end_seconds is None:
        return None  # an open-ended range has no frame count to partition
    base = start_seconds or 0.0
    if end_seconds - base < MIN_SPLIT_DURATION_SECONDS:
        return None

    per_job = max(MIN_FRAMES_PER_SPLIT_JOB, math.ceil(target_frames / jobs))
    if per_job >= target_frames:
        return None  # one worker would do all the work

    plan: list[tuple[float, float, int, int]] = []
    first = 0
    while first < target_frames:
        count = min(per_job, target_frames - first)
        job_start = base + first / fps
        job_end = min(end_seconds, base + (first + count) / fps)
        if job_end <= job_start:
            break
        plan.append((job_start, job_end, first + 1, count))
        first += count
    return plan if len(plan) > 1 else None


def _ffmpeg_extract_cmd(
    ffmpeg: str,
    video_path: str,
    output_pattern: str,
    *,
    fps: float,
    resolution: int,
    frames: int,
    start_seconds: float | None,
    end_seconds: float | None,
    start_number: int | None = None,
) -> list[str]:
    cmd: list[str] = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y"]
    # -ss before -i = fast seek (keyframe-snap, good enough for preview frames).
    if start_seconds is not None:
        cmd += ["-ss", f"{start_seconds:.3f}"]
    if end_seconds is not None:
        cmd += ["-to", f"{end_seconds:.3f}"]
    cmd += [
        "-i", video_path,
        "-vf", f"fps={fps},scale={resolution}:-2",
        "-frames:v", str(frames),
    ]
    if start_number is not None:
        cmd += ["-start_number", str(start_number)]
    cmd += ["-q:v", "4", output_pattern]
    return cmd


def _frames_are_contiguous(frame_files: list[Path]) -> bool:
    """True when the files are exactly frame_0001..frame_000N with no gaps.

    Frame timestamps are derived from each file's position in this sorted list,
    so a hole (a sub-range that produced fewer frames than its slot) would shift
    every later timestamp. Callers treat a gap as a failed split and redo the
    range in one pass.
    """
    return all(
        path.name == f"frame_{i:04d}.jpg" for i, path in enumerate(frame_files, start=1)
    )


def _run_split_extraction(
    ffmpeg: str,
    video_path: str,
    frames_dir: Path,
    plan: list[tuple[float, float, int, int]],
    *,
    fps: float,
    resolution: int,
) -> list[Path] | None:
    """Run a split plan concurrently. Returns the frame files, or None on failure.

    ``None`` means "fall back to a single pass": either ffmpeg failed in one of
    the workers or the combined output had a gap. The caller then re-runs the
    whole range serially, so a failed split costs time but never correctness.
    """
    output_pattern = str(frames_dir / "frame_%04d.jpg")

    def _run(slot: tuple[float, float, int, int]) -> bool:
        job_start, job_end, start_number, count = slot
        cmd = _ffmpeg_extract_cmd(
            ffmpeg,
            video_path,
            output_pattern,
            fps=fps,
            resolution=resolution,
            frames=count,
            start_seconds=job_start,
            end_seconds=job_end,
            start_number=start_number,
        )
        return subprocess.run(cmd, capture_output=True, text=True).returncode == 0

    with ThreadPoolExecutor(max_workers=len(plan)) as pool:
        ok = all(list(pool.map(_run, plan)))

    produced = _list_frames(frames_dir)
    if not ok or not produced or not _frames_are_contiguous(produced):
        for stale in produced:
            try:
                stale.unlink()
            except OSError:
                pass
        return None
    return produced


def extract(
    video_path: str,
    out_dir: Path,
    fps: float,
    resolution: int = 512,
    max_frames: int = 120,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
    force: bool = False,
    jobs: int = 1,
    target_frames: int | None = None,
    keyframes_only: bool = False,
) -> tuple[list[dict], Path]:
    """Extract frames into a signature-keyed subdirectory of out_dir.

    Returns ``(frames, frames_dir)``. ``frames_dir`` is the actual directory the
    frames live in (a fresh subdir per distinct extraction signature), which the
    caller should use when building the contact sheet so stale frames from a
    different run can't leak in.

    With ``jobs`` > 1 and a known ``target_frames``, the range is split into
    contiguous sub-ranges decoded concurrently. A single ffmpeg decode does not
    saturate a multi-core machine, so this roughly halves wall-clock extraction
    on the common unchunked path while producing the identical frame set.
    ``jobs`` is not part of the extraction signature: it changes how the frames
    are produced, never which frames are produced.

    ``keyframes_only`` decodes keyframes only, which is far cheaper but snaps
    each frame to the nearest preceding keyframe. Callers should gate it with
    :func:`can_sample_keyframes_only`. If it yields nothing (a codec or container
    that exposes no keyframe flags), this transparently redoes the extraction
    with a full decode.
    """
    ffmpeg = _require_tool("ffmpeg")

    out_dir.mkdir(parents=True, exist_ok=True)
    signature = _extract_signature(
        video_path, fps, resolution, max_frames, start_seconds, end_seconds,
        keyframes_only,
    )
    # Frames for this exact signature get their own directory. A run with any
    # different input lands elsewhere, so we never delete another run's files.
    frames_dir = out_dir / _signature_key(signature)
    sig_path = frames_dir / ".extract.json"
    existing = _list_frames(frames_dir)

    # Resume: reuse a completed extraction whose inputs are unchanged. This is
    # what lets a re-run after a timeout pick up where it left off instead of
    # restarting from zero.
    if not force and existing and sig_path.exists():
        try:
            if json.loads(sig_path.read_text()) == signature:
                # Reuse the listing we already have instead of re-globbing.
                return (
                    _frames_from_dir(frames_dir, fps, start_seconds, frame_files=existing),
                    frames_dir,
                )
        except (OSError, json.JSONDecodeError):
            pass

    frames_dir.mkdir(parents=True, exist_ok=True)
    # No deletion needed: an interrupted prior run with this same signature wrote
    # a prefix of the identical frame set, so ffmpeg's -y overwrites it exactly
    # (same inputs => same frame count, no orphans). Best-effort tidy-up only;
    # ignore failures because some sandboxes forbid cross-session deletes.
    if force:
        for stale in existing:
            try:
                stale.unlink()
            except OSError:
                pass
    try:
        if sig_path.exists():
            sig_path.unlink()
    except OSError:
        pass

    output_pattern = str(frames_dir / "frame_%04d.jpg")
    frame_files: list[Path] | None = None
    plan = (
        None
        if keyframes_only
        else plan_split(fps, target_frames, start_seconds, end_seconds, jobs)
    )
    if plan:
        print(
            f"[analyze-video] extracting {target_frames} frames in {len(plan)} "
            f"parallel sub-ranges",
            file=sys.stderr,
        )
        frame_files = _run_split_extraction(
            ffmpeg, video_path, frames_dir, plan, fps=fps, resolution=resolution
        )
        if frame_files is None:
            print(
                "[analyze-video] parallel extraction did not produce a complete "
                "frame set; redoing this range in a single pass",
                file=sys.stderr,
            )

    if frame_files is None:
        cmd = _ffmpeg_extract_cmd(
            ffmpeg,
            video_path,
            output_pattern,
            fps=fps,
            resolution=resolution,
            frames=max_frames,
            start_seconds=start_seconds,
            end_seconds=end_seconds,
        )
        if keyframes_only:
            cmd.insert(cmd.index("-i"), "-skip_frame")
            cmd.insert(cmd.index("-i"), "nokey")
        result = subprocess.run(cmd, capture_output=True, text=True)
        frame_files = _list_frames(frames_dir)
        if keyframes_only and (result.returncode != 0 or not frame_files):
            print(
                "[frames] keyframe-only sampling produced no frames; "
                "retrying with a full decode",
                file=sys.stderr,
            )
            return extract(
                video_path,
                out_dir,
                fps=fps,
                resolution=resolution,
                max_frames=max_frames,
                start_seconds=start_seconds,
                end_seconds=end_seconds,
                force=True,
                jobs=jobs,
                target_frames=target_frames,
                keyframes_only=False,
            )
        if result.returncode != 0:
            raise SystemExit(f"ffmpeg frame extraction failed: {result.stderr.strip()}")

    # Record the signature so a later run can reuse these frames (resume).
    sig_path.write_text(json.dumps(signature))
    return (
        _frames_from_dir(frames_dir, fps, start_seconds, frame_files=frame_files),
        frames_dir,
    )


def _sheet_signature(
    frames_dir: Path,
    frame_count: int,
    cols: int,
    tile_width: int,
    quality: int,
) -> dict:
    """Fingerprint the inputs that determine a contact sheet's pixels.

    ``frames_dir`` is already keyed by the extraction signature (see
    :func:`_signature_key`), so naming it plus the frame count pins the exact
    frame set. The tiling parameters cover the rest of the ffmpeg command.
    """
    return {
        "frames_dir": str(frames_dir),
        "frame_count": frame_count,
        "cols": cols,
        "tile_width": tile_width,
        "quality": quality,
    }


def _sheet_sig_path(out_path: Path) -> Path:
    return out_path.with_name(f".{out_path.name}.sheet.json")


def make_contact_sheet(
    frames_dir: Path,
    out_path: Path,
    cols: int = 8,
    tile_width: int = 200,
    quality: int = 5,
    frame_count: int | None = None,
    force: bool = False,
) -> Path:
    """Tile every frame_*.jpg in frames_dir into a single contact sheet.

    Output is row-major in chronological order (top-left = first frame, then
    left-to-right, top-to-bottom). The skill's manifest tells the model which
    timestamp each tile corresponds to. No text overlay (keeps ffmpeg simple
    and avoids font dependencies).

    Pass ``frame_count`` when the caller already knows it (it comes back from
    ``extract``) to skip re-listing the directory.

    A sheet whose inputs are unchanged is reused instead of re-tiled. This
    matters on the documented recovery path ("re-run the exact same command"):
    ``extract`` already resumes by reusing cached frames, but without this the
    run still paid a full ffmpeg decode-and-tile of every frame in every chunk
    to rebuild sheets that were already on disk. Pass ``force=True`` to rebuild.

    Returns the contact sheet path. Raises SystemExit on failure or if no
    frames are present.
    """
    if frame_count is None:
        frame_count = len(_list_frames(frames_dir))
    if not frame_count:
        raise SystemExit(f"No frames found in {frames_dir} to tile")

    signature = _sheet_signature(frames_dir, frame_count, cols, tile_width, quality)
    sig_path = _sheet_sig_path(out_path)
    if not force and out_path.exists():
        try:
            if out_path.stat().st_size > 0 and json.loads(sig_path.read_text()) == signature:
                return out_path
        except (OSError, json.JSONDecodeError):
            pass

    ffmpeg = _require_tool("ffmpeg")

    rows = (frame_count + cols - 1) // cols
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Drop any stale marker first, so a run killed mid-tile can't leave a marker
    # that vouches for a truncated sheet.
    try:
        sig_path.unlink()
    except OSError:
        pass

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-pattern_type", "glob",
        "-i", str(frames_dir / "frame_*.jpg"),
        "-vf",
        f"scale={tile_width}:-2,tile={cols}x{rows}:padding=4:margin=4:color=0x111111",
        "-frames:v", "1",
        "-q:v", str(quality),
        str(out_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"contact sheet tile failed: {result.stderr.strip()}")
    if not out_path.exists() or out_path.stat().st_size == 0:
        raise SystemExit("contact sheet produced no output")
    # Record the inputs so an identical later run can reuse this sheet.
    try:
        sig_path.write_text(json.dumps(signature))
    except OSError:
        pass
    return out_path


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(
            "usage: frames.py <video-path> <out-dir> [--fps F] [--resolution W] "
            "[--max-frames N] [--jobs N] [--start T] [--end T] [--contact-sheet] "
            "[--keyframe-sampling auto|never|always]",
            file=sys.stderr,
        )
        raise SystemExit(2)

    video = sys.argv[1]
    out = Path(sys.argv[2])
    args = sys.argv[3:]

    fps_override = None
    resolution = 512
    max_frames = 100
    start_arg = None
    end_arg = None
    contact_sheet = False
    jobs = 1
    keyframe_sampling = "auto"
    i = 0
    while i < len(args):
        if args[i] == "--fps":
            fps_override = float(args[i + 1]); i += 2
        elif args[i] == "--resolution":
            resolution = int(args[i + 1]); i += 2
        elif args[i] == "--max-frames":
            max_frames = int(args[i + 1]); i += 2
        elif args[i] == "--jobs":
            jobs = max(1, int(args[i + 1])); i += 2
        elif args[i] == "--start":
            start_arg = args[i + 1]; i += 2
        elif args[i] == "--end":
            end_arg = args[i + 1]; i += 2
        elif args[i] == "--contact-sheet":
            contact_sheet = True; i += 1
        elif args[i] == "--keyframe-sampling":
            keyframe_sampling = args[i + 1]; i += 2
        else:
            i += 1

    if keyframe_sampling not in {"auto", "never", "always"}:
        raise SystemExit("--keyframe-sampling must be one of: auto, never, always")

    meta = get_metadata(video)
    start_sec = parse_time(start_arg)
    end_sec = parse_time(end_arg)
    full_duration = meta["duration_seconds"]

    effective_start = start_sec if start_sec is not None else 0.0
    effective_end = end_sec if end_sec is not None else full_duration
    effective_duration = max(0.0, effective_end - effective_start)

    focused = start_sec is not None or end_sec is not None
    if focused:
        fps, target = auto_fps_focus(effective_duration, max_frames=max_frames)
    else:
        fps, target = auto_fps(effective_duration, max_frames=max_frames)
    if fps_override is not None:
        fps = fps_override
        target = max(1, int(round(fps * effective_duration)))

    keyframe_interval = (
        probe_keyframe_interval(video) if keyframe_sampling != "never" else None
    )
    if keyframe_sampling == "always":
        keyframes_only = True
    else:
        keyframes_only = keyframe_sampling == "auto" and can_sample_keyframes_only(
            fps, keyframe_interval
        )

    frames, frames_dir = extract(
        video, out,
        fps=fps,
        resolution=resolution,
        max_frames=max_frames,
        start_seconds=start_sec,
        end_seconds=end_sec,
        jobs=jobs,
        target_frames=target,
        keyframes_only=keyframes_only,
    )

    sheet_path: str | None = None
    if contact_sheet:
        sheet_path = str(
            make_contact_sheet(
                frames_dir,
                out.parent / "contact_sheet.jpg",
                frame_count=len(frames),
            )
        )

    print(json.dumps(
        {
            "meta": meta,
            "fps": fps,
            "target": target,
            "focused": focused,
            "keyframe_interval_seconds": (
                None if keyframe_interval is None else round(keyframe_interval, 3)
            ),
            "keyframe_sampled": keyframes_only,
            "frames": frames,
            "contact_sheet": sheet_path,
        },
        indent=2,
    ))
