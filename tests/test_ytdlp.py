"""Tests for scripts/ytdlp.py: the single yt-dlp boundary and its update policy."""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import download  # noqa: E402
import ytdlp  # noqa: E402


def _proc(cmd, rc=0, out="", err=""):
    return subprocess.CompletedProcess(cmd, rc, out, err)


class TestArgs:
    def test_client_args(self):
        assert ytdlp.client_args(None) == []
        assert ytdlp.client_args("tv") == ["--extractor-args", "youtube:player-client=tv"]

    def test_auth_args(self):
        assert ytdlp.auth_args(None, None) == []
        assert ytdlp.auth_args(Path("/c.txt"), None) == ["--cookies", str(Path("/c.txt"))]
        assert ytdlp.auth_args(None, "firefox") == ["--cookies-from-browser", "firefox"]

    def test_runtime_prefers_deno(self, monkeypatch):
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: f"/bin/{name}")
        assert ytdlp.runtime_args()[:2] == ["--js-runtimes", "deno:/bin/deno"]
        assert ytdlp.js_runtime_name() == "deno"

    def test_runtime_none(self, monkeypatch):
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: None)
        assert ytdlp.runtime_args() == []
        assert ytdlp.js_runtime_name() is None

    def test_require_exits_with_install_hint(self, monkeypatch):
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: None)
        with pytest.raises(SystemExit, match="pipx install"):
            ytdlp.require()


class TestVersion:
    def test_version_from_info(self):
        assert ytdlp.version_from_info({"_version": {"version": "2026.10.21"}}) == "2026.10.21"
        assert ytdlp.version_from_info({}) is None
        assert ytdlp.version_from_info(None) is None

    def test_age_and_staleness(self):
        today = dt.date(2026, 11, 5)
        assert ytdlp.age_days("2026.10.21", today=today) == 15
        assert ytdlp.staleness_warning("2026.10.21", today=today) is None
        warning = ytdlp.staleness_warning("2026.08.01", today=today)
        assert warning and "96 days old" in warning and "--update-ytdlp" in warning

    def test_nightly_version_string_parses(self):
        assert ytdlp.version_date("2026.11.02.233154") == dt.date(2026, 11, 2)

    def test_unparseable_version_is_never_stale(self):
        assert ytdlp.staleness_warning("unknown") is None
        assert ytdlp.staleness_warning(None) is None

    def test_version_is_cached(self, monkeypatch):
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            return _proc(cmd, out="2026.10.21\n")

        monkeypatch.setattr(subprocess, "run", fake_run)
        assert ytdlp.version("/bin/yt-dlp") == "2026.10.21"
        assert ytdlp.version("/bin/yt-dlp") == "2026.10.21"
        assert len(calls) == 1


class TestUpdateMayHelp:
    @pytest.mark.parametrize(
        "text",
        [
            "ERROR: [youtube] abc: Unable to extract nsig function",
            "WARNING: [youtube] n challenge solving failed",
            "ERROR: Requested format is not available",
            "yt-dlp selected 360p despite higher-resolution formats",
            "WARNING: YouTube is forcing SABR streaming for this client",
        ],
    )
    def test_extraction_breakage_triggers_update(self, text):
        assert ytdlp.update_may_help(text)

    @pytest.mark.parametrize(
        "text",
        [
            "Sign in to confirm you're not a bot",
            "ERROR: Private video",
            "HTTP Error 429: Too Many Requests",
            "This video is members-only. Unable to extract",
            "",
        ],
    )
    def test_access_problems_do_not(self, text):
        assert not ytdlp.update_may_help(text)


class TestInstallMethod:
    def test_pip_script(self, tmp_path):
        script = tmp_path / "yt-dlp"
        script.write_text("#!/usr/bin/python3\nimport yt_dlp\n")
        assert ytdlp.install_method(str(script)) == "pip"
        cmds = ytdlp.update_commands(str(script), "stable")
        assert cmds[0][:5] == ["/usr/bin/python3", "-m", "pip", "install", "--upgrade"]
        assert cmds[0][-1] == "yt-dlp[default]"
        nightly = ytdlp.update_commands(str(script), "nightly")
        assert "--pre" in nightly[0]

    def test_binary(self, tmp_path):
        binary = tmp_path / "yt-dlp"
        binary.write_bytes(b"\x7fELF....")
        assert ytdlp.install_method(str(binary)) == "binary"
        assert ytdlp.update_commands(str(binary), "nightly") == [[str(binary), "--update-to", "nightly"]]

    def test_pipx(self, tmp_path):
        venv = tmp_path / "pipx" / "venvs" / "yt-dlp" / "bin"
        venv.mkdir(parents=True)
        script = venv / "yt-dlp"
        script.write_text("#!/x/python\n")
        assert ytdlp.install_method(str(script)) == "pipx"
        assert ytdlp.update_commands(str(script), "stable")[0][-2:] == ["upgrade", "yt-dlp"]

    def test_brew_has_no_nightly(self, tmp_path):
        cellar = tmp_path / "Cellar" / "yt-dlp" / "bin"
        cellar.mkdir(parents=True)
        script = cellar / "yt-dlp"
        script.write_text("#!/x/python\n")
        assert ytdlp.install_method(str(script)) == "brew"
        assert ytdlp.update_commands(str(script), "nightly") == []


