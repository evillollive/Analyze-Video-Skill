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
    _write_status,
    _write_partial_manifest,
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
