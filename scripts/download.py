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

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from env_utils import resolve_tool as _resolve_tool
except ImportError:  # pragma: no cover
    def _resolve_tool(name: str) -> str | None:
        return shutil.which(name)


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


def is_youtube(url: str) -> bool:
    """True for youtube.com (and subdomains) or youtu.be URLs.

    Uses exact host / suffix matching so non-YouTube domains are never affected.
    """
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


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


def _valid_video(out_dir: Path) -> Path | None:
    """A picked video that is actually present and non-empty.

    yt-dlp can leave a zero-byte or fragment file behind on a failed format
    negotiation; treating that as success would let a broken download slip
    through to ffprobe/frame extraction instead of the web-client retry.
    """
    video = _pick_video(out_dir)
    if video is not None and video.stat().st_size > 0:
        return video
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


def _source_marker_matches(out_dir: Path, url: str, requested_auth: str = "none") -> bool:
    """True if this out_dir's recorded download source matches `url`.

    Prevents reusing a previously downloaded video when the user points the same
    --out-dir at a different URL. Reuse requires positive confirmation: a
    `.source.json` marker (written on download) or a matching URL in
    video.info.json. Absent any evidence, we re-download to be safe.

    `requested_auth` guards against serving a cached *anonymous* capture to an
    authenticated request: if cookies are now supplied but the cached download
    was unauthenticated, we re-download so the authenticated session (which may
    expose higher quality or members-only content) is honored.
    """
    marker = out_dir / ".source.json"
    if marker.exists():
        try:
            data = json.loads(marker.read_text())
            if data.get("url"):
                if data["url"] != url:
                    return False
                recorded_auth = data.get("auth", "none")
                if requested_auth != "none" and recorded_auth == "none":
                    return False
                return True
        except (OSError, json.JSONDecodeError):
            pass
    # info.json alone proves the URL but not the auth mode it was fetched under.
    # Only trust it for anonymous requests so an authenticated run never reuses
    # an unauthenticated capture.
    if requested_auth != "none":
        return False
    info_path = out_dir / "video.info.json"
    if info_path.exists():
        try:
            raw = json.loads(info_path.read_text())
            return url in {raw.get("webpage_url"), raw.get("original_url")}
        except (OSError, json.JSONDecodeError):
            pass
    return False


def _is_partial(path: Path) -> bool:
    """True for a yt-dlp in-progress file (a resumable partial download).

    yt-dlp streams each format into `<name>.part` (plus a `.ytdl` progress
    sidecar and `.part-FragN` fragment files) and renames only once the format
    is complete, so these are exactly the files a resumed download can continue
    from instead of re-fetching.
    """
    name = path.name
    return name.endswith((".part", ".ytdl")) or ".part-Frag" in name


def _clear_download_artifacts(out_dir: Path, *, keep_partials: bool = False) -> None:
    """Best-effort removal of prior download artifacts in a cache directory.

    Removes the video, its sidecar subtitles, info.json, and the source marker so
    a re-download can't be paired with a leftover subtitle/title from a different
    capture. Deletion failures are ignored (read-only/locked sandboxes); yt-dlp's
    own overwrite still handles same-named files.

    ``keep_partials`` spares yt-dlp's in-progress files so an interrupted
    download of the *same* URL and auth mode resumes from where it stopped.
    Only the caller can know that the leftovers belong to this request, so it
    defaults off.
    """
    patterns = ("video.*", "video", ".source.json")
    for pattern in patterns:
        for stale in out_dir.glob(pattern):
            if keep_partials and _is_partial(stale):
                continue
            try:
                stale.unlink()
            except OSError:
                pass


def _discard_partials(out_dir: Path) -> None:
    """Drop leftover in-progress files (called once a download has succeeded).

    yt-dlp removes its own partials, so anything left belongs to an abandoned
    attempt with different formats. Clearing it keeps the shared download cache
    from accruing dead weight that also counts toward its size cap.
    """
    for stale in out_dir.glob("video.*"):
        if not _is_partial(stale):
            continue
        try:
            stale.unlink()
        except OSError:
            pass


def _write_source_marker(out_dir: Path, url: str, auth: str, client: str, complete: bool) -> None:
    """Record what this directory holds (or is in the middle of fetching).

    Written *before* each attempt as well as after success: a run killed
    mid-download otherwise leaves `.part` files that the next run cannot prove
    belong to this URL, forcing a full re-download.
    """
    try:
        (out_dir / ".source.json").write_text(
            json.dumps({"url": url, "auth": auth, "client": client, "complete": complete})
        )
    except OSError:
        pass


