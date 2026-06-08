#!/usr/bin/env python3
"""Download-cache location and maintenance for the analyze-video skill.

The pipeline caches each downloaded source video under
`~/.cache/analyze-video/downloads/<sha256(url)[:16]>/` and reuses it across runs.
Left unmanaged that grows without bound (full-size videos), so this module adds
age- and size-based eviction plus a manual clear.

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

import os
import re
import shutil
import time
from pathlib import Path


CACHE_ROOT = Path.home() / ".cache" / "analyze-video"
DOWNLOADS_DIR = CACHE_ROOT / "downloads"

# Cache keys are sha256(url)[:16]; only dirs matching this are ever deleted.
ENTRY_NAME_RE = re.compile(r"^[0-9a-f]{16}$")

LAST_USED_MARKER = ".last_used"
IN_USE_MARKER = ".in_use"
# A run's lease is honored for this long; stale leases (crashed runs) expire.
LEASE_TTL_SECONDS = 6 * 3600

DEFAULT_MAX_AGE_DAYS = 14.0
DEFAULT_MAX_SIZE_GB = 5.0


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


def _remove(entry: Path) -> int:
    """rmtree an entry, returning bytes freed (best-effort)."""
    before = dir_size(entry)
    try:
        shutil.rmtree(entry)
    except OSError:
        return before - dir_size(entry)
    return before


def clear_downloads() -> dict:
    """Remove cached downloads. Returns removed/freed/failed/skipped counts.

    Entries with a fresh `.in_use` lease (a concurrent run actively using them)
    are skipped so clearing the cache can't crash an in-progress analysis.
    """
    now = time.time()
    entries = [e for e in _cache_entries() if not _leased(e, now)]
    before = sum(dir_size(e) for e in entries)
    skipped = len([e for e in _cache_entries() if _leased(e, now)])
    failed = 0
    for entry in entries:
        try:
            shutil.rmtree(entry)
        except OSError:
            failed += 1
    after = sum(dir_size(e) for e in entries if e.exists())
    return {
        "removed": len(entries) - failed,
        "freed_bytes": max(0, before - after),
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

    protected_size = 0
    candidates: list[Path] = []
    for entry in _cache_entries():
        if _norm(entry) == protect_norm or _leased(entry, now):
            protected_size += dir_size(entry)
            continue
        candidates.append(entry)

    # Age-based eviction.
    if max_age > 0:
        survivors: list[Path] = []
        for entry in candidates:
            if now - _recency(entry) > max_age:
                f = _remove(entry)
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
        sizes = {e: dir_size(e) for e in candidates}
        total = protected_size + sum(sizes.values())
        if total > max_bytes:
            for entry in sorted(candidates, key=_recency):
                if total <= max_bytes:
                    break
                f = _remove(entry)
                if not entry.exists():
                    removed += 1
                    freed += f
                    total -= sizes.get(entry, 0)

    return {"removed": removed, "freed_bytes": freed}
