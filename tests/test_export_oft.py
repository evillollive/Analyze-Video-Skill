"""Tests for exporting analysis specs as Open Filmmaker Toolkit projects."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from export_oft import export_oft  # noqa: E402


def test_exports_analysis_with_relative_frame_paths(tmp_path):
    frame = tmp_path / "frames" / "frame_0001.jpg"
    frame.parent.mkdir()
    frame.write_bytes(b"image")
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "title": "A Video Analysis",
                "videos": [
                    {
                        "title": "A Video",
                        "source": "https://example.com/video",
                        "sections": [
                            {
                                "heading": "Opening (0:00 to 0:18)",
                                "time_range": "0:00 to 0:18",
                                "body": "A person enters the room.",
                                "frames": [
                                    {
                                        "path": str(frame),
                                        "caption": "The doorway is visible.",
                                        "timestamp_seconds": 3.5,
                                        "timestamp_formatted": "0:03",
                                    }
                                ],
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "project.oft.json"

    export_oft(spec_path, output_path)

    project = json.loads(output_path.read_text(encoding="utf-8"))
    assert project["format"] == "open-filmmaker-toolkit/project"
    assert project["formatVersion"] == 1
    assert project["project"] == {"title": "A Video Analysis"}
    assert project["thirdPartyElements"] == []
    asset = project["assets"][0]
    assert asset["type"] == "video"
    assert asset["source"] == "https://example.com/video"
    assert "A person enters the room." in asset["notes"]
    assert "- 0:03 - The doorway is visible." in asset["notes"]
    assert asset["analysis"]["sections"][0]["timeRange"] == "0:00 to 0:18"
    frame_record = asset["analysis"]["sections"][0]["frames"][0]
    assert frame_record == {
        "relativePath": "frames/frame_0001.jpg",
        "timecode": "0:03",
        "timestampSeconds": 3.5,
        "caption": "The doorway is visible.",
    }


def test_rejects_missing_frame_file_without_writing_output(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(
        json.dumps(
            {
                "videos": [
                    {
                        "sections": [
                            {"frames": [{"path": str(tmp_path / "missing.jpg")}]}
                        ]
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "project.oft.json"

    try:
        export_oft(spec_path, output_path)
    except ValueError as exc:
        assert "frame file does not exist" in str(exc)
    else:
        raise AssertionError("expected missing frame validation to fail")
    assert not output_path.exists()


def test_does_not_overwrite_existing_project(tmp_path):
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps({"videos": [{"sections": []}]}), encoding="utf-8")
    output_path = tmp_path / "project.oft.json"
    output_path.write_text("keep me", encoding="utf-8")

    try:
        export_oft(spec_path, output_path)
    except FileExistsError:
        pass
    else:
        raise AssertionError("expected existing output to be protected")
    assert output_path.read_text(encoding="utf-8") == "keep me"
