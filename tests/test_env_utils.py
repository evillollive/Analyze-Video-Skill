"""Tests for env_utils.py shared module."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from env_utils import read_dotenv_key


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
