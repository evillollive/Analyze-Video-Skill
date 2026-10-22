"""Tests for frames.py resumable extraction (signature-keyed dirs + --force).

Frames live in a per-signature subdirectory of the passed out_dir. Identical
inputs reuse/overwrite the same subdir (resume); any changed input lands in a
fresh subdir, so a prior run's frames can never pollute the current one and no
cross-session deletion is ever required.
"""
import json
import pytest
import sys

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


class TestPlanSplit:
    """Sub-range planning for parallel extraction."""

    def test_no_split_for_single_job(self):
        assert frames_mod.plan_split(0.5, 100, 0.0, 600.0, 1) is None

    def test_no_split_without_target_frames(self):
        assert frames_mod.plan_split(0.5, None, 0.0, 600.0, 4) is None

    def test_no_split_for_open_ended_range(self):
        assert frames_mod.plan_split(0.5, 100, 0.0, None, 4) is None

    def test_no_split_for_short_range(self):
        assert frames_mod.plan_split(2.0, 60, 0.0, 30.0, 4) is None

    def test_no_split_when_frames_per_job_too_small(self):
        assert frames_mod.plan_split(0.1, 10, 0.0, 100.0, 4) is None

    def test_slots_tile_the_fps_grid_without_gaps(self):
        fps = 100 / 600
        plan = frames_mod.plan_split(fps, 100, 0.0, 600.0, 4)
        assert [(slot[2], slot[3]) for slot in plan] == [
            (1, 25), (26, 25), (51, 25), (76, 25)
        ]
        # Every slot starts exactly on the global grid the manifest records.
        for start, _end, start_number, _count in plan:
            assert start == pytest.approx((start_number - 1) / fps)
        # Slots are contiguous: each ends where the next begins.
        for earlier, later in zip(plan, plan[1:]):
            assert earlier[1] == pytest.approx(later[0])
        assert plan[-1][1] == pytest.approx(600.0)

    def test_respects_start_offset_and_end_clamp(self):
        fps = 0.5
        plan = frames_mod.plan_split(fps, 50, 300.0, 400.0, 2)
        assert plan[0][0] == pytest.approx(300.0)
        assert plan[-1][1] <= 400.0

    def test_uneven_split_keeps_total_frame_count(self):
        plan = frames_mod.plan_split(0.5, 101, 0.0, 300.0, 4)
        assert sum(slot[3] for slot in plan) == 101


class TestFramesAreContiguous:
    def test_contiguous(self, tmp_path):
        files = [tmp_path / f"frame_{i:04d}.jpg" for i in range(1, 4)]
        assert frames_mod._frames_are_contiguous(files)

    def test_gap_is_rejected(self, tmp_path):
        files = [tmp_path / "frame_0001.jpg", tmp_path / "frame_0003.jpg"]
        assert not frames_mod._frames_are_contiguous(files)

    def test_not_starting_at_one_is_rejected(self, tmp_path):
        assert not frames_mod._frames_are_contiguous([tmp_path / "frame_0002.jpg"])


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


class TestParallelExtract:
    def _run(self, tmp_path, monkeypatch, *, drop_slot=None):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 1000)
        calls = []
        monkeypatch.setattr(frames_mod.shutil, "which", lambda name: "/usr/bin/ffmpeg")

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            target_dir = Path(cmd[-1]).parent
            target_dir.mkdir(parents=True, exist_ok=True)
            count = int(cmd[cmd.index("-frames:v") + 1])
            first = int(cmd[cmd.index("-start_number") + 1]) if "-start_number" in cmd else 1
            if first == drop_slot:
                count -= 1
            for offset in range(count):
                (target_dir / f"frame_{first + offset:04d}.jpg").write_bytes(b"jpeg")
            return _FakeResult()

        monkeypatch.setattr(frames_mod.subprocess, "run", fake_run)
        frames, frame_dir = extract(
            str(video), tmp_path / "frames", fps=100 / 600, resolution=512,
            max_frames=100, start_seconds=0.0, end_seconds=600.0,
            jobs=4, target_frames=100,
        )
        return calls, frames, frame_dir

    def test_fans_out_and_returns_ordered_frames(self, tmp_path, monkeypatch):
        calls, frames, _ = self._run(tmp_path, monkeypatch)
        assert len(calls) == 4
        assert len(frames) == 100
        assert [frame["index"] for frame in frames] == list(range(1, 101))
        assert frames[25]["timestamp_seconds"] == 150.0

    def test_gap_falls_back_to_single_pass(self, tmp_path, monkeypatch):
        calls, frames, _ = self._run(tmp_path, monkeypatch, drop_slot=26)
        assert len(calls) == 5
        assert "-start_number" not in calls[-1]
        assert [frame["index"] for frame in frames] == list(range(1, 101))

    def test_job_count_does_not_change_resume_signature(self, tmp_path, monkeypatch):
        calls, _, frame_dir = self._run(tmp_path, monkeypatch)

        def fail_if_rerun(*args, **kwargs):
            raise AssertionError("unchanged inputs should resume without ffmpeg")

        monkeypatch.setattr(frames_mod.subprocess, "run", fail_if_rerun)
        video = tmp_path / "video.mp4"
        frames, resumed_dir = extract(
            str(video), tmp_path / "frames", fps=100 / 600, resolution=512,
            max_frames=100, start_seconds=0.0, end_seconds=600.0,
            jobs=1, target_frames=100,
        )
        assert len(calls) == 4
        assert resumed_dir == frame_dir
        assert len(frames) == 100
