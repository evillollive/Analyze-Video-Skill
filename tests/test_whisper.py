"""Tests for whisper.py audio extraction and transcript caching."""
import os
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import whisper


class TestExtractAudio:
    def test_focus_range_uses_ss_and_duration(self, tmp_path):
        out = tmp_path / "audio.mp3"
        calls = []

        def fake_run(cmd, capture_output, text):
            calls.append(cmd)
            out.write_bytes(b"fake mp3")
            return SimpleNamespace(returncode=0, stderr="")

        with patch.object(whisper.shutil, "which", return_value="/usr/bin/ffmpeg"):
            with patch.object(whisper.subprocess, "run", side_effect=fake_run):
                whisper.extract_audio("video.mp4", out, start_seconds=10.0, end_seconds=25.0)

        cmd = calls[0]
        assert cmd[cmd.index("-ss") + 1] == "10.000"
        assert cmd[cmd.index("-t") + 1] == "15.000"

    def test_audio_size_guard_rejects_oversized_file(self, tmp_path):
        audio = tmp_path / "audio.mp3"
        audio.write_bytes(b"x" * (whisper.MAX_WHISPER_AUDIO_BYTES + 1))
        with pytest.raises(SystemExit) as exc:
            whisper._check_audio_size(audio)
        assert "too large" in str(exc.value)


import cache_utils


@pytest.fixture
def transcript_cache(tmp_path, monkeypatch):
    """Point the shared transcript cache at a temp dir for the duration of a test."""
    monkeypatch.setattr(cache_utils, "TRANSCRIPTS_DIR", tmp_path / "transcripts")
    return tmp_path / "transcripts"


class TestTranscriptSignature:
    def test_signature_tracks_video_identity_and_range(self, tmp_path):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"x" * 100)
        base = cache_utils.transcript_signature(video, backend="groq", model="m")

        assert cache_utils.transcript_signature(video, backend="groq", model="m") == base
        assert cache_utils.transcript_signature(video, backend="openai", model="m") != base
        assert cache_utils.transcript_signature(video, backend="groq", model="other") != base
        assert (
            cache_utils.transcript_signature(video, backend="groq", model="m", start_seconds=5.0)
            != base
        )

        video.write_bytes(b"y" * 200)
        assert cache_utils.transcript_signature(video, backend="groq", model="m") != base

    def test_missing_video_disables_caching(self, tmp_path):
        signature = cache_utils.transcript_signature(
            tmp_path / "nope.mp4", backend="groq", model="m"
        )
        assert signature == {}
        assert cache_utils.transcript_key(signature) is None
        assert cache_utils.read_transcript(signature) is None
        assert cache_utils.write_transcript(signature, [{"start": 0.0, "text": "hi"}]) is None


class TestTranscriptCacheRoundTrip:
    def test_write_then_read(self, transcript_cache):
        signature = {"schema": 1, "video_sig": "1:2", "backend": "groq", "model": "m"}
        segments = [{"start": 0.0, "end": 1.0, "text": "hello"}]
        assert cache_utils.write_transcript(signature, segments) is not None
        assert cache_utils.read_transcript(signature) == segments

    def test_miss_on_different_signature(self, transcript_cache):
        signature = {"schema": 1, "video_sig": "1:2", "backend": "groq", "model": "m"}
        cache_utils.write_transcript(signature, [{"start": 0.0, "text": "hello"}])
        other = dict(signature, video_sig="9:9")
        assert cache_utils.read_transcript(other) is None

    def test_corrupt_entry_is_a_miss_not_a_crash(self, transcript_cache):
        signature = {"schema": 1, "video_sig": "1:2", "backend": "groq", "model": "m"}
        cache_utils.write_transcript(signature, [{"start": 0.0, "text": "hello"}])
        path = transcript_cache / f"{cache_utils.transcript_key(signature)}.json"
        path.write_text("{not json")
        assert cache_utils.read_transcript(signature) is None

    def test_mismatched_stored_signature_is_rejected(self, transcript_cache):
        signature = {"schema": 1, "video_sig": "1:2", "backend": "groq", "model": "m"}
        cache_utils.write_transcript(signature, [{"start": 0.0, "text": "hello"}])
        path = transcript_cache / f"{cache_utils.transcript_key(signature)}.json"
        import json as _json

        payload = _json.loads(path.read_text())
        payload["signature"] = dict(signature, backend="openai")
        path.write_text(_json.dumps(payload))
        assert cache_utils.read_transcript(signature) is None

    def test_empty_segments_are_not_cached(self, transcript_cache):
        signature = {"schema": 1, "video_sig": "1:2", "backend": "groq", "model": "m"}
        assert cache_utils.write_transcript(signature, []) is None
        assert cache_utils.read_transcript(signature) is None


