"""Tests for frames.py resumable extraction (signature reuse + --force)."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import frames as frames_mod
from frames import extract, _extract_signature


class _FakeResult:
    returncode = 0
    stderr = ""


def _seed_frames(out_dir: Path, count: int):
    out_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, count + 1):
        (out_dir / f"frame_{i:04d}.jpg").write_bytes(b"jpeg")


class TestExtractResume:
    def test_reuses_frames_when_signature_matches(self, tmp_path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        out_dir = tmp_path / "frames"
        _seed_frames(out_dir, 2)
        sig = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0)
        (out_dir / ".extract.json").write_text(json.dumps(sig))

        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

        def boom(*args, **kwargs):
            raise AssertionError("ffmpeg must not run when resuming")

        monkeypatch.setattr(frames_mod.subprocess, "run", boom)

        result = extract(
            str(video), out_dir, fps=1.0, resolution=512, max_frames=120,
            start_seconds=0.0, end_seconds=10.0,
        )
        assert [f["index"] for f in result] == [1, 2]
        assert result[0]["timestamp_seconds"] == 0.0
        assert result[1]["timestamp_seconds"] == 1.0

    def test_reextracts_when_signature_differs(self, tmp_path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        out_dir = tmp_path / "frames"
        _seed_frames(out_dir, 1)
        # Stale signature from a different fps.
        old_sig = _extract_signature(str(video), 0.5, 512, 120, 0.0, 10.0)
        (out_dir / ".extract.json").write_text(json.dumps(old_sig))

        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        ran = {"count": 0}

        def fake_run(cmd, **kwargs):
            ran["count"] += 1
            # Simulate ffmpeg writing two fresh frames.
            for i in range(1, 3):
                (out_dir / f"frame_{i:04d}.jpg").write_bytes(b"new")
            return _FakeResult()

        monkeypatch.setattr(frames_mod.subprocess, "run", fake_run)

        result = extract(
            str(video), out_dir, fps=1.0, resolution=512, max_frames=120,
            start_seconds=0.0, end_seconds=10.0,
        )
        assert ran["count"] == 1
        assert len(result) == 2
        # Signature is refreshed to the new parameters.
        new_sig = json.loads((out_dir / ".extract.json").read_text())
        assert new_sig["fps"] == 1.0

    def test_force_reextracts_even_on_match(self, tmp_path, monkeypatch):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        out_dir = tmp_path / "frames"
        _seed_frames(out_dir, 2)
        sig = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0)
        (out_dir / ".extract.json").write_text(json.dumps(sig))

        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        ran = {"count": 0}

        def fake_run(cmd, **kwargs):
            ran["count"] += 1
            (out_dir / "frame_0001.jpg").write_bytes(b"forced")
            return _FakeResult()

        monkeypatch.setattr(frames_mod.subprocess, "run", fake_run)

        result = extract(
            str(video), out_dir, fps=1.0, resolution=512, max_frames=120,
            start_seconds=0.0, end_seconds=10.0, force=True,
        )
        assert ran["count"] == 1
        assert len(result) == 1
