"""Best copy: Premium streams, free-space check, cookie copy, bot-check message, playback note.

From real YouTube Premium downloads on 2026-11-14.
"""
import subprocess
import sys
from collections import namedtuple
from pathlib import Path

import pytest

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import download  # noqa: E402
from download import (  # noqa: E402
    _access_problem,
    _check_free_space,
    _estimated_size,
    _prefer_premium,
    download_best_copy,
    playback_note,
)

H264_1080 = {"format_id": "137", "vcodec": "avc1.640028", "acodec": "none", "protocol": "https",
             "height": 1080, "fps": 30, "tbr": 1700, "format_note": "1080p"}
OPUS = {"format_id": "251", "vcodec": "none", "acodec": "opus", "protocol": "https",
        "format_note": "medium", "tbr": 129}
VP9_PREMIUM = {"format_id": "356", "vcodec": "vp9", "acodec": "none", "protocol": "https",
               "height": 1080, "fps": 30, "tbr": 1830, "format_note": "1080p Premium"}
AV1_PREMIUM = {"format_id": "721", "vcodec": "av01.0.08M.08", "acodec": "none", "protocol": "https",
               "height": 1080, "fps": 30, "tbr": 1038, "format_note": "1080p Premium"}
HLS_PREMIUM = {"format_id": "616", "vcodec": "vp09.00.40.08", "acodec": "none", "protocol": "m3u8_native",
               "height": 1080, "fps": 30, "tbr": 4609, "format_note": "Premium"}


def _info(selected, *formats):
    return {"requested_formats": [selected, OPUS], "formats": [selected, OPUS, *formats],
            "duration": 5400}


class TestPreferPremium:
    def test_premium_vp9_beats_regular_h264(self):
        assert _prefer_premium(_info(H264_1080, VP9_PREMIUM), "mkv")["format_id"] == "356"

    def test_premium_av1_beats_higher_bitrate_regular_h264(self):
        # yt-dlp's bitrate sort picked 137; the Premium AV1 encode is the better copy.
        assert _prefer_premium(_info(H264_1080, AV1_PREMIUM), "mkv")["format_id"] == "721"

    def test_https_premium_beats_hls_premium_despite_nominal_bitrate(self):
        chosen = _prefer_premium(_info(H264_1080, HLS_PREMIUM, VP9_PREMIUM), "mkv")
        assert chosen["format_id"] == "356"

    def test_hls_premium_used_when_it_is_the_only_premium(self):
        assert _prefer_premium(_info(H264_1080, HLS_PREMIUM), "mkv")["format_id"] == "616"

    def test_vp9_beats_av1_among_premium(self):
        assert _prefer_premium(_info(H264_1080, AV1_PREMIUM, VP9_PREMIUM), "mkv")["format_id"] == "356"

    def test_lower_resolution_premium_never_replaces_4k(self):
        uhd = {**H264_1080, "format_id": "313", "vcodec": "vp9", "height": 2160}
        assert _prefer_premium(_info(uhd, VP9_PREMIUM), "mkv") is None

    def test_mp4_ignores_premium_that_is_not_h264(self):
        assert _prefer_premium(_info(H264_1080, VP9_PREMIUM, AV1_PREMIUM), "mp4") is None

    def test_no_premium_formats_means_no_change(self):
        assert _prefer_premium(_info(H264_1080), "mkv") is None


class TestFreeSpace:
    Usage = namedtuple("Usage", "total used free")

    def test_estimate_uses_filesize_then_bitrate(self):
        info = {"duration": 100, "requested_formats": [
            {"vcodec": "vp9", "filesize": 1_000_000}, {"acodec": "opus", "vcodec": "none", "tbr": 128}]}
        assert _estimated_size(info) == 1_000_000 + 1_600_000

    def test_unknown_size_skips_the_check(self, tmp_path):
        assert _estimated_size({"requested_formats": [{"vcodec": "vp9"}]}) is None
        _check_free_space(None, tmp_path, tmp_path)

    def test_stops_when_temp_folder_is_too_small(self, tmp_path, monkeypatch):
        monkeypatch.setattr(download.shutil, "disk_usage", lambda p: self.Usage(10, 9, 1_400_000_000))
        with pytest.raises(SystemExit, match="Not enough free space in the temp folder"):
            _check_free_space(1_800_000_000, tmp_path, tmp_path)

    def test_passes_with_room(self, tmp_path, monkeypatch):
        monkeypatch.setattr(download.shutil, "disk_usage", lambda p: self.Usage(10, 1, 600_000_000_000))
        _check_free_space(1_800_000_000, tmp_path, tmp_path)


