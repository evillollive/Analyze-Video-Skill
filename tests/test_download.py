"""Tests for download.py failure classification and local resolution."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from download import classify_download_error, resolve_local, _clear_download_artifacts, _is_partial


class TestClassifyDownloadError:
    def test_login_required_guidance_mentions_cookies(self):
        result = classify_download_error("Sign in to confirm you're not a bot")
        assert result["kind"] in {"login_required", "bot_check"}
        assert "--cookies-from-browser" in result["guidance"]

    def test_rate_limit_guidance(self):
        result = classify_download_error("HTTP Error 429: Too Many Requests")
        assert result["kind"] == "rate_limited"
        assert "rate-limiting" in result["guidance"]

    def test_unknown_download_failure(self):
        result = classify_download_error("network exploded")
        assert result["kind"] == "download_failed"
        assert "local video file" in result["guidance"]


class TestResolveLocal:
    def test_finds_sibling_subtitle_and_info(self, tmp_path):
        video = tmp_path / "myvid.mp4"
        video.write_bytes(b"x")
        (tmp_path / "myvid.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (tmp_path / "myvid.info.json").write_text(
            json.dumps({"title": "Cool Title", "channel": "Chan", "webpage_url": "http://x"})
        )
        res = resolve_local(str(video))
        assert res["subtitle_path"].endswith("myvid.en.vtt")
        assert res["info"]["title"] == "Cool Title"
        assert res["info"]["uploader"] == "Chan"
        assert res["info"]["url"] == "http://x"
        assert res["downloaded"] is False

    def test_prefers_stem_match_over_other_video_subtitle(self, tmp_path):
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        (tmp_path / "a.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (tmp_path / "b.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        res = resolve_local(str(video))
        assert res["subtitle_path"].endswith("a.en.vtt")

    def test_falls_back_to_filename_when_no_sidecars(self, tmp_path):
        video = tmp_path / "plain.mp4"
        video.write_bytes(b"x")
        res = resolve_local(str(video))
        assert res["subtitle_path"] is None
        assert res["info"]["title"] == "plain.mp4"
        assert res["info"]["url"] == str(video.resolve())

    def test_two_unrelated_subtitles_are_not_attached(self, tmp_path):
        video = tmp_path / "a.mp4"
        video.write_bytes(b"x")
        (tmp_path / "x.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (tmp_path / "y.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        res = resolve_local(str(video))
        assert res["subtitle_path"] is None

    def test_stem_prefix_does_not_grab_longer_stem(self, tmp_path):
        # clip1.mp4 must not pick up clip10.en.vtt when clip10.mp4 also exists.
        (tmp_path / "clip1.mp4").write_bytes(b"x")
        (tmp_path / "clip10.mp4").write_bytes(b"x")
        (tmp_path / "clip10.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        res = resolve_local(str(tmp_path / "clip1.mp4"))
        assert res["subtitle_path"] is None

    def test_lone_vtt_attached_when_single_pair(self, tmp_path):
        # Exactly one video + one subtitle in a folder: treat as a pair.
        (tmp_path / "movie.mp4").write_bytes(b"x")
        (tmp_path / "captions.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        res = resolve_local(str(tmp_path / "movie.mp4"))
        assert res["subtitle_path"].endswith("captions.en.vtt")


class TestClearDownloadArtifacts:
    def test_removes_video_subtitle_info_and_marker(self, tmp_path):
        (tmp_path / "video.mp4").write_bytes(b"x")
        (tmp_path / "video.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (tmp_path / "video.info.json").write_text("{}", encoding="utf-8")
        (tmp_path / ".source.json").write_text("{}", encoding="utf-8")
        # An unrelated file must be left alone.
        (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")

        _clear_download_artifacts(tmp_path)

        assert not (tmp_path / "video.mp4").exists()
        assert not (tmp_path / "video.en.vtt").exists()
        assert not (tmp_path / "video.info.json").exists()
        assert not (tmp_path / ".source.json").exists()
        assert (tmp_path / "keep.txt").exists()

    def test_missing_dir_contents_is_noop(self, tmp_path):
        # No artifacts present: must not raise.
        _clear_download_artifacts(tmp_path)

    def test_partials_removed_by_default(self, tmp_path):
        (tmp_path / "video.f399.mp4.part").write_bytes(b"x")
        (tmp_path / "video.f399.mp4.ytdl").write_text("{}", encoding="utf-8")

        _clear_download_artifacts(tmp_path)

        assert not (tmp_path / "video.f399.mp4.part").exists()
        assert not (tmp_path / "video.f399.mp4.ytdl").exists()

    def test_keep_partials_spares_in_progress_files(self, tmp_path):
        (tmp_path / "video.f399.mp4.part").write_bytes(b"x")
        (tmp_path / "video.f251.m4a.part").write_bytes(b"x")
        (tmp_path / "video.f399.mp4.ytdl").write_text("{}", encoding="utf-8")
        (tmp_path / "video.mp4.part-Frag12").write_bytes(b"x")
        # Non-resumable leftovers must still go: a stale subtitle or info.json
        # from an earlier capture cannot be paired with the resumed download.
        (tmp_path / "video.en.vtt").write_text("WEBVTT\n", encoding="utf-8")
        (tmp_path / "video.info.json").write_text("{}", encoding="utf-8")
        (tmp_path / "video.mp4").write_bytes(b"x")

        _clear_download_artifacts(tmp_path, keep_partials=True)

        assert (tmp_path / "video.f399.mp4.part").exists()
        assert (tmp_path / "video.f251.m4a.part").exists()
        assert (tmp_path / "video.f399.mp4.ytdl").exists()
        assert (tmp_path / "video.mp4.part-Frag12").exists()
        assert not (tmp_path / "video.en.vtt").exists()
        assert not (tmp_path / "video.info.json").exists()
        assert not (tmp_path / "video.mp4").exists()


from download import (  # noqa: E402
    is_youtube,
    _build_ytdlp_cmd,
    _valid_video,
    _source_marker_matches,
    fetch_captions,
    fetch_title,
    download_url,
)
import subprocess as _subprocess  # noqa: E402


class TestIsYoutube:
    def test_youtube_hosts(self):
        assert is_youtube("https://www.youtube.com/watch?v=abc")
        assert is_youtube("https://youtu.be/abc")
        assert is_youtube("https://m.youtube.com/watch?v=abc")
        assert is_youtube("https://music.youtube.com/watch?v=abc")

    def test_non_youtube_hosts(self):
        assert not is_youtube("https://vimeo.com/123")
        assert not is_youtube("https://notyoutube.com/x")
        assert not is_youtube("https://youtube.com.evil.example/x")


class TestBuildYtdlpCmd:
    def test_android_extractor_arg_present(self):
        cmd = _build_ytdlp_cmd(
            "yt-dlp", "u", "/o/video.%(ext)s",
            cookie_path=None, cookies_from_browser=None, player_client="android",
        )
        assert "--extractor-args" in cmd
        assert "youtube:player-client=android" in cmd

    def test_no_extractor_arg_when_client_none(self):
        cmd = _build_ytdlp_cmd(
            "yt-dlp", "u", "/o/video.%(ext)s",
            cookie_path=None, cookies_from_browser=None, player_client=None,
        )
        assert "--extractor-args" not in cmd

    def test_cookies_appended(self):
        cmd = _build_ytdlp_cmd(
            "yt-dlp", "u", "/o/video.%(ext)s",
            cookie_path=Path("/tmp/c.txt"), cookies_from_browser=None, player_client=None,
        )
        assert "--cookies" in cmd and "/tmp/c.txt" in cmd


class TestValidVideo:
    def test_zero_byte_video_is_invalid(self, tmp_path):
        (tmp_path / "video.mp4").write_bytes(b"")
        assert _valid_video(tmp_path) is None

    def test_nonempty_video_is_valid(self, tmp_path):
        (tmp_path / "video.mp4").write_bytes(b"data")
        assert _valid_video(tmp_path) is not None


class TestSourceMarkerAuthGating:
    def test_anon_request_reuses_anon_marker(self, tmp_path):
        (tmp_path / ".source.json").write_text(json.dumps({"url": "u", "auth": "none"}))
        assert _source_marker_matches(tmp_path, "u", "none") is True

    def test_auth_request_rejects_anon_marker(self, tmp_path):
        (tmp_path / ".source.json").write_text(json.dumps({"url": "u", "auth": "none"}))
        assert _source_marker_matches(tmp_path, "u", "cookies") is False

    def test_auth_request_reuses_auth_marker(self, tmp_path):
        (tmp_path / ".source.json").write_text(json.dumps({"url": "u", "auth": "cookies"}))
        assert _source_marker_matches(tmp_path, "u", "cookies") is True

    def test_url_mismatch_never_reuses(self, tmp_path):
        (tmp_path / ".source.json").write_text(json.dumps({"url": "u", "auth": "none"}))
        assert _source_marker_matches(tmp_path, "other", "none") is False

    def test_auth_request_ignores_info_json_only(self, tmp_path):
        (tmp_path / "video.info.json").write_text(json.dumps({"webpage_url": "u"}))
        assert _source_marker_matches(tmp_path, "u", "cookies") is False
        assert _source_marker_matches(tmp_path, "u", "none") is True


def _fake_runner(out_dir, succeed_on):
    """Return a subprocess.run stand-in that writes video.mp4 for chosen clients.

    succeed_on: "android", "web", or "never".
    """
    calls = []

    def run(cmd, capture_output=True, text=True):
        is_android = "youtube:player-client=android" in cmd
        client = "android" if is_android else "web"
        calls.append(client)
        produce = (succeed_on == client)
        if produce:
            (out_dir / "video.mp4").write_bytes(b"data")
            rc = 0
        else:
            rc = 1
        return _subprocess.CompletedProcess(cmd, rc, stdout="", stderr="HTTP Error 403")

    run.calls = calls
    return run


class TestDownloadUrlAttempts:
    def test_youtube_android_first_then_web_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = _fake_runner(tmp_path, succeed_on="web")
        monkeypatch.setattr("download.subprocess.run", runner)
        res = download_url("https://www.youtube.com/watch?v=x", tmp_path)
        assert res["downloaded"] is True
        assert runner.calls == ["android", "web"]
        marker = json.loads((tmp_path / ".source.json").read_text())
        assert marker["client"] == "web" and marker["auth"] == "none"

    def test_youtube_android_succeeds_first(self, tmp_path, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = _fake_runner(tmp_path, succeed_on="android")
        monkeypatch.setattr("download.subprocess.run", runner)
        download_url("https://youtu.be/x", tmp_path)
        assert runner.calls == ["android"]
        assert json.loads((tmp_path / ".source.json").read_text())["client"] == "android"

    def test_non_youtube_uses_web_only(self, tmp_path, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = _fake_runner(tmp_path, succeed_on="web")
        monkeypatch.setattr("download.subprocess.run", runner)
        download_url("https://vimeo.com/123", tmp_path)
        assert runner.calls == ["web"]

    def test_youtube_with_cookies_skips_android(self, tmp_path, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        cookie = tmp_path / "c.txt"
        cookie.write_text("# cookies")
        runner = _fake_runner(tmp_path, succeed_on="web")
        monkeypatch.setattr("download.subprocess.run", runner)
        download_url("https://www.youtube.com/watch?v=x", tmp_path, cookies=str(cookie))
        assert runner.calls == ["web"]
        assert json.loads((tmp_path / ".source.json").read_text())["auth"] == "cookies"

    def test_all_attempts_fail_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = _fake_runner(tmp_path, succeed_on="never")
        monkeypatch.setattr("download.subprocess.run", runner)
        try:
            download_url("https://www.youtube.com/watch?v=x", tmp_path)
            assert False, "expected SystemExit"
        except SystemExit as exc:
            assert "forbidden" in str(exc)


class TestDownloadResumesPartials:
    """An interrupted download must be resumable instead of restarting at zero.

    yt-dlp resumes `.part` files by default, so the only thing that matters is
    whether they survive to the next run.
    """

    @staticmethod
    def _observing_runner(out_dir, succeed_on="android"):
        """Runner that records the directory state yt-dlp would have seen."""
        seen = []

        def run(cmd, capture_output=True, text=True):
            client = "android" if "youtube:player-client=android" in cmd else "web"
            seen.append({
                "client": client,
                "partials": sorted(p.name for p in out_dir.glob("*") if _is_partial(p)),
                "marker": json.loads((out_dir / ".source.json").read_text()),
            })
            if client == succeed_on:
                (out_dir / "video.mp4").write_bytes(b"data")
                return _subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")
            return _subprocess.CompletedProcess(cmd, 1, stdout="", stderr="HTTP Error 403")

        run.seen = seen
        return run

    @staticmethod
    def _interrupted(out_dir, url, client="android", auth="none"):
        """Leave behind exactly what a run killed mid-download would."""
        (out_dir / ".source.json").write_text(
            json.dumps({"url": url, "auth": auth, "client": client, "complete": False})
        )
        (out_dir / "video.f399.mp4.part").write_bytes(b"partial-video")
        (out_dir / "video.f399.mp4.ytdl").write_text("{}", encoding="utf-8")

    def test_partials_survive_for_matching_request(self, tmp_path, monkeypatch):
        url = "https://www.youtube.com/watch?v=x"
        self._interrupted(tmp_path, url)
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = self._observing_runner(tmp_path)
        monkeypatch.setattr("download.subprocess.run", runner)

        download_url(url, tmp_path)

        assert runner.seen[0]["partials"] == [
            "video.f399.mp4.part",
            "video.f399.mp4.ytdl",
        ]

    def test_force_discards_partials(self, tmp_path, monkeypatch):
        url = "https://www.youtube.com/watch?v=x"
        self._interrupted(tmp_path, url)
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = self._observing_runner(tmp_path)
        monkeypatch.setattr("download.subprocess.run", runner)

        download_url(url, tmp_path, force=True)

        assert runner.seen[0]["partials"] == []

    def test_different_url_discards_partials(self, tmp_path, monkeypatch):
        self._interrupted(tmp_path, "https://www.youtube.com/watch?v=other")
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = self._observing_runner(tmp_path)
        monkeypatch.setattr("download.subprocess.run", runner)

        download_url("https://www.youtube.com/watch?v=x", tmp_path)

        assert runner.seen[0]["partials"] == []

    def test_partials_survive_the_player_client_fallback(self, tmp_path, monkeypatch):
        # The android attempt runs first and fails; it must not destroy the
        # partials before the web attempt (which recorded them) gets to resume.
        url = "https://www.youtube.com/watch?v=x"
        self._interrupted(tmp_path, url, client="web")
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = self._observing_runner(tmp_path, succeed_on="web")
        monkeypatch.setattr("download.subprocess.run", runner)

        download_url(url, tmp_path)

        assert [s["client"] for s in runner.seen] == ["android", "web"]
        assert runner.seen[1]["partials"] == [
            "video.f399.mp4.part",
            "video.f399.mp4.ytdl",
        ]

    def test_anon_partials_not_reused_for_authenticated_request(self, tmp_path, monkeypatch):
        url = "https://www.youtube.com/watch?v=x"
        self._interrupted(tmp_path, url, auth="none")
        cookie = tmp_path / "c.txt"
        cookie.write_text("# cookies")
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = self._observing_runner(tmp_path, succeed_on="web")
        monkeypatch.setattr("download.subprocess.run", runner)

        download_url(url, tmp_path, cookies=str(cookie))

        assert runner.seen[0]["partials"] == []

    def test_marker_written_before_download_so_a_kill_is_recoverable(self, tmp_path, monkeypatch):
        url = "https://www.youtube.com/watch?v=x"
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = self._observing_runner(tmp_path)
        monkeypatch.setattr("download.subprocess.run", runner)

        download_url(url, tmp_path)

        in_flight = runner.seen[0]["marker"]
        assert in_flight == {
            "url": url,
            "auth": "none",
            "client": "android",
            "complete": False,
        }
        assert json.loads((tmp_path / ".source.json").read_text())["complete"] is True

    def test_successful_download_sweeps_leftover_partials(self, tmp_path, monkeypatch):
        url = "https://www.youtube.com/watch?v=x"
        self._interrupted(tmp_path, url, client="web")
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        monkeypatch.setattr(
            "download.subprocess.run", self._observing_runner(tmp_path, succeed_on="android")
        )

        download_url(url, tmp_path)

        assert sorted(p.name for p in tmp_path.glob("*") if _is_partial(p)) == []

    def test_completed_download_is_still_reused_without_redownloading(self, tmp_path, monkeypatch):
        url = "https://www.youtube.com/watch?v=x"
        (tmp_path / ".source.json").write_text(
            json.dumps({"url": url, "auth": "none", "client": "android", "complete": True})
        )
        (tmp_path / "video.mp4").write_bytes(b"data")
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        runner = self._observing_runner(tmp_path)
        monkeypatch.setattr("download.subprocess.run", runner)

        res = download_url(url, tmp_path)

        assert runner.seen == []
        assert res["video_path"].endswith("video.mp4")


class TestFetchCaptions:
    def test_writes_vtt_via_android_for_youtube(self, tmp_path, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")

        def run(cmd, capture_output=True, text=True):
            assert "--skip-download" in cmd
            assert "youtube:player-client=android" in cmd
            (tmp_path / "captions.en.vtt").write_text("WEBVTT\n")
            return _subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr("download.subprocess.run", run)
        sub = fetch_captions("https://youtu.be/x", tmp_path)
        assert sub.name == "captions.en.vtt"

    def test_no_subs_raises(self, tmp_path, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        monkeypatch.setattr(
            "download.subprocess.run",
            lambda *a, **k: _subprocess.CompletedProcess([], 1, stdout="", stderr="no subs"),
        )
        try:
            fetch_captions("https://vimeo.com/1", tmp_path)
            assert False, "expected SystemExit"
        except SystemExit:
            pass


class TestFetchTitle:
    def test_youtube_uses_android_first(self, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")

        calls = []

        def run(cmd, capture_output=True, text=True):
            calls.append(cmd)
            return _subprocess.CompletedProcess(cmd, 0, stdout="Real Title\n", stderr="")

        monkeypatch.setattr("download.subprocess.run", run)
        title = fetch_title("https://youtu.be/x")
        assert title == "Real Title"
        assert any("youtube:player-client=android" in str(part) for part in calls[0])

    def test_falls_back_to_web_when_android_fails(self, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        calls = []

        def run(cmd, capture_output=True, text=True):
            calls.append(cmd)
            is_android = any("youtube:player-client=android" == part for part in cmd)
            if is_android:
                return _subprocess.CompletedProcess(cmd, 1, stdout="", stderr="403")
            return _subprocess.CompletedProcess(cmd, 0, stdout="Recovered Title\n", stderr="")

        monkeypatch.setattr("download.subprocess.run", run)
        title = fetch_title("https://www.youtube.com/watch?v=x")
        assert title == "Recovered Title"
        assert len(calls) == 2

    def test_returns_none_when_all_attempts_fail(self, monkeypatch):
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        monkeypatch.setattr(
            "download.subprocess.run",
            lambda *a, **k: _subprocess.CompletedProcess([], 1, stdout="", stderr="blocked"),
        )
        assert fetch_title("https://vimeo.com/1") is None

    def test_stale_vtt_does_not_short_circuit_failure(self, tmp_path, monkeypatch):
        # A leftover captions file from a prior run must not be returned when the
        # current fetch produces nothing; the failure path must still fire.
        monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
        (tmp_path / "captions.en.vtt").write_text("WEBVTT\nstale\n")
        monkeypatch.setattr(
            "download.subprocess.run",
            lambda *a, **k: _subprocess.CompletedProcess([], 1, stdout="", stderr="no subs"),
        )
        try:
            fetch_captions("https://vimeo.com/1", tmp_path)
            assert False, "expected SystemExit"
        except SystemExit:
            pass
