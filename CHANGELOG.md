# Changelog

All notable changes to `/analyze-video` are documented here.

## [1.6.1] - 2026-06-10

Patch release focused on local-file workflows when the original source was a URL.

### Added
- **`--source-url` for local-file runs.** `process.py` now accepts `--source-url <original-url>` when `--source` is a local video file, so the pipeline can recover missing metadata from the original remote source.
- **Automatic transcript recovery from `--source-url`.** If a local-file run has no transcript after the normal caption/Whisper path, `process.py` now fetches captions from `--source-url` (without re-downloading media) and writes `transcript.txt` before manifest output.
- **Remote title recovery helper.** `download.py` adds `fetch_title()` (yt-dlp, android-first for public YouTube) so local placeholder titles can be replaced with the real video title.

### Changed
- **Title fallback for local files.** When local metadata title looks like a filename placeholder (for example `video.mp4`), `process.py` now recovers the remote title from `--source-url` and uses it for `suggested_docx_name`.
- **SKILL.md path consistency.** Command examples now consistently use `"$SKILL_DIR/scripts/..."` and explicitly warn against hardcoding `~/.cache/analyze-video/scripts/...`.

## [1.6.0] - 2026-06-08

Reliability fixes driven by a real-world YouTube run inside a Linux sandbox: bot detection on cloud IPs, the 256 KB manifest Read limit, and transcript gaps on locally-downloaded files.

### Added
- **Android-first YouTube downloads.** For public YouTube URLs, `download.py` now leads with the android player client (`--extractor-args youtube:player-client=android`), which bypasses YouTube's n-challenge without a JavaScript runtime and avoids the 403s the default web client hits from server/cloud IPs. If it can't produce a usable video, it retries once with the web client. The same client is applied to the subtitle pass. When cookies are supplied, the authenticated web session is honored instead (the android client ignores cookies).
- **`--captions-only` retrofit mode.** `process.py --captions-only --source <url> --out-dir <dir>` fetches auto-subtitles and writes `transcript.txt` without re-downloading or re-extracting the video, then patches any existing `manifest(_lite).json` transcript fields (including per-chunk slices). This recovers a transcript for an output directory whose video was processed from a separately downloaded local file.

