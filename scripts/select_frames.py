#!/usr/bin/env python3
"""Pick N frames to embed in the docx, given a manifest_lite.json.

Encapsulates the frame-selection algorithm the skill used to inline as
pseudocode (proportional distribution across chunks + step formula +
min-1-per-chunk + endpoints).

Usage:
    python3 select_frames.py <manifest_lite.json> <N>

Output: JSON list to stdout, one entry per selected frame:
    [
        {
            "chunk_index": 1,
            "frame_index": 7,
            "absolute_path": "/.../frames/frame_0007.jpg",
            "timestamp_seconds": 14.0,
            "timestamp_formatted": "00:14"
        },
        ...
    ]

The skill is free to refine the picks afterward (shifting frames toward
visible transitions or transcript boundaries) but no longer needs to
re-derive the math each run.

If N is omitted or 0, picks a sensible default for the video duration:
  <2 min  ->  6
  2-5 min ->  10
  5-15min -> 15
  >15 min -> 20
"""
from __future__ import annotations

import json
import sys
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
        "absolute_path": frame.get("absolute_path"),
        "timestamp_seconds": frame.get("timestamp_seconds"),
        "timestamp_formatted": frame.get("timestamp_formatted"),
    }


def main() -> int:
    if len(sys.argv) < 2:
        print(
            "usage: select_frames.py <manifest_lite.json> [<N>]",
            file=sys.stderr,
        )
        return 2
    manifest_path = Path(sys.argv[1])
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    manifest = json.loads(manifest_path.read_text())
    picks = select(manifest, n)
    json.dump(picks, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
