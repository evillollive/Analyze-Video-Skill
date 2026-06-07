#!/usr/bin/env python3
"""Download a video via yt-dlp, or resolve a local file path.

Also fetches subtitles (manual first, then auto-generated) in VTT format so
transcribe.py can parse them without needing Whisper.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}
BLOCKED_PATTERNS: tuple[tuple[str, str], ...] = (
    ("sign in to confirm", "login_required"),
    ("not a bot", "bot_check"),
    ("confirm you", "login_required"),
    ("members-only", "members_only"),
    ("members only", "members_only"),
    ("private video", "private"),
    ("video unavailable", "unavailable"),
    ("geo-restricted", "geo_restricted"),
    ("not available in your country", "geo_restricted"),
    ("http error 403", "forbidden"),
    ("403 forbidden", "forbidden"),
    ("http error 429", "rate_limited"),
    ("too many requests", "rate_limited"),
    ("age-restricted", "age_restricted"),
)


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https")


def resolve_local(path: str) -> dict:
    p = Path(path).expanduser().resolve()
    if not p.exists():
        raise SystemExit(f"File not found: {p}")
    if p.suffix.lower() not in VIDEO_EXTS:
        print(
            f"[analyze-video] warning: {p.suffix} is not a known video extension, proceeding anyway",
            file=sys.stderr,
        )
    subtitle = _local_subtitle(p)
    info = _local_info(p)
    title = info.get("title") or p.name
    return {
        "video_path": str(p),
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": {
            "title": title,
            "uploader": info.get("uploader"),
            "duration": info.get("duration"),
            "url": info.get("url") or str(p),
        },
        "downloaded": False,
    }


def _pick_subtitle(out_dir: Path) -> Path | None:
    candidates = sorted(out_dir.glob("video*.vtt"))
    if not candidates:
        return None
    preferred = [c for c in candidates if ".en" in c.name]
    return preferred[0] if preferred else candidates[0]


def _local_subtitle(video: Path) -> Path | None:
    """Find a VTT sitting next to a local video file.

    Matching is separator-anchored on the video's stem so ``clip1.mp4`` never
    grabs ``clip10.en.vtt``. Order: English stem variant, any stem language
    variant, the exact ``<stem>.vtt``, then a lone VTT only when the directory
    holds exactly one video and one subtitle (an unambiguous pair).
    """
    parent = video.parent
    stem = video.stem
    for pattern in (f"{stem}.en*.vtt", f"{stem}.*.vtt"):
        matches = sorted(parent.glob(pattern))
        if matches:
            english = [m for m in matches if ".en" in m.name.lower()]
            return english[0] if english else matches[0]
    exact = parent / f"{stem}.vtt"
    if exact.exists():
        return exact
    vtts = sorted(parent.glob("*.vtt"))
    videos = [p for p in parent.iterdir() if p.suffix.lower() in VIDEO_EXTS]
    if len(vtts) == 1 and len(videos) == 1:
        return vtts[0]
    return None


def _local_info(video: Path) -> dict:
    """Read yt-dlp's co-located <stem>.info.json (or a lone *.info.json)."""
    parent = video.parent
    stem = video.stem
    candidates = [parent / f"{stem}.info.json", parent / "video.info.json"]
    loose = sorted(parent.glob("*.info.json"))
    if len(loose) == 1 and loose[0] not in candidates:
        candidates.append(loose[0])
    for info_path in candidates:
        if info_path.exists():
            try:
                raw = json.loads(info_path.read_text())
            except (OSError, json.JSONDecodeError):
                continue
            return {
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "url": raw.get("webpage_url"),
            }
    return {}


def _pick_video(out_dir: Path) -> Path | None:
    for ext in (".mp4", ".mkv", ".webm", ".mov"):
        for candidate in out_dir.glob(f"video*{ext}"):
            return candidate
    for candidate in out_dir.glob("video.*"):
        if candidate.suffix.lower() in VIDEO_EXTS:
            return candidate
    return None


def classify_download_error(stderr: str) -> dict:
    """Classify common yt-dlp access failures and return actionable guidance."""
    text = (stderr or "").lower()
    kind = "download_failed"
    for pattern, candidate in BLOCKED_PATTERNS:
        if pattern in text:
            kind = candidate
            break

    if kind in {"login_required", "bot_check", "age_restricted", "members_only", "private"}:
        guidance = (
            "The site appears to require an authenticated browser session. If you have permission "
            "to view this video, re-run with --cookies-from-browser <browser> or --cookies <file>."
        )
    elif kind == "rate_limited":
        guidance = (
            "The site appears to be rate-limiting requests. Wait before retrying; authenticated "
            "cookies may help if the content is available in your browser."
        )
    elif kind == "geo_restricted":
        guidance = (
            "The video appears to be region restricted. Use a local video file or another source "
            "you are authorized to access from this environment."
        )
    elif kind == "forbidden":
        guidance = (
            "The site returned 403 Forbidden. If the video works in your browser, retry with "
            "--cookies-from-browser <browser> or provide a local file."
        )
    else:
        guidance = "Retry later, update yt-dlp, or provide a local video file."

    excerpt = " ".join((stderr or "").strip().split())[:600]
    return {"kind": kind, "guidance": guidance, "excerpt": excerpt}


