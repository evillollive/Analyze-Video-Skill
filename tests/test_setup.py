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

    def test_status_includes_host_fingerprint(self):
        with patch.object(setup, "_docx_available", return_value=True):
            with patch.object(setup, "_check_binaries", return_value=[]):
                with patch.object(setup, "_have_api_key", return_value=(False, None)):
                    status = setup._status()
                    assert "host_fingerprint" in status
                    assert "setup_state_file" in status


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


class TestInstallDocxFallback:
    def test_falls_back_to_cache_when_scripts_readonly(self, tmp_path, monkeypatch):
        """When scripts/ is read-only, docx installs into the per-user cache."""
        cache_nm = tmp_path / "cache" / "node_modules"
        monkeypatch.setattr(setup, "CACHE_NODE_MODULES", cache_nm)
        monkeypatch.setattr(setup, "SCRIPTS_DIR", tmp_path / "ro-scripts")
        monkeypatch.setattr(setup, "_docx_available", lambda: False)
        monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/npm")
        # scripts dir reports not writable; cache parent is.
        monkeypatch.setattr(setup.os, "access", lambda p, mode: False)

        calls = []

        def fake_install(prefix):
            calls.append(prefix)
            return True, f"installed into {prefix / 'node_modules'}"

        monkeypatch.setattr(setup, "_npm_install_docx", fake_install)
        ok, _msg = setup._install_docx()
        assert ok is True
        # Should target the cache parent (not the read-only scripts dir).
        assert calls == [cache_nm.parent]

    def test_prefers_scripts_dir_when_writable(self, tmp_path, monkeypatch):
        cache_nm = tmp_path / "cache" / "node_modules"
        scripts = tmp_path / "scripts"
        monkeypatch.setattr(setup, "CACHE_NODE_MODULES", cache_nm)
        monkeypatch.setattr(setup, "SCRIPTS_DIR", scripts)
        monkeypatch.setattr(setup, "_docx_available", lambda: False)
        monkeypatch.setattr(setup.shutil, "which", lambda name: "/usr/bin/npm")
        monkeypatch.setattr(setup.os, "access", lambda p, mode: True)

        calls = []
        monkeypatch.setattr(
            setup, "_npm_install_docx",
            lambda prefix: (calls.append(prefix) or (True, "ok")),
        )
        ok, _msg = setup._install_docx()
        assert ok is True
        assert calls[0] == scripts


class TestPathExportHint:
    def test_returns_hint_when_off_path(self, tmp_path, monkeypatch):
        bindir = tmp_path / "userbin"
        bindir.mkdir()
        tool = bindir / "yt-dlp"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
        monkeypatch.setattr(setup, "_resolve_tool", lambda name: str(tool))
        monkeypatch.setenv("PATH", "/usr/bin:/bin")
        hint = setup._path_export_hint("yt-dlp")
        assert hint == f'export PATH="$PATH:{bindir.resolve()}"'

    def test_returns_none_when_already_on_path(self, tmp_path, monkeypatch):
        bindir = tmp_path / "userbin"
        bindir.mkdir()
        tool = bindir / "yt-dlp"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
        monkeypatch.setattr(setup, "_resolve_tool", lambda name: str(tool))
        monkeypatch.setenv("PATH", str(bindir))
        assert setup._path_export_hint("yt-dlp") is None

    def test_returns_none_when_unresolved(self, monkeypatch):
        monkeypatch.setattr(setup, "_resolve_tool", lambda name: None)
        assert setup._path_export_hint("yt-dlp") is None
