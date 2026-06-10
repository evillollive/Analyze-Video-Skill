"""Tests for process.py helper functions."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from process import (
    _aspect_ratio_label,
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