def _result_from_dir(out_dir: Path, video: Path, url: str) -> dict:
    subtitle = _pick_subtitle(out_dir)
    info_path = out_dir / "video.info.json"
    info: dict = {}
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text())
            info = {
                "title": raw.get("title"),
                "uploader": raw.get("uploader") or raw.get("channel"),
                "duration": raw.get("duration"),
                "url": raw.get("webpage_url") or url,
            }
        except Exception:
            info = {"url": url}
    return {
        "video_path": str(video),
        "subtitle_path": str(subtitle) if subtitle else None,
        "info": info or {"url": url},
        "downloaded": True,
    }


def _source_marker_matches(out_dir: Path, url: str) -> bool:
    """True if this out_dir's recorded download source matches `url`.

    Prevents reusing a previously downloaded video when the user points the same
    --out-dir at a different URL. Reuse requires positive confirmation: a
    `.source.json` marker (written on download) or a matching URL in
    video.info.json. Absent any evidence, we re-download to be safe.
    """
    marker = out_dir / ".source.json"
    if marker.exists():
        try:
            data = json.loads(marker.read_text())
            if data.get("url"):
                return data["url"] == url
        except (OSError, json.JSONDecodeError):
            pass
    info_path = out_dir / "video.info.json"
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text())
            return url in {raw.get("webpage_url"), raw.get("original_url")}
        except (OSError, json.JSONDecodeError):
            pass
    return False


def download_url(
    url: str,
    out_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    force: bool = False,
) -> dict:
    if shutil.which("yt-dlp") is None:
        raise SystemExit("yt-dlp is not installed. Install with: brew install yt-dlp")
    if cookies and cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")
    if cookies:
        cookie_path = Path(cookies).expanduser().resolve()
        if not cookie_path.exists():
            raise SystemExit(f"Cookie file not found: {cookie_path}")

    out_dir.mkdir(parents=True, exist_ok=True)

    # Resume: a video already downloaded into out_dir is reused as-is (unless
    # --force), so a re-run after a timeout doesn't re-download the whole file.
    # Only reuse when the recorded source matches this URL.
    if not force:
        existing = _pick_video(out_dir)
        if (
            existing is not None
            and existing.stat().st_size > 0
            and _source_marker_matches(out_dir, url)
        ):
            print(
                f"[download] reusing existing video {existing.name} (pass --force to re-download)",
                file=sys.stderr,
            )
            return _result_from_dir(out_dir, existing, url)

    output_template = str(out_dir / "video.%(ext)s")

    cmd = [
        "yt-dlp",
        "-N", "8",
        "-f", "bv*[height<=720]+ba/b[height<=720]/bv+ba/b",
        "--merge-output-format", "mp4",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en,en-US,en-GB,en-orig",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "-o", output_template,
    ]
    if cookies:
        cmd += ["--cookies", str(cookie_path)]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd.append(url)

    # yt-dlp may exit non-zero if a subtitle variant fails (e.g. 429) even when
    # the video itself downloaded fine. Treat "video file present" as success,
    # but warn so partial downloads don't hide silently.
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, file=sys.stderr, end="" if result.stdout.endswith("\n") else "\n")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    video = _pick_video(out_dir)
    if video is None:
        classified = classify_download_error((result.stdout or "") + "\n" + (result.stderr or ""))
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {result.returncode}; "
            f"{classified['kind']}). {classified['guidance']}"
        )
    if result.returncode != 0:
        print(
            f"[download] WARNING: yt-dlp exited {result.returncode} but video "
            f"file exists — subtitle fetch may have failed",
            file=sys.stderr,
        )

    # Record the source so a later resume can confirm this out_dir holds *this*
    # URL before reusing the download.
    try:
        (out_dir / ".source.json").write_text(json.dumps({"url": url}))
    except OSError:
        pass

    return _result_from_dir(out_dir, video, url)


def download(
    source: str,
    out_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    force: bool = False,
) -> dict:
    if is_url(source):
        return download_url(
            source,
            out_dir,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            force=force,
        )
    return resolve_local(source)


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("usage: download.py <url-or-path> <out-dir>", file=sys.stderr)
        raise SystemExit(2)
    result = download(sys.argv[1], Path(sys.argv[2]))
    print(json.dumps(result, indent=2))