### Changed
- **Slimmer `manifest_lite.json`.** The lite manifest no longer carries per-frame arrays (it already dropped transcript text). On long videos those arrays could push the file past the 256 KB Read-tool limit. `select_frames.py` now transparently loads the full `manifest.json` (via the lite file's `manifest_path` pointer or a sibling) to read frame paths, so the existing invocation keeps working.
- **Download cache lease is always released.** The pipeline now runs cache `end_use` + prune in a `finally`, so a failed download (more likely now that there are two attempts) can't leave an `.in_use` lease protecting a cache entry from eviction until its TTL.
- **Auth-aware cache reuse.** A cached anonymous download is no longer served to a later cookie-authenticated request for the same URL; the `.source.json` marker now records the auth mode and client.

### Docs
- SKILL.md documents the android-first fallback, the `--cookies-from-browser` same-OS caveat (run yt-dlp host-side in a sandbox), the preference for pip/pipx yt-dlp over a frozen binary, the `--captions-only` recovery step, and the real chunk schema field names (`index`, `start_formatted`, `end_formatted`).

## [1.5.0] - 2026-06-08

### Added
- **Self-managing download cache.** The shared download cache (`~/.cache/analyze-video/downloads/`) no longer grows without bound. At the end of each run, `process.py` evicts entries older than 14 days and trims the total back under 5 GB (least-recently-used first), never removing the download the current run (or a concurrent run, via an `.in_use` lease) depends on. Limits are tunable with `ANALYZE_VIDEO_CACHE_MAX_AGE_DAYS` and `ANALYZE_VIDEO_CACHE_MAX_GB` (either set to `0` disables that limit).
- **Manual cache clear.** `setup.py --clear-cache` wipes every cached download (reporting how much was freed) and leaves the `docx` module cache untouched. `setup.py --json` now reports `download_cache_bytes`.

### Changed
- Cache maintenance is intentionally strict: it only ever deletes directories directly under the downloads cache whose names are 16-character hex cache keys, and refuses to operate if that path is a symlink, so unrelated data (including the sibling `docx` `node_modules` cache) is never at risk.

## [1.4.0] - 2026-06-08

Robustness fixes for constrained/sandboxed environments, driven by a batch of real-world run reports. Adds a constrained-Linux integration test to catch these seams in CI.

### Added
- **PATH-robust tool resolution.** A shared `resolve_tool()` finds `ffmpeg`, `ffprobe`, and `yt-dlp` even when they're installed in a user-local bin (`~/.local/bin`, the Python userbase) that isn't on `PATH`, and the pipeline now invokes them by absolute path. This fixes "tool installed but invisible" failures after `pip install --user` / `pipx`.
- **Auto-install on Linux.** `setup.py` now runs the no-sudo installs itself (`yt-dlp` via `pipx`/`pip --user`, with a PEP 668 `--break-system-packages` fallback; the `docx` npm module into the per-user cache) instead of only printing commands. It prints exact `export PATH=...` hints when a tool lands off `PATH`, and sudo hints only for packages that need root (`ffmpeg`, Node.js/npm).
- **Constrained-environment integration test.** A new CI job makes the skill directory read-only, runs the real pipeline on a synthetic local video, and builds the document, asserting the `docx` module self-installs into the writable cache. This catches environment/integration seams that mocked unit tests can't.

### Changed
- **`docx` installs into the per-user cache when the skill dir is read-only.** `setup.py` now installs `docx` into `~/.cache/analyze-video` (matching what `build-docx.js` already does) when `scripts/` isn't writable, instead of silently failing against a read-only mount.
- **Compact contact-sheet appendix.** Contact sheets in the document appendix are now sized so about two fit per page, instead of one sheet ballooning to a full page each.
- **Hardened delivery gate.** SKILL.md Step 8 now makes the "include contact sheets / transcript?" question a mandatory, explicit gate before the single document build, so appendices are never added without asking.

### Docs
- SKILL.md documents skill-directory resolution when `CLAUDE_SKILL_DIR` is unset, the setup exit-code contract (0 = ready, non-zero = not ready), and the harmless yt-dlp "no JavaScript runtime" warning.

## [1.3.0] - 2026-06-08

### Added
- **Source recorded in the document.** Each video's analysis now shows a readable "Source:" line (the original URL or local path) directly under its title, so the finished document records exactly what was analyzed. Driven by a new per-video `source` spec field.
- **Standalone transcript file.** When a transcript is available, `process.py` writes a human-readable `transcript.txt` (`[mm:ss] text` per line) next to the manifest and exposes its location as `transcript_path`.
- **Optional transcript appendix.** `build-docx.js` accepts an `appendix_transcript` array; each entry points at a `transcript.txt` by `path` (the builder reads the file directly, so long transcripts don't bloat the spec) with inline `lines`/`text` as a fallback.
- **Keep-artifacts options.** At delivery the skill now offers to include the contact sheet(s) and/or the full transcript as in-document appendices, and to keep standalone copies (slug-named, collision-safe) next to the finished document even when the working files are cleaned up.

## [1.2.1] - 2026-06-08

### Added
- **Title-based output naming.** The produced Word document is now named after the video(s) analyzed plus the word "analysis" (for example `how-to-bake-bread-analysis.docx`) instead of a generic `output.docx`. `process.py` emits a ready-made, slug-safe `suggested_docx_name` in the manifest (falling back to the source's basename when a title isn't available), and SKILL.md instructs the builder to use it, including a combined name for multi-video documents.

## [1.1.0] - 2026-06-08

Reliability fixes for long videos in sandboxed environments, a shared download cache, and a 2-up document layout. Driven by a second real-world run report.

### Added
- **Shared download cache:** a URL is now downloaded once into `~/.cache/analyze-video/downloads/<url-hash>/` and reused across runs, so a focused `--start`/`--end` rerun (even in a different `--out-dir`) no longer re-downloads the whole video. The full video is always fetched, so timestamps stay correct. New `process.py --no-download-cache` keeps the source under the out-dir instead; `--force` refreshes a cached download.
- **2-up document layout:** `build-docx.js` accepts a `frame_layout` spec field (`"1up"` default, or `"2up"` for side-by-side frame pairs in a borderless table). Settable at the spec, video, or section level. Captions and required alt text are preserved in both layouts.
- **Interrupted-run visibility:** `status.json` now records the in-flight `current_chunk` (written before extraction starts) and a `resume_hint`, and chunked runs print a "re-run to resume" note, so a timeout makes it obvious that re-running continues from where it stopped.

### Changed
- **Frames now extract into a signature-keyed subdirectory** (`chunks/chunk_N/frames/<sig>/`). Each distinct extraction configuration gets its own directory, so a re-run never has to delete a previous run's files and stale frames from an earlier run can't pollute the result. Always use the manifest's `absolute_path` to locate frames.
- `setup.py` now detects an installed `docx` module across all the locations the builder actually checks (`DOCX_NODE_MODULES`, `NODE_PATH`, `scripts/node_modules`, `~/.cache/analyze-video/node_modules`, and Node's default resolution), and prints an honest "docx pending" note instead of implying everything is ready when it can't be found.

### Fixed
- **Resume no longer crashes with `PermissionError` after a timeout.** Environments that forbid cross-session file deletion previously hard-crashed when a resume tried to clear a prior run's frames. Frame extraction no longer deletes across runs at all (different configs use different directories; identical configs are safely overwritten by ffmpeg).
- A stale subtitle or `info.json` left in the download cache can no longer be paired with a freshly downloaded video (prior artifacts are cleared before each re-download).

## [1.0.0] — 2026-06-07

First stable release. Reliability, resume, and real-world failure-handling overhaul driven by a production run report.

### Added
- **Resume support:** frame extraction now skips re-extraction when a chunk's frames already exist and the source video plus extraction parameters are unchanged (signature-based check). Use `process.py --force` to re-extract from scratch.
- **Local sidecar pickup:** a co-located subtitle (`<video>.en.vtt`, `<video>.*.vtt`, or a lone `.vtt` paired with a single video) and a co-located `<video>.info.json` are now used for captions, title, uploader, and source URL when analyzing a local file.
- **Trailing promo/outro detection:** `detect_trailing_promo()` flags repeated end cards and static outros. New opt-in `process.py --trim-static-outro` trims the detected promo from analysis, but only for high-confidence detections so quiet legitimate endings are reported, never silently dropped.
- **Progress and partial output:** `status.json` is written per stage and `manifest_partial.json` is emitted after each chunk (removed on success), so a run killed by a timeout leaves a clear record of how far it got.
- **Contact-sheet appendix:** `build-docx.js` accepts an optional `appendix_contact_sheets` spec field that renders the chunk contact sheets as a visual appendix in the docx.
- **Re-download guard:** a `.source.json` marker records the source URL so a cached download is reused only when it matches the requested URL.
- Tests for resume, sidecar resolution, promo detection, status/partial-manifest writes, and stem-matching edge cases.

### Changed
- `build-docx.js` resolves the `docx` module across `DOCX_NODE_MODULES`, `NODE_PATH`, `scripts/node_modules`, and `~/.cache/analyze-video`, falling back to a cache-dir install only as a last resort. This fixes silent `EACCES` failures when the skill directory is mounted read-only.
- `SKILL.md` documents the new outputs (`status.json`, `manifest_partial.json`), resuming, promo trimming, the docx `NODE_PATH` guidance, and the contact-sheet appendix prompt.

### Fixed
- Local files no longer lose their downloaded subtitle or report the bare filename as the title.
- Frame extraction no longer wipes and re-extracts every chunk on each run, which previously prevented long videos from ever completing in a single pass.

## [0.4.0] — 2026-06-03

Safety, failure-mode, and documentation alignment release.

### Added
- Blocked-download classification for common yt-dlp failures, including login/bot checks, 403/429s, age gates, members-only/private access, and geo restrictions.
- User-authorized retry options via `process.py --cookies-from-browser <browser>` and `--cookies <file>`.
- Focused Whisper extraction: `--start`/`--end` ranges now upload only the focused audio instead of the full video audio.
- Whisper audio size guard with a clear focused-range fallback message.
- Tests for download failure classification, no-key setup readiness, and focused audio extraction.

### Changed
- `SKILL.md` now matches the actual schema v3 pipeline: `manifest_lite.json`, chunked contact sheets, `select_frames.py`, and JSON-spec docx generation via `scripts/build-docx.js`.
- `setup.py --check` treats missing Whisper keys as a degraded-but-ready state when required local dependencies are present.
- README privacy/failure guidance now distinguishes authorized browser-cookie use from bot-detection evasion.

### Fixed
- `SKILL.md` license metadata now matches the AGPL-3.0 license used by the repository.
- `process.py` documentation now correctly identifies manifest schema version 3.

## [0.3.0] — 2026-06-01

Skill-definition and documentation alignment for the v2 workflow.

### Changed
- `SKILL.md` replaced with the new v2 spec content.
- README updated to reflect the merged `/watch` + `/analyze-video` workflow and current guidance.

## [0.2.0] — 2026-05-06

Major UX, ergonomics, and token-budget overhaul.

### Added
- `scripts/select_frames.py` — encapsulates the proportional-distribution + min-1-per-chunk frame-selection algorithm so the skill no longer derives it inline.
- `manifest_lite.json` — slim per-video manifest (no transcript text) emitted alongside the full `manifest.json` for cheap default reads.
- `manifest.docx_image_dimensions` — pre-computed docx image dimensions per video (no more cheat-sheet lookup in SKILL.md).
- `process.py --quick` — quick mode (lower frame budget, signals the skill to skip the contact-sheet preview step via `manifest.quick_mode`).
- `setup.py --set-key <groq|openai> <KEY>` — write Whisper keys safely from the command line.
- `setup.py --install-docx` — install the npm `docx` module once into `scripts/node_modules/` so per-session `npm init && npm install` is no longer needed.
- Node.js + npm added to `setup.py` preflight checks.
- `templates/caption_guide.md` — extracted caption style guide; SKILL.md points to it at write-time only.
- Whisper auto-fallback: when both keys are present and `--whisper` is not pinned, `process.py` automatically falls back from Groq to OpenAI on backend failure.
- `audio.mp3` is reused on re-runs against the same `--out-dir` (no re-extraction).
- Per-video failure handling documented for batch mode in SKILL.md.

### Changed
- **BREAKING (manifest schema_version 3):** transcript text now lives only at `manifest.transcript_segments` (top level). Per-chunk `transcript_slice` carries `{start_index, end_index, segment_count}` index pointers into that array. `transcript.formatted` strings dropped (agent formats on demand).
- Per-frame `path` field (relative) dropped; only `absolute_path` remains.
- Per-chunk `extraction.{fps,target_frames,frame_resolution_width,max_frames_cap}` debug fields dropped; `frame_count` promoted to chunk top-level.
- `process.py` now prints the path to `manifest_lite.json` on stdout (full manifest path is recorded inside it).
- Contact-sheet `tile_width` is auto-picked by chunk frame count (256 / 200 / 160 px) unless `--contact-sheet-tile-width` overrides.
- `scripts/build-docx.js` rewritten as a JSON-spec consumer (`--spec spec.json`); `templates/build.js.template` removed. The skill no longer hand-writes JavaScript at runtime.
- `commands/analyze-video.md` rewritten for v2 (no more `/watch` references).
- SessionStart hook (`hooks/scripts/check-node.sh`) is now silent in all cases (preflight is the single source of truth).
- SKILL.md trimmed (~30 lines): frame-selection pseudocode replaced by `select_frames.py`, aspect-ratio cheat sheet replaced by `docx_image_dimensions`, caption guide and docx rules moved to sidecar files.
- `commands/`, README structure, `plugin.json`, and `marketplace.json` descriptions all aligned with v2 (no `/watch` mentions).
- Plugin version bumped to `0.2.0`.

### Removed
- `templates/build.js.template` (replaced by JSON-spec builder).
- `transcript.formatted` rendered strings from the manifest.

## [0.1.0] — 2026-04-30

Initial release.

### Added
- `/analyze-video` slash command for Claude Code plugin mode.
- Plugin packaging: `.claude-plugin/`, `.codex-plugin/`, `commands/`.
- `scripts/build-docx.js` — reusable Node.js docx builder template with helpers for headings, body text, image embedding, and captions.
- `scripts/build-skill.sh` — builds `dist/analyze-video.skill` for claude.ai upload.
- SessionStart hook to verify Node.js availability.
- `.github/workflows/release.yml` — auto-builds and attaches `.skill` to GitHub releases on tag push.
- `.gitignore`, `.gitattributes`, `CHANGELOG.md`.
- Multi-surface install instructions in README.
