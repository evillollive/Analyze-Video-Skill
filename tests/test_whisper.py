"""Tests for whisper.py audio extraction helpers."""
import sys
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
