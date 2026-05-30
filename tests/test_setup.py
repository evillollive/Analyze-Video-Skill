"""Tests for setup.py key management."""
import sys
from pathlib import Path
from unittest.mock import patch

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import setup


class TestSetKey:
    def test_rejects_newline_in_key(self):
        """Keys with embedded newlines should be rejected (injection risk)."""
        with patch.object(setup, "CONFIG_DIR", Path("/tmp/test-setup")):
            with patch.object(setup, "CONFIG_FILE", Path("/tmp/test-setup/.env")):
                result = setup._set_key("openai", "sk-abc\nGROQ_API_KEY=injected")
                assert result == 2

    def test_rejects_empty_key(self):
        result = setup._set_key("openai", "")
        assert result == 2

    def test_rejects_whitespace_only_key(self):
        result = setup._set_key("openai", "   ")
        assert result == 2

    def test_rejects_unknown_backend(self):
        result = setup._set_key("gemini", "sk-abc123")
        assert result == 2

    def test_writes_key_to_file(self, tmp_path):
        config_dir = tmp_path / "config"
        config_file = config_dir / ".env"
        with patch.object(setup, "CONFIG_DIR", config_dir):
            with patch.object(setup, "CONFIG_FILE", config_file):
                result = setup._set_key("openai", "sk-test123")
                assert result == 0
                content = config_file.read_text()
                assert "OPENAI_API_KEY=sk-test123" in content

    def test_replaces_existing_key(self, tmp_path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        config_file = config_dir / ".env"
        config_file.write_text("OPENAI_API_KEY=old-key\nGROQ_API_KEY=groq-key\n")
        with patch.object(setup, "CONFIG_DIR", config_dir):
            with patch.object(setup, "CONFIG_FILE", config_file):
                result = setup._set_key("openai", "new-key")
                assert result == 0
                content = config_file.read_text()
                assert "OPENAI_API_KEY=new-key" in content
                assert "old-key" not in content
                assert "GROQ_API_KEY=groq-key" in content
