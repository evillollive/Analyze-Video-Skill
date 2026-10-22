"""Tests for frames.py resumable extraction (signature-keyed dirs + --force).

Frames live in a per-signature subdirectory of the passed out_dir. Identical
inputs reuse/overwrite the same subdir (resume); any changed input lands in a
fresh subdir, so a prior run's frames can never pollute the current one and no
cross-session deletion is ever required.
"""
import json
import sys

import pytest
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import frames as frames_mod
from frames import extract, make_contact_sheet, _extract_signature, _signature_key


class _FakeResult:
    returncode = 0
    stderr = ""


def _seed_frames(sig_dir: Path, count: int):
    sig_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        (sig_dir / f"frame_{i:04d}.jpg").write_bytes(b"jpeg")


def _dir_for(video: Path, base: Path, *, fps, resolution=512, max_frames=120,
             start=0.0, end=10.0) -> Path:
    sig = _extract_signature(str(video), fps, resolution, max_frames, start, end)
    return base / _signature_key(sig)


class TestExtractResume:
    def test_reuses_frames_when_signature_matches(self, tmp_path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        base = tmp_path / "frames"
        sig_dir = _dir_for(video, base, fps=1.0)
        _seed_frames(sig_dir, 2)
        sig = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0)
        (sig_dir / ".extract.json").write_text(json.dumps(sig))

        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

        def boom(*args, **kwargs):
            raise AssertionError("ffmpeg must not run when resuming")

        monkeypatch.setattr(frames_mod.subprocess, "run", boom)

        result, frames_dir = extract(
            str(video), base, fps=1.0, resolution=512, max_frames=120,
            start_seconds=0.0, end_seconds=10.0,
        )
        assert frames_dir == sig_dir
        assert [f["index"] for f in result] == [1, 2]
        assert result[0]["timestamp_seconds"] == 0.0
        assert result[1]["timestamp_seconds"] == 1.0

    def test_reextracts_into_fresh_dir_when_signature_differs(self, tmp_path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        base = tmp_path / "frames"
        # A completed prior run at fps=0.5 leaves frames in its own subdir.
        old_dir = _dir_for(video, base, fps=0.5)
        _seed_frames(old_dir, 5)
        old_sig = _extract_signature(str(video), 0.5, 512, 120, 0.0, 10.0)
        (old_dir / ".extract.json").write_text(json.dumps(old_sig))

        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        ran = {"count": 0}

        def fake_run(cmd, **kwargs):
            ran["count"] += 1
            target_dir = Path(cmd[-1]).parent
            target_dir.mkdir(parents=True, exist_ok=True)
            for i in range(1, 3):
                (target_dir / f"frame_{i:04d}.jpg").write_bytes(b"new")
            return _FakeResult()

        monkeypatch.setattr(frames_mod.subprocess, "run", fake_run)

        result, frames_dir = extract(
            str(video), base, fps=1.0, resolution=512, max_frames=120,
            start_seconds=0.0, end_seconds=10.0,
        )
        assert ran["count"] == 1
        # New extraction landed in a *different* directory than the stale one.
        assert frames_dir != old_dir
        assert len(result) == 2  # only the fresh frames, no pollution from old_dir
        # Stale dir is untouched (never deleted), proving no cross-session delete.
        assert len(list(old_dir.glob("frame_*.jpg"))) == 5
        new_sig = json.loads((frames_dir / ".extract.json").read_text())
        assert new_sig["fps"] == 1.0

    def test_force_reextracts_even_on_match(self, tmp_path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        base = tmp_path / "frames"
        sig_dir = _dir_for(video, base, fps=1.0)
        _seed_frames(sig_dir, 2)
        sig = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0)
        (sig_dir / ".extract.json").write_text(json.dumps(sig))

        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        ran = {"count": 0}

        def fake_run(cmd, **kwargs):
            ran["count"] += 1
            target_dir = Path(cmd[-1]).parent
            (target_dir / "frame_0001.jpg").write_bytes(b"forced")
            return _FakeResult()

        monkeypatch.setattr(frames_mod.subprocess, "run", fake_run)

        result, frames_dir = extract(
            str(video), base, fps=1.0, resolution=512, max_frames=120,
            start_seconds=0.0, end_seconds=10.0, force=True,
        )
        assert ran["count"] == 1
        assert frames_dir == sig_dir
        assert len(result) == 1

    def test_unlink_failure_does_not_crash(self, tmp_path, monkeypatch):
        """A sandbox that forbids deletion must not crash the run (issue #1)."""
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        base = tmp_path / "frames"
        sig_dir = _dir_for(video, base, fps=1.0)
        _seed_frames(sig_dir, 2)
        sig = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0)
        (sig_dir / ".extract.json").write_text(json.dumps(sig))

        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

        real_unlink = Path.unlink

        def deny_unlink(self, *a, **k):
            raise PermissionError("Operation not permitted")

        monkeypatch.setattr(Path, "unlink", deny_unlink)

        def fake_run(cmd, **kwargs):
            target_dir = Path(cmd[-1]).parent
            (target_dir / "frame_0001.jpg").write_bytes(b"forced")
            (target_dir / "frame_0002.jpg").write_bytes(b"forced")
            return _FakeResult()

        monkeypatch.setattr(frames_mod.subprocess, "run", fake_run)

        try:
            # force=True drives the unlink path; it must swallow PermissionError.
            result, frames_dir = extract(
                str(video), base, fps=1.0, resolution=512, max_frames=120,
                start_seconds=0.0, end_seconds=10.0, force=True,
            )
        finally:
            monkeypatch.setattr(Path, "unlink", real_unlink)
        assert len(result) == 2