class _FakeInstall:
    """A yt-dlp whose version changes when an update command runs."""

    def __init__(self, tmp_path, versions):
        self.path = tmp_path / "yt-dlp"
        self.path.write_bytes(b"\x7fELF")  # looks like a standalone binary
        self.versions = list(versions)
        self.updates: list[list[str]] = []

    def run(self, cmd, **kwargs):
        if cmd[-1] == "--version":
            return _proc(cmd, out=self.versions[0] + "\n")
        if "--update-to" in cmd:
            self.updates.append(cmd)
            if len(self.versions) > 1:
                self.versions.pop(0)
            return _proc(cmd)
        raise AssertionError(f"unexpected command {cmd}")


class TestTryUpdate:
    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv(ytdlp.NO_UPDATE_ENV, "1")
        changed, msg = ytdlp.try_update("stable")
        assert not changed and "off" in msg

    def test_updates_once_per_channel(self, monkeypatch, tmp_path):
        monkeypatch.delenv(ytdlp.NO_UPDATE_ENV)
        fake = _FakeInstall(tmp_path, ["2026.08.01", "2026.11.01"])
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: str(fake.path) if name == "yt-dlp" else None)
        monkeypatch.setattr(subprocess, "run", fake.run)
        changed, msg = ytdlp.try_update("stable")
        assert changed and "2026.08.01 -> 2026.11.01" in msg
        again, _ = ytdlp.try_update("stable")
        assert not again
        assert len(fake.updates) == 1

    def test_unchanged_version_is_not_reported_as_updated(self, monkeypatch, tmp_path):
        monkeypatch.delenv(ytdlp.NO_UPDATE_ENV)
        fake = _FakeInstall(tmp_path, ["2026.11.01"])
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: str(fake.path) if name == "yt-dlp" else None)
        monkeypatch.setattr(subprocess, "run", fake.run)
        changed, msg = ytdlp.try_update("stable")
        assert not changed and "still 2026.11.01" in msg


class TestWithUpdateRetry:
    def test_success_needs_no_update(self, monkeypatch):
        monkeypatch.setattr(ytdlp, "try_update", lambda c: pytest.fail("should not update"))
        assert ytdlp.with_update_retry(lambda: "ok", failed=lambda o: False, output=lambda o: "") == "ok"

    def test_access_failure_is_not_retried(self, monkeypatch):
        monkeypatch.setattr(ytdlp, "try_update", lambda c: pytest.fail("should not update"))
        out = ytdlp.with_update_retry(
            lambda: None, failed=lambda o: True, output=lambda o: "Private video"
        )
        assert out is None

    def test_retries_after_stable_update(self, monkeypatch):
        channels = []
        monkeypatch.setattr(ytdlp, "try_update", lambda c: (channels.append(c) or True, "updated"))
        results = iter([None, "video"])
        out = ytdlp.with_update_retry(
            lambda: next(results),
            failed=lambda o: o is None,
            output=lambda o: "Unable to extract nsig",
        )
        assert out == "video"
        assert channels == ["stable"]

    def test_falls_through_to_nightly(self, monkeypatch):
        channels = []

        def fake_update(channel):
            channels.append(channel)
            return channel == "nightly", ""

        monkeypatch.setattr(ytdlp, "try_update", fake_update)
        results = iter([None, "video"])
        out = ytdlp.with_update_retry(
            lambda: next(results),
            failed=lambda o: o is None,
            output=lambda o: "Unable to extract nsig",
        )
        assert out == "video"
        assert channels == ["stable", "nightly"]


class TestDownloadUrlUpdates:
    def test_download_retries_after_update(self, monkeypatch, tmp_path):
        """A broken extraction triggers an update, then the retry succeeds."""
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: "yt-dlp" if name == "yt-dlp" else None)
        updated = {"done": False}

        def fake_update(channel):
            updated["done"] = True
            return True, "updated"

        monkeypatch.setattr(ytdlp, "try_update", fake_update)

        def fake_run(cmd, **kwargs):
            if not updated["done"]:
                return _proc(cmd, 1, "", "ERROR: [youtube] x: Unable to extract nsig function")
            (tmp_path / "video.mp4").write_bytes(b"data")
            (tmp_path / "video.info.json").write_text(
                json.dumps({"title": "T", "_version": {"version": "2026.11.01"}, "height": 1080})
            )
            return _proc(cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = download.download_url("https://vimeo.com/123", tmp_path)
        assert result["video_path"].endswith("video.mp4")
        marker = json.loads((tmp_path / ".source.json").read_text())
        assert marker["ytdlp_version"] == "2026.11.01"
        assert marker["complete"] is True

    def test_failure_message_offers_local_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: "yt-dlp" if name == "yt-dlp" else None)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _proc(cmd, 1, "", "ERROR: boom"))
        with pytest.raises(SystemExit, match="local file path"):
            download.download_url("https://vimeo.com/123", tmp_path)


