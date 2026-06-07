#!/usr/bin/env python3
"""Parse a WebVTT subtitle file into a clean, timestamped transcript.

YouTube auto-subs emit rolling-duplicate cues (each line appears 2-3 times as it
scrolls). We dedupe consecutive identical cues and merge their time ranges.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path


TS_RE = re.compile(
    r"(\d{2}):(\d{2}):(\d{2})[.,](\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2})[.,](\d{3})"
)
TAG_RE = re.compile(r"<[^>]+>")


def _to_seconds(h: str, m: str, s: str, ms: str) -> float:
    return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000.0


def parse_vtt(path: str) -> list[dict]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    lines = text.splitlines()

    segments: list[dict] = []
    i = 0
    while i < len(lines):
        match = TS_RE.match(lines[i])
        if not match:
            i += 1
            continue

        start = _to_seconds(*match.groups()[:4])
        end = _to_seconds(*match.groups()[4:])
        i += 1

        cue_lines: list[str] = []
        while i < len(lines) and lines[i].strip():
            cleaned = TAG_RE.sub("", lines[i]).strip()
            if cleaned:
                cue_lines.append(cleaned)
            i += 1

        cue_text = " ".join(cue_lines).strip()
        if cue_text:
            segments.append({"start": round(start, 2), "end": round(end, 2), "text": cue_text})
        i += 1

    return _dedupe(segments)


def _dedupe(segments: list[dict]) -> list[dict]:
    """Collapse rolling duplicates common in YouTube auto-subs."""
    out: list[dict] = []
    for seg in segments:
        if out and seg["text"] == out[-1]["text"]:
            out[-1]["end"] = seg["end"]
            continue
        if out and seg["text"].startswith(out[-1]["text"] + " "):
            out[-1]["text"] = seg["text"]
            out[-1]["end"] = seg["end"]
            continue
        out.append(seg)
    return out


def filter_range(
    segments: list[dict],
    start_seconds: float | None,
    end_seconds: float | None,
) -> list[dict]:
    """Return segments whose time range overlaps [start, end]."""
    if start_seconds is None and end_seconds is None:
        return segments
    lo = start_seconds if start_seconds is not None else float("-inf")
    hi = end_seconds if end_seconds is not None else float("inf")
    return [seg for seg in segments if seg["end"] >= lo and seg["start"] <= hi]


def format_transcript(segments: list[dict]) -> str:
    lines = []
    for seg in segments:
        start = int(seg["start"])
        stamp = f"[{start // 60:02d}:{start % 60:02d}]"
        lines.append(f"{stamp} {seg['text']}")
    return "\n".join(lines)


_PHRASE_NORM_RE = re.compile(r"[^a-z0-9 ]+")

# Phrases that strongly suggest a promo/outro card rather than a quiet ending.
# Used to raise confidence for a single non-repeated low-words-per-second cue.
_PROMO_KEYWORDS = (
    "subscribe", "full episode", "dont miss", "don t miss", "like and subscribe",
    "link in", "patreon", "notification", "hit the bell", "smash that",
    "watch the full", "next episode", "comment below", "follow us",
)


def _normalize_phrase(text: str) -> str:
    return " ".join(_PHRASE_NORM_RE.sub(" ", (text or "").lower()).split())


def detect_trailing_promo(
    segments: list[dict],
    full_duration: float,
    *,
    min_seconds: float = 30.0,
    lookback_seconds: float = 240.0,
    max_words_per_second: float = 0.8,
) -> dict | None:
    """Detect a repetitive / static promo or outro block at the end of a video.

    Walks backward from the end and keeps the maximal trailing run in which every
    segment is itself "promo-like": its normalized phrase repeats elsewhere in
    the tail (a looped "DON'T MISS THE FULL EPISODE" card), or it holds a very low
    words-per-second rate (a static held card). The run stops at the first real
    speech segment, so the reported boundary is the actual promo start.

    Returns a dict (including a ``confidence`` of "high" or "low"), or ``None``.
    "low" confidence means a single, non-repeated, low-words-per-second cue that
    could be a legitimate quiet ending; callers should not auto-trim those.
    Advisory only: callers decide whether to surface a hint or trim.
    """
    if not segments or full_duration <= 0:
        return None

    tail_floor = max(0.0, full_duration - lookback_seconds)
    # Overlap test (end >= floor), so a long held card that *starts* before the
    # lookback window is still considered.
    tail = [
        s
        for s in segments
        if (s.get("text") or "").strip()
        and (s.get("end") or s.get("start") or 0.0) >= tail_floor
    ]
    if not tail:
        return None

    norms = [_normalize_phrase(s["text"]) for s in tail]
    counts: dict[str, int] = {}
    for n in norms:
        if n:
            counts[n] = counts.get(n, 0) + 1
    repeated = {phrase for phrase, c in counts.items() if c >= 2}

    def _promo_like(idx: int) -> bool:
        phrase = norms[idx]
        if not phrase:  # punctuation-only / non-speech cue
            return True
        if phrase in repeated:
            return True
        seg = tail[idx]
        duration = (seg.get("end") or seg["start"]) - seg["start"]
        if duration <= 0:
            return False
        return len(phrase.split()) / duration <= max_words_per_second

    i = len(tail)
    while i - 1 >= 0 and _promo_like(i - 1):
        i -= 1
    if i >= len(tail):
        return None

    run = tail[i:]
    run_norms = [n for n in norms[i:] if n]
    start = round(run[0]["start"], 2)
    end = round(min(run[-1].get("end") or run[-1]["start"], full_duration), 2)
    span = end - start
    if span < min_seconds:
        return None

    unique_phrases = sorted(set(run_norms))
    unique_count = len(unique_phrases)
    repeated_present = any(n in repeated for n in run_norms)
    keyword_present = any(
        any(kw in n for kw in _PROMO_KEYWORDS) for n in run_norms
    )
    # High confidence when the block genuinely repeats, has several cues, or
    # carries explicit promo language. A lone quiet cue stays low-confidence.
    high_confidence = repeated_present or len(run_norms) >= 3 or keyword_present
    return {
        "detected": True,
        "confidence": "high" if high_confidence else "low",
        "start_seconds": start,
        "end_seconds": end,
        "duration_seconds": round(span, 2),
        "unique_phrase_count": unique_count,
        "sample_phrases": unique_phrases[:5],
        "reason": (
            f"trailing {span:.0f}s collapses to {unique_count} repeated phrase"
            f"{'s' if unique_count != 1 else ''}"
        ),
    }


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: transcribe.py <vtt-path>", file=sys.stderr)
        raise SystemExit(2)
    print(format_transcript(parse_vtt(sys.argv[1])))
