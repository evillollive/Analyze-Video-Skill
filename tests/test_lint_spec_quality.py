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


class TestTextOnlySections:
    FRAMED = {"heading": "Opening (0:00 to 0:30)", "body": "A framed section with enough words in it to pass.",
              "frames": [{"path": "/tmp/f.jpg", "caption": "A concrete caption"}]}

    def test_lens_closing_section_marked_text_only_passes(self):
        closing = {"heading": "What to borrow", "text_only": True,
                   "body": "Open on the strongest line from the interview, before any title."}
        errors, warnings = lint_spec({"videos": [{"sections": [self.FRAMED, closing]}]})
        assert errors == []
        assert warnings == []

    def test_unmarked_frameless_section_still_errors_and_says_how_to_fix(self):
        closing = {"heading": "What to borrow", "body": "Open on the strongest line from the interview."}
        errors, _ = lint_spec({"videos": [{"sections": [self.FRAMED, closing]}]})
        assert any('"text_only": true' in e for e in errors)

    def test_a_video_with_no_frames_at_all_errors(self):
        only_text = {"heading": "Summary", "text_only": True, "body": "Words only, and enough of them to pass."}
        errors, _ = lint_spec({"videos": [{"sections": [only_text]}]})
        assert any("no frames in any section" in e for e in errors)
