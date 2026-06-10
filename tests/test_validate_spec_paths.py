"""Tests for validate_spec_paths.py."""
import json
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from validate_spec_paths import validate_spec_paths  # noqa: E402


def _write_spec(path: Path, spec: dict) -> None:
    path.write_text(json.dumps(spec), encoding="utf-8")


class TestValidateSpecPaths:
    def test_accepts_existing_absolute_paths(self, tmp_path):
        frame = tmp_path / "frame.jpg"
        transcript = tmp_path / "transcript.txt"
        sheet = tmp_path / "sheet.jpg"
        for p in (frame, transcript, sheet):
            p.write_bytes(b"x")

        spec_path = tmp_path / "spec.json"
        _write_spec(
            spec_path,
            {
                "title": "t",
                "out": str(tmp_path / "out.docx"),
                "videos": [
                    {
                        "sections": [
                            {
                                "frames": [{"path": str(frame), "caption": "c"}],
                            }
                        ]
                    }
                ],
                "appendix_contact_sheets": [{"path": str(sheet)}],
                "appendix_transcript": [{"path": str(transcript)}],
            },
        )

        assert validate_spec_paths(spec_path) == 0

    def test_rejects_missing_frame_file(self, tmp_path):
        missing = tmp_path / "missing.jpg"
        spec_path = tmp_path / "spec.json"
        _write_spec(
            spec_path,
            {
                "title": "t",
                "out": str(tmp_path / "out.docx"),
                "videos": [{"sections": [{"frames": [{"path": str(missing)}]}]}],
            },
        )
        assert validate_spec_paths(spec_path) == 1

    def test_rejects_relative_paths(self, tmp_path):
        (tmp_path / "frame.jpg").write_bytes(b"x")
        spec_path = tmp_path / "spec.json"
        _write_spec(
            spec_path,
            {
                "title": "t",
                "out": str(tmp_path / "out.docx"),
                "videos": [{"sections": [{"frames": [{"path": "frame.jpg"}]}]}],
            },
        )
        assert validate_spec_paths(spec_path) == 1