class TestTranscriptPruning:
    def test_prune_drops_only_old_entries(self, transcript_cache):
        fresh = {"schema": 1, "video_sig": "1:1", "backend": "groq", "model": "m"}
        old = {"schema": 1, "video_sig": "2:2", "backend": "groq", "model": "m"}
        cache_utils.write_transcript(fresh, [{"start": 0.0, "text": "a"}])
        old_path = cache_utils.write_transcript(old, [{"start": 0.0, "text": "b"}])
        stale = time.time() - 200 * 86400
        os.utime(old_path, (stale, stale))

        result = cache_utils.prune_transcripts()
        assert result["removed"] == 1
        assert cache_utils.read_transcript(fresh) is not None
        assert cache_utils.read_transcript(old) is None

    def test_zero_max_age_disables_pruning(self, transcript_cache):
        sig = {"schema": 1, "video_sig": "1:1", "backend": "groq", "model": "m"}
        path = cache_utils.write_transcript(sig, [{"start": 0.0, "text": "a"}])
        stale = time.time() - 500 * 86400
        os.utime(path, (stale, stale))
        assert cache_utils.prune_transcripts(max_age=0)["removed"] == 0
        assert cache_utils.read_transcript(sig) is not None

    def test_clear_removes_everything(self, transcript_cache):
        for i in range(3):
            cache_utils.write_transcript(
                {"schema": 1, "video_sig": f"{i}:{i}", "backend": "groq", "model": "m"},
                [{"start": 0.0, "text": "x"}],
            )
        assert cache_utils.clear_transcripts()["removed"] == 3
        assert cache_utils.clear_transcripts()["removed"] == 0

    def test_prune_on_missing_dir_is_safe(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cache_utils, "TRANSCRIPTS_DIR", tmp_path / "absent")
        assert cache_utils.prune_transcripts() == {"removed": 0, "freed_bytes": 0}
        assert cache_utils.clear_transcripts()["removed"] == 0


