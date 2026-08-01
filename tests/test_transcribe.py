"""Tests for VTT parsing (transcribe.py)."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from transcribe import parse_vtt, filter_range, format_transcript, _dedupe, detect_trailing_promo


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


def _seg(start, end, text):
    return {"start": start, "end": end, "text": text}


class TestDetectTrailingPromo:
    def _speech(self, count, step=5.0):
        # Distinct, dense sentences: high words/sec, all-unique phrases.
        return [
            _seg(i * step, i * step + step,
                 f"this is sentence number {i} with plenty of distinct spoken content")
            for i in range(count)
        ]

    def test_detects_repeated_trailing_card(self):
        speech = self._speech(20)  # 0..100s
        promo = [_seg(100 + i * 10, 100 + i * 10 + 10, "dont miss the full episode")
                 for i in range(6)]  # 100..160s, ~0.5 words/sec
        res = detect_trailing_promo(speech + promo, full_duration=160)
        assert res is not None and res["detected"]
        assert res["confidence"] == "high"
        assert 95 <= res["start_seconds"] <= 105

    def test_detects_single_held_card(self):
        # After dedupe a held card is one long, low-words-per-second segment.
        speech = self._speech(20)  # 0..100s
        held = [_seg(100.0, 145.0, "subscribe now")]  # 45s, 2 words, promo keyword
        res = detect_trailing_promo(speech + held, full_duration=145)
        assert res is not None
        assert res["start_seconds"] == 100.0
        assert res["confidence"] == "high"  # "subscribe" keyword raises confidence

    def test_quiet_ending_is_low_confidence(self):
        # A single sparse, non-promo final cue should be flagged low confidence
        # so it is reported but never auto-trimmed.
        speech = self._speech(20)  # 0..100s
        ending = [_seg(100.0, 140.0, "and that is the end")]  # 40s, 5 words, no keyword
        res = detect_trailing_promo(speech + ending, full_duration=140)
        assert res is not None
        assert res["confidence"] == "low"

    def test_detects_card_starting_before_lookback_window(self):
        # A long held card that starts before the 240s lookback floor but ends
        # after it must still be detected (overlap, not start-only, membership).
        speech = [_seg(i * 10, i * 10 + 10, f"distinct spoken sentence number {i} here")
                  for i in range(15)]  # 0..150s
        held = [_seg(150.0, 420.0, "subscribe now")]  # 270s card; starts before 420-240=180
        res = detect_trailing_promo(speech + held, full_duration=420)
        assert res is not None
        assert res["start_seconds"] == 150.0

    def test_ignores_dense_speech(self):
        speech = self._speech(40)  # 200s of distinct dense speech
        res = detect_trailing_promo(speech, full_duration=200)
        assert res is None

    def test_requires_minimum_duration(self):
        speech = self._speech(20)
        promo = [_seg(100 + i * 5, 100 + i * 5 + 5, "buy now") for i in range(4)]  # 20s < 30s
        res = detect_trailing_promo(speech + promo, full_duration=120)
        assert res is None

    def test_empty_transcript(self):
        assert detect_trailing_promo([], full_duration=120) is None


from transcribe import SegmentIndex  # noqa: E402


class TestSegmentIndex:
    def _segs(self):
        return [
            {"start": 0.0, "end": 2.0, "text": "a"},
            {"start": 3.0, "end": 5.0, "text": "b"},
            {"start": 6.0, "end": 9.0, "text": "c"},
        ]

    def test_build_refuses_unsorted(self):
        assert SegmentIndex.build([{"start": 5.0, "end": 6.0}, {"start": 1.0, "end": 2.0}]) is None

    def test_build_accepts_equal_starts(self):
        assert SegmentIndex.build([{"start": 1.0, "end": 2.0}, {"start": 1.0, "end": 3.0}]) is not None

    def test_filter_range_matches_linear(self):
        segs = self._segs()
        idx = SegmentIndex.build(segs)
        for lo, hi in [(0, 9), (4, 11), (4, 9), (2, 2), (10, 20), (-5, -1)]:
            expected = [s for s in segs if s["end"] >= lo and s["start"] <= hi]
            assert idx.filter_range(lo, hi) == expected, (lo, hi)

    def test_range_indices_reports_absolute_positions(self):
        idx = SegmentIndex.build(self._segs())
        assert idx.range_indices(3, 7) == (1, 2, 2)
        assert idx.range_indices(100, 200) == (None, None, 0)

    def test_none_bounds_returns_everything(self):
        segs = self._segs()
        assert SegmentIndex.build(segs).filter_range(None, None) == segs

    def test_empty_index(self):
        idx = SegmentIndex.build([])
        assert idx.range_indices(0, 10) == (None, None, 0)
        assert idx.filter_range(0, 10) == []

    def test_segment_without_end_uses_start(self):
        segs = [{"start": 4.0, "text": "no end"}]
        idx = SegmentIndex.build(segs)
        assert idx.range_indices(3, 5) == (0, 0, 1)
        assert idx.range_indices(6, 8) == (None, None, 0)