def _build_ytdlp_cmd(
    ytdlp: str,
    url: str,
    output_template: str,
    *,
    cookie_path: Path | None,
    cookies_from_browser: str | None,
    player_client: str | None,
) -> list[str]:
    """Assemble the yt-dlp command, optionally pinning a YouTube player client.

    When `player_client` is set we pass it via --extractor-args; this applies to
    the whole invocation, so the subtitle fetch uses the same client too.
    """
    cmd = [
        ytdlp,
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
    if player_client:
        cmd += ["--extractor-args", f"youtube:player-client={player_client}"]
    if cookie_path is not None:
        cmd += ["--cookies", str(cookie_path)]
    if cookies_from_browser:
        cmd += ["--cookies-from-browser", cookies_from_browser]
    cmd.append(url)
    return cmd


def download_url(
    url: str,
    out_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    force: bool = False,
) -> dict:
    ytdlp = _resolve_tool("yt-dlp")
    if ytdlp is None:
        raise SystemExit(
            "yt-dlp is not installed. Install with: brew install yt-dlp (macOS), "
            "pipx install yt-dlp, or pip install --user yt-dlp"
        )
    if cookies and cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")
    cookie_path: Path | None = None
    if cookies:
        cookie_path = Path(cookies).expanduser().resolve()
        if not cookie_path.exists():
            raise SystemExit(f"Cookie file not found: {cookie_path}")

    if cookies:
        requested_auth = "cookies"
    elif cookies_from_browser:
        requested_auth = "cookies_from_browser"
    else:
        requested_auth = "none"

    out_dir.mkdir(parents=True, exist_ok=True)

    # Resume: a video already downloaded into out_dir is reused as-is (unless
    # --force), so a re-run after a timeout doesn't re-download the whole file.
    # Only reuse when the recorded source matches this URL and auth mode.
    if not force:
        existing = _pick_video(out_dir)
        if (
            existing is not None
            and existing.stat().st_size > 0
            and _source_marker_matches(out_dir, url, requested_auth)
        ):
            print(
                f"[download] reusing existing video {existing.name} (pass --force to re-download)",
                file=sys.stderr,
            )
            return _result_from_dir(out_dir, existing, url)

    output_template = str(out_dir / "video.%(ext)s")

    # Choose the client strategy. For public YouTube URLs with no cookies we lead
    # with the android player client: it bypasses YouTube's n-challenge without a
    # JavaScript runtime (which sandboxed/cloud yt-dlp installs lack) and avoids
    # the 403s the default web client hits from server IPs. If that attempt fails
    # to produce a usable video, we retry once with the default web client. When
    # cookies are supplied we honor the authenticated web session instead, since
    # the android client ignores cookies.
    if is_youtube(url) and requested_auth == "none":
        attempts: list[str | None] = ["android", None]
    else:
        attempts = [None]

    # A prior run may have been killed mid-download (the documented recovery path
    # is "re-run the exact same command"). yt-dlp resumes `.part` files by
    # default, but only if they survive: check the marker it left behind *before*
    # the loop rewrites it, so partials from a matching URL and auth mode can be
    # handed back to yt-dlp instead of re-fetched.
    resume_partials = not force and _source_marker_matches(out_dir, url, requested_auth)
    if resume_partials and any(_is_partial(p) for p in out_dir.glob("video.*")):
        print(
            f"[download] found an interrupted download in {out_dir}; "
            "resuming instead of starting over",
            file=sys.stderr,
        )

    result: subprocess.CompletedProcess | None = None
    video: Path | None = None
    used_client = "web"
    for player_client in attempts:
        client_label = "android" if player_client == "android" else "web"
        # Clear prior artifacts before each attempt so a stale subtitle/info.json
        # from a different capture can't get paired with the next download.
        # Best-effort: ignore failures (some sandboxes forbid deletes) since
        # yt-dlp's -y still overwrites by name. In-progress files are exempt when
        # they belong to this same URL and auth mode; they are named per format,
        # so yt-dlp only resumes the ones matching the format it selects and
        # ignores the rest. Leftovers are swept once a download succeeds.
        _clear_download_artifacts(out_dir, keep_partials=resume_partials)
        # Record the in-flight request so a run killed mid-download leaves proof
        # of which URL and auth mode its `.part` files belong to.
        _write_source_marker(out_dir, url, requested_auth, client_label, complete=False)
        cmd = _build_ytdlp_cmd(
            ytdlp,
            url,
            output_template,
            cookie_path=cookie_path,
            cookies_from_browser=cookies_from_browser,
            player_client=player_client,
        )
        if player_client:
            print(f"[download] trying yt-dlp player-client={player_client}", file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
        video = _valid_video(out_dir)
        if video is not None:
            used_client = client_label
            break
        if player_client != attempts[-1]:
            print(
                "[download] no usable video from this client, retrying with the "
                "default web client...",
                file=sys.stderr,
            )

    if video is None:
        out = (result.stdout or "") + "\n" + (result.stderr or "") if result else ""
        classified = classify_download_error(out)
        rc = result.returncode if result else -1
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {rc}; "
            f"{classified['kind']}). {classified['guidance']}"
        )
    if result is not None and result.returncode != 0:
        print(
            f"[download] WARNING: yt-dlp exited {result.returncode} but a video "
            f"file exists; subtitle fetch may have failed",
            file=sys.stderr,
        )

    # Any partial left now belongs to an abandoned attempt with different
    # formats; yt-dlp cleans up its own once a format completes.
    _discard_partials(out_dir)

    # Record the source so a later resume can confirm this out_dir holds *this*
    # URL (and auth mode) before reusing the download.
    _write_source_marker(out_dir, url, requested_auth, used_client, complete=True)

    return _result_from_dir(out_dir, video, url)


def _pick_caption(out_dir: Path, stem: str) -> Path | None:
    candidates = sorted(out_dir.glob(f"{stem}*.vtt"))
    if not candidates:
        return None
    english = [c for c in candidates if ".en" in c.name.lower()]
    return english[0] if english else candidates[0]


def fetch_captions(
    url: str,
    out_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> Path | None:
    """Fetch subtitles only (no video download) and return the VTT path.

    Retrofits a transcript for an output directory whose video was processed from
    a local file (so the caption pass never ran). Uses the same android-first
    strategy as download_url for public YouTube URLs.
    """
    ytdlp = _resolve_tool("yt-dlp")
    if ytdlp is None:
        raise SystemExit(
            "yt-dlp is not installed. Install with: brew install yt-dlp (macOS), "
            "pipx install yt-dlp, or pip install --user yt-dlp"
        )
    if cookies and cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")
    cookie_path: Path | None = None
    if cookies:
        cookie_path = Path(cookies).expanduser().resolve()
        if not cookie_path.exists():
            raise SystemExit(f"Cookie file not found: {cookie_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = "captions"
    template = str(out_dir / f"{stem}.%(ext)s")

    no_auth = not (cookies or cookies_from_browser)
    attempts: list[str | None] = ["android", None] if (is_youtube(url) and no_auth) else [None]

    result: subprocess.CompletedProcess | None = None
    for player_client in attempts:
        # Clear any prior captions so _pick_caption only ever matches a file the
        # current attempt produced; otherwise a stale VTT from an earlier run
        # would short-circuit the web-client fallback and the failure path.
        for stale in out_dir.glob(f"{stem}*.vtt"):
            try:
                stale.unlink()
            except OSError:
                pass
        cmd = [
            ytdlp,
            "--skip-download",
            "--write-subs",
            "--write-auto-subs",
            "--sub-langs", "en,en-US,en-GB,en-orig",
            "--sub-format", "vtt",
            "--convert-subs", "vtt",
            "--no-playlist",
            "-o", template,
        ]
        if player_client:
            cmd += ["--extractor-args", f"youtube:player-client={player_client}"]
        if cookie_path is not None:
            cmd += ["--cookies", str(cookie_path)]
        if cookies_from_browser:
            cmd += ["--cookies-from-browser", cookies_from_browser]
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
        sub = _pick_caption(out_dir, stem)
        if sub is not None:
            return sub

    out = (result.stdout or "") + "\n" + (result.stderr or "") if result else ""
    classified = classify_download_error(out)
    raise SystemExit(
        f"yt-dlp did not produce subtitles for {url} ({classified['kind']}). "
        f"{classified['guidance']}"
    )


def fetch_title(
    url: str,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> str | None:
    """Fetch a video's remote title without downloading media.

    Uses the same client strategy as download/captions: android-first for public
    YouTube URLs, otherwise default web client.
    """
    ytdlp = _resolve_tool("yt-dlp")
    if ytdlp is None:
        raise SystemExit(
            "yt-dlp is not installed. Install with: brew install yt-dlp (macOS), "
            "pipx install yt-dlp, or pip install --user yt-dlp"
        )
    if cookies and cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")
    cookie_path: Path | None = None
    if cookies:
        cookie_path = Path(cookies).expanduser().resolve()
        if not cookie_path.exists():
            raise SystemExit(f"Cookie file not found: {cookie_path}")

    no_auth = not (cookies or cookies_from_browser)
    attempts: list[str | None] = ["android", None] if (is_youtube(url) and no_auth) else [None]

    for player_client in attempts:
        cmd = [ytdlp, "--no-playlist", "--get-title"]
        if player_client:
            cmd += ["--extractor-args", f"youtube:player-client={player_client}"]
        if cookie_path is not None:
            cmd += ["--cookies", str(cookie_path)]
        if cookies_from_browser:
            cmd += ["--cookies-from-browser", cookies_from_browser]
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            title = (result.stdout or "").strip().splitlines()
            if title:
                return title[0].strip()
        if result.stdout:
            print(result.stdout, file=sys.stderr, end="" if result.stdout.endswith("\n") else "\n")
        if result.stderr:
            print(result.stderr, file=sys.stderr, end="" if result.stderr.endswith("\n") else "\n")
    return None


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
