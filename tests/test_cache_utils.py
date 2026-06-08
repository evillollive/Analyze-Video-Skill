"""Tests for cache_utils.py download-cache maintenance."""
import os
import sys
import time
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import cache_utils


def _make_entry(downloads: Path, key: str, size: int = 1024, age_days: float = 0.0) -> Path:
    entry = downloads / key
    entry.mkdir(parents=True, exist_ok=True)
    (entry / "video.mp4").write_bytes(b"x" * size)
    when = time.time() - age_days * 86400.0
    marker = entry / cache_utils.LAST_USED_MARKER
    marker.write_text(str(when))
    os.utime(marker, (when, when))
    os.utime(entry, (when, when))
    return entry


HEX16 = "0123456789abcdef"


class TestDirSize:
    def test_sums_file_sizes(self, tmp_path):
        (tmp_path / "a").write_bytes(b"x" * 10)
        (tmp_path / "b").write_bytes(b"y" * 5)
        assert cache_utils.dir_size(tmp_path) == 15


class TestEntryValidation:
    def test_only_hex16_dirs_are_entries(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        good = downloads / HEX16
        good.mkdir()
        (downloads / "not-a-key").mkdir()        # wrong shape
        (downloads / "node_modules").mkdir()      # must be ignored
        (downloads / "video.mp4").write_bytes(b"x")  # a file, not a dir
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)
        entries = cache_utils._cache_entries()
        assert entries == [good]

    def test_symlinked_downloads_dir_is_refused(self, tmp_path, monkeypatch):
        real = tmp_path / "real"
        real.mkdir()
        (real / HEX16).mkdir()
        link = tmp_path / "downloads"
        link.symlink_to(real, target_is_directory=True)
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", link)
        assert cache_utils._cache_entries() == []
        assert cache_utils.prune_downloads() == {"removed": 0, "freed_bytes": 0}


class TestClearDownloads:
    def test_removes_only_cache_entries(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        _make_entry(downloads, HEX16)
        _make_entry(downloads, "f" * 16)
        keep = downloads / "node_modules"
        keep.mkdir()
        (keep / "docx").mkdir()
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)

        result = cache_utils.clear_downloads()
        assert result["removed"] == 2
        assert result["freed_bytes"] > 0
        assert not (downloads / HEX16).exists()
        assert not (downloads / ("f" * 16)).exists()
        # The docx node_modules sibling-shaped dir is never touched.
        assert (keep / "docx").exists()

    def test_skips_leased_entry(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        free = _make_entry(downloads, "a" * 16)
        busy = _make_entry(downloads, "b" * 16)
        (busy / cache_utils.IN_USE_MARKER).write_text(str(time.time()))
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)

        result = cache_utils.clear_downloads()
        assert result["removed"] == 1
        assert result["skipped"] == 1
        assert not free.exists()
        assert busy.exists()


class TestPruneAge:
    def test_evicts_old_keeps_fresh(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        old = _make_entry(downloads, "a" * 16, age_days=30)
        fresh = _make_entry(downloads, "b" * 16, age_days=1)
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)

        # 14-day age cap, disable size cap.
        result = cache_utils.prune_downloads(max_bytes=0, max_age=14 * 86400)
        assert not old.exists()
        assert fresh.exists()
        assert result["removed"] == 1

    def test_protects_current_entry_even_if_old(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        old = _make_entry(downloads, "a" * 16, age_days=30)
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)
        cache_utils.prune_downloads(protect=old, max_bytes=0, max_age=14 * 86400)
        assert old.exists()

    def test_protects_leased_entry(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        leased = _make_entry(downloads, "a" * 16, age_days=30)
        # A fresh in-use lease from a "concurrent run".
        (leased / cache_utils.IN_USE_MARKER).write_text(str(time.time()))
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)
        cache_utils.prune_downloads(max_bytes=0, max_age=14 * 86400)
        assert leased.exists()


class TestPruneSize:
    def test_evicts_oldest_until_under_cap(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        # Three 1000-byte entries; cap at 2500 so the oldest must go.
        oldest = _make_entry(downloads, "a" * 16, size=1000, age_days=10)
        middle = _make_entry(downloads, "b" * 16, size=1000, age_days=5)
        newest = _make_entry(downloads, "c" * 16, size=1000, age_days=1)
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)

        cache_utils.prune_downloads(max_bytes=2500, max_age=0)
        assert not oldest.exists()
        assert middle.exists()
        assert newest.exists()

    def test_never_evicts_protected_even_over_cap(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        downloads.mkdir()
        cur = _make_entry(downloads, "a" * 16, size=5000, age_days=1)
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)
        # Cap below the single protected entry's size: it must survive.
        cache_utils.prune_downloads(protect=cur, max_bytes=1000, max_age=0)
        assert cur.exists()


class TestBeginEndUse:
    def test_begin_use_writes_markers(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)
        entry = downloads / HEX16
        cache_utils.begin_use(entry)
        assert (entry / cache_utils.IN_USE_MARKER).exists()
        assert (entry / cache_utils.LAST_USED_MARKER).exists()

    def test_end_use_releases_lease(self, tmp_path, monkeypatch):
        downloads = tmp_path / "downloads"
        monkeypatch.setattr(cache_utils, "DOWNLOADS_DIR", downloads)
        entry = downloads / HEX16
        cache_utils.begin_use(entry)
        cache_utils.end_use(entry)
        assert not (entry / cache_utils.IN_USE_MARKER).exists()
        assert (entry / cache_utils.LAST_USED_MARKER).exists()


class TestEnvOverrides:
    def test_zero_disables_limits(self, monkeypatch):
        monkeypatch.setenv("ANALYZE_VIDEO_CACHE_MAX_AGE_DAYS", "0")
        monkeypatch.setenv("ANALYZE_VIDEO_CACHE_MAX_GB", "0")
        assert cache_utils.max_age_seconds() == 0
        assert cache_utils.max_size_bytes() == 0

    def test_reads_overrides(self, monkeypatch):
        monkeypatch.setenv("ANALYZE_VIDEO_CACHE_MAX_AGE_DAYS", "7")
        monkeypatch.setenv("ANALYZE_VIDEO_CACHE_MAX_GB", "2")
        assert cache_utils.max_age_seconds() == 7 * 86400
        assert cache_utils.max_size_bytes() == 2 * (1024 ** 3)
