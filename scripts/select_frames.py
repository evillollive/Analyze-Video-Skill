#!/usr/bin/env python3
"""Pick N frames to embed in the docx, given a manifest.

Usage:
    python3 select_frames.py <manifest_lite.json|manifest.json> [<N>] [--even]
    python3 select_frames.py <manifest_lite.json|manifest.json> --tiles 1:4,2:59

By default the picks are spread evenly across the video, and any pick that
looks nearly identical to one already chosen is swapped for the most
different-looking frame still available. A lecture filmed from one camera
therefore yields a few shots of the speaker plus the cutaways, instead of
twenty copies of the same shot. Near-blank frames (black, fades) are avoided.
Comparing frames needs ffmpeg; if it is missing or fails, the picks fall back
to plain even spacing. Pass ``--even`` to ask for plain even spacing.

``--tiles`` returns exactly the frames named, in the order given. A tile is
written ``<chunk>:<tile>``, where the tile number is the one printed on that
chunk's contact sheet (and is the frame's ``index`` in the manifest). Use it
to swap in frames chosen by eye.

manifest_lite.json carries no per-frame arrays; this script transparently
loads the full manifest.json (via the lite file's `manifest_path` pointer or a
sibling manifest.json) to read frame paths.

Output: JSON list to stdout, one entry per selected frame:
    [
        {
            "chunk_index": 1,
            "frame_index": 7,
            "tile": "1:7",
            "absolute_path": "/.../frames/frame_0007.jpg",
            "timestamp_seconds": 14.0,
            "timestamp_formatted": "00:14"
        },
        ...
    ]

If N is omitted or 0, picks a sensible default for the video duration:
  <2 min  ->  6
  2-5 min ->  10
  5-15min -> 15
  >15 min -> 20
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def default_n(duration_seconds: float) -> int:
    if duration_seconds < 120:
        return 6
    if duration_seconds < 300:
        return 10
    if duration_seconds < 900:
        return 15
    return 20


def _step_indices(total: int, n: int) -> list[int]:
    """Pick n frame indices (1-based) evenly spread over `total` frames."""
    if total <= 0 or n <= 0:
        return []
    if n >= total:
        return list(range(1, total + 1))
    step = total / n
    return [max(1, min(total, int(round(step * i + step / 2)))) for i in range(n)]


def select(manifest: dict, n: int) -> list[dict]:
    chunks = manifest.get("chunks") or []
    if not chunks:
        return []

    duration = float(manifest.get("duration_seconds") or 0.0)
    if n <= 0:
        n = default_n(duration)

    if len(chunks) == 1:
        chunk = chunks[0]
        frames = chunk.get("frames") or []
        picks = _step_indices(len(frames), n)
        return [_emit(chunk, frames[i - 1]) for i in picks]

    # Distribute across chunks proportionally to chunk duration; ensure at
    # least 1 per chunk so short tail chunks don't get squeezed out.
    total_dur = sum(c.get("duration_seconds") or 0.0 for c in chunks) or duration
    raw_share: list[float] = []
    for c in chunks:
        cd = c.get("duration_seconds") or 0.0
        raw_share.append(n * (cd / total_dur) if total_dur > 0 else n / len(chunks))

    # Floor each share to int >= 1 (where the chunk has frames), then
    # distribute leftovers to the chunks with the largest fractional remainder.
    shares = [max(1, int(s)) if (chunks[i].get("frames") or []) else 0
              for i, s in enumerate(raw_share)]
    remaining = n - sum(shares)
    if remaining > 0:
        order = sorted(
            range(len(chunks)),
            key=lambda i: (raw_share[i] - shares[i]),
            reverse=True,
        )
        for i in order:
            if remaining == 0:
                break
            if not (chunks[i].get("frames") or []):
                continue
            shares[i] += 1
            remaining -= 1
    elif remaining < 0:
        # Trim from chunks with the smallest fractional remainder, but keep >=1
        order = sorted(
            range(len(chunks)),
            key=lambda i: (raw_share[i] - shares[i]),
        )
        for i in order:
            if remaining == 0:
                break
            if shares[i] > 1:
                shares[i] -= 1
                remaining += 1

    picks: list[dict] = []
    for chunk, share in zip(chunks, shares):
        if share <= 0:
            continue
        frames = chunk.get("frames") or []
        if not frames:
            continue
        for idx in _step_indices(len(frames), share):
            picks.append(_emit(chunk, frames[idx - 1]))
    return picks


def _emit(chunk: dict, frame: dict) -> dict:
    return {
        "chunk_index": chunk.get("index"),
        "frame_index": frame.get("index"),
        "tile": f"{chunk.get('index')}:{frame.get('index')}",
        "absolute_path": frame.get("absolute_path"),
        "timestamp_seconds": frame.get("timestamp_seconds"),
        "timestamp_formatted": frame.get("timestamp_formatted"),
    }


# ---------------------------------------------------------------------------
# Picking frames by tile
# ---------------------------------------------------------------------------

def parse_tiles(text: str) -> list[tuple[int, int]]:
    """Parse "1:4,2:59" (or "4,19" for a single-chunk video) into (chunk, tile) pairs."""
    pairs: list[tuple[int, int]] = []
    for part in (p.strip() for p in text.replace(" ", ",").split(",")):
        if not part:
            continue
        chunk_text, _, tile_text = part.rpartition(":")
        try:
            pairs.append((int(chunk_text) if chunk_text else 1, int(tile_text)))
        except ValueError:
            raise SystemExit(f"bad tile {part!r}: write <chunk>:<tile>, for example 2:59")
    if not pairs:
        raise SystemExit("--tiles needs at least one <chunk>:<tile>")
    return pairs


def select_tiles(manifest: dict, tiles: list[tuple[int, int]]) -> list[dict]:
    """Return the frames named by (chunk, tile) pairs, in the order given."""
    by_chunk = {c.get("index"): c for c in manifest.get("chunks") or []}
    picks: list[dict] = []
    for chunk_index, tile in tiles:
        chunk = by_chunk.get(chunk_index)
        if chunk is None:
            raise SystemExit(
                f"no chunk {chunk_index}; this video has chunks {sorted(by_chunk)}"
            )
        frames = {f.get("index"): f for f in chunk.get("frames") or []}
        if tile not in frames:
            raise SystemExit(
                f"chunk {chunk_index} has no tile {tile}; it has tiles 1 to {len(frames)}"
            )
        picks.append(_emit(chunk, frames[tile]))
    return picks


# ---------------------------------------------------------------------------
# Look-alike-aware selection
# ---------------------------------------------------------------------------

SIGNATURE_SIDE = 16                      # each frame is compared as a 16x16 RGB thumbnail
SIGNATURE_BYTES = SIGNATURE_SIDE * SIGNATURE_SIDE * 3
DUPLICATE_DISTANCE = 22.0                # mean per-channel difference (0-255) below which two frames look alike
BLANK_SPREAD = 8.0                       # brightness spread below which a frame is near-blank
MIDDLE_PULL = 0.15                       # how strongly a slot prefers its middle frame over a slightly newer-looking one


def _distance(a: bytes, b: bytes) -> float:
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def _is_blank(sig: bytes) -> bool:
    """True for a near-uniform frame: black, white, or the middle of a fade."""
    lum = [(sig[i] * 3 + sig[i + 1] * 6 + sig[i + 2]) / 10 for i in range(0, len(sig), 3)]
    mean = sum(lum) / len(lum)
    spread = (sum((v - mean) ** 2 for v in lum) / len(lum)) ** 0.5
    return spread < BLANK_SPREAD


def _concat_list(paths: list[str]) -> str:
    lines = []
    for path in paths:
        escaped = Path(path).resolve().as_posix().replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
    return "\n".join(lines) + "\n"


def frame_signatures(paths: list[str]) -> list[bytes] | None:
    """Shrink every frame to a tiny RGB thumbnail with one ffmpeg call.

    Returns one ``bytes`` per path, or None when ffmpeg is unavailable or the
    output is not what was asked for. Callers treat None as "cannot compare".
    """
    try:
        from env_utils import resolve_tool
    except ImportError:  # pragma: no cover
        return None
    ffmpeg = resolve_tool("ffmpeg")
    if not ffmpeg or not paths:
        return None
    fd, list_path = tempfile.mkstemp(suffix=".txt", prefix="analyze-video-frames-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(_concat_list(paths))
        result = subprocess.run(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", list_path,
                "-vf", f"scale={SIGNATURE_SIDE}:{SIGNATURE_SIDE}:flags=area,format=rgb24",
                "-f", "rawvideo", "-",
            ],
            capture_output=True,
        )
    except OSError:
        return None
    finally:
        try:
            os.unlink(list_path)
        except OSError:
            pass
    raw = result.stdout or b""
    if result.returncode != 0 or len(raw) != SIGNATURE_BYTES * len(paths):
        return None
    return [raw[i * SIGNATURE_BYTES:(i + 1) * SIGNATURE_BYTES] for i in range(len(paths))]


def pick_diverse(times: list[float], signatures: list[bytes], n: int) -> list[int]:
    """Choose ``n`` positions: even coverage in time, without look-alikes.

    ``times`` and ``signatures`` describe the candidate frames in time order.
    The timeline is cut into ``n`` equal slots. Each slot contributes its
    frame that looks least like the frames already chosen, skipping blank
    ones. A slot with nothing new gives its turn to the most different-looking
    frame anywhere in the video. If the video runs out of new-looking frames,
    the remaining picks fill the largest gaps in time, so the result always
    has ``n`` frames when ``n`` candidates exist.
    """
    total = len(times)
    if n >= total:
        return list(range(total))
    if n <= 0:
        return []
    blank = [_is_blank(sig) for sig in signatures]
    picked: list[int] = []

    def novelty(i: int) -> float:
        if not picked:
            return float("inf")
        return min(_distance(signatures[i], signatures[j]) for j in picked)

    start, end = times[0], times[-1]
    span = (end - start) or 1.0
    slots: list[list[int]] = [[] for _ in range(n)]
    for i, t in enumerate(times):
        slots[min(n - 1, int((t - start) / span * n))].append(i)

    spare = 0
    for slot in slots:
        usable = [i for i in slot if not blank[i]]
        if not usable:
            spare += 1
            continue
        middle = slot[len(slot) // 2]
        # Take the slot's most different-looking frame, leaning slightly toward
        # its middle so ties stay evenly spaced.
        best = max(usable, key=lambda i: novelty(i) - MIDDLE_PULL * abs(i - middle))
        if novelty(best) >= DUPLICATE_DISTANCE:
            picked.append(best)
        else:
            spare += 1

    for _ in range(spare):
        candidates = [i for i in range(total) if i not in picked and not blank[i]]
        if not candidates:
            break
        best = max(candidates, key=novelty)
        if novelty(best) < DUPLICATE_DISTANCE:
            break
        picked.append(best)

    while len(picked) < n:
        chosen = set(picked)
        candidates = [i for i in range(total) if i not in chosen]
        if not candidates:
            break
        pool = [i for i in candidates if not blank[i]] or candidates
        picked.append(max(pool, key=lambda i: min((abs(times[i] - times[j]) for j in picked), default=float("inf"))))
    return sorted(picked)


def select_diverse(manifest: dict, n: int, signature_fn=frame_signatures) -> list[dict] | None:
    """Look-alike-aware selection. Returns None when frames cannot be compared."""
    chunks = manifest.get("chunks") or []
    duration = float(manifest.get("duration_seconds") or 0.0)
    if n <= 0:
        n = default_n(duration)
    candidates: list[tuple[float, dict, dict]] = []
    for chunk in chunks:
        for frame in chunk.get("frames") or []:
            if frame.get("absolute_path") is None:
                continue
            candidates.append((float(frame.get("timestamp_seconds") or 0.0), chunk, frame))
    if not candidates:
        return []
    candidates.sort(key=lambda item: (item[0], item[1].get("index") or 0))
    signatures = signature_fn([frame["absolute_path"] for _, _, frame in candidates])
    if signatures is None:
        return None
    positions = pick_diverse([t for t, _, _ in candidates], signatures, n)
    return [_emit(candidates[i][1], candidates[i][2]) for i in positions]


def _chunks_have_frames(manifest: dict) -> bool:
    for chunk in manifest.get("chunks") or []:
        if chunk.get("frames"):
            return True
    return False


def load_manifest_for_selection(path: Path) -> dict:
    """Load a manifest for frame selection, upgrading lite -> full when needed.

    manifest_lite.json no longer carries per-frame arrays, so if the loaded
    manifest's chunks have no `frames` we resolve the full manifest.json. We try,
    in order: the recorded `manifest_path` (absolute), that path resolved
    relative to the lite file's directory (handles relocated output dirs), then a
    sibling `manifest.json`. The original manifest is returned unchanged if it
    already has frames or no fuller manifest can be found.
    """
    manifest = json.loads(path.read_text())
    if not (manifest.get("chunks") and not _chunks_have_frames(manifest)):
        return manifest

    candidates: list[Path] = []
    recorded = manifest.get("manifest_path")
    if recorded:
        rec = Path(recorded)
        candidates.append(rec)
        if not rec.is_absolute():
            candidates.append(path.parent / rec)
        else:
            candidates.append(path.parent / rec.name)
    candidates.append(path.parent / "manifest.json")

    for candidate in candidates:
        try:
            if candidate.resolve() == path.resolve():
                continue
            full = json.loads(candidate.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if _chunks_have_frames(full):
            return full

    raise SystemExit(
        f"{path.name} has no per-frame data and the full manifest.json could not "
        f"be loaded from {path.parent}. Pass manifest.json directly."
    )


USAGE = (
    "usage: select_frames.py <manifest_lite.json|manifest.json> [<N>] [--even]\n"
    "       select_frames.py <manifest_lite.json|manifest.json> --tiles 1:4,2:59"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    even = False
    tiles_text: str | None = None
    positional: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--even":
            even = True
        elif arg == "--tiles":
            if i + 1 >= len(args):
                print(USAGE, file=sys.stderr)
                return 2
            tiles_text = args[i + 1]
            i += 1
        elif arg.startswith("--tiles="):
            tiles_text = arg.split("=", 1)[1]
        elif arg in ("-h", "--help"):
            print(USAGE)
            return 0
        else:
            positional.append(arg)
        i += 1
    if not positional:
        print(USAGE, file=sys.stderr)
        return 2
    manifest_path = Path(positional[0])
    n = int(positional[1]) if len(positional) > 1 else 0
    manifest = load_manifest_for_selection(manifest_path)

    if tiles_text is not None:
        picks = select_tiles(manifest, parse_tiles(tiles_text))
    else:
        picks = None if even else select_diverse(manifest, n)
        if picks is None:
            if not even:
                print(
                    "[select_frames] could not compare frames (ffmpeg missing or failed); "
                    "using even spacing",
                    file=sys.stderr,
                )
            picks = select(manifest, n)
    json.dump(picks, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
