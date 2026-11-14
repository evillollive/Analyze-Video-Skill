"""Saved answers to the skill's questions."""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

import defaults  # noqa: E402


def _run(tmp_path, *args):
    return defaults.main(["--file", str(tmp_path / "defaults.json"), *args])


def test_no_file_means_no_defaults(tmp_path, capsys):
    _run(tmp_path, "--show")
    out = json.loads(capsys.readouterr().out)
    assert out["defaults"] == {}
    assert out["summary"] == "No saved defaults."


def test_set_show_and_frames_for_length(tmp_path, capsys):
    folder = tmp_path / "Reports"
    _run(tmp_path, "--set", "pdf=yes", "appendix_transcript=no", "frames.5_to_15=14",
         "frames.under_2=6", f"save_folder={folder}", "download.container=mkv", "download.subs=none")
    capsys.readouterr()
    _run(tmp_path, "--show", "--duration", "640")
    out = json.loads(capsys.readouterr().out)
    saved = out["defaults"]
    assert saved["pdf"] is True and saved["appendix_transcript"] is False
    assert saved["frames"] == {"5_to_15": 14, "under_2": 6}
    assert saved["download"] == {"container": "mkv", "subs": "none"}
    assert out["frames_for_duration"] == 14
    assert "yes to pdf" in out["summary"] and "no to appendix transcript" in out["summary"]
    assert "14 (5-15 min)" in out["summary"]


def test_frames_for_unsaved_band_is_none():
    assert defaults.frames_for({"frames": {"under_2": 6}}, 1000) is None
    assert defaults.frames_for({"frames": {"over_15": 20}}, 3600) == 20
    assert defaults.frames_for({}, None) is None


@pytest.mark.parametrize("pair", ["pdf=maybe", "frames.forever=3", "frames.2_to_5=0",
                                  "download.container=avi", "save_folder=relative/path", "colour=blue"])
def test_bad_values_are_refused(tmp_path, pair):
    with pytest.raises(SystemExit):
        _run(tmp_path, "--set", pair)
    assert not (tmp_path / "defaults.json").exists()


def test_unset_and_clear(tmp_path, capsys):
    _run(tmp_path, "--set", "pdf=true", "download.subs=srt")
    _run(tmp_path, "--unset", "download.subs")
    assert json.loads((tmp_path / "defaults.json").read_text()) == {"pdf": True}
    _run(tmp_path, "--clear")
    assert not (tmp_path / "defaults.json").exists()


def test_keys_lists_everything(capsys):
    defaults.main(["--keys"])
    out = capsys.readouterr().out
    for key in ("pdf=", "oft_export=", "download.container=", "save_folder=", "frames.over_15="):
        assert key in out
