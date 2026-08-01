"""Tests for process.py helper functions."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from process import (
    _aspect_ratio_label,
    _check_runner_timeout,
    _docx_image_dimensions,
    _download_dir,
    _looks_like_local_title,
    _slugify,
    _suggested_docx_name,
    _write_status,
    _write_partial_manifest,
    _write_transcript_file,
)


class TestAspectRatioLabel:
    def test_16_9(self):
        assert _aspect_ratio_label(1920, 1080) == "16:9"

    def test_4_3(self):
        assert _aspect_ratio_label(640, 480) == "4:3"

    def test_9_16(self):
        assert _aspect_ratio_label(1080, 1920) == "9:16"

    def test_1_1(self):
        assert _aspect_ratio_label(500, 500) == "1:1"

    def test_custom_ratio(self):
        result = _aspect_ratio_label(2560, 1080)
        assert ":1" in result

    def test_none_inputs(self):
        assert _aspect_ratio_label(None, 1080) is None
        assert _aspect_ratio_label(1920, None) is None
        assert _aspect_ratio_label(0, 0) is None


class TestDocxImageDimensions:
    def test_16_9(self):
        dim = _docx_image_dimensions(1920, 1080, "16:9")
        assert dim == {"width": 480, "height": 270}

    def test_4_3(self):
        dim = _docx_image_dimensions(640, 480, "4:3")
        assert dim == {"width": 480, "height": 360}

    def test_9_16(self):
        dim = _docx_image_dimensions(1080, 1920, "9:16")
        assert dim == {"width": 240, "height": 427}

    def test_1_1(self):
        dim = _docx_image_dimensions(500, 500, "1:1")
        assert dim == {"width": 360, "height": 360}

    def test_unknown_with_dimensions(self):
        dim = _docx_image_dimensions(2560, 1080, "2.37:1")
        assert dim["width"] == 480
        assert dim["height"] == int(round(480 * 1080 / 2560))

    def test_fallback_default(self):
        dim = _docx_image_dimensions(None, None, None)
        assert dim == {"width": 480, "height": 270}


class TestWriteStatus:
    def test_writes_stage_and_extras(self, tmp_path):
        _write_status(tmp_path, "extracting", chunk_count=3, chunks_completed=1)
        data = json.loads((tmp_path / "status.json").read_text())
        assert data["stage"] == "extracting"
        assert data["chunk_count"] == 3
        assert data["chunks_completed"] == 1
        assert "updated_at" in data

    def test_overwrites_previous_status(self, tmp_path):
        _write_status(tmp_path, "downloading")
        _write_status(tmp_path, "complete", manifest_path="/x/manifest.json")
        data = json.loads((tmp_path / "status.json").read_text())
        assert data["stage"] == "complete"
        assert data["manifest_path"] == "/x/manifest.json"


class TestWritePartialManifest:
    def test_partial_manifest_shape(self, tmp_path):
        chunks = [{"index": 1, "frame_count": 10}]
        _write_partial_manifest(
            tmp_path,
            info={"title": "My Video"},
            full_duration=123.4,
            chunk_count=3,
            processed_chunks=chunks,
            transcript_source="captions",
            segment_count=42,
        )
        data = json.loads((tmp_path / "manifest_partial.json").read_text())
        assert data["partial"] is True
        assert data["status"] == "in_progress"
        assert data["title"] == "My Video"
        assert data["chunk_count"] == 3
        assert data["chunks_completed"] == 1
        assert data["transcript_segment_count"] == 42
        assert data["chunks"] == chunks


class TestDownloadDir:
    def test_url_uses_shared_cache_keyed_by_url(self, tmp_path):
        url = "https://youtu.be/abc123"
        d1 = _download_dir(url, tmp_path / "runA", no_cache=False)
        d2 = _download_dir(url, tmp_path / "runB", no_cache=False)
        # Same URL -> same cache dir regardless of out-dir (reuse across runs).
        assert d1 == d2
        assert "downloads" in d1.parts
        # Different URL -> different cache dir.
        other = _download_dir("https://youtu.be/zzz999", tmp_path / "runA", no_cache=False)
        assert other != d1

    def test_no_cache_uses_out_dir(self, tmp_path):
        url = "https://youtu.be/abc123"
        d = _download_dir(url, tmp_path / "run", no_cache=True)
        assert d == tmp_path / "run" / "download"

    def test_local_source_uses_out_dir(self, tmp_path):
        d = _download_dir("/some/local/video.mp4", tmp_path / "run", no_cache=False)
        assert d == tmp_path / "run" / "download"


class TestSuggestedDocxName:
    def test_slugify_basic(self):
        assert _slugify("How to Bake Bread!") == "how-to-bake-bread"

    def test_slugify_collapses_and_trims_separators(self):
        assert _slugify("  --Multiple   Spaces & Symbols--  ") == "multiple-spaces-symbols"

    def test_slugify_truncates_without_trailing_hyphen(self):
        out = _slugify("a" * 40 + " " + "b" * 40, max_len=50)
        assert len(out) <= 50
        assert not out.endswith("-")

    def test_slugify_empty_when_no_usable_chars(self):
        assert _slugify("???") == ""

    def test_name_from_title(self):
        assert (
            _suggested_docx_name("My Great Video", "https://youtu.be/abc")
            == "my-great-video-analysis.docx"
        )

    def test_falls_back_to_url_basename(self):
        assert (
            _suggested_docx_name(None, "https://example.com/clips/cool-clip.mp4?t=10")
            == "cool-clip-analysis.docx"
        )

    def test_falls_back_to_local_stem(self):
        assert (
            _suggested_docx_name("", "/home/me/My Recording.mov")
            == "my-recording-analysis.docx"
        )

    def test_final_fallback_is_video(self):
        assert _suggested_docx_name("???", "???") == "video-analysis.docx"

    def test_always_ends_in_analysis_docx(self):
        for title in ["Anything", "", None, "🎬🎬🎬"]:
            assert _suggested_docx_name(title, "src").endswith("-analysis.docx")


class TestLooksLikeLocalTitle:
    def test_matches_exact_filename(self):
        assert _looks_like_local_title("video.mp4", "/tmp/video.mp4") is True

    def test_matches_stem(self):
        assert _looks_like_local_title("video", "/tmp/video.mp4") is True

    def test_generic_video_names(self):
        assert _looks_like_local_title("video.mov", "/tmp/whatever.mp4") is True

    def test_real_remote_title_not_local_placeholder(self):
        assert _looks_like_local_title("Episode 4: The Reveal", "/tmp/video.mp4") is False


class TestRunnerTimeoutCheck:
    def test_no_timeout_budget_noop(self):
        _check_runner_timeout(
            runner_timeout_seconds=None,
            expected_duration_minutes=90,
            focused=False,
            quick=False,
        )

    def test_focused_run_skips_timeout_guard(self):
        _check_runner_timeout(
            runner_timeout_seconds=45,
            expected_duration_minutes=120,
            focused=True,
            quick=False,
        )

    def test_raises_when_expected_exceeds_timeout(self):
        try:
            _check_runner_timeout(
                runner_timeout_seconds=60,
                expected_duration_minutes=5,
                focused=False,
                quick=False,
            )
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert "exceeds runner timeout" in str(exc)


class TestWriteTranscriptFile:
    def test_writes_timestamped_lines(self, tmp_path):
        segments = [
            {"start": 0.0, "end": 4.0, "text": "Hello world."},
            {"start": 83.4, "end": 90.0, "text": "Later moment."},
        ]
        path = _write_transcript_file(tmp_path, segments)
        assert path == tmp_path / "transcript.txt"
        content = path.read_text(encoding="utf-8")
        assert content == "[00:00] Hello world.\n[01:23] Later moment.\n"

    def test_skips_blank_segments(self, tmp_path):
        segments = [
            {"start": 0.0, "text": "  "},
            {"start": 1.0, "text": "Real line."},
        ]
        path = _write_transcript_file(tmp_path, segments)
        assert path.read_text(encoding="utf-8") == "[00:01] Real line.\n"

    def test_no_segments_returns_none_and_writes_nothing(self, tmp_path):
        assert _write_transcript_file(tmp_path, []) is None
        assert not (tmp_path / "transcript.txt").exists()

    def test_all_blank_segments_returns_none(self, tmp_path):
        assert _write_transcript_file(tmp_path, [{"start": 0.0, "text": ""}]) is None
        assert not (tmp_path / "transcript.txt").exists()


from process import (  # noqa: E402
    _transcript_slice,
    _patch_manifest_transcript,
)


def _segs():
    return [
        {"start": 0.0, "end": 5.0, "text": "a"},
        {"start": 6.0, "end": 9.0, "text": "b"},
        {"start": 11.0, "end": 14.0, "text": "c"},
    ]


class TestTranscriptSlice:
    def test_empty_segments(self):
        assert _transcript_slice([], 0, 10) == {
            "segment_count": 0, "start_index": None, "end_index": None
        }

    def test_range_indices(self):
        sl = _transcript_slice(_segs(), 0.0, 10.0)
        assert sl["segment_count"] == 2
        assert sl["start_index"] == 0
        assert sl["end_index"] == 1

    def test_out_of_range(self):
        sl = _transcript_slice(_segs(), 100.0, 200.0)
        assert sl["segment_count"] == 0


class TestPatchManifestTranscript:
    def test_patches_full_and_lite(self, tmp_path):
        chunk = {"index": 1, "start_seconds": 0.0, "end_seconds": 10.0,
                 "transcript_slice": {"segment_count": 0, "start_index": None, "end_index": None}}
        (tmp_path / "manifest.json").write_text(json.dumps({
            "transcript_source": None, "transcript_segment_count": 0,
            "transcript_path": None, "transcript_segments": [],
            "chunks": [dict(chunk)],
        }))
        (tmp_path / "manifest_lite.json").write_text(json.dumps({
            "transcript_source": None, "transcript_segment_count": 0,
            "transcript_path": None, "chunks": [dict(chunk)],
        }))
        segs = _segs()
        _patch_manifest_transcript(tmp_path, segs, tmp_path / "transcript.txt")

        full = json.loads((tmp_path / "manifest.json").read_text())
        assert full["transcript_source"] == "captions"
        assert full["transcript_segment_count"] == 3
        assert full["transcript_segments"] == segs
        assert full["chunks"][0]["transcript_slice"]["segment_count"] == 2

        lite = json.loads((tmp_path / "manifest_lite.json").read_text())
        assert lite["transcript_segment_count"] == 3
        assert "transcript_segments" not in lite
        assert lite["chunks"][0]["transcript_slice"]["segment_count"] == 2

    def test_no_manifests_is_noop(self, tmp_path):
        _patch_manifest_transcript(tmp_path, _segs(), tmp_path / "t.txt")  # must not raise


from process import DEFAULT_MAX_PARALLEL_CHUNKS, _resolve_jobs  # noqa: E402


class TestResolveJobs:
    def test_single_chunk_never_parallel(self):
        assert _resolve_jobs(None, 1) == 1
        assert _resolve_jobs(8, 1) == 1
        assert _resolve_jobs(None, 0) == 1

    def test_explicit_request_is_capped_by_chunk_count(self):
        assert _resolve_jobs(8, 3) == 3
        assert _resolve_jobs(2, 10) == 2

    def test_explicit_one_forces_sequential(self):
        assert _resolve_jobs(1, 10) == 1

    def test_auto_is_bounded(self):
        jobs = _resolve_jobs(None, 10)
        assert 1 <= jobs <= DEFAULT_MAX_PARALLEL_CHUNKS

    def test_non_positive_request_falls_back_to_auto(self):
        assert _resolve_jobs(0, 6) == _resolve_jobs(None, 6)
        assert _resolve_jobs(-3, 6) == _resolve_jobs(None, 6)


from process import _segment_index, _shift_segments, _slice_indices  # noqa: E402


class TestShiftSegments:
    def test_zero_offset_returns_same_object(self):
        segs = [{"start": 1.0, "end": 2.0, "text": "a"}]
        assert _shift_segments(segs, 0.0) is segs

    def test_shifts_start_and_end(self):
        segs = [{"start": 0.0, "end": 2.0, "text": "a"}, {"start": 3.0, "end": 5.0, "text": "b"}]
        out = _shift_segments(segs, 300.0)
        assert [(s["start"], s["end"]) for s in out] == [(300.0, 302.0), (303.0, 305.0)]

    def test_does_not_mutate_input(self):
        segs = [{"start": 0.0, "end": 2.0, "text": "a"}]
        _shift_segments(segs, 10.0)
        assert segs[0]["start"] == 0.0

    def test_missing_end_is_left_absent(self):
        out = _shift_segments([{"start": 1.0, "text": "x"}], 10.0)
        assert out[0]["start"] == 11.0
        assert "end" not in out[0]

    def test_focused_whisper_slice_is_recovered(self):
        """A focused Whisper run returns 0-based times; chunks slice absolutely."""
        whisper = [{"start": s, "end": s + 2.0, "text": f"l{s}"} for s in range(0, 60, 3)]
        # Without the shift the chunk slice is empty even though audio transcribed.
        assert _slice_indices(whisper, _segment_index(whisper), 300.0, 360.0) == {
            "segment_count": 0, "start_index": None, "end_index": None,
        }
        shifted = _shift_segments(whisper, 300.0)
        assert _slice_indices(shifted, _segment_index(shifted), 300.0, 360.0) == {
            "segment_count": 20, "start_index": 0, "end_index": 19,
        }


class TestSliceIndices:
    def test_empty_segments(self):
        assert _slice_indices([], None, 0.0, 10.0) == {
            "segment_count": 0, "start_index": None, "end_index": None,
        }

    def test_unsorted_segments_fall_back_to_linear(self):
        segs = [
            {"start": 9.0, "end": 10.0, "text": "c"},
            {"start": 1.0, "end": 2.0, "text": "a"},
        ]
        assert _segment_index(segs) is None  # refuses to index unsorted input
        assert _slice_indices(segs, None, 0.0, 3.0) == {
            "segment_count": 1, "start_index": 1, "end_index": 1,
        }

    def test_long_held_cue_starting_before_window_is_found(self):
        segs = [
            {"start": 0.0, "end": 500.0, "text": "held card"},
            {"start": 310.0, "end": 312.0, "text": "speech"},
        ]
        got = _slice_indices(segs, _segment_index(segs), 300.0, 360.0)
        assert got == {"segment_count": 2, "start_index": 0, "end_index": 1}
