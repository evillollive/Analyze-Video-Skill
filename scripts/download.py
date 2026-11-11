#!/usr/bin/env python3
"""Download a video via yt-dlp, or resolve a local file path.

Also fetches subtitles (manual first, then auto-generated) in VTT format so
transcribe.py can parse them without needing Whisper.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from fractions import Fraction
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from env_utils import resolve_tool as _resolve_tool
except ImportError:  # pragma: no cover
    def _resolve_tool(name: str) -> str | None:
        return shutil.which(name)

import ytdlp as yt  # noqa: E402  (the one module that talks to yt-dlp)


VIDEO_EXTS = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".flv", ".wmv"}
STREAM_MANIFEST_EXTS = {".m3u8", ".mpd"}

# Frames are rendered at --resolution pixels wide (512 by default) and embedded
# in the docx at roughly 640 px, so every pixel of source width beyond ~854 is
# downloaded and then thrown away by the scaler. Capping the format selection by
# *width* rather than height is what keeps this safe for portrait video: a 9:16
# clip needs a much taller stream to reach the same frame width, and a width cap
# lets yt-dlp pick it.
DEFAULT_DOWNLOAD_MAX_WIDTH = 854
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


# yt-dlp's JS-enabled web clients expose the full YouTube format ladder on
# current installations. Keep the older clients as a fallback for environments
# where those clients are blocked or do not expose a usable stream.
YOUTUBE_PLAYER_CLIENTS = "default,web_safari,tv"
YOUTUBE_FALLBACK_PLAYER_CLIENTS = "android,ios,web"


def is_url(source: str) -> bool:
    parsed = urlparse(source)
    return parsed.scheme in ("http", "https")


def is_youtube(url: str) -> bool:
    """True for youtube.com (and subdomains) or youtu.be URLs.

    Uses exact host / suffix matching so non-YouTube domains are never affected.
    """
    host = (urlparse(url).hostname or "").lower().rstrip(".")
    return host == "youtu.be" or host == "youtube.com" or host.endswith(".youtube.com")


def player_client_attempts(url: str, *, authenticated: bool) -> list[str | None]:
    """Player-client values to try, in order, for `url`.

    Public YouTube tries the JS-enabled web clients first, then the legacy
    android/ios/web group, then yt-dlp's own defaults. Authenticated requests
    use yt-dlp defaults because alternate clients may ignore cookies.
    """
    if is_youtube(url) and not authenticated:
        return [YOUTUBE_PLAYER_CLIENTS, YOUTUBE_FALLBACK_PLAYER_CLIENTS, None]
    return [None]


def _ytdlp_runtime_args() -> list[str]:
    """Kept for callers of the old name; see ytdlp.runtime_args()."""
    return yt.runtime_args()


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


def _source_marker_matches(
    out_dir: Path,
    url: str,
    requested_auth: str = "none",
    requested_max_width: int | None = None,
) -> bool:
    """True if this out_dir's recorded download source matches `url`.

    Prevents reusing a previously downloaded video when the user points the same
    --out-dir at a different URL. Reuse requires positive confirmation: a
    `.source.json` marker (written on download) or a matching URL in
    video.info.json. Absent any evidence, we re-download to be safe.

    `requested_auth` guards against serving a cached *anonymous* capture to an
    authenticated request: if cookies are now supplied but the cached download
    was unauthenticated, we re-download so the authenticated session (which may
    expose higher quality or members-only content) is honored.

    `requested_max_width` guards the same way for quality: a capture made under
    a narrower width budget is not good enough for a run that asked for a wider
    one. Markers written before width budgets existed carry no `max_width` and
    are accepted as-is, so upgrading the skill never forces a re-download.
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
                return _width_budget_satisfied(data, requested_max_width)
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


def _width_budget_satisfied(marker: dict, requested_max_width: int | None) -> bool:
    """True when a cached capture is at least as wide as the current request.

    An uncapped capture (recorded ``max_width`` of 0/None) satisfies anything.
    A marker with no ``max_width`` key predates width budgets; it is accepted so
    an existing cache stays usable after an upgrade.
    """
    if requested_max_width is None or requested_max_width <= 0:
        # The caller wants the best available; only an uncapped capture is
        # guaranteed to be that, but a legacy marker gets the benefit of doubt.
        if "max_width" not in marker:
            return True
        recorded = marker.get("max_width")
        return not recorded or recorded <= 0
    if "max_width" not in marker:
        return True
    recorded = marker.get("max_width")
    if not recorded or recorded <= 0:
        return True  # uncapped capture is at least as good
    return recorded >= requested_max_width


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


def _write_source_marker(
    out_dir: Path,
    url: str,
    auth: str,
    client: str,
    complete: bool,
    max_width: int | None = None,
    ytdlp_version: str | None = None,
    js_runtime: str | None = None,
) -> None:
    """Record what this directory holds (or is in the middle of fetching).

    Written *before* each attempt as well as after success: a run killed
    mid-download otherwise leaves `.part` files that the next run cannot prove
    belong to this URL, forcing a full re-download. `max_width` records the
    quality budget the capture was made under; `ytdlp_version` and
    `js_runtime` record the tools that produced it, when known.
    """
    data = {
        "url": url,
        "auth": auth,
        "client": client,
        "complete": complete,
        "max_width": max_width or 0,
    }
    if ytdlp_version:
        data["ytdlp_version"] = ytdlp_version
    if js_runtime:
        data["js_runtime"] = js_runtime
    try:
        (out_dir / ".source.json").write_text(json.dumps(data))
    except OSError:
        pass


