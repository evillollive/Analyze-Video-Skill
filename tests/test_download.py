"""Tests for download.py failure classification and local resolution."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from download import classify_download_error, resolve_local


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
