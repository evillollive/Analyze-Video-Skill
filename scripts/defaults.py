#!/usr/bin/env python3
"""Saved answers to the skill's questions, so a regular user is asked once.

The skill asks the same questions on every run: how many frames, where to
save, which appendices and extras, and for downloads which container and
captions. This file stores the user's usual answers in the skill's config
folder (``~/.config/analyze-video/defaults.json``). The agent shows them and
asks once whether to use them, instead of asking each question again.

    defaults.py --show                    # JSON plus a one-line summary
    defaults.py --show --duration 640     # also the frame count for a 10:40 video
    defaults.py --set pdf=true frames.5_to_15=14 save_folder=~/Reports
    defaults.py --unset pdf
    defaults.py --clear
    defaults.py --keys                    # every key and what it takes

Nothing here is required. With no saved defaults the skill asks as before.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from env_utils import CONFIG_DIR  # noqa: E402

DEFAULTS_FILE = CONFIG_DIR / "defaults.json"

# Video length bands, matching the frame-count suggestions in SKILL.md.
FRAME_BANDS: dict[str, tuple[float, float]] = {
    "under_2": (0, 120),
    "2_to_5": (120, 300),
    "5_to_15": (300, 900),
    "over_15": (900, float("inf")),
}

BOOL_KEYS = {
    "appendix_contact_sheets": "contact sheets as an appendix inside the document",
    "appendix_transcript": "full transcript as an appendix inside the document",
    "standalone_files": "separate copies of the contact sheets and transcript",
    "pdf": "a PDF version of the document",
    "cleanup": "delete the working files afterward",
    "oft_export": "an Open Filmmaker Toolkit project file",
}
CHOICE_KEYS = {
    "download.container": ("mkv", "mp4", "both", "audio", "mp3"),
    "download.subs": ("none", "srt", "srt+transcript"),
    "output_format": ("combined", "separate"),
}
PATH_KEYS = {
    "save_folder": "where analysis documents go",
    "download_folder": "where downloaded videos go",
}


def load(path: Path | None = None) -> dict:
    path = path or DEFAULTS_FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save(data: dict, path: Path | None = None) -> Path:
    path = path or DEFAULTS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _parse_bool(value: str) -> bool:
    text = value.strip().lower()
    if text in {"true", "yes", "y", "1", "on"}:
        return True
    if text in {"false", "no", "n", "0", "off"}:
        return False
    raise SystemExit(f"expected true or false, got {value!r}")


def parse_value(key: str, value: str):
    """Check one ``key=value`` pair and return the value to store."""
    if key in BOOL_KEYS:
        return _parse_bool(value)
    if key in CHOICE_KEYS:
        if value not in CHOICE_KEYS[key]:
            raise SystemExit(f"{key} must be one of: {', '.join(CHOICE_KEYS[key])}")
        return value
    if key in PATH_KEYS:
        folder = Path(value).expanduser()
        if not folder.is_absolute():
            raise SystemExit(f"{key} must be an absolute folder path")
        return str(folder)
    if key.startswith("frames."):
        band = key.split(".", 1)[1]
        if band not in FRAME_BANDS:
            raise SystemExit(f"frame bands are: {', '.join(FRAME_BANDS)}")
        try:
            count = int(value)
        except ValueError:
            raise SystemExit(f"{key} must be a whole number") from None
        if not 1 <= count <= 60:
            raise SystemExit(f"{key} must be between 1 and 60")
        return count
    raise SystemExit(f"unknown key {key!r}; run defaults.py --keys")


def set_value(data: dict, key: str, value) -> None:
    """Store a dotted key (``frames.2_to_5``) as nested JSON."""
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def unset_value(data: dict, key: str) -> None:
    parts = key.split(".")
    node = data
    for part in parts[:-1]:
        node = node.get(part)
        if not isinstance(node, dict):
            return
    node.pop(parts[-1], None)
    if len(parts) > 1 and not node:
        data.pop(parts[0], None)


def frames_for(data: dict, duration_seconds: float | None) -> int | None:
    """The saved frame count for a video of this length, or None."""
    if duration_seconds is None:
        return None
    frames = data.get("frames") or {}
    for band, (low, high) in FRAME_BANDS.items():
        if low <= duration_seconds < high:
            value = frames.get(band)
            return int(value) if isinstance(value, int) else None
    return None


def summary(data: dict) -> str:
    """One line for the agent to read back: 'Use your usual settings: ...?'"""
    if not data:
        return "No saved defaults."
    bits: list[str] = []
    frames = data.get("frames") or {}
    if frames:
        labels = {"under_2": "<2 min", "2_to_5": "2-5 min", "5_to_15": "5-15 min", "over_15": ">15 min"}
        bits.append("frames " + ", ".join(f"{frames[b]} ({labels[b]})" for b in FRAME_BANDS if b in frames))
    extras_on = [k for k in BOOL_KEYS if data.get(k) is True]
    extras_off = [k for k in BOOL_KEYS if data.get(k) is False]
    if extras_on:
        bits.append("yes to " + ", ".join(k.replace("_", " ") for k in extras_on))
    if extras_off:
        bits.append("no to " + ", ".join(k.replace("_", " ") for k in extras_off))
    if data.get("output_format"):
        bits.append(f"{data['output_format']} document for several videos")
    for key in PATH_KEYS:
        if data.get(key):
            bits.append(f"{key.replace('_', ' ')} {data[key]}")
    download = data.get("download") or {}
    if download:
        bits.append("downloads " + ", ".join(f"{k} {v}" for k, v in sorted(download.items())))
    return "Saved defaults: " + "; ".join(bits) + "."


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="defaults.py", description=__doc__.split("\n\n")[0])
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--show", action="store_true", help="print saved defaults and a summary")
    group.add_argument("--set", nargs="+", metavar="KEY=VALUE", help="save one or more answers")
    group.add_argument("--unset", nargs="+", metavar="KEY", help="forget one or more answers")
    group.add_argument("--clear", action="store_true", help="forget all saved answers")
    group.add_argument("--keys", action="store_true", help="list every key")
    ap.add_argument("--duration", type=float, help="with --show: also give the frame count for this length")
    ap.add_argument("--file", type=Path, default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    path = args.file or DEFAULTS_FILE

    if args.keys:
        for key, text in BOOL_KEYS.items():
            print(f"{key}=true|false  ({text})")
        for key, choices in CHOICE_KEYS.items():
            print(f"{key}={'|'.join(choices)}")
        for key, text in PATH_KEYS.items():
            print(f"{key}=/absolute/folder  ({text})")
        for band in FRAME_BANDS:
            print(f"frames.{band}=N  (frames per video, {band.replace('_', ' ')} minutes)")
        return 0
    data = load(path)
    if args.clear:
        path.unlink(missing_ok=True)
        print("Cleared saved defaults.")
        return 0
    if args.set:
        for pair in args.set:
            if "=" not in pair:
                raise SystemExit(f"expected KEY=VALUE, got {pair!r}")
            key, value = pair.split("=", 1)
            set_value(data, key.strip(), parse_value(key.strip(), value.strip()))
        save(data, path)
        print(summary(data))
        return 0
    if args.unset:
        for key in args.unset:
            unset_value(data, key.strip())
        save(data, path)
        print(summary(data))
        return 0
    result = {"file": str(path), "defaults": data, "summary": summary(data)}
    if args.duration is not None:
        result["frames_for_duration"] = frames_for(data, args.duration)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
