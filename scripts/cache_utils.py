#!/usr/bin/env python3
"""Download-cache location and maintenance for the analyze-video skill.

The pipeline caches each downloaded source video under
`~/.cache/analyze-video/downloads/<sha256(url)[:16]>/` and reuses it across runs.
Left unmanaged that grows without bound (full-size videos), so this module adds
age- and size-based eviction plus a manual clear.

It also caches Whisper transcripts under `~/.cache/analyze-video/transcripts/`.
Transcription is the single most expensive non-download step (a full-video audio
decode, an upload, and a paid API call), and the pipeline's documented recovery
path is "re-run the exact same command". Without a cache every such re-run pays
for it again, so results are keyed by the audio's identity and reused.

Safety rules (deliberately strict, because this deletes files):
- Only ever touch entries directly under DOWNLOADS_DIR whose name is exactly a
  16-character lowercase hex cache key. Anything else (including the sibling
  `node_modules` docx cache one level up) is never considered.
- Refuse to operate if DOWNLOADS_DIR is a symlink.
- Never evict the entry the current run is using (`protect`) or any entry with a
  fresh `.in_use` lease (a concurrent run). Their sizes still count toward the
  size cap so we don't over-evict.
- All deletions are best-effort; failures (read-only/locked sandboxes) are
  ignored, matching the rest of the pipeline.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from pathlib import Path


CACHE_ROOT = Path.home() / ".cache" / "analyze-video"
DOWNLOADS_DIR = CACHE_ROOT / "downloads"
TRANSCRIPTS_DIR = CACHE_ROOT / "transcripts"

# Cache keys are sha256(url)[:16]; only dirs matching this are ever deleted.
ENTRY_NAME_RE = re.compile(r"^[0-9a-f]{16}$")

LAST_USED_MARKER = ".last_used"
IN_USE_MARKER = ".in_use"
# A run's lease is honored for this long; stale leases (crashed runs) expire.
LEASE_TTL_SECONDS = 6 * 3600

DEFAULT_MAX_AGE_DAYS = 14.0
DEFAULT_MAX_SIZE_GB = 5.0

# Transcripts are a few hundred kB at most, so they are kept far longer than
# downloads and are bounded by age alone.
DEFAULT_TRANSCRIPT_MAX_AGE_DAYS = 90.0
TRANSCRIPT_SCHEMA_VERSION = 1


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def max_age_seconds() -> float:
    """Age limit in seconds (0 disables age eviction)."""
    return max(0.0, _env_float("ANALYZE_VIDEO_CACHE_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS)) * 86400.0


def max_size_bytes() -> int:
    """Total cache size cap in bytes (0 disables size eviction)."""
    return int(max(0.0, _env_float("ANALYZE_VIDEO_CACHE_MAX_GB", DEFAULT_MAX_SIZE_GB)) * (1024 ** 3))


def _downloads_usable() -> bool:
    """True only if the downloads dir is a real (non-symlink) directory."""
    try:
        return DOWNLOADS_DIR.is_dir() and not DOWNLOADS_DIR.is_symlink()
    except OSError:
        return False


def _norm(path: Path | None) -> Path | None:
    if path is None:
        return None
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def dir_size(path: Path) -> int:
    """Sum of file sizes under `path` (bytes). Best-effort, 0 on error."""
    total = 0
    try:
        for child in path.rglob("*"):
            try:
                if child.is_file() and not child.is_symlink():
                    total += child.stat().st_size
            except OSError:
                continue
    except OSError:
        pass
    return total


def _cache_entries() -> list[Path]:
    """Validated cache entry dirs (16-hex names, real dirs, not symlinks)."""
    if not _downloads_usable():
        return []
    entries: list[Path] = []
    try:
        for child in DOWNLOADS_DIR.iterdir():
            try:
                if (
                    child.is_dir()
                    and not child.is_symlink()
                    and ENTRY_NAME_RE.match(child.name)
                ):
                    entries.append(child)
            except OSError:
                continue
    except OSError:
        return []
    return entries


def _recency(entry: Path) -> float:
    """Most-recent 'used' time for an entry (markers preferred over dir mtime)."""
    times = []
    for marker in (LAST_USED_MARKER, IN_USE_MARKER):
        try:
            mp = entry / marker
            if mp.exists():
                times.append(mp.stat().st_mtime)
        except OSError:
            continue
    try:
        times.append(entry.stat().st_mtime)
    except OSError:
        pass
    return max(times) if times else 0.0


def _leased(entry: Path, now: float) -> bool:
    """True if a (non-expired) `.in_use` lease from a concurrent run exists."""
    try:
        mp = entry / IN_USE_MARKER
        if mp.exists():
            return (now - mp.stat().st_mtime) < LEASE_TTL_SECONDS
    except OSError:
        return False
    return False


def begin_use(entry: Path) -> None:
    """Mark a cache entry as in use by the current run (lease + recency)."""
    if entry is None:
        return
    try:
        entry.mkdir(parents=True, exist_ok=True)
        now = str(time.time())
        (entry / IN_USE_MARKER).write_text(now)
        (entry / LAST_USED_MARKER).write_text(now)
    except OSError:
        pass


def end_use(entry: Path) -> None:
    """Refresh recency and release the in-use lease at the end of a run."""
    if entry is None:
        return
    try:
        if entry.is_dir():
            (entry / LAST_USED_MARKER).write_text(str(time.time()))
    except OSError:
        pass
    try:
        (entry / IN_USE_MARKER).unlink()
    except OSError:
        pass


def _remove(entry: Path, known_size: int | None = None) -> int:
    """rmtree an entry, returning bytes freed (best-effort).

    Pass ``known_size`` when the caller already measured the entry: sizing is a
    full recursive walk over a multi-GB video directory, so re-measuring here
    would double the I/O for every eviction.
    """
    before = dir_size(entry) if known_size is None else known_size
    try:
        shutil.rmtree(entry)
    except OSError:
        return max(0, before - dir_size(entry))
    return before


def clear_downloads() -> dict:
    """Remove cached downloads. Returns removed/freed/failed/skipped counts.

    Entries with a fresh `.in_use` lease (a concurrent run actively using them)
    are skipped so clearing the cache can't crash an in-progress analysis.
    """
    now = time.time()
    # One directory listing and one size walk per entry. The previous version
    # listed the cache twice and sized each entry up to three times (before, and
    # again after deletion), which is expensive on multi-GB video directories.
    entries: list[Path] = []
    skipped = 0
    for entry in _cache_entries():
        if _leased(entry, now):
            skipped += 1
        else:
            entries.append(entry)

    sizes = {entry: dir_size(entry) for entry in entries}
    freed = 0
    failed = 0
    for entry in entries:
        try:
            shutil.rmtree(entry)
            freed += sizes[entry]
        except OSError:
            failed += 1
            # Partial deletion is possible, so measure what actually went away.
            freed += max(0, sizes[entry] - dir_size(entry))
    return {
        "removed": len(entries) - failed,
        "freed_bytes": max(0, freed),
        "failed": failed,
        "skipped": skipped,
    }


def prune_downloads(
    protect: Path | None = None,
    max_bytes: int | None = None,
    max_age: float | None = None,
) -> dict:
    """Evict cached downloads by age, then by total size (LRU, oldest first).

    Never evicts `protect` or any leased entry; their sizes still count toward
    the size cap. A limit of 0 disables that dimension. Best-effort throughout.
    """
    if not _downloads_usable():
        return {"removed": 0, "freed_bytes": 0}
    if max_bytes is None:
        max_bytes = max_size_bytes()
    if max_age is None:
        max_age = max_age_seconds()

    now = time.time()
    protect_norm = _norm(protect)
    removed = 0
    freed = 0

    # Size every candidate exactly once up front and reuse the measurement for
    # both eviction passes. Sizing is a full recursive walk, so the old version
    # (measure for the size cap, then measure again inside _remove) walked
    # multi-GB directories twice.
    protected_size = 0
    candidates: list[Path] = []
    for entry in _cache_entries():
        if _norm(entry) == protect_norm or _leased(entry, now):
            protected_size += dir_size(entry)
            continue
        candidates.append(entry)
    sizes = {entry: dir_size(entry) for entry in candidates}

    # Age-based eviction.
    if max_age > 0:
        survivors: list[Path] = []
        for entry in candidates:
            if now - _recency(entry) > max_age:
                f = _remove(entry, known_size=sizes.get(entry))
                if not entry.exists():
                    removed += 1
                    freed += f
                else:
                    survivors.append(entry)
            else:
                survivors.append(entry)
        candidates = survivors

    # Size-based eviction (oldest first) until under the cap.
    if max_bytes > 0:
        total = protected_size + sum(sizes[e] for e in candidates)
        if total > max_bytes:
            for entry in sorted(candidates, key=_recency):
                if total <= max_bytes:
                    break
                f = _remove(entry, known_size=sizes.get(entry))
                if not entry.exists():
                    removed += 1
                    freed += f
                    total -= sizes.get(entry, 0)

    return {"removed": removed, "freed_bytes": freed}


# ---------------------------------------------------------------------------
# Whisper transcript cache
# ---------------------------------------------------------------------------
# Transcribing is the most expensive non-download step in the pipeline: it
# decodes the whole video's audio, uploads it, and pays for a Whisper API call.
# The pipeline's documented recovery path for an interrupted run is "re-run the
# exact same command", and a focused re-run normally lands in a *different*
# out-dir, so neither the extracted audio nor the API result was reused. Caching
# the parsed segments makes every repeat run skip transcription entirely.


def transcript_max_age_seconds() -> float:
    """Age limit for cached transcripts in seconds (0 disables age eviction)."""
    days = _env_float(
        "ANALYZE_VIDEO_TRANSCRIPT_CACHE_MAX_AGE_DAYS", DEFAULT_TRANSCRIPT_MAX_AGE_DAYS
    )
    return max(0.0, days) * 86400.0


def transcript_signature(
    video_path: str | Path,
    *,
    backend: str,
    model: str,
    start_seconds: float | None = None,
    end_seconds: float | None = None,
) -> dict:
    """Fingerprint the inputs that determine a transcription's result.

    Keyed on the source file's size + mtime rather than its path, so the same
    cached download reused from a different out-dir still hits. A swapped or
    re-downloaded video changes the signature and forces a fresh transcription.
    Returns ``{}`` when the video cannot be stat'd, which disables caching.
    """
    try:
        st = os.stat(video_path)
    except OSError:
        return {}
    return {
        "schema": TRANSCRIPT_SCHEMA_VERSION,
        "video_sig": f"{st.st_size}:{st.st_mtime_ns}",
        "backend": backend,
        "model": model,
        "start_seconds": None if start_seconds is None else round(start_seconds, 3),
        "end_seconds": None if end_seconds is None else round(end_seconds, 3),
    }


def transcript_key(signature: dict) -> str | None:
    """Stable cache filename stem for a transcript signature."""
    if not signature:
        return None
    blob = json.dumps(signature, sort_keys=True).encode("utf-8")
    return "tr_" + hashlib.sha256(blob).hexdigest()[:24]


def _transcript_path(signature: dict) -> Path | None:
    key = transcript_key(signature)
    return None if key is None else TRANSCRIPTS_DIR / f"{key}.json"


def read_transcript(signature: dict) -> list[dict] | None:
    """Cached segments for `signature`, or None on any miss.

    The stored signature is re-checked so a hash collision or an older cache
    format can never feed the wrong transcript into a run.
    """
    path = _transcript_path(signature)
    if path is None:
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(payload, dict) or payload.get("signature") != signature:
        return None
    segments = payload.get("segments")
    if not isinstance(segments, list) or not segments:
        return None
    # Refresh recency so an actively reused transcript survives age eviction.
    try:
        os.utime(path, None)
    except OSError:
        pass
    return segments


def write_transcript(signature: dict, segments: list[dict]) -> Path | None:
    """Persist segments for `signature`. Best-effort; returns the path or None.

    Written via a temp file + replace so a run killed mid-write can't leave a
    truncated JSON file that a later run would have to parse and discard.
    """
    path = _transcript_path(signature)
    if path is None or not segments:
        return None
    payload = {
        "signature": signature,
        "created_at": time.time(),
        "segment_count": len(segments),
        "segments": segments,
    }
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        TRANSCRIPTS_DIR.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return None
    return path


def _transcript_entries() -> list[Path]:
    """Cached transcript files (`tr_*.json` directly under TRANSCRIPTS_DIR)."""
    try:
        if not TRANSCRIPTS_DIR.is_dir() or TRANSCRIPTS_DIR.is_symlink():
            return []
        return [
            p
            for p in TRANSCRIPTS_DIR.glob("tr_*.json")
            if p.is_file() and not p.is_symlink()
        ]
    except OSError:
        return []


def prune_transcripts(max_age: float | None = None) -> dict:
    """Drop cached transcripts older than the age limit. Best-effort."""
    if max_age is None:
        max_age = transcript_max_age_seconds()
    if max_age <= 0:
        return {"removed": 0, "freed_bytes": 0}
    now = time.time()
    removed = 0
    freed = 0
    for path in _transcript_entries():
        try:
            stat = path.stat()
            if now - stat.st_mtime <= max_age:
                continue
            size = stat.st_size
            path.unlink()
        except OSError:
            continue
        removed += 1
        freed += size
    return {"removed": removed, "freed_bytes": freed}


def clear_transcripts() -> dict:
    """Remove every cached transcript. Returns removed/freed/failed counts."""
    removed = 0
    freed = 0
    failed = 0
    for path in _transcript_entries():
        try:
            size = path.stat().st_size
        except OSError:
            size = 0
        try:
            path.unlink()
        except OSError:
            failed += 1
            continue
        removed += 1
        freed += size
    return {"removed": removed, "freed_bytes": freed, "failed": failed}
