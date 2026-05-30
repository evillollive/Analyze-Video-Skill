"""Tests for VTT parsing (transcribe.py)."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from transcribe import parse_vtt, filter_range, format_transcript, _dedupe


def _write_vtt(tmp_path: Path, content: str) -> Path:
    vtt = tmp_path / "test.vtt"
    vtt.write_text(content, encoding="utf-8")
    return vtt


class TestParseVtt:
    def test_basic_vtt(self, tmp_path):
        vtt = _write_vtt(tmp_path, (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:03.000\n"
            "Hello world\n\n"
            "00:00:04.000 --> 00:00:06.500\n"
            "Second line\n"
        ))
        segs = parse_vtt(str(vtt))
        assert len(segs) == 2
        assert segs[0] == {"start": 1.0, "end": 3.0, "text": "Hello world"}
        assert segs[1] == {"start": 4.0, "end": 6.5, "text": "Second line"}

    def test_strips_html_tags(self, tmp_path):
        vtt = _write_vtt(tmp_path, (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "<c.colorCCCCCC>Hello</c> <b>world</b>\n"
        ))
        segs = parse_vtt(str(vtt))
        assert segs[0]["text"] == "Hello world"

    def test_deduplicates_rolling_subs(self, tmp_path):
        vtt = _write_vtt(tmp_path, (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:02.000\n"
            "Hello\n\n"
            "00:00:02.000 --> 00:00:03.000\n"
            "Hello\n\n"
            "00:00:03.000 --> 00:00:04.000\n"
            "World\n"
        ))
        segs = parse_vtt(str(vtt))
        assert len(segs) == 2
        assert segs[0] == {"start": 1.0, "end": 3.0, "text": "Hello"}

    def test_empty_file(self, tmp_path):
        vtt = _write_vtt(tmp_path, "WEBVTT\n\n")
        segs = parse_vtt(str(vtt))
        assert segs == []

    def test_multiline_cue(self, tmp_path):
        vtt = _write_vtt(tmp_path, (
            "WEBVTT\n\n"
            "00:00:01.000 --> 00:00:05.000\n"
            "First line\n"
            "Second line\n"
        ))
        segs = parse_vtt(str(vtt))
        assert segs[0]["text"] == "First line Second line"


class TestDedupe:
    def test_prefix_merge(self):
        segs = [
            {"start": 0, "end": 1, "text": "Hello"},
            {"start": 1, "end": 2, "text": "Hello world"},
        ]
        result = _dedupe(segs)
        assert len(result) == 1
        assert result[0]["text"] == "Hello world"
        assert result[0]["end"] == 2


class TestFilterRange:
    def test_full_range(self):
        segs = [
            {"start": 0, "end": 5, "text": "a"},
            {"start": 5, "end": 10, "text": "b"},
            {"start": 10, "end": 15, "text": "c"},
        ]
        assert filter_range(segs, None, None) == segs

    def test_time_filter(self):
        segs = [
            {"start": 0, "end": 5, "text": "a"},
            {"start": 5, "end": 10, "text": "b"},
            {"start": 10, "end": 15, "text": "c"},
        ]
        # filter_range includes any segment overlapping [lo, hi]
        # seg c starts at 10 which is <= 11, and ends at 15 >= 4, so it's included
        result = filter_range(segs, 4, 11)
        assert len(result) == 3

        # Narrower range that excludes seg c
        result2 = filter_range(segs, 4, 9)
        assert len(result2) == 2
        assert result2[0]["text"] == "a"
        assert result2[1]["text"] == "b"


class TestFormatTranscript:
    def test_format(self):
        segs = [{"start": 65.0, "end": 70.0, "text": "Test"}]
        output = format_transcript(segs)
        assert output == "[01:05] Test"