class TestTranscribeVideoCaching:
    """The cache must short-circuit *before* audio extraction and the API call."""

    def _video(self, tmp_path):
        video = tmp_path / "video.mp4"
        video.write_bytes(b"video-bytes")
        return video

    def test_second_call_skips_extraction_and_upload(self, tmp_path, transcript_cache):
        video = self._video(tmp_path)
        segments = [{"start": 0.0, "end": 2.0, "text": "hello"}]
        calls = {"extract": 0, "post": 0}

        def fake_extract(video_path, out_path, start_seconds=None, end_seconds=None):
            calls["extract"] += 1
            out_path.write_bytes(b"audio")
            return out_path

        def fake_transcribe_audio(audio_path, backend, api_key):
            calls["post"] += 1
            return segments

        with patch.object(whisper, "extract_audio", side_effect=fake_extract):
            with patch.object(whisper, "transcribe_audio", side_effect=fake_transcribe_audio):
                first, backend = whisper.transcribe_video(
                    str(video), tmp_path / "a.mp3", backend="groq", api_key="k"
                )
                second, _ = whisper.transcribe_video(
                    str(video), tmp_path / "fresh-outdir.mp3", backend="groq", api_key="k"
                )

        assert first == second == segments
        assert backend == "groq"
        assert calls == {"extract": 1, "post": 1}

    def test_use_cache_false_always_transcribes(self, tmp_path, transcript_cache):
        video = self._video(tmp_path)
        calls = {"post": 0}

        def fake_extract(video_path, out_path, start_seconds=None, end_seconds=None):
            out_path.write_bytes(b"audio")
            return out_path

        def fake_transcribe_audio(audio_path, backend, api_key):
            calls["post"] += 1
            return [{"start": 0.0, "end": 1.0, "text": "x"}]

        with patch.object(whisper, "extract_audio", side_effect=fake_extract):
            with patch.object(whisper, "transcribe_audio", side_effect=fake_transcribe_audio):
                for _ in range(2):
                    whisper.transcribe_video(
                        str(video),
                        tmp_path / "a.mp3",
                        backend="groq",
                        api_key="k",
                        use_cache=False,
                    )

        assert calls["post"] == 2
        assert cache_utils.clear_transcripts()["removed"] == 0

    def test_refresh_cache_ignores_and_rewrites_entry(self, tmp_path, transcript_cache):
        video = self._video(tmp_path)
        responses = [
            [{"start": 0.0, "end": 1.0, "text": "first"}],
            [{"start": 0.0, "end": 1.0, "text": "second"}],
        ]

        def fake_extract(video_path, out_path, start_seconds=None, end_seconds=None):
            out_path.write_bytes(b"audio")
            return out_path

        def fake_transcribe_audio(audio_path, backend, api_key):
            return responses.pop(0)

        with patch.object(whisper, "extract_audio", side_effect=fake_extract):
            with patch.object(whisper, "transcribe_audio", side_effect=fake_transcribe_audio):
                whisper.transcribe_video(
                    str(video), tmp_path / "a.mp3", backend="groq", api_key="k"
                )
                refreshed, _ = whisper.transcribe_video(
                    str(video),
                    tmp_path / "a2.mp3",
                    backend="groq",
                    api_key="k",
                    refresh_cache=True,
                )
                cached, _ = whisper.transcribe_video(
                    str(video), tmp_path / "a3.mp3", backend="groq", api_key="k"
                )

        assert refreshed[0]["text"] == "second"
        assert cached[0]["text"] == "second"

    def test_focus_range_and_backend_do_not_share_entries(self, tmp_path, transcript_cache):
        video = self._video(tmp_path)
        calls = {"post": 0}

        def fake_extract(video_path, out_path, start_seconds=None, end_seconds=None):
            out_path.write_bytes(b"audio")
            return out_path

        def fake_transcribe_audio(audio_path, backend, api_key):
            calls["post"] += 1
            return [{"start": 0.0, "end": 1.0, "text": f"take{calls['post']}"}]

        with patch.object(whisper, "extract_audio", side_effect=fake_extract):
            with patch.object(whisper, "transcribe_audio", side_effect=fake_transcribe_audio):
                whisper.transcribe_video(
                    str(video), tmp_path / "a.mp3", backend="groq", api_key="k"
                )
                whisper.transcribe_video(
                    str(video),
                    tmp_path / "b.mp3",
                    backend="groq",
                    api_key="k",
                    start_seconds=10.0,
                    end_seconds=20.0,
                )
                whisper.transcribe_video(
                    str(video), tmp_path / "c.mp3", backend="openai", api_key="k"
                )

        assert calls["post"] == 3

    def test_backend_model_is_part_of_identity(self):
        assert whisper.backend_model("groq") == whisper.GROQ_MODEL
        assert whisper.backend_model("openai") == whisper.OPENAI_MODEL
        with pytest.raises(SystemExit):
            whisper.backend_model("nope")