# How yt-dlp ranks the formats that pass the width budget for analysis runs:
# plain HTTP(S) streams first (HLS estimates are unreliable and merge badly),
# then the highest resolution within the budget, then the highest bitrate.
# Without the explicit "res,br", yt-dlp ranks codec family above bitrate and
# picks the smallest AV1 stream, whose frames look soft and blocky when the
# source is low resolution.
ANALYSIS_FORMAT_SORT = "proto:https,res,br"


def format_selector(max_width: int | None) -> str:
    """Build the yt-dlp ``-f`` expression for a source-width budget.

    Preference order: a stream no wider than ``max_width``, then the old
    720p-height cap, then whatever exists. The ``?`` in ``width<=?N`` makes the
    filter non-fatal for extractors that don't report width, so a site with
    incomplete format metadata still downloads instead of failing outright.

    ``max_width`` of ``None`` or ``0`` disables the cap entirely.
    """
    if not max_width or max_width <= 0:
        return "bv*+ba/b"
    return (
        f"bv*[width<=?{max_width}]+ba/b[width<=?{max_width}]"
        "/bv*[height<=720]+ba/b[height<=720]"
        "/bv+ba/b"
    )


def _build_ytdlp_cmd(
    ytdlp: str,
    url: str,
    output_template: str,
    *,
    cookie_path: Path | None,
    cookies_from_browser: str | None,
    player_client: str | None,
    max_width: int | None = DEFAULT_DOWNLOAD_MAX_WIDTH,
    selector: str | None = None,
) -> list[str]:
    """Assemble the yt-dlp command, optionally pinning a YouTube player client.

    When `player_client` is set we pass it via --extractor-args; this applies to
    the whole invocation, so the subtitle fetch uses the same client too.

    `--ignore-no-formats-error` is deliberate: YouTube now gates most player
    clients' media formats behind a PO token, and when the chosen client can only
    offer gated formats yt-dlp raises a fatal "Requested format is not available"
    *after* it has already located the subtitle tracks -- aborting before the
    `.vtt` files are written. The flag downgrades that to a warning so the
    subtitles still land. Whether a real video was produced is decided separately
    by `_valid_video`, which drives the player-client fallback, so this never
    masks a genuine download failure.
    """
    cmd = [
        ytdlp,
        "-N", "8",
        "-f", selector or format_selector(max_width),
        "-S", ANALYSIS_FORMAT_SORT,
        "--merge-output-format", "mp4",
        "--ignore-no-formats-error",
        "--write-info-json",
        "--write-subs",
        "--write-auto-subs",
        "--sub-langs", "en,en-US,en-GB,en-orig",
        "--sub-format", "vtt",
        "--convert-subs", "vtt",
        "--no-playlist",
        "-o", output_template,
    ]
    cmd += yt.runtime_args()
    cmd += yt.client_args(player_client)
    cmd += yt.auth_args(cookie_path, cookies_from_browser)
    cmd.append(url)
    return cmd


def _dash_only_selector(max_width: int | None) -> str:
    """Format selector restricted to HTTP(S) media, excluding HLS playlists."""
    if not max_width or max_width <= 0:
        return "bv*[protocol^=http]+ba[protocol^=http]/b[protocol^=http]"
    return (
        f"bv*[protocol^=http][width<=?{max_width}]+ba[protocol^=http]"
        f"/b[protocol^=http][width<=?{max_width}]"
        f"/bv*[protocol^=http][height<=720]+ba[protocol^=http]"
        f"/b[protocol^=http][height<=720]"
        f"/bv*[protocol^=http]+ba[protocol^=http]/b[protocol^=http]"
    )