class TestContactSheetReuse:
    """A sheet whose inputs are unchanged is reused instead of re-tiled.

    ``extract`` already resumes by reusing cached frames; without this the run
    still paid a full ffmpeg decode-and-tile per chunk to rebuild sheets that
    were already on disk.
    """

    def _build(self, tmp_path, monkeypatch, *, frame_count=4, **kwargs):
        frames_dir = tmp_path / "frames" / "fr_abc123"
        _seed_frames(frames_dir, frame_count)
        out_path = tmp_path / "contact_sheet.jpg"
        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        ran = {"count": 0}

        def fake_run(cmd, **kw):
            ran["count"] += 1
            Path(cmd[-1]).write_bytes(b"sheet")
            return _FakeResult()

        monkeypatch.setattr(frames_mod.subprocess, "run", fake_run)
        return frames_dir, out_path, ran

    def test_reuses_sheet_when_inputs_unchanged(self, tmp_path, monkeypatch):
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch)
        make_contact_sheet(frames_dir, out_path, frame_count=4)
        assert ran["count"] == 1
        make_contact_sheet(frames_dir, out_path, frame_count=4)
        assert ran["count"] == 1  # second call served from the marker

    def test_retiles_when_tiling_params_change(self, tmp_path, monkeypatch):
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch)
        make_contact_sheet(frames_dir, out_path, frame_count=4, tile_width=200)
        make_contact_sheet(frames_dir, out_path, frame_count=4, tile_width=160)
        assert ran["count"] == 2

    def test_retiles_when_frame_count_changes(self, tmp_path, monkeypatch):
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch)
        make_contact_sheet(frames_dir, out_path, frame_count=4)
        _seed_frames(frames_dir, 6)
        make_contact_sheet(frames_dir, out_path, frame_count=6)
        assert ran["count"] == 2

    def test_force_retiles(self, tmp_path, monkeypatch):
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch)
        make_contact_sheet(frames_dir, out_path, frame_count=4)
        make_contact_sheet(frames_dir, out_path, frame_count=4, force=True)
        assert ran["count"] == 2

    def test_retiles_when_sheet_is_missing(self, tmp_path, monkeypatch):
        """The marker alone never vouches for a sheet: the file must exist."""
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch)
        make_contact_sheet(frames_dir, out_path, frame_count=4)
        out_path.unlink()
        make_contact_sheet(frames_dir, out_path, frame_count=4)
        assert ran["count"] == 2

    def test_retiles_when_sheet_is_truncated(self, tmp_path, monkeypatch):
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch)
        make_contact_sheet(frames_dir, out_path, frame_count=4)
        out_path.write_bytes(b"")  # zero-byte sheet from a killed run
        make_contact_sheet(frames_dir, out_path, frame_count=4)
        assert ran["count"] == 2

    def test_interrupted_tile_leaves_no_vouching_marker(self, tmp_path, monkeypatch):
        """A run killed mid-tile must not leave a marker for a partial sheet."""
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch)
        make_contact_sheet(frames_dir, out_path, frame_count=4)

        def die(cmd, **kw):
            Path(cmd[-1]).write_bytes(b"partial")
            raise KeyboardInterrupt

        monkeypatch.setattr(frames_mod.subprocess, "run", die)
        try:
            make_contact_sheet(frames_dir, out_path, frame_count=4, tile_width=160)
        except KeyboardInterrupt:
            pass
        assert not frames_mod._sheet_sig_path(out_path).exists()

    def test_marker_write_failure_does_not_crash(self, tmp_path, monkeypatch):
        """Read-only sandboxes just lose the reuse benefit, they don't fail."""
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch)
        real_write = Path.write_text

        def deny_write(self, *a, **k):
            if self.name.endswith(".sheet.json"):
                raise PermissionError("Operation not permitted")
            return real_write(self, *a, **k)

        monkeypatch.setattr(Path, "write_text", deny_write)
        assert make_contact_sheet(frames_dir, out_path, frame_count=4) == out_path

    def test_no_frames_still_raises(self, tmp_path, monkeypatch):
        frames_dir, out_path, ran = self._build(tmp_path, monkeypatch, frame_count=0)
        with pytest.raises(SystemExit):
            make_contact_sheet(frames_dir, out_path)
        assert ran["count"] == 0
