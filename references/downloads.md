# Downloads, captions and access problems

Read this when a download fails or comes back degraded, when the user wants a best-quality local copy, or when you need the full caption options.

## Best-copy download details

Use `--audio-lang <code>` only when the user requests a specific language. The default `original` selects the track yt-dlp labels original and excludes DRC variants. MP4 selects H.264 + AAC streams without re-encoding video; if those exact compatible streams are unavailable, explain that and stop rather than silently re-encoding. For "both", run the selected container choices separately.

The script prints the title, channel, duration, and selected streams before downloading. If the best available stream is below 720p, stop and ask whether the user wants to accept it; only after approval retry with `--allow-low-quality`. After downloading, report actual stream codecs, resolution, fps, and video bitrate measured from the downloaded packets, plus the short decode-test result. Do not use yt-dlp's estimated `~` sizes or bitrates to choose a format.

If the user also wants analysis, first download the local file, then run the normal analysis flow against that file. Do not analyze or build a `.docx` when the user asked for the file only.

## Caption options

- `--format`: `srt` (default), `vtt` (raw track as served), `txt` (deduped `[mm:ss] text` transcript), a comma list like `srt,txt`, or `all`.
- `--langs`: `en` (default, covers en/en-US/en-GB/en-orig), comma-separated codes or yt-dlp regexes (`es,fr`, `pt.*`), or `all`. `all` can be large on YouTube when auto-translations are included; combine with `--no-auto` for uploader tracks only.
- `--no-auto`: manual (uploader) captions only. Without it, auto-generated captions fill in languages with no manual track.
- `--list`: print available manual and auto languages without downloading. Use it when the user asks what languages exist or when the requested language fails.
- Files are named `<title> [<id>].<lang>.<ext>`; existing files are never overwritten (a ` (2)` suffix is added).

Stdout is a JSON array with one entry per source: `files` (`lang`, `format`, `path`) on success or `error` on failure. Exit code is non-zero if any source failed; report successful files and explain each error. Access failures follow the same rules as the main pipeline (see Download failures below): offer user-authorized `--cookies-from-browser`/`--cookies` only after a failure. Local video files are not supported in this mode; point the user to any sidecar `.srt`/`.vtt` next to the file instead.

## Retrying with the user's own browser session

Only after a failure, and only when the user says they are allowed to view the video and agrees.

```bash
python3 "${SKILL_DIR}/scripts/process.py" \
  --source "<url>" \
  --out-dir "$VIDEO_DIR" \
  --cookies-from-browser safari
```

or:

```bash
python3 "${SKILL_DIR}/scripts/process.py" \
  --source "<url>" \
  --out-dir "$VIDEO_DIR" \
  --cookies "/path/to/cookies.txt"
```

`--cookies-from-browser` only works when yt-dlp runs on the same operating system as the browser.

## Download failures

- **Interrupted download**: re-run the exact same command. A download killed partway through resumes from the bytes already on disk rather than starting over, so a timed-out or dropped transfer does not re-fetch the whole video. Use `--force` only when the partial data should be discarded.
- **Download blocked by login, age gate, bot check, members-only, or private access**: explain the specific access issue. Public YouTube attempts use JS-enabled `default,web_safari,tv` clients first, then try `android,ios,web` and yt-dlp's defaults. A detected SABR-only or unexpectedly capped 360p result is retried with another client; if still degraded, stop and explain instead of silently accepting it. Every attempt uses `--ignore-no-formats-error` so gated media formats do not prevent subtitle writes. If access still fails and the user can view the video and authorizes it, retry with `--cookies-from-browser <browser>` or `--cookies <file>`. Otherwise ask for a local file. `--cookies-from-browser` only works when yt-dlp runs on the same OS as the browser.
- **Rate limited**: wait before retrying. User-authorized browser cookies may help if the content is accessible in their browser.
- **Geo restricted**: ask for a local file or another source the user can access from this environment.
- **yt-dlp "No supported JavaScript runtime" warning**: YouTube's android/ios clients may now be SABR-limited and return only 360p. Run `setup.py` to install/check Deno or use the installed Node.js runtime, then retry. Do not deliver a detected degraded stream without asking first.
- **yt-dlp can't find Node / n-challenge fails**: prefer a pipx/pip-installed `yt-dlp` over a frozen standalone binary. The standalone binary cannot reliably locate a system Node.js for subprocess-based extraction even with `PATH` exported, whereas the pip-installed version uses the system interpreter. `setup.py` installs the pip version.
- **yt-dlp out of date**: downloads, caption fetches, and best-copy format checks update yt-dlp automatically (latest stable, then nightly) and retry once when the failure looks like a yt-dlp bug: broken signature/n-challenge extraction, "Requested format is not available", a SABR warning, or a 360p-only result. Access problems (login, bot check, private, members-only, geo, 429) never trigger an update. If `setup.py --check` prints a staleness note, or the user turned automatic updates off with `ANALYZE_VIDEO_NO_YTDLP_UPDATE=1`, run `python3 "${SKILL_DIR}/scripts/setup.py" --update-ytdlp` (add `--nightly` if stable is still broken) and retry. To check YouTube without downloading, run `python3 "${SKILL_DIR}/scripts/health_check.py"` on the download host.
- **Every download route failed**: say so plainly and offer the fallback: the user downloads the video any way they like and gives you the local file path, then run the normal flow on that file (add `--source-url` so captions and the title can still be recovered). Direct media links (`.mp4`, `.webm`, `.mov`, `.m3u8`, `.mpd`, etc.) are fetched with ffmpeg automatically when yt-dlp is missing or fails, so this only comes up for page URLs.
- **yt-dlp installed but not on PATH (Linux)**: apply the exact `export PATH=...` line from setup in the same shell invocation that runs `process.py`, or run by absolute tool path.