class TestAccessProblem:
    def test_storyboards_only_with_bot_check_is_reported_as_blocked(self):
        info = {"formats": [{"format_id": "sb0", "ext": "mhtml", "vcodec": "none", "acodec": "none"}]}
        message = _access_problem(info, "WARNING: Sign in to confirm you're not a bot")
        assert message.startswith("The site blocked the download")
        assert "cookies" in message

    def test_real_formats_are_not_an_access_problem(self):
        assert _access_problem({"formats": [H264_1080, OPUS]}, "Sign in to confirm") is None


class TestPlaybackNote:
    def test_mkv_vp9_names_players_and_says_no_reencode(self):
        note = playback_note("vp9", "opus", "mkv")
        assert "No re-encode is needed" in note
        assert "VLC" in note and "QuickTime" in note

    def test_mp4_h264_plays_in_quicktime(self):
        assert "plays in QuickTime" in playback_note("h264", "aac", "mp4")

    def test_audio_only_has_no_note(self):
        assert playback_note(None, "opus", "audio") is None


def _common(monkeypatch, info):
    monkeypatch.setattr("download._resolve_tool", lambda name: "yt-dlp")
    monkeypatch.setattr("ytdlp._resolve_tool", lambda name: "yt-dlp")
    monkeypatch.setattr("ytdlp.runtime_args", lambda: [])
    monkeypatch.setattr(download.shutil, "disk_usage",
                        lambda p: TestFreeSpace.Usage(10, 1, 600_000_000_000))
    monkeypatch.setattr("download._probe_copy", lambda path: {
        "path": str(path), "height": 1080, "video_codec": "vp9", "audio_codec": "opus"})
    seen = {}

    def inspect(*a, **k):
        seen["cookie"] = k.get("cookie_path")
        seen["cookie_text"] = Path(k["cookie_path"]).read_text() if k.get("cookie_path") else None
        return info, subprocess.CompletedProcess([], 0, "", "")

    monkeypatch.setattr("download._inspect_best_format", inspect)
    return seen


class TestBestCopyEndToEnd:
    def test_downloads_premium_and_leaves_users_cookies_untouched(self, tmp_path, monkeypatch):
        cookies = tmp_path / "youtube-cookies.txt"
        cookies.write_text("# Netscape HTTP Cookie File\nsecret\n")
        info = {"title": "Movie", **_info(H264_1080, VP9_PREMIUM)}
        seen = _common(monkeypatch, info)
        final = tmp_path / "Movie [abc].mkv"
        commands = []

        def run(cmd, capture_output=True, text=True):
            commands.append(cmd)
            used = Path(cmd[cmd.index("--cookies") + 1])
            assert used != cookies.resolve()
            used.write_text("")  # yt-dlp rewriting (or emptying) its cookies file
            final.write_bytes(b"v")
            return subprocess.CompletedProcess(cmd, 0, f"{final}\n", "")

        monkeypatch.setattr("download.subprocess.run", run)
        result = download_best_copy("https://www.youtube.com/watch?v=abc", tmp_path,
                                    cookies=str(cookies), temp_dir=tmp_path / "s")
        assert commands[0][commands[0].index("-f") + 1] == "356+251"
        assert result["premium_stream"] is True
        assert "No re-encode" in result["playback_note"]
        assert seen["cookie_text"].startswith("# Netscape")
        assert cookies.read_text().endswith("secret\n"), "the user's own file is never rewritten"
        assert not list((tmp_path / "s").glob(".cookies-*")), "the private copy is deleted"

    def test_bot_check_gives_a_clear_message(self, tmp_path, monkeypatch):
        info = {"title": "x", "formats": [{"format_id": "sb0", "ext": "mhtml", "vcodec": "none",
                                           "acodec": "none"}]}
        _common(monkeypatch, info)
        monkeypatch.setattr("download._inspect_best_format", lambda *a, **k: (
            info, subprocess.CompletedProcess([], 0, "", "ERROR: Sign in to confirm you're not a bot")))
        monkeypatch.setattr("download.subprocess.run",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not download")))
        with pytest.raises(SystemExit) as exc:
            download_best_copy("https://www.youtube.com/watch?v=abc", tmp_path, temp_dir=tmp_path / "s")
        assert "blocked the download (bot check)" in str(exc.value)
        assert "own computer" in str(exc.value)
        assert "audio stream" not in str(exc.value)


def test_leftover_cookie_copies_from_killed_runs_are_cleared(tmp_path):
    from download import _private_cookie_copy
    (tmp_path / ".cookies-123.txt").write_text("old sign-in")
    assert _private_cookie_copy(None, tmp_path) is None
    assert not list(tmp_path.glob(".cookies-*"))
