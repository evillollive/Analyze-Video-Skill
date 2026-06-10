"""Tests for lint_spec_quality.py."""
import sys
from pathlib import Path

SCRIPTS_DIR = str(Path(__file__).resolve().parent.parent / "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from lint_spec_quality import lint_spec  # noqa: E402


class TestLintSpecQuality:
    def test_happy_path(self):
        spec = {
            "videos": [
                {
                    "sections": [
                        {
                            "heading": "Opening (0:00 to 0:30)",
                            "body": "This section describes concrete visuals and narrative beats in detail.",
                            "frames": [{"path": "/tmp/f.jpg", "caption": "Presenter introduces workflow"}],
                        }
                    ]
                }
            ]
        }
        errors, warnings = lint_spec(spec)
        assert errors == []
        assert warnings == []

    def test_missing_sections_is_error(self):
        errors, warnings = lint_spec({"videos": [{}]})
        assert any("has no sections" in e for e in errors)

    def test_short_body_and_caption_warn(self):
        spec = {
            "videos": [
                {
                    "sections": [
                        {
                            "heading": "Intro 0:00",
                            "body": "short",
                            "frames": [{"path": "/tmp/f.jpg", "caption": "tiny"}],
                        }
                    ]
                }
            ]
        }
        errors, warnings = lint_spec(spec)
        assert errors == []
        assert any("body is very short" in w for w in warnings)
        assert any("caption is very short" in w for w in warnings)
