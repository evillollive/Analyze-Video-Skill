"""Tests for env_utils.py shared module."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from env_utils import read_dotenv_key, resolve_tool
import env_utils


class TestReadDotenvKey:
    def test_reads_simple_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("API_KEY=abc123\n")
        assert read_dotenv_key(env, "API_KEY") == "abc123"

    def test_reads_quoted_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text('API_KEY="abc 123"\n')
        assert read_dotenv_key(env, "API_KEY") == "abc 123"

    def test_single_quoted_key(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("API_KEY='abc 123'\n")
        assert read_dotenv_key(env, "API_KEY") == "abc 123"

    def test_ignores_comments(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("# API_KEY=secret\nAPI_KEY=real\n")
        assert read_dotenv_key(env, "API_KEY") == "real"

    def test_returns_none_for_missing(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("OTHER_KEY=value\n")
        assert read_dotenv_key(env, "API_KEY") is None

    def test_returns_none_for_empty_value(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("API_KEY=\n")
        assert read_dotenv_key(env, "API_KEY") is None

    def test_returns_none_for_missing_file(self, tmp_path):
        env = tmp_path / "nonexistent"
        assert read_dotenv_key(env, "API_KEY") is None

    def test_ignores_blank_lines(self, tmp_path):
        env = tmp_path / ".env"
        env.write_text("\n\n\nKEY=val\n\n")
        assert read_dotenv_key(env, "KEY") == "val"


class TestResolveTool:
    def test_finds_on_path(self):
        """A tool on PATH resolves to an existing absolute path."""
        # python3 is always on PATH in the test environment.
        resolved = resolve_tool("python3")
        assert resolved is not None
        assert Path(resolved).is_absolute()
        assert Path(resolved).exists()

    def test_finds_user_local_bin_off_path(self, tmp_path, monkeypatch):
        """A tool only present in a user-local bin (not on PATH) still resolves."""
        userbin = tmp_path / "userbin"
        userbin.mkdir()
        fake = userbin / "yt-dlp"
        fake.write_text("#!/bin/sh\necho fake\n")
        fake.chmod(0o755)

        # Empty PATH so shutil.which can't find it; force our user-bin fallback.
        monkeypatch.setenv("PATH", "")
        monkeypatch.setattr(env_utils, "_user_bin_dirs", lambda: [userbin])

        resolved = resolve_tool("yt-dlp")
        assert resolved == str(fake)

    def test_returns_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setenv("PATH", "")
        monkeypatch.setattr(env_utils, "_user_bin_dirs", lambda: [tmp_path])
        assert resolve_tool("definitely-not-a-real-tool-xyz") is None

    def test_ignores_non_executable_user_bin_entry(self, tmp_path, monkeypatch):
        """A non-executable file in a user-bin dir must not be treated as the tool."""
        userbin = tmp_path / "userbin"
        userbin.mkdir()
        notexec = userbin / "yt-dlp"
        notexec.write_text("not executable")
        notexec.chmod(0o644)

        monkeypatch.setenv("PATH", "")
        monkeypatch.setattr(env_utils, "_user_bin_dirs", lambda: [userbin])
        assert resolve_tool("yt-dlp") is None
