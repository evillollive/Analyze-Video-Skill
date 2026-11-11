"""Tests for env_utils.py shared module."""
import sys
from pathlib import Path

import pytest

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

    @pytest.mark.skipif(sys.platform == "win32", reason="Windows has no executable permission bit")
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


class TestWindowsToolLookup:
    """On Windows a freshly installed tool is not on the running shell's PATH."""

    def test_finds_exe_in_a_known_dir(self, tmp_path, monkeypatch):
        tooldir = tmp_path / "Links"
        tooldir.mkdir()
        exe = tooldir / "ffmpeg.exe"
        exe.write_text("x")
        exe.chmod(0o755)
        monkeypatch.setenv("PATH", "")
        monkeypatch.setattr(env_utils.os, "name", "nt")
        monkeypatch.setattr(env_utils, "_user_bin_dirs", lambda: [tooldir])
        monkeypatch.setattr(env_utils, "_windows_winget_package_tool", lambda name: None)
        monkeypatch.setattr(env_utils.shutil, "which", lambda name: None)
        try:
            found = resolve_tool("ffmpeg")
        finally:
            monkeypatch.undo()
        assert found == str(exe)

    def test_candidate_names_add_windows_extensions(self, monkeypatch):
        monkeypatch.setattr(env_utils.os, "name", "nt")
        try:
            names = env_utils._candidate_names("npm")
            keeps_suffix = env_utils._candidate_names("npm.cmd")
        finally:
            monkeypatch.undo()
        assert names[:3] == ["npm.exe", "npm.cmd", "npm.bat"]
        assert keeps_suffix == ["npm.cmd"]

    def test_finds_tool_inside_a_winget_package_folder(self, tmp_path, monkeypatch):
        pkg = tmp_path / "Microsoft" / "WinGet" / "Packages" / "Gyan.FFmpeg_x" / "ffmpeg-7-full_build" / "bin"
        pkg.mkdir(parents=True)
        (pkg / "ffprobe.exe").write_text("x")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
        assert env_utils._windows_winget_package_tool("ffprobe") == str(pkg / "ffprobe.exe")
        assert env_utils._windows_winget_package_tool("nope") is None

    def test_posix_names_are_unchanged(self):
        if env_utils.os.name != "nt":
            assert env_utils._candidate_names("ffmpeg") == ["ffmpeg"]
