#!/usr/bin/env python3
"""Quick YouTube health check for the skill's download path.

Asks YouTube which formats it offers for a known video, without downloading
anything, and reports whether full quality is still reachable:

    health_check.py [--url URL] [--min-height 1080] [--no-update]

Prints a JSON report to stdout and exits 0 when healthy, 1 when something
needs attention (low quality only, SABR, missing JS runtime, extraction
failure), 2 when yt-dlp is not installed.

If extraction looks broken in a way an update can fix, it updates yt-dlp
(stable, then nightly) and checks again, the same way real downloads do.
Run it from the machine that does the downloads: cloud servers are often
blocked by YouTube's bot check, which says nothing about the skill's health.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ytdlp as yt  # noqa: E402
from download import player_client_attempts  # noqa: E402

# 1:56:49, 1080p24 H.264/VP9/AV1, 20 audio languages. Used as the acceptance
# test for best-copy mode, so a drop below 1080p here is a real regression.
DEFAULT_URL = "https://www.youtube.com/watch?v=vTQzbvC9CW0"


def _probe(ytdlp_path: str, url: str) -> dict:
    """Best result across the player clients the skill uses."""
    best: dict = {"max_height": 0, "client": None, "format_count": 0, "output": ""}
    outputs = []
    for client in player_client_attempts(url, authenticated=False):
        cmd = [ytdlp_path, "-J", "--skip-download", "--ignore-no-formats-error", "--no-playlist"]
        cmd += yt.runtime_args() + yt.client_args(client) + [url]
        result = yt.run(cmd, timeout=180)
        outputs.append(result.stderr or "")
        try:
            info = json.loads(result.stdout) if result.stdout.strip() else {}
        except json.JSONDecodeError:
            info = {}
        # Storyboards (thumbnail strips) carry a height but no video codec.
        formats = [
            f for f in (info.get("formats") or [])
            if f.get("vcodec") not in (None, "none") or f.get("acodec") not in (None, "none")
        ]
        height = max(
            (f.get("height") or 0 for f in formats if f.get("vcodec") not in (None, "none")),
            default=0,
        )
        if height > best["max_height"]:
            best.update(
                max_height=height,
                client=client or "default",
                format_count=len(formats),
                title=info.get("title"),
                ytdlp_version=yt.version_from_info(info),
            )
    best["output"] = "\n".join(outputs)
    best["sabr_warning"] = "sabr" in best["output"].lower()
    return best


def check(url: str, min_height: int) -> dict:
    ytdlp_path = yt.find()
    if not ytdlp_path:
        return {"status": "not_installed", "problems": [yt.MISSING_MESSAGE]}

    def attempt() -> dict:
        return _probe(yt.find() or ytdlp_path, url)

    def output(probe: dict) -> str:
        text = probe["output"]
        if 0 < probe["max_height"] < min_height:
            text += f"\nselected {probe['max_height']}p despite higher-resolution formats"
        return text

    probe = yt.with_update_retry(
        attempt, failed=lambda p: p["max_height"] < min_height, output=output
    )
    version = probe.get("ytdlp_version") or yt.version(yt.find() or ytdlp_path)
    problems = []
    lowered = probe["output"].lower()
    if "not a bot" in lowered or "http error 429" in lowered:
        problems.append(
            "YouTube's bot check or rate limit blocked this machine; that is about this "
            "network, not yt-dlp. Run the check from the machine that does the downloads"
        )
    if probe["max_height"] == 0:
        problems.append("yt-dlp returned no formats (extraction failed or the video is gone)")
    elif probe["max_height"] < min_height:
        problems.append(f"best available is {probe['max_height']}p, expected {min_height}p")
    if not yt.js_runtime_name():
        problems.append("no Deno or Node.js runtime found; YouTube quality will be limited")
    stale = yt.staleness_warning(version)
    if stale:
        problems.append(stale)
    notes = []
    if probe["sabr_warning"]:
        # Expected on some clients; only a problem if quality dropped too.
        notes.append("yt-dlp printed a SABR warning for at least one player client")
    tail = [line for line in probe["output"].splitlines() if "ERROR" in line or "WARNING" in line]
    return {
        "status": "healthy" if not problems else "needs_attention",
        "url": url,
        "title": probe.get("title"),
        "max_height": probe["max_height"],
        "best_client": probe["client"],
        "format_count": probe["format_count"],
        "ytdlp_version": version,
        "ytdlp_age_days": yt.age_days(version),
        "js_runtime": yt.js_runtime_name(),
        "problems": problems,
        "notes": notes,
        "ytdlp_messages": tail[-8:],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--min-height", type=int, default=1080)
    parser.add_argument("--no-update", action="store_true", help="never update yt-dlp")
    args = parser.parse_args(argv)
    if args.no_update:
        import os

        os.environ[yt.NO_UPDATE_ENV] = "1"
    report = check(args.url, args.min_height)
    print(json.dumps(report, indent=2, ensure_ascii=False))
    if report["status"] == "not_installed":
        return 2
    return 0 if report["status"] == "healthy" else 1


if __name__ == "__main__":
    raise SystemExit(main())
