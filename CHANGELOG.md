# Changelog

All notable changes to `/analyze-video` are documented here.

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
