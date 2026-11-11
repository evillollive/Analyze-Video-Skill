"""The Word builder keeps a section's text, frames and captions together."""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import setup  # noqa: E402

# A 1x1 JPEG, enough for the builder to embed.
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d"
    "1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f000001050101"
    "0101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d010203000411051221314106"
    "13516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738393a434445464748494a535455"
    "565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9ba"
    "c2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7f8f9faffda0008010100003f00fbfcffd9"
)

needs_node_docx = pytest.mark.skipif(
    shutil.which("node") is None or not setup._docx_available(),
    reason="needs Node.js and an installed docx module",
)


def _build(tmp_path, layout, appendices=False, extra=None):
    frame = tmp_path / "frame_0001.jpg"
    frame.write_bytes(TINY_JPEG)
    out = tmp_path / "out.docx"
    spec = {
        "out": str(out),
        "title": "Layout test",
        "frame_layout": layout,
        "videos": [{
            "title": "v",
            "source": "https://example.com/v",
            "sections": [{
                "heading": "Opening (0:00 to 0:10)",
                "body": "First paragraph.\n\nSecond paragraph.",
                "frames": [
                    {"path": str(frame), "caption": "Caption one.", "alt": "a",
                     "timestamp_seconds": 19.04, "timestamp_formatted": "00:19"},
                    {"path": str(frame), "caption": "Caption two.", "alt": "b",
                     "timestamp_seconds": 3725.0},
                ],
            }],
        }],
    }
    if appendices:
        transcript = tmp_path / "transcript.txt"
        transcript.write_text("[00:01] hello\n")
        spec["appendix_contact_sheets"] = [
            {"path": str(frame), "heading": "Chunk 1", "caption": "Overview.", "alt": "grid"},
            {"path": str(frame), "heading": "Chunk 2"},
        ]
        spec["appendix_transcript"] = [{"heading": "v", "path": str(transcript)}]
        spec["observations"] = "Shared structure."
    if extra:
        spec.update(extra)
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    subprocess.run(
        ["node", str(ROOT / "scripts" / "build-docx.js"), "--spec", str(spec_path)],
        check=True, capture_output=True, text=True,
    )
    with zipfile.ZipFile(out) as z:
        return z.read("word/document.xml").decode("utf-8")


@needs_node_docx
def test_one_up_keeps_heading_text_frames_and_captions_together(tmp_path):
    xml = _build(tmp_path, "1up")
    # heading, last body paragraph, and each captioned image carry keepNext
    assert xml.count("<w:keepNext/>") >= 4
    assert "<w:keepLines/>" in xml


@needs_node_docx
def test_two_up_rows_do_not_split_across_pages(tmp_path):
    xml = _build(tmp_path, "2up")
    assert "<w:cantSplit/>" in xml
    # Only the heading and the last body paragraph chain forward. Images inside
    # cells must not, or every row of the table is glued onto one page and long
    # sections leave pages mostly empty.
    assert xml.count("<w:keepNext/>") == 2


@needs_node_docx
def test_every_spec_feature_builds(tmp_path):
    """Appendices, observations and both layouts go through the builder without error."""
    xml = _build(tmp_path, "2up", appendices=True)
    assert "Contact Sheet Appendix" in xml
    assert "hello" in xml


@needs_node_docx
@pytest.mark.parametrize("layout", ["1up", "2up"])
def test_frame_times_are_printed_beside_captions(tmp_path, layout):
    xml = _build(tmp_path, layout)
    assert ">0:19<" in xml          # "00:19" tidied
    assert ">1:02:05<" in xml       # derived from seconds when no formatted time is given
    assert "Caption one." in xml


@needs_node_docx
def test_frame_times_can_be_turned_off(tmp_path):
    xml = _build(tmp_path, "1up", extra={"show_timestamps": False})
    assert ">0:19<" not in xml and ">1:02:05<" not in xml
    assert "Caption one." in xml
