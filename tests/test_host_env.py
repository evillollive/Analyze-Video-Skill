"""Tests for host_env helpers."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import host_env  # noqa: E402


class TestHostFingerprint:
    def test_current_host_has_expected_keys(self):
        fp = host_env.current_host_fingerprint()
        for key in ("platform", "release", "machine", "hostname", "id"):
            assert key in fp
        assert isinstance(fp["id"], str) and len(fp["id"]) == 16


class TestSetupState:
    def test_write_and_read_state(self, tmp_path):
        state_file = tmp_path / "setup_state.json"
        host_env.write_setup_state(state_file)
        data = host_env.read_setup_state(state_file)
        assert data is not None
        assert "host" in data
        assert "written_at" in data

    def test_host_matches_true_for_same_fingerprint(self):
        current = host_env.current_host_fingerprint()
        state = {"host": dict(current)}
        assert host_env.host_matches(state, current) is True

    def test_host_matches_false_for_different_host(self):
        current = host_env.current_host_fingerprint()
        other = dict(current)
        other["hostname"] = "different-host-name"
        state = {"host": other}
        assert host_env.host_matches(state, current) is False

    def test_read_invalid_json_returns_none(self, tmp_path):
        state_file = tmp_path / "bad.json"
        state_file.write_text("{not-json", encoding="utf-8")
        assert host_env.read_setup_state(state_file) is None
