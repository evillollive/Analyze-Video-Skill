"""Tests for frame selection (select_frames.py)."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import json

import pytest

import select_frames
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


from select_frames import load_manifest_for_selection  # noqa: E402


def _full_manifest():
    return {
        "duration_seconds": 20.0,
        "chunks": [
            {
                "index": 1, "duration_seconds": 20.0,
                "frames": [
                    {"index": 1, "absolute_path": "/f/1.jpg",
                     "timestamp_seconds": 1.0, "timestamp_formatted": "00:01"},
                    {"index": 2, "absolute_path": "/f/2.jpg",
                     "timestamp_seconds": 10.0, "timestamp_formatted": "00:10"},
                ],
            }
        ],
    }


def _lite_manifest(manifest_path):
    return {
        "duration_seconds": 20.0,
        "manifest_path": manifest_path,
        "chunks": [{"index": 1, "duration_seconds": 20.0, "frame_count": 2}],
    }


class TestLoadManifestForSelection:
    def test_full_manifest_returned_as_is(self, tmp_path):
        p = tmp_path / "manifest.json"
        p.write_text(json.dumps(_full_manifest()))
        loaded = load_manifest_for_selection(p)
        assert loaded["chunks"][0]["frames"]

    def test_lite_upgrades_via_manifest_path(self, tmp_path):
        full = tmp_path / "manifest.json"
        full.write_text(json.dumps(_full_manifest()))
        lite = tmp_path / "manifest_lite.json"
        lite.write_text(json.dumps(_lite_manifest(str(full))))
        loaded = load_manifest_for_selection(lite)
        assert len(loaded["chunks"][0]["frames"]) == 2
        # selection works end-to-end on the upgraded manifest
        assert select(loaded, 2)

    def test_lite_upgrades_via_sibling_when_path_moved(self, tmp_path):
        # manifest_path points somewhere stale; sibling manifest.json is the truth.
        full = tmp_path / "manifest.json"
        full.write_text(json.dumps(_full_manifest()))
        lite = tmp_path / "manifest_lite.json"
        lite.write_text(json.dumps(_lite_manifest("/gone/old/manifest.json")))
        loaded = load_manifest_for_selection(lite)
        assert loaded["chunks"][0]["frames"]

    def test_raises_when_no_full_manifest(self, tmp_path):
        lite = tmp_path / "manifest_lite.json"
        lite.write_text(json.dumps(_lite_manifest("/gone/manifest.json")))
        with pytest.raises(SystemExit):
            load_manifest_for_selection(lite)


# ---------------------------------------------------------------------------
# Look-alike-aware selection and pick-by-tile
# ---------------------------------------------------------------------------

def _sig(r, g, b, accent=0):
    """A 16x16 RGB thumbnail: a flat color with a contrasting band so it is not blank."""
    px = bytearray()
    for i in range(select_frames.SIGNATURE_SIDE ** 2):
        if i % 16 < 4:
            px += bytes((255 - r, 255 - g, (b + accent) % 256))
        else:
            px += bytes((r, g, b))
    return bytes(px)


BLACK = bytes(select_frames.SIGNATURE_BYTES)
PODIUM = _sig(200, 40, 40)
CROWD = _sig(40, 200, 40)
WIDE = _sig(40, 40, 200)


class TestPickDiverse:
    def test_static_shot_is_not_repeated(self):
        """Twenty frames of one shot with two cutaways: all three looks are picked."""
        sigs = [PODIUM] * 20
        sigs[7] = CROWD
        sigs[15] = WIDE
        picks = select_frames.pick_diverse([float(i) for i in range(20)], sigs, 5)
        assert len(picks) == 5
        assert 7 in picks and 15 in picks

    def test_returns_the_count_asked_for_even_without_variety(self):
        picks = select_frames.pick_diverse([float(i) for i in range(30)], [PODIUM] * 30, 6)
        assert len(picks) == 6
        assert len(set(picks)) == 6
        # With nothing new to show, the extra picks spread out in time.
        assert max(picks) - min(picks) >= 20

    def test_blank_frames_are_avoided(self):
        sigs = [BLACK, PODIUM, BLACK, CROWD, BLACK, WIDE, BLACK, _sig(120, 120, 0)]
        picks = select_frames.pick_diverse([float(i) for i in range(8)], sigs, 4)
        assert picks == [1, 3, 5, 7]

    def test_all_frames_when_fewer_than_asked(self):
        assert select_frames.pick_diverse([0.0, 1.0], [PODIUM, CROWD], 5) == [0, 1]

    def test_is_blank(self):
        assert select_frames._is_blank(BLACK)
        assert select_frames._is_blank(bytes([255]) * select_frames.SIGNATURE_BYTES)
        assert not select_frames._is_blank(PODIUM)


class TestSelectDiverse:
    def _manifest(self):
        frames = [_make_frame(i, float(i)) for i in range(1, 11)]
        return _make_manifest([{"index": 1, "duration_seconds": 10.0, "frames": frames}], duration=10.0)

    def test_uses_signatures_and_emits_tiles(self):
        sigs = [PODIUM] * 10
        sigs[4] = CROWD
        picks = select_frames.select_diverse(self._manifest(), 3, signature_fn=lambda paths: sigs)
        assert len(picks) == 3
        assert "1:5" in [p["tile"] for p in picks]
        assert all(p["absolute_path"] for p in picks)

    def test_returns_none_when_frames_cannot_be_compared(self):
        assert select_frames.select_diverse(self._manifest(), 3, signature_fn=lambda paths: None) is None

    def test_cli_falls_back_to_even_spacing(self, tmp_path, monkeypatch, capsys):
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(self._manifest()))
        monkeypatch.setattr(select_frames, "frame_signatures", lambda paths: None)
        monkeypatch.setattr(select_frames, "select_diverse",
                            lambda manifest, n: None)
        assert select_frames.main([str(path), "3"]) == 0
        out = capsys.readouterr()
        assert len(json.loads(out.out)) == 3
        assert "even spacing" in out.err

    def test_cli_even_flag_skips_comparison(self, tmp_path, monkeypatch, capsys):
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(self._manifest()))

        def boom(manifest, n):
            raise AssertionError("--even must not compare frames")

        monkeypatch.setattr(select_frames, "select_diverse", boom)
        assert select_frames.main([str(path), "3", "--even"]) == 0
        out = capsys.readouterr()
        assert len(json.loads(out.out)) == 3
        assert out.err == ""


class TestTiles:
    def _manifest(self):
        return _make_manifest([
            {"index": 1, "duration_seconds": 5.0, "frames": [_make_frame(i, float(i)) for i in range(1, 6)]},
            {"index": 2, "duration_seconds": 5.0, "frames": [_make_frame(i, 5.0 + i) for i in range(1, 6)]},
        ], duration=10.0)

    def test_parse(self):
        assert select_frames.parse_tiles("1:4,2:59") == [(1, 4), (2, 59)]
        assert select_frames.parse_tiles("4, 19") == [(1, 4), (1, 19)]

    def test_parse_rejects_garbage(self):
        with pytest.raises(SystemExit):
            select_frames.parse_tiles("two:five")
        with pytest.raises(SystemExit):
            select_frames.parse_tiles("")

    def test_returns_named_frames_in_order(self):
        picks = select_frames.select_tiles(self._manifest(), [(2, 3), (1, 1)])
        assert [p["tile"] for p in picks] == ["2:3", "1:1"]
        assert picks[0]["timestamp_seconds"] == 8.0

    def test_unknown_chunk_or_tile_explains_itself(self):
        with pytest.raises(SystemExit, match="chunks \\[1, 2\\]"):
            select_frames.select_tiles(self._manifest(), [(3, 1)])
        with pytest.raises(SystemExit, match="tiles 1 to 5"):
            select_frames.select_tiles(self._manifest(), [(1, 9)])

    def test_cli(self, tmp_path, capsys):
        path = tmp_path / "manifest.json"
        path.write_text(json.dumps(self._manifest()))
        assert select_frames.main([str(path), "--tiles", "2:5"]) == 0
        assert json.loads(capsys.readouterr().out)[0]["tile"] == "2:5"
