"""Captions mode for local video files: sidecar subtitles, then Whisper."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import captions  # noqa: E402
from captions import local_captions, sidecar_subtitles  # noqa: E402

SRT = """1
00:00:01,000 --> 00:00:03,500
Hello there.

2
00:00:04,000 --> 00:00:06,000
Second line.
"""

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
Hola.
"""


def _video(tmp_path, name="clip.mp4"):
    video = tmp_path / name
    video.write_bytes(b"not really a video")
    return video


def test_sidecar_matching_is_anchored_on_the_stem(tmp_path):
    video = _video(tmp_path, "clip1.mp4")
    (tmp_path / "clip1.srt").write_text(SRT)
    (tmp_path / "clip1.es.vtt").write_text(VTT)
    (tmp_path / "clip10.en.srt").write_text(SRT)
    tracks = sidecar_subtitles(video)
    assert set(tracks) == {"und", "es"}


def test_srt_sidecar_becomes_every_format(tmp_path):
    video = _video(tmp_path)
    (tmp_path / "clip.en.srt").write_text(SRT)
    out = tmp_path / "out"
    result = local_captions(video, out, formats=["srt", "vtt", "txt"])
    assert result["method"] == "sidecar"
    paths = {f["format"]: Path(f["path"]) for f in result["files"]}
    assert paths["srt"].name == "clip.en.srt"
    assert paths["vtt"].read_text().startswith("WEBVTT")
    assert "00:00:01.000 --> 00:00:03.500" in paths["vtt"].read_text()
    txt = paths["txt"].read_text()
    assert "Hello there." in txt and "Second line." in txt


def test_no_sidecar_and_no_key_explains_what_to_do(tmp_path, monkeypatch):
    video = _video(tmp_path)
    import whisper
    monkeypatch.setattr(whisper, "load_api_key", lambda *a, **k: (None, None))
    with pytest.raises(SystemExit) as exc:
        local_captions(video, tmp_path / "out", formats=["srt"])
    assert "No subtitle file" in str(exc.value)
    assert "--set-key" in str(exc.value)


def test_no_whisper_never_uploads(tmp_path, monkeypatch):
    video = _video(tmp_path)
    import whisper
    monkeypatch.setattr(whisper, "transcribe_video",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not upload")))
    with pytest.raises(SystemExit, match="Whisper is turned off"):
        local_captions(video, tmp_path / "out", formats=["srt"], whisper=False)


def test_whisper_transcribes_when_no_sidecar(tmp_path, monkeypatch):
    video = _video(tmp_path)
    import whisper
    monkeypatch.setattr(whisper, "load_api_key", lambda *a, **k: ("groq", "key"))
    calls = {}

    def fake(video_path, audio_out, backend=None, api_key=None, **k):
        calls["video"] = video_path
        return [{"start": 0.0, "end": 2.0, "text": "Transcribed words."}], backend

    monkeypatch.setattr(whisper, "transcribe_video", fake)
    result = local_captions(video, tmp_path / "out", formats=["srt", "txt"])
    assert result["method"] == "whisper (groq)"
    assert calls["video"] == str(video.resolve())
    srt = next(Path(f["path"]) for f in result["files"] if f["format"] == "srt")
    assert srt.name == "clip.whisper.srt"
    assert "Transcribed words." in srt.read_text()


def test_cli_accepts_local_files(tmp_path, capsys):
    video = _video(tmp_path)
    (tmp_path / "clip.srt").write_text(SRT)
    code = captions.main(["--source", str(video), "--out-dir", str(tmp_path / "out"), "--format", "txt"])
    assert code == 0
    result = json.loads(capsys.readouterr().out)
    assert result[0]["files"][0]["format"] == "txt"


def test_cli_list_shows_sidecars(tmp_path, capsys):
    video = _video(tmp_path)
    (tmp_path / "clip.fr.vtt").write_text(VTT)
    captions.main(["--source", str(video), "--list"])
    result = json.loads(capsys.readouterr().out)
    assert "fr" in result[0]["sidecar"]
