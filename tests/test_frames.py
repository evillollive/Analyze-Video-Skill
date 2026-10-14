"""Tests for frames.py resumable extraction (signature-keyed dirs + --force).

Frames live in a per-signature subdirectory of the passed out_dir. Identical
inputs reuse/overwrite the same subdir (resume); any changed input lands in a
fresh subdir, so a prior run's frames can never pollute the current one and no
cross-session deletion is ever required.
"""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import frames as frames_mod
from frames import extract, _extract_signature, _signature_key


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


class TestKeyframeIntervalProbe:
    """probe_keyframe_interval reads packet headers; it never decodes."""

    def _probe(self, monkeypatch, stdout, returncode=0):
        monkeypatch.setattr(frames_mod, "_resolve_tool", lambda name: "/usr/bin/ffprobe")

        class _R:
            pass

        r = _R()
        r.returncode = returncode
        r.stdout = stdout
        r.stderr = ""
        monkeypatch.setattr(frames_mod.subprocess, "run", lambda *a, **k: r)
        return frames_mod.probe_keyframe_interval("video.mp4")

    def test_median_of_keyframe_gaps(self, monkeypatch):
        stdout = "\n".join([
            "0.000000,K__",
            "0.040000,___",
            "4.000000,K__",
            "8.000000,K__",
            "12.000000,K__",
        ])
        assert self._probe(monkeypatch, stdout) == 4.0

    def test_ignores_na_timestamps(self, monkeypatch):
        stdout = "\n".join(["N/A,K__", "0.000000,K__", "2.000000,K__"])
        assert self._probe(monkeypatch, stdout) == 2.0

    def test_one_irregular_gap_does_not_skew_median(self, monkeypatch):
        stdout = "\n".join([
            "0.000000,K__",
            "2.000000,K__",
            "4.000000,K__",
            "4.100000,K__",  # scene-cut keyframe
            "6.000000,K__",
            "8.000000,K__",
        ])
        assert self._probe(monkeypatch, stdout) == 2.0

    def test_none_when_too_few_keyframes(self, monkeypatch):
        assert self._probe(monkeypatch, "0.000000,K__") is None

    def test_none_when_ffprobe_fails(self, monkeypatch):
        assert self._probe(monkeypatch, "", returncode=1) is None

    def test_none_when_ffprobe_missing(self, monkeypatch):
        monkeypatch.setattr(frames_mod, "_resolve_tool", lambda name: None)
        assert frames_mod.probe_keyframe_interval("video.mp4") is None


class TestCanSampleKeyframesOnly:
    def test_sparse_sampling_allowed(self):
        # 15s between frames against a 4s keyframe interval: 15 >= 2 * 4.
        assert frames_mod.can_sample_keyframes_only(1 / 15, 4.0) is True

    def test_dense_sampling_rejected(self):
        # 1s between frames against a 10s keyframe interval would repeat frames.
        assert frames_mod.can_sample_keyframes_only(1.0, 10.0) is False

    def test_exact_safety_boundary_allowed(self):
        assert frames_mod.can_sample_keyframes_only(1 / 8, 4.0) is True

    def test_just_under_boundary_rejected(self):
        assert frames_mod.can_sample_keyframes_only(1 / 7.9, 4.0) is False

    def test_unknown_interval_rejected(self):
        assert frames_mod.can_sample_keyframes_only(0.1, None) is False

    def test_nonsense_inputs_rejected(self):
        assert frames_mod.can_sample_keyframes_only(0.0, 4.0) is False
        assert frames_mod.can_sample_keyframes_only(0.1, 0.0) is False


class TestKeyframeOnlyExtraction:
    def _run(self, tmp_path, monkeypatch, *, keyframes_only, frames_written=2):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        base = tmp_path / "frames"
        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        monkeypatch.setattr(frames_mod, "_resolve_tool", lambda name: "/usr/bin/ffmpeg")

        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            target_dir = Path(cmd[-1]).parent
            target_dir.mkdir(parents=True, exist_ok=True)
            count = frames_written if len(calls) == 1 else 2
            for i in range(1, count + 1):
                (target_dir / f"frame_{i:04d}.jpg").write_bytes(b"jpeg")
            return _FakeResult()

        monkeypatch.setattr(frames_mod.subprocess, "run", fake_run)
        result, frames_dir = extract(
            str(video), base, fps=0.1, resolution=512, max_frames=120,
            start_seconds=0.0, end_seconds=10.0, keyframes_only=keyframes_only,
        )
        return calls, result, frames_dir

    def test_skip_frame_precedes_input(self, tmp_path, monkeypatch):
        calls, _, _ = self._run(tmp_path, monkeypatch, keyframes_only=True)
        cmd = calls[0]
        assert "-skip_frame" in cmd
        assert cmd[cmd.index("-skip_frame") + 1] == "nokey"
        # Decoder options only apply when they come before -i.
        assert cmd.index("-skip_frame") < cmd.index("-i")

    def test_absent_by_default(self, tmp_path, monkeypatch):
        calls, _, _ = self._run(tmp_path, monkeypatch, keyframes_only=False)
        assert "-skip_frame" not in calls[0]

    def test_signature_unchanged_for_full_decode(self, tmp_path):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 10)
        baseline = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0)
        explicit = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0, False)
        # Pre-existing cache dirs must stay valid.
        assert baseline == explicit
        assert "keyframes_only" not in baseline

    def test_signature_diverges_for_keyframe_mode(self, tmp_path):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 10)
        full = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0, False)
        keyed = _extract_signature(str(video), 1.0, 512, 120, 0.0, 10.0, True)
        assert _signature_key(full) != _signature_key(keyed)

    def test_falls_back_to_full_decode_when_no_frames(self, tmp_path, monkeypatch):
        calls, result, _ = self._run(
            tmp_path, monkeypatch, keyframes_only=True, frames_written=0
        )
        assert len(calls) == 2
        assert "-skip_frame" in calls[0]
        assert "-skip_frame" not in calls[1]
        assert len(result) == 2