def _degraded_youtube_result(out_dir: Path, stderr: str) -> str | None:
    """Describe an unexpectedly low-quality YouTube extraction, if detectable."""
    if "sabr" in (stderr or "").lower():
        return "yt-dlp reported YouTube's SABR-only streaming mode"
    try:
        info = json.loads((out_dir / "video.info.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    selected = info.get("requested_formats") or [info]
    selected_ids = {str(fmt.get("format_id")) for fmt in selected}
    selected_height = max((fmt.get("height") or 0 for fmt in selected), default=0)
    available = info.get("formats") or []
    has_higher = any((fmt.get("height") or 0) > selected_height for fmt in available)
    if "18" in selected_ids and has_higher:
        return "yt-dlp selected progressive format 18 despite higher-resolution formats"
    if selected_height and selected_height <= 360 and has_higher:
        return f"yt-dlp selected {selected_height}p despite higher-resolution formats"
    return None


def is_direct_media(url: str) -> bool:
    """True when the URL points straight at a media file or HLS/DASH manifest.

    These can be fetched with ffmpeg alone, so they still work when yt-dlp is
    missing or broken.
    """
    if not is_url(url):
        return False
    suffix = Path(urlparse(url).path).suffix.lower()
    return suffix in VIDEO_EXTS or suffix in STREAM_MANIFEST_EXTS


def download_direct_media(url: str, out_dir: Path) -> dict:
    """Fetch a direct media URL or HLS/DASH manifest with ffmpeg (no yt-dlp)."""
    ffmpeg = _resolve_tool("ffmpeg")
    if ffmpeg is None:
        raise SystemExit("ffmpeg is required to download this video without yt-dlp")
    out_dir.mkdir(parents=True, exist_ok=True)
    _clear_download_artifacts(out_dir, keep_partials=False)
    suffix = Path(urlparse(url).path).suffix.lower()
    ext = ".mkv" if suffix in {".mkv", ".webm"} else ".mp4"
    target = out_dir / f"video{ext}"
    print(f"[download] fetching direct media with ffmpeg: {url}", file=sys.stderr)
    result = subprocess.run(
        [
            ffmpeg, "-v", "error", "-y", "-i", url,
            "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", str(target),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode or not target.exists() or target.stat().st_size == 0:
        try:
            target.unlink()
        except OSError:
            pass
        raise SystemExit(
            f"ffmpeg could not download {url}: {(result.stderr or '').strip()[-300:]} "
            f"{yt.LOCAL_FILE_FALLBACK}"
        )
    _write_source_marker(out_dir, url, "none", "ffmpeg", complete=True, max_width=0)
    name = Path(urlparse(url).path).name or url
    return {
        "video_path": str(target),
        "subtitle_path": None,
        "info": {"title": name, "uploader": None, "duration": None, "url": url},
        "downloaded": True,
    }


def _ytdlp_attempts(
    ytdlp: str,
    url: str,
    out_dir: Path,
    *,
    cookie_path: Path | None,
    cookies_from_browser: str | None,
    requested_auth: str,
    max_width: int | None,
    resume_partials: bool,
) -> dict:
    """One full pass over the player clients and format selectors.

    Returns ``{"video", "result", "degraded", "client"}``; ``video`` is None
    when nothing usable was produced.
    """
    output_template = str(out_dir / "video.%(ext)s")
    attempts = player_client_attempts(url, authenticated=requested_auth != "none")
    result: subprocess.CompletedProcess | None = None
    video: Path | None = None
    degraded_reason: str | None = None
    used_client = "web"
    for player_client in attempts:
        client_label = player_client if player_client else "web"
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
        _write_source_marker(
            out_dir, url, requested_auth, client_label, complete=False, max_width=max_width
        )
        if player_client:
            print(f"[download] trying yt-dlp player-client={player_client}", file=sys.stderr)
        for selector in (format_selector(max_width), _dash_only_selector(max_width)):
            if selector != format_selector(max_width):
                if result is None or "conversion failed!" not in yt.output_of(result).lower():
                    break
                print("[download] merge failed; retrying with HTTP(S)-only formats", file=sys.stderr)
                _clear_download_artifacts(out_dir, keep_partials=False)
                _write_source_marker(
                    out_dir, url, requested_auth, client_label, complete=False, max_width=max_width
                )
            cmd = _build_ytdlp_cmd(
                ytdlp,
                url,
                output_template,
                cookie_path=cookie_path,
                cookies_from_browser=cookies_from_browser,
                player_client=player_client,
                max_width=max_width,
                selector=selector,
            )
            result = yt.run(cmd)
            yt.echo(result)
            conversion_failed = "conversion failed!" in yt.output_of(result).lower()
            video = None if conversion_failed else _valid_video(out_dir)
            if video is not None:
                break
        if video is not None:
            degraded_reason = (
                _degraded_youtube_result(out_dir, result.stderr or "")
                if is_youtube(url)
                else None
            )
            if degraded_reason:
                print(f"[download] {degraded_reason}; trying another client", file=sys.stderr)
                video = None
                if player_client != attempts[-1]:
                    continue
                break
            used_client = client_label
            break
        if player_client != attempts[-1]:
            print(
                "[download] no usable video from this client, retrying with the "
                "next player client...",
                file=sys.stderr,
            )
    return {"video": video, "result": result, "degraded": degraded_reason, "client": used_client}


def download_url(
    url: str,
    out_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    force: bool = False,
    max_width: int | None = DEFAULT_DOWNLOAD_MAX_WIDTH,
) -> dict:
    ytdlp = yt.find()
    if ytdlp is None:
        if is_direct_media(url) and not (cookies or cookies_from_browser):
            print("[download] yt-dlp is not installed; using ffmpeg for this direct link", file=sys.stderr)
            return download_direct_media(url, out_dir)
        raise SystemExit(f"{yt.MISSING_MESSAGE}. {yt.LOCAL_FILE_FALLBACK}")
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
            and _source_marker_matches(out_dir, url, requested_auth, max_width)
        ):
            print(
                f"[download] reusing existing video {existing.name} (pass --force to re-download)",
                file=sys.stderr,
            )
            return _result_from_dir(out_dir, existing, url)

    if is_youtube(url) and not yt.runtime_args():
        print(
            "[download] WARNING: no Deno or Node.js runtime found; "
            "YouTube may offer only low-resolution formats",
            file=sys.stderr,
        )

    # A prior run may have been killed mid-download (the documented recovery path
    # is "re-run the exact same command"). yt-dlp resumes `.part` files by
    # default, but only if they survive: check the marker it left behind *before*
    # the loop rewrites it, so partials from a matching URL and auth mode can be
    # handed back to yt-dlp instead of re-fetched.
    resume_partials = not force and _source_marker_matches(
        out_dir, url, requested_auth, max_width
    )
    if resume_partials and any(_is_partial(p) for p in out_dir.glob("video.*")):
        print(
            f"[download] found an interrupted download in {out_dir}; "
            "resuming instead of starting over",
            file=sys.stderr,
        )

    def attempt() -> dict:
        return _ytdlp_attempts(
            yt.find() or ytdlp,
            url,
            out_dir,
            cookie_path=cookie_path,
            cookies_from_browser=cookies_from_browser,
            requested_auth=requested_auth,
            max_width=max_width,
            resume_partials=resume_partials,
        )

    # When the failure looks like yt-dlp is out of date (YouTube changed
    # something), update yt-dlp and try again before giving up.
    outcome = yt.with_update_retry(
        attempt,
        failed=lambda o: o["video"] is None,
        output=lambda o: yt.output_of(o["result"]) + "\n" + (o["degraded"] or ""),
    )
    video: Path | None = outcome["video"]
    result: subprocess.CompletedProcess | None = outcome["result"]
    degraded_reason: str | None = outcome["degraded"]

    if video is None and is_direct_media(url) and requested_auth == "none":
        print("[download] yt-dlp failed; trying ffmpeg on the direct link", file=sys.stderr)
        return download_direct_media(url, out_dir)
    if video is None and degraded_reason:
        raise SystemExit(
            f"Download stopped because the available YouTube formats appear degraded: "
            f"{degraded_reason}. Install a supported JavaScript runtime (Deno or Node.js), "
            "then retry; if no higher-quality format is available, ask before accepting it."
        )
    if video is None:
        classified = classify_download_error(yt.output_of(result))
        rc = result.returncode if result else -1
        raise SystemExit(
            f"yt-dlp did not produce a video file in {out_dir} (exit {rc}; "
            f"{classified['kind']}). {classified['guidance']} {yt.LOCAL_FILE_FALLBACK}"
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
    # URL (and auth mode) before reusing the download. The yt-dlp version and
    # JS runtime make a bad result traceable to the tools that produced it.
    info_version = None
    try:
        info_version = yt.version_from_info(
            json.loads((out_dir / "video.info.json").read_text(encoding="utf-8"))
        )
    except (OSError, json.JSONDecodeError):
        pass
    _write_source_marker(
        out_dir,
        url,
        requested_auth,
        outcome["client"],
        complete=True,
        max_width=max_width,
        ytdlp_version=info_version,
        js_runtime=yt.js_runtime_name(),
    )
    warning = yt.staleness_warning(info_version)
    if warning:
        print(f"[download] NOTE: {warning}", file=sys.stderr)

    return _result_from_dir(out_dir, video, url)


def _pick_caption(out_dir: Path, stem: str) -> Path | None:
    candidates = sorted(out_dir.glob(f"{stem}*.vtt"))
    if not candidates:
        return None
    english = [c for c in candidates if ".en" in c.name.lower()]
    return english[0] if english else candidates[0]


DEFAULT_SUB_LANGS = "en,en-US,en-GB,en-orig"


def fetch_caption_tracks(
    url: str,
    out_dir: Path,
    *,
    stem: str = "captions",
    langs: str = DEFAULT_SUB_LANGS,
    include_auto: bool = True,
    write_info_json: bool = False,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> list[Path]:
    """Fetch subtitle tracks only (no video download) as VTT files.

    Returns every ``<stem>.<lang>.vtt`` written, sorted. ``langs`` is passed to
    yt-dlp's ``--sub-langs`` (comma-separated codes or regexes, or ``all``).
    Manual tracks win over auto-generated ones for the same language; set
    ``include_auto=False`` for manual tracks only. Uses the same merged
    multi-client strategy as download_url for public YouTube URLs.
    """
    ytdlp = yt.require()
    if cookies and cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")
    cookie_path: Path | None = None
    if cookies:
        cookie_path = Path(cookies).expanduser().resolve()
        if not cookie_path.exists():
            raise SystemExit(f"Cookie file not found: {cookie_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    template = str(out_dir / f"{stem}.%(ext)s")

    no_auth = not (cookies or cookies_from_browser)
    attempts = player_client_attempts(url, authenticated=not no_auth)

    def attempt() -> tuple[list[Path], subprocess.CompletedProcess | None]:
        result: subprocess.CompletedProcess | None = None
        for player_client in attempts:
            # Clear any prior captions so only files the current attempt produced
            # are picked; otherwise a stale VTT from an earlier run would
            # short-circuit the web-client fallback and the failure path.
            for stale in [*out_dir.glob(f"{stem}*.vtt"), out_dir / f"{stem}.info.json"]:
                try:
                    stale.unlink()
                except OSError:
                    pass
            cmd = [
                yt.find() or ytdlp,
                "--skip-download",
                "--ignore-no-formats-error",
                "--write-subs",
            ]
            if include_auto:
                cmd.append("--write-auto-subs")
            cmd += [
                "--sub-langs", langs,
                "--sub-format", "vtt",
                "--convert-subs", "vtt",
                "--no-playlist",
                "-o", template,
            ]
            cmd += yt.runtime_args()
            if write_info_json:
                cmd.append("--write-info-json")
            cmd += yt.client_args(player_client)
            cmd += yt.auth_args(cookie_path, cookies_from_browser)
            cmd.append(url)
            result = yt.run(cmd)
            yt.echo(result)
            tracks = sorted(out_dir.glob(f"{stem}*.vtt"))
            if tracks:
                return tracks, result
        return [], result

    tracks, result = yt.with_update_retry(
        attempt, failed=lambda o: not o[0], output=lambda o: yt.output_of(o[1])
    )
    if tracks:
        return tracks

    classified = classify_download_error(yt.output_of(result))
    raise SystemExit(
        f"yt-dlp did not produce subtitles for {url} ({classified['kind']}). "
        f"{classified['guidance']}"
    )


def fetch_captions(
    url: str,
    out_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> Path | None:
    """Fetch English subtitles only (no video download) and return one VTT path.

    Retrofits a transcript for an output directory whose video was processed from
    a local file (so the caption pass never ran).
    """
    stem = "captions"
    fetch_caption_tracks(
        url,
        out_dir,
        stem=stem,
        cookies=cookies,
        cookies_from_browser=cookies_from_browser,
    )
    return _pick_caption(out_dir, stem)


def list_caption_tracks(
    url: str,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> dict:
    """Return available subtitle languages without downloading anything.

    Result: ``{"title", "id", "manual": [langs], "auto": [langs]}``.
    """
    ytdlp = yt.require()
    if cookies and cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")
    no_auth = not (cookies or cookies_from_browser)
    attempts = player_client_attempts(url, authenticated=not no_auth)
    def attempt() -> tuple[dict | None, subprocess.CompletedProcess | None]:
        result: subprocess.CompletedProcess | None = None
        for player_client in attempts:
            cmd = [yt.find() or ytdlp, "-J", "--skip-download", "--ignore-no-formats-error", "--no-playlist"]
            cmd += yt.runtime_args()
            cmd += yt.client_args(player_client)
            if cookies:
                cmd += ["--cookies", str(Path(cookies).expanduser().resolve())]
            if cookies_from_browser:
                cmd += ["--cookies-from-browser", cookies_from_browser]
            cmd.append(url)
            result = yt.run(cmd)
            if result.returncode != 0 or not result.stdout.strip():
                continue
            try:
                info = json.loads(result.stdout)
            except json.JSONDecodeError:
                continue
            manual = sorted(k for k in (info.get("subtitles") or {}) if k != "live_chat")
            auto = sorted((info.get("automatic_captions") or {}).keys())
            if not manual and not auto and player_client is not None:
                # Some clients omit caption metadata; try the next one.
                continue
            return {"title": info.get("title"), "id": info.get("id"), "manual": manual, "auto": auto}, result
        return None, result

    listing, result = yt.with_update_retry(
        attempt, failed=lambda o: o[0] is None, output=lambda o: yt.output_of(o[1])
    )
    if listing is not None:
        return listing

    classified = classify_download_error(yt.output_of(result))
    raise SystemExit(
        f"Could not list subtitles for {url} ({classified['kind']}). {classified['guidance']}"
    )


def fetch_title(
    url: str,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
) -> str | None:
    """Fetch a video's remote title without downloading media.

    Uses the same client strategy as download/captions: one merged
    multi-client attempt for public YouTube URLs, otherwise the default client.
    """
    ytdlp = yt.require()
    if cookies and cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")
    cookie_path: Path | None = None
    if cookies:
        cookie_path = Path(cookies).expanduser().resolve()
        if not cookie_path.exists():
            raise SystemExit(f"Cookie file not found: {cookie_path}")

    no_auth = not (cookies or cookies_from_browser)
    attempts = player_client_attempts(url, authenticated=not no_auth)

    for player_client in attempts:
        cmd = [ytdlp, "--no-playlist", "--get-title"]
        cmd += yt.runtime_args()
        cmd += yt.client_args(player_client)
        cmd += yt.auth_args(cookie_path, cookies_from_browser)
        cmd.append(url)
        result = yt.run(cmd)
        if result.returncode == 0:
            title = (result.stdout or "").strip().splitlines()
            if title:
                return title[0].strip()
        yt.echo(result)
    return None


def download(
    source: str,
    out_dir: Path,
    *,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    force: bool = False,
    max_width: int | None = DEFAULT_DOWNLOAD_MAX_WIDTH,
) -> dict:
    if is_url(source):
        return download_url(
            source,
            out_dir,
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
            force=force,
            max_width=max_width,
        )
    return resolve_local(source)


def _best_format_selector(container: str, audio_lang: str) -> str:
    audio_filter = (
        "[format_note*=original]"
        if audio_lang == "original"
        else f"[language^={audio_lang}]"
    )
    audio_filter += "[format_note!*=DRC]"
    if container == "mp4":
        return (
            f"bv*[vcodec^=avc1]+ba{audio_filter}[acodec^=mp4a]"
            f"/b[vcodec^=avc1][acodec^=mp4a]{audio_filter}"
        )
    return f"bv*+ba{audio_filter}/b{audio_filter}"


def _best_dash_selector(container: str, audio_lang: str) -> str:
    audio_filter = (
        "[format_note*=original]"
        if audio_lang == "original"
        else f"[language^={audio_lang}]"
    )
    audio_filter = f"[protocol^=http]{audio_filter}[format_note!*=DRC]"
    if container == "mp4":
        return (
            "bv*[protocol^=http][vcodec^=avc1]+ba"
            f"{audio_filter}[acodec^=mp4a]"
            f"/b[protocol^=http][vcodec^=avc1][acodec^=mp4a]{audio_filter}"
        )
    return f"bv*[protocol^=http]+ba{audio_filter}/b[protocol^=http]{audio_filter}"


def _format_parts(info: dict) -> tuple[dict, dict | None, list[dict]]:
    formats = info.get("requested_formats") or [info]
    video = next(
        (fmt for fmt in formats if fmt.get("vcodec") not in (None, "none")),
        None,
    )
    audio = next(
        (fmt for fmt in formats if fmt.get("acodec") not in (None, "none")),
        None,
    )
    return video or {}, audio, formats


def _audio_matches(audio: dict | None, audio_lang: str, container: str) -> bool:
    if not audio or "drc" in str(audio.get("format_note") or "").lower():
        return False
    if container == "mp4" and not str(audio.get("acodec") or "").startswith("mp4a"):
        return False
    if audio_lang == "original":
        return "original" in str(audio.get("format_note") or "").lower()
    return str(audio.get("language") or "").lower().startswith(audio_lang.lower())


def _fps(fmt: dict) -> float | None:
    value = fmt.get("fps")
    try:
        return float(value) if value else None
    except (TypeError, ValueError):
        return None


def _format_summary(fmt: dict, *, kind: str) -> str:
    fields = [
        f"id={fmt.get('format_id', '?')}",
        f"codec={fmt.get('vcodec' if kind == 'video' else 'acodec', '?')}",
        f"protocol={fmt.get('protocol', '?')}",
    ]
    if kind == "video":
        fields += [
            f"resolution={fmt.get('width', '?')}x{fmt.get('height', '?')}",
            f"fps={fmt.get('fps', '?')}",
        ]
    else:
        fields += [
            f"language={fmt.get('language') or '?'}",
            f"track={fmt.get('format_note') or '?'}",
        ]
    return ", ".join(fields)


def _inspect_best_format(
    ytdlp: str,
    url: str,
    *,
    container: str,
    audio_lang: str,
    player_client: str | None,
    cookie_path: Path | None,
    cookies_from_browser: str | None,
) -> tuple[dict | None, subprocess.CompletedProcess]:
    cmd = [
        ytdlp, "-J", "--skip-download", "--ignore-no-formats-error", "--no-playlist",
        "-f", _best_format_selector(container, audio_lang),
        "-S", "res,fps,proto:https,br,codec:h264",
    ]
    cmd += yt.runtime_args()
    cmd += yt.client_args(player_client)
    cmd += yt.auth_args(cookie_path, cookies_from_browser)
    cmd.append(url)
    result = yt.run(cmd)
    yt.echo(result, stdout=False)
    if result.returncode or not result.stdout.strip():
        return None, result
    try:
        return json.loads(result.stdout), result
    except json.JSONDecodeError:
        return None, result


def _probe_copy(path: Path) -> dict:
    ffprobe = _resolve_tool("ffprobe")
    ffmpeg = _resolve_tool("ffmpeg")
    if not ffprobe or not ffmpeg:
        raise SystemExit("ffprobe and ffmpeg are required to verify the downloaded copy")
    result = subprocess.run(
        [
            ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode:
        raise SystemExit(f"ffprobe could not read the downloaded file: {result.stderr.strip()[:300]}")
    try:
        metadata = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"ffprobe returned invalid metadata: {exc}") from exc
    streams = metadata.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if not video:
        raise SystemExit("The downloaded file has no video stream")
    duration = float((metadata.get("format") or {}).get("duration") or 0)
    if not duration:
        duration = float(video.get("duration") or 0)
    if not duration:
        raise SystemExit("ffprobe could not determine the downloaded video's duration")
    packet_sizes = subprocess.run(
        [
            ffprobe, "-v", "error", "-select_streams", "v:0",
            "-show_entries", "packet=size", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    video_bitrate = None
    if packet_sizes.returncode == 0:
        try:
            bits = sum(int(line) for line in packet_sizes.stdout.splitlines() if line.strip().isdigit())
            video_bitrate = round(bits * 8 / duration)
        except ValueError:
            pass
    if video_bitrate is None and video.get("bit_rate"):
        video_bitrate = int(video["bit_rate"])
    frame_rate = video.get("avg_frame_rate") or video.get("r_frame_rate")
    try:
        frame_rate = round(float(Fraction(frame_rate)), 3)
    except (ValueError, ZeroDivisionError, TypeError):
        frame_rate = None
    midpoint = max(0, duration / 2)
    decode = subprocess.run(
        [
            ffmpeg, "-v", "error", "-ss", f"{midpoint:.3f}", "-i", str(path),
            "-t", "2", "-map", "0:v:0", "-map", "0:a:0?", "-f", "null", "-",
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if decode.returncode:
        raise SystemExit(f"Short decode test failed: {decode.stderr.strip()[:300]}")
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "duration_seconds": round(duration, 3),
        "width": video.get("width"),
        "height": video.get("height"),
        "video_codec": video.get("codec_name"),
        "audio_codec": audio.get("codec_name") if audio else None,
        "fps": frame_rate,
        "video_bitrate_bps": video_bitrate,
        "decode_test": "passed (2 seconds at the midpoint)",
    }


def download_best_copy(
    url: str,
    out_dir: Path,
    *,
    container: str = "mkv",
    audio_lang: str = "original",
    subs: str = "none",
    allow_low_quality: bool = False,
    cookies: str | None = None,
    cookies_from_browser: str | None = None,
    temp_dir: Path | None = None,
) -> dict:
    if not is_url(url):
        raise SystemExit("--best requires an HTTP(S) URL")
    if container not in {"mkv", "mp4"}:
        raise SystemExit("--container must be mkv or mp4")
    if subs not in {"none", "srt", "srt+transcript"}:
        raise SystemExit("--subs must be none, srt, or srt+transcript")
    if not audio_lang or audio_lang.strip() != audio_lang:
        raise SystemExit("--audio-lang must be 'original' or a language code")
    if audio_lang != "original" and any(
        not (character.isalnum() or character == "-") for character in audio_lang
    ):
        raise SystemExit("--audio-lang must be 'original' or a simple language code")
    if cookies and cookies_from_browser:
        raise SystemExit("Use only one of --cookies or --cookies-from-browser")
    cookie_path = Path(cookies).expanduser().resolve() if cookies else None
    if cookie_path and not cookie_path.exists():
        raise SystemExit(f"Cookie file not found: {cookie_path}")
    ytdlp = yt.require()
    if is_youtube(url) and not yt.runtime_args():
        print(
            "[download] WARNING: no Deno or Node.js runtime found; YouTube may expose "
            "only low-resolution formats",
            file=sys.stderr,
        )
    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    temp_dir = (temp_dir or (Path.home() / "dltmp")).expanduser().resolve()
    temp_dir.mkdir(parents=True, exist_ok=True)
    attempts = player_client_attempts(url, authenticated=bool(cookies or cookies_from_browser))

    def inspect() -> dict:
        state: dict = {
            "info": None, "client": None, "video": {}, "audio": None,
            "error": "", "low_height": None,
        }
        for client in attempts:
            info, result = _inspect_best_format(
                yt.find() or ytdlp, url, container=container, audio_lang=audio_lang,
                player_client=client, cookie_path=cookie_path,
                cookies_from_browser=cookies_from_browser,
            )
            if not info:
                state["error"] = (result.stderr or result.stdout or "").strip()
                continue
            video, audio, formats = _format_parts(info)
            if not video or not audio or not _audio_matches(audio, audio_lang, container):
                state["error"] = (
                    f"No {audio_lang} audio stream satisfies the requested "
                    f"{container.upper()} codec requirements"
                )
                continue
            height = max((fmt.get("height") or 0 for fmt in formats), default=0)
            state.update(info=info, client=client, video=video, audio=audio)
            if height >= 720 or allow_low_quality:
                break
            print(
                f"[download] available best stream is only {height or 'unknown'}p; "
                "checking another YouTube client",
                file=sys.stderr,
            )
            state["low_height"] = height or None
            state["info"] = None
        return state

    def inspection_output(state: dict) -> str:
        text = state["error"] or ""
        if state["low_height"] is not None:
            # A low-quality-only result on YouTube is the classic sign of a stale
            # yt-dlp, so let the update policy treat it like a degraded download.
            text += f"\nselected {state['low_height']}p despite higher-resolution formats"
        return text

    state = yt.with_update_retry(
        inspect, failed=lambda st: st["info"] is None, output=inspection_output
    )
    selected_info: dict | None = state["info"]
    selected_client: str | None = state["client"]
    selected_video: dict = state["video"]
    selected_audio: dict | None = state["audio"]
    last_error: str = state["error"]
    low_height: int | None = state["low_height"]
    if selected_info is None:
        if last_error and "audio stream" in last_error:
            raise SystemExit(last_error)
        if low_height is not None:
            raise SystemExit(
                f"The best available stream is only {low_height}p, below 720p. Ask the user "
                "before accepting a lower-quality copy; pass --allow-low-quality only after approval."
            )
        raise SystemExit(
            "yt-dlp could not select a compatible video and original-audio format."
            + (f" {last_error[:300]}" if last_error else "")
            + f" {yt.LOCAL_FILE_FALLBACK}"
        )

    print(
        f"[download] title: {selected_info.get('title') or 'Unknown'}\n"
        f"[download] channel: {selected_info.get('channel') or selected_info.get('uploader') or 'Unknown'}\n"
        f"[download] duration: {selected_info.get('duration_string') or selected_info.get('duration') or 'Unknown'}\n"
        f"[download] video: {_format_summary(selected_video, kind='video')}\n"
        f"[download] audio: {_format_summary(selected_audio or {}, kind='audio')}",
        file=sys.stderr,
    )
    requested_formats = selected_info.get("requested_formats") or [selected_info]
    format_ids = "+".join(str(fmt["format_id"]) for fmt in requested_formats if fmt.get("format_id"))
    if not format_ids:
        raise SystemExit("yt-dlp did not identify the selected format IDs")
    template = str(out_dir / "%(title)s [%(id)s].%(ext)s")
    download_cmd = [
        yt.find() or ytdlp, "--no-playlist", "--no-progress", "--no-overwrites",
        "-f", format_ids, "-S", "res,fps,proto:https,br,codec:h264",
        "--merge-output-format", container,
        "-P", f"temp:{temp_dir}",
        "-o", template,
        "--print", "after_move:filepath",
    ]
    download_cmd += yt.runtime_args()
    download_cmd += yt.client_args(selected_client)
    download_cmd += yt.auth_args(cookie_path, cookies_from_browser)
    download_cmd.append(url)
    result = yt.run(download_cmd)
    yt.echo(result, stdout=False)
    if result.returncode and "conversion failed!" in yt.output_of(result).lower():
        print("[download] merge failed; retrying with HTTP(S)-only formats", file=sys.stderr)
        download_cmd[download_cmd.index("-f") + 1] = _best_dash_selector(container, audio_lang)
        result = yt.run(download_cmd)
        yt.echo(result, stdout=False)
    if result.returncode:
        raise SystemExit(f"yt-dlp failed (exit {result.returncode}): {(result.stderr or '').strip()[-600:]}")
    video_path = next(
        (Path(line.strip()) for line in reversed((result.stdout or "").splitlines())
         if Path(line.strip()).is_file()),
        None,
    )
    if video_path is None:
        raise SystemExit("yt-dlp reported success but did not return the downloaded file path")
    verified = _probe_copy(video_path)
    if (verified.get("height") or 0) < 720 and not allow_low_quality:
        raise SystemExit(
            f"The downloaded file is only {verified.get('height') or 'unknown'}p. Ask before accepting "
            "this quality; pass --allow-low-quality only after approval."
        )
    if subs != "none":
        from captions import download_captions

        summary = download_captions(
            url,
            out_dir,
            langs="en",
            formats=["srt", "txt"] if subs == "srt+transcript" else ["srt"],
            cookies=cookies,
            cookies_from_browser=cookies_from_browser,
        )
        verified["captions"] = summary["files"]
    return {
        "title": selected_info.get("title"),
        "channel": selected_info.get("channel") or selected_info.get("uploader"),
        "duration_seconds": selected_info.get("duration"),
        "video": verified,
        "ytdlp_version": yt.version_from_info(selected_info),
        "js_runtime": yt.js_runtime_name(),
    }


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if "--best" in argv:
        parser = argparse.ArgumentParser(
            prog="download.py --best",
            description="Download and verify a best-quality local video copy without analysis.",
        )
        parser.add_argument("--best", action="store_true", required=True)
        parser.add_argument("--source", required=True, help="Video URL")
        parser.add_argument("--out-dir", required=True, help="Final destination directory")
        parser.add_argument("--container", choices=("mkv", "mp4"), default="mkv")
        parser.add_argument("--audio-lang", default="original", help="original or a language code")
        parser.add_argument("--subs", choices=("none", "srt", "srt+transcript"), default="none")
        parser.add_argument("--allow-low-quality", action="store_true")
        parser.add_argument("--cookies")
        parser.add_argument("--cookies-from-browser")
        parser.add_argument("--temp-dir", default=str(Path.home() / "dltmp"))
        args = parser.parse_args(argv)
        result = download_best_copy(
            args.source,
            Path(args.out_dir),
            container=args.container,
            audio_lang=args.audio_lang,
            subs=args.subs,
            allow_low_quality=args.allow_low_quality,
            cookies=args.cookies,
            cookies_from_browser=args.cookies_from_browser,
            temp_dir=Path(args.temp_dir),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if len(argv) < 2:
        print("usage: download.py <url-or-path> <out-dir>", file=sys.stderr)
        return 2
    result = download(argv[0], Path(argv[1]))
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
