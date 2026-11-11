"""Guards for SKILL.md and its reference files.

The skill is public: its instructions must not assume one agent, machine or
storage service, and every reference file the core points at must exist.
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = (ROOT / "SKILL.md").read_text(encoding="utf-8")
REFERENCES = sorted((ROOT / "references").glob("*.md"))

# Wording tied to one product or one person's setup.
ENVIRONMENT_SPECIFIC = ["Cowork", "device_bash", "computer://", "Desktop Commander", "session outputs"]


def _body(text: str) -> str:
    """Drop YAML frontmatter, whose keys are agent-specific by design."""
    return text.split("---", 2)[2] if text.startswith("---") else text


def test_reference_files_named_in_skill_exist():
    named = set(re.findall(r"`(?:references/)?([a-z-]+\.md)`", SKILL))
    named -= {"caption_guide.md"}
    assert named, "SKILL.md should point at its reference files"
    for name in named:
        assert (ROOT / "references" / name).is_file(), f"missing references/{name}"


def test_every_reference_file_is_listed_in_skill():
    for path in REFERENCES:
        assert f"`{path.name}`" in SKILL, f"{path.name} is not listed in SKILL.md"


def test_instructions_are_environment_neutral():
    texts = {"SKILL.md": _body(SKILL)}
    texts.update({p.name: p.read_text(encoding="utf-8") for p in REFERENCES})
    for name, text in texts.items():
        for term in ENVIRONMENT_SPECIFIC:
            assert term not in text, f"{name} mentions {term!r}"
    assert "Dropbox" not in _body(SKILL), "SKILL.md must not assume a storage service"


def test_core_stays_small():
    assert len(SKILL.encode("utf-8")) < 24_000, "SKILL.md core has grown; move detail into references/"
