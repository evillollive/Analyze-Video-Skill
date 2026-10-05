#!/usr/bin/env python3
"""Download caption/subtitle files for one or more video URLs.

Standalone mode of the analyze-video skill: no video download, no frames, no
Word document. Only yt-dlp is required.

    captions.py --source URL [--source URL ...] [--langs en] [--format srt]
                [--no-auto] [--list] [--out-dir DIR]
                [--cookies FILE | --cookies-from-browser BROWSER]

Prints a JSON summary of written files (or available tracks with --list) to
stdout. Progress and yt-dlp output go to stderr.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from download import (  # noqa: E402
    DEFAULT_SUB_LANGS,
    fetch_caption_tracks,
    is_url,
    list_caption_tracks,
)
from transcribe import format_transcript, parse_vtt  # noqa: E402

FORMATS = ("vtt", "srt", "txt")
_UNSAFE_RE = re.compile(r'[\\/:*?"<>|\x00-\x1f]+')
_TMP_STEM = "captions"


def safe_name(title: str | None, video_id: str | None, max_len: int = 80) -> str:
    """Filesystem-safe ``<title> [<id>]`` base name, like yt-dlp's default."""
    base = _UNSAFE_RE.sub("_", title or "").strip()
    base = " ".join(base.split()).strip(". ")[:max_len].rstrip(". ")
    if video_id:
        safe_id = _UNSAFE_RE.sub("_", video_id)
        return f"{base} [{safe_id}]" if base else safe_id
    return base or "captions"


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def segments_to_srt(segments: list[dict]) -> str:
    blocks = []
    for i, seg in enumerate(segments, 1):
        blocks.append(f"{i}\n{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}\n{seg['text']}\n")
    return "\n".join(blocks)


def resolve_formats(value: str) -> list[str]:
    if value == "all":
        return list(FORMATS)
    fmts = [f.strip().lower() for f in value.split(",") if f.strip()]
    bad = [f for f in fmts if f not in FORMATS]
    if bad or not fmts:
        raise SystemExit(f"Unsupported --format {value!r}. Use vtt, srt, txt, a comma list, or all.")
    return list(dict.fromkeys(fmts))


def resolve_langs(value: str | None) -> str:
    if not value or value.strip().lower() == "en":
        return DEFAULT_SUB_LANGS
    if value.strip().lower() == "all":
        # Excludes YouTube's live chat replay, which is not a caption track.
        return "all,-live_chat"
    return value


def _read_info(tmp: Path) -> dict:
    info_path = tmp / f"{_TMP_STEM}.info.json"
    try:
        return json.loads(info_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _unique(path: Path) -> Path:
    if not path.exists():
        return path
    n = 2
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def download_captions(
    url: str,
    out_dir: Path,
    *,
    langs: str,
    formats: list[str],
    include_auto: bool = True,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> dict:
    """Fetch tracks for one URL and write them to ``out_dir`` in each format."""
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix=".captions-", dir=out_dir))
    try:
        tracks = fetch_caption_tracks(
            url,
            tmp,
            stem=_TMP_STEM,
            langs=langs,
            include_auto=include_auto,
            write_info_json=True,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
        )
        info = _read_info(tmp)
        base = safe_name(info.get("title"), info.get("id"))
        files: list[dict] = []
        # captions.<lang>.vtt -> <lang>
        by_lang = {vtt.name[len(_TMP_STEM) + 1:-len(".vtt")] or "und": vtt for vtt in tracks}
        for lang, vtt in by_lang.items():
            # YouTube's "xx-orig" duplicates "xx" when both are present.
            if lang.endswith("-orig") and lang[:-len("-orig")] in by_lang:
                continue
            segments = None
            for fmt in formats:
                dest = _unique(out_dir / f"{base}.{lang}.{fmt}")
                if fmt == "vtt":
                    shutil.copyfile(vtt, dest)
                else:
                    if segments is None:
                        segments = parse_vtt(str(vtt))
                    if not segments:
                        print(f"[captions] {vtt.name}: no cues parsed, skipping {fmt}", file=sys.stderr)
                        continue
                    text = segments_to_srt(segments) if fmt == "srt" else format_transcript(segments) + "\n"
                    dest.write_text(text, encoding="utf-8")
                files.append({"lang": lang, "format": fmt, "path": str(dest)})
        return {"source": url, "title": info.get("title"), "id": info.get("id"), "files": files}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="captions",
        description="Download caption/subtitle files for video URLs (no video download).",
    )
    ap.add_argument("--source", action="append", required=True, help="Video URL (repeatable)")
    ap.add_argument("--out-dir", default=".", help="Directory for caption files (default: current dir)")
    ap.add_argument(
        "--langs",
        default="en",
        help="Comma-separated language codes or yt-dlp regexes (e.g. 'en,es' or 'es.*'), or 'all'. Default: en",
    )
    ap.add_argument(
        "--format",
        default="srt",
        help="vtt, srt, txt (timestamped plain text), a comma list, or all. Default: srt",
    )
    ap.add_argument("--no-auto", action="store_true", help="Manual (uploader) captions only; skip auto-generated")
    ap.add_argument("--list", action="store_true", help="List available caption languages without downloading")
    ap.add_argument("--cookies", default=None, help="Netscape cookies file (only with user authorization)")
    ap.add_argument("--cookies-from-browser", default=None, help="Browser to read cookies from (user-authorized)")
    args = ap.parse_args(argv)

    formats = resolve_formats(args.format)
    langs = resolve_langs(args.langs)
    out_dir = Path(args.out_dir).expanduser().resolve()

    results: list[dict] = []
    failures = 0
    for src in args.source:
        if not is_url(src):
            results.append({
                "source": src,
                "error": "Only http(s) URLs are supported. For local files, use a sidecar .srt/.vtt if present.",
            })
            failures += 1
            continue
        try:
            if args.list:
                tracks = list_caption_tracks(
                    src, cookies=args.cookies, cookies_from_browser=args.cookies_from_browser
                )
                results.append({"source": src, **tracks})
            else:
                print(f"[captions] fetching {src}", file=sys.stderr)
                results.append(download_captions(
                    src,
                    out_dir,
                    langs=langs,
                    formats=formats,
                    include_auto=not args.no_auto,
                    cookies=args.cookies,
                    cookies_from_browser=args.cookies_from_browser,
                ))
        except SystemExit as exc:
            results.append({"source": src, "error": str(exc)})
            failures += 1

    print(json.dumps(results, indent=2, ensure_ascii=False))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
