"""Tests for frame selection (select_frames.py)."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from select_frames import default_n, _step_indices, select


class TestDefaultN:
    def test_short_video(self):
        assert default_n(60) == 6

    def test_medium_video(self):
        assert default_n(200) == 10

    def test_long_video(self):
        assert default_n(600) == 15

    def test_very_long_video(self):
        assert default_n(1200) == 20


class TestStepIndices:
    def test_basic_spread(self):
        result = _step_indices(10, 3)
        assert len(result) == 3
        # Should be roughly evenly spread
        assert result[0] >= 1
        assert result[-1] <= 10

    def test_n_exceeds_total(self):
        result = _step_indices(3, 10)
        assert result == [1, 2, 3]

    def test_empty(self):
        assert _step_indices(0, 5) == []
        assert _step_indices(5, 0) == []

    def test_single_frame(self):
        result = _step_indices(1, 1)
        assert result == [1]

    def test_no_duplicates(self):
        result = _step_indices(100, 10)
        assert len(result) == len(set(result))


def _make_frame(idx, ts):
    return {
        "index": idx,
        "absolute_path": f"/tmp/frame_{idx:04d}.jpg",
        "timestamp_seconds": ts,
        "timestamp_formatted": f"{int(ts)//60:02d}:{int(ts)%60:02d}",
    }


def _make_manifest(chunks, duration=120.0):
    return {"duration_seconds": duration, "chunks": chunks}


class TestSelect:
    def test_single_chunk(self):
        frames = [_make_frame(i, i * 2.0) for i in range(20)]
        chunk = {"index": 0, "duration_seconds": 40.0, "frames": frames}
        manifest = _make_manifest([chunk], duration=40.0)
        picks = select(manifest, 5)
        assert len(picks) == 5
        assert all(p["chunk_index"] == 0 for p in picks)

    def test_multi_chunk_distribution(self):
        """Each chunk should get at least 1 frame."""
        chunks = []
        for ci in range(3):
            frames = [_make_frame(ci * 10 + i, ci * 60 + i * 6.0) for i in range(10)]
            chunks.append({"index": ci, "duration_seconds": 60.0, "frames": frames})
        manifest = _make_manifest(chunks, duration=180.0)
        picks = select(manifest, 6)
        chunk_indices = {p["chunk_index"] for p in picks}
        assert chunk_indices == {0, 1, 2}

    def test_empty_manifest(self):
        assert select({"chunks": []}, 5) == []
        assert select({}, 5) == []

    def test_total_frames_match_n(self):
        frames = [_make_frame(i, i * 1.0) for i in range(50)]
        chunk = {"index": 0, "duration_seconds": 50.0, "frames": frames}
        manifest = _make_manifest([chunk], duration=50.0)
        picks = select(manifest, 10)
        assert len(picks) == 10
