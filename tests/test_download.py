"""Tests for download.py failure classification."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from download import classify_download_error


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
