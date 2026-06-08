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


class TestStatus:
    def test_ready_without_whisper_key_when_required_deps_exist(self):
        with patch.object(setup, "_docx_available", return_value=True):
            with patch.object(setup, "_check_binaries", return_value=[]):
                with patch.object(setup, "_have_api_key", return_value=(False, None)):
                    status = setup._status()
                    assert status["status"] == "ready_no_whisper_key"

    def test_check_succeeds_without_whisper_key(self):
        with patch.object(setup, "_docx_available", return_value=True):
            with patch.object(setup, "_check_binaries", return_value=[]):
                with patch.object(setup, "_have_api_key", return_value=(False, None)):
                    assert setup.cmd_check() == 0


class TestDocxAvailable:
    def test_detects_cache_node_modules(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache" / "node_modules"
        (cache / "docx").mkdir(parents=True)
        (cache / "docx" / "package.json").write_text('{"name":"docx"}', encoding="utf-8")
        monkeypatch.setattr(setup, "CACHE_NODE_MODULES", cache)
        monkeypatch.setattr(setup, "SCRIPTS_DIR", tmp_path / "empty-scripts")
        monkeypatch.delenv("DOCX_NODE_MODULES", raising=False)
        monkeypatch.delenv("NODE_PATH", raising=False)
        assert setup._docx_available() is True

    def test_detects_node_path_root(self, tmp_path, monkeypatch):
        root = tmp_path / "np"
        (root / "docx").mkdir(parents=True)
        (root / "docx" / "package.json").write_text('{"name":"docx"}', encoding="utf-8")
        monkeypatch.setattr(setup, "CACHE_NODE_MODULES", tmp_path / "nope")
        monkeypatch.setattr(setup, "SCRIPTS_DIR", tmp_path / "empty-scripts")
        monkeypatch.delenv("DOCX_NODE_MODULES", raising=False)
        monkeypatch.setenv("NODE_PATH", str(root))
        assert setup._docx_available() is True

    def test_absent_everywhere_is_false(self, tmp_path, monkeypatch):
        monkeypatch.setattr(setup, "CACHE_NODE_MODULES", tmp_path / "nope")
        monkeypatch.setattr(setup, "SCRIPTS_DIR", tmp_path / "empty-scripts")
        monkeypatch.delenv("DOCX_NODE_MODULES", raising=False)
        monkeypatch.delenv("NODE_PATH", raising=False)
        assert setup._docx_available() is False