class TestDirectMediaFallback:
    def test_is_direct_media(self):
        assert download.is_direct_media("https://cdn.example.com/a/clip.mp4?sig=1")
        assert download.is_direct_media("https://cdn.example.com/live/index.m3u8")
        assert not download.is_direct_media("https://www.youtube.com/watch?v=abc")
        assert not download.is_direct_media("/local/clip.mp4")

    def test_missing_ytdlp_uses_ffmpeg_for_direct_links(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: None)
        monkeypatch.setattr(download, "_resolve_tool", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
        seen = []

        def fake_run(cmd, **kwargs):
            seen.append(cmd)
            Path(cmd[-1]).write_bytes(b"video")
            return _proc(cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = download.download_url("https://cdn.example.com/clip.webm", tmp_path)
        assert seen[0][0] == "/usr/bin/ffmpeg"
        assert result["video_path"].endswith("video.mkv")
        assert json.loads((tmp_path / ".source.json").read_text())["client"] == "ffmpeg"

    def test_missing_ytdlp_for_a_page_url_explains_both_options(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: None)
        with pytest.raises(SystemExit) as exc:
            download.download_url("https://www.youtube.com/watch?v=abc", tmp_path)
        assert "pipx install" in str(exc.value) and "local file path" in str(exc.value)

    def test_ytdlp_failure_falls_back_to_ffmpeg(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: "yt-dlp" if name == "yt-dlp" else None)
        monkeypatch.setattr(download, "_resolve_tool", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)

        def fake_run(cmd, **kwargs):
            if cmd[0] == "yt-dlp":
                return _proc(cmd, 1, "", "ERROR: Unsupported URL")
            Path(cmd[-1]).write_bytes(b"video")
            return _proc(cmd)

        monkeypatch.setattr(subprocess, "run", fake_run)
        result = download.download_url("https://cdn.example.com/clip.mp4", tmp_path)
        assert result["video_path"].endswith("video.mp4")

    def test_ffmpeg_failure_mentions_local_file(self, monkeypatch, tmp_path):
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: None)
        monkeypatch.setattr(download, "_resolve_tool", lambda name: "/usr/bin/ffmpeg" if name == "ffmpeg" else None)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _proc(cmd, 1, "", "404 Not Found"))
        with pytest.raises(SystemExit, match="local file path"):
            download.download_url("https://cdn.example.com/clip.mp4", tmp_path)


class TestHealthCheck:
    def _info(self, height):
        return json.dumps({
            "title": "Metropolis",
            "_version": {"version": "2026.11.01"},
            "formats": [
                {"height": 90, "vcodec": "none", "acodec": "none", "ext": "mhtml"},
                {"height": 360, "vcodec": "avc1"},
                {"height": height, "vcodec": "avc1"},
            ],
        })

    def test_healthy(self, monkeypatch):
        import health_check

        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: name if name in {"yt-dlp", "deno"} else None)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _proc(cmd, out=self._info(1080)))
        monkeypatch.setattr(ytdlp, "staleness_warning", lambda v, **k: None)
        report = health_check.check("https://www.youtube.com/watch?v=x", 1080)
        assert report["status"] == "healthy"
        assert report["max_height"] == 1080 and report["js_runtime"] == "deno"

    def test_low_quality_needs_attention(self, monkeypatch):
        import health_check

        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: name if name in {"yt-dlp", "deno"} else None)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **k: _proc(cmd, out=self._info(360)))
        monkeypatch.setattr(ytdlp, "staleness_warning", lambda v, **k: None)
        report = health_check.check("https://www.youtube.com/watch?v=x", 1080)
        assert report["status"] == "needs_attention"
        assert "360p" in report["problems"][0]

    def test_not_installed(self, monkeypatch):
        import health_check

        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: None)
        assert health_check.main(["--no-update"]) == 2

    def test_storyboards_are_not_video_and_bot_check_is_named(self, monkeypatch):
        import health_check

        info = json.dumps({"formats": [{"height": 90, "vcodec": "none", "acodec": "none"}]})
        monkeypatch.setattr(ytdlp, "_resolve_tool", lambda name: name if name in {"yt-dlp", "node"} else None)
        monkeypatch.setattr(
            subprocess, "run",
            lambda cmd, **k: _proc(cmd, out=info, err="WARNING: Sign in to confirm you're not a bot"),
        )
        report = health_check.check("https://www.youtube.com/watch?v=x", 1080)
        assert report["max_height"] == 0
        assert "bot check" in report["problems"][0]
