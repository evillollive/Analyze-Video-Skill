import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import mcp_server  # noqa: E402


def test_prepare_args_minimal_and_full():
    assert mcp_server.prepare_args("u") == ["--source", "u"]
    assert mcp_server.prepare_args("u", "/o", True, 20, "0:10", "1:00") == [
        "--source", "u", "--out-dir", "/o", "--quick", "--max-frames", "20", "--start", "0:10", "--end", "1:00",
    ]


def test_captions_args():
    assert mcp_server.captions_args("u", "/o", "es", "srt", True) == [
        "--source", "u", "--out-dir", "/o", "--langs", "es", "--format", "srt", "--list",
    ]


def test_run_reports_failure_and_timeout(monkeypatch):
    done = subprocess.CompletedProcess([], 2, stdout="", stderr="boom")
    monkeypatch.setattr(mcp_server.subprocess, "run", lambda *a, **k: done)
    result = mcp_server._run("captions.py", [])
    assert result["ok"] is False and result["stderr"] == "boom"

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired("x", 1)

    monkeypatch.setattr(mcp_server.subprocess, "run", raise_timeout)
    assert "timed out" in mcp_server._run("process.py", [], timeout=1)["error"]


def test_analyze_prepare_reads_manifest(monkeypatch, tmp_path):
    manifest = tmp_path / "manifest_lite.json"
    manifest.write_text('{"frames": 3}')
    monkeypatch.setattr(mcp_server, "_run", lambda *a, **k: {"ok": True, "stdout": f"log\n{manifest}"})
    result = mcp_server.analyze_prepare("u")
    assert result["manifest"] == {"frames": 3}
    assert result["manifest_lite"] == str(manifest)


def test_read_image_validates(tmp_path):
    img = tmp_path / "f.jpg"
    img.write_bytes(b"\xff\xd8")
    assert mcp_server.read_image(str(img)) == (b"\xff\xd8", "jpeg")
    with pytest.raises(ValueError):
        mcp_server.read_image("relative.jpg")
    with pytest.raises(ValueError):
        mcp_server.read_image(str(tmp_path / "missing.jpg"))
    txt = tmp_path / "a.txt"
    txt.write_text("x")
    with pytest.raises(ValueError):
        mcp_server.read_image(str(txt))


def test_validate_spec_combines(monkeypatch):
    monkeypatch.setattr(mcp_server, "_run", lambda s, a, timeout=0: {"ok": s != "lint_spec_quality.py"})
    assert mcp_server.validate_spec("/s.json")["ok"] is False
