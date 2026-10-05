import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import captions  # noqa: E402
from captions import (  # noqa: E402
    download_captions,
    main,
    resolve_formats,
    resolve_langs,
    safe_name,
    segments_to_srt,
)

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.500
Hello <c>world</c>

00:01:02.250 --> 00:01:04.000
Second line
"""


def _client_of(cmd):
    for part in cmd:
        if part == "youtube:player-client=android":
            return "android"
        if part == "youtube:player-client=ios":
            return "ios"
    return "web"


def _template_dir(cmd):
    return Path(cmd[cmd.index("-o") + 1]).parent


def _runner(succeed_on="android", langs=("en",), info=None):
    """Fake yt-dlp that writes captions.<lang>.vtt (+ info.json) into the -o dir."""
    calls = []
    info = info if info is not None else {"title": "My Video: Part 1", "id": "abc123"}

    def run(cmd, capture_output=True, text=True):
        client = _client_of(cmd)
        calls.append(cmd)
        if succeed_on == client:
            d = _template_dir(cmd)
            for lang in langs:
                (d / f"captions.{lang}.vtt").write_text(VTT)
            if "--write-info-json" in cmd:
                (d / "captions.info.json").write_text(json.dumps(info))
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP Error 403")

    run.calls = calls
    return run


@pytest.fixture
def ytdlp(monkeypatch):
    monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")

    def install(runner):
        monkeypatch.setattr("download.subprocess.run", runner)
        return runner

    return install


class TestHelpers:
    def test_safe_name_strips_unsafe_chars_and_adds_id(self):
        assert safe_name("My Video: Part/1?", "abc") == "My Video_ Part_1_ [abc]"

    def test_safe_name_fallbacks(self):
        assert safe_name(None, "abc") == "abc"
        assert safe_name(None, None) == "captions"

    def test_safe_name_truncates(self):
        assert len(safe_name("x" * 300, None)) == 80

    def test_srt_output(self):
        srt = segments_to_srt([{"start": 1.0, "end": 3.5, "text": "Hi"}, {"start": 3661.25, "end": 3662.0, "text": "Yo"}])
        assert srt == "1\n00:00:01,000 --> 00:00:03,500\nHi\n\n2\n01:01:01,250 --> 01:01:02,000\nYo\n"

    def test_resolve_formats(self):
        assert resolve_formats("all") == ["vtt", "srt", "txt"]
        assert resolve_formats("srt, txt,srt") == ["srt", "txt"]
        with pytest.raises(SystemExit):
            resolve_formats("docx")

    def test_resolve_langs(self):
        assert resolve_langs("en") == "en,en-US,en-GB,en-orig"
        assert resolve_langs("all") == "all,-live_chat"
        assert resolve_langs("es,fr") == "es,fr"


class TestDownloadCaptions:
    def test_writes_named_files_in_each_format(self, tmp_path, ytdlp):
        ytdlp(_runner())
        res = download_captions("https://youtu.be/x", tmp_path, langs="en", formats=["vtt", "srt", "txt"])
        names = sorted(Path(f["path"]).name for f in res["files"])
        assert names == [
            "My Video_ Part 1 [abc123].en.srt",
            "My Video_ Part 1 [abc123].en.txt",
            "My Video_ Part 1 [abc123].en.vtt",
        ]
        srt = (tmp_path / "My Video_ Part 1 [abc123].en.srt").read_text()
        assert "00:00:01,000 --> 00:00:03,500\nHello world" in srt
        txt = (tmp_path / "My Video_ Part 1 [abc123].en.txt").read_text()
        assert txt.startswith("[00:01] Hello world\n[01:02] Second line")
        assert res["title"] == "My Video: Part 1"
        # Temp working dir is cleaned up.
        assert not any(p.name.startswith(".captions-") for p in tmp_path.iterdir())

    def test_falls_back_through_clients(self, tmp_path, ytdlp):
        runner = ytdlp(_runner(succeed_on="web"))
        download_captions("https://www.youtube.com/watch?v=x", tmp_path, langs="en", formats=["srt"])
        assert [_client_of(c) for c in runner.calls] == ["android", "ios", "web"]

    def test_multiple_languages_and_orig_dedupe(self, tmp_path, ytdlp):
        ytdlp(_runner(langs=("en", "en-orig", "es")))
        res = download_captions("https://youtu.be/x", tmp_path, langs="all", formats=["srt"])
        assert sorted(f["lang"] for f in res["files"]) == ["en", "es"]

    def test_no_auto_omits_flag_and_passes_langs(self, tmp_path, ytdlp):
        runner = ytdlp(_runner())
        download_captions("https://youtu.be/x", tmp_path, langs="es.*", formats=["srt"], include_auto=False)
        cmd = runner.calls[0]
        assert "--write-auto-subs" not in cmd
        assert cmd[cmd.index("--sub-langs") + 1] == "es.*"

    def test_existing_file_not_overwritten(self, tmp_path, ytdlp):
        ytdlp(_runner())
        (tmp_path / "My Video_ Part 1 [abc123].en.srt").write_text("keep")
        res = download_captions("https://youtu.be/x", tmp_path, langs="en", formats=["srt"])
        assert Path(res["files"][0]["path"]).name == "My Video_ Part 1 [abc123].en (2).srt"
        assert (tmp_path / "My Video_ Part 1 [abc123].en.srt").read_text() == "keep"

    def test_no_subs_raises_and_cleans_up(self, tmp_path, ytdlp):
        ytdlp(_runner(succeed_on="never"))
        with pytest.raises(SystemExit):
            download_captions("https://vimeo.com/1", tmp_path, langs="en", formats=["srt"])
        assert list(tmp_path.iterdir()) == []


class TestMain:
    def test_reports_json_and_partial_failure(self, tmp_path, ytdlp, capsys):
        ytdlp(_runner())
        rc = main(["--source", "https://youtu.be/x", "--source", "/local/file.mp4", "--out-dir", str(tmp_path)])
        out = json.loads(capsys.readouterr().out)
        assert rc == 1
        assert out[0]["files"][0]["format"] == "srt"
        assert "error" in out[1]

    def test_list_mode(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        info = {"title": "T", "id": "i", "subtitles": {"en": [], "live_chat": []}, "automatic_captions": {"fr": [], "en": []}}

        def run(cmd, capture_output=True, text=True):
            assert "-J" in cmd
            return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(info), stderr="")

        monkeypatch.setattr("download.subprocess.run", run)
        rc = main(["--source", "https://youtu.be/x", "--list"])
        out = json.loads(capsys.readouterr().out)
        assert rc == 0
        assert out[0]["manual"] == ["en"] and out[0]["auto"] == ["en", "fr"]
        assert not list(tmp_path.iterdir())


def test_module_exposes_formats():
    assert captions.FORMATS == ("vtt", "srt", "txt")
