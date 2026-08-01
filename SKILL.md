---
name: analyze-video
description: Use when the user wants to analyze one or more videos (URLs or local files) and produce a Word document with embedded frames and a written timestamp-based analysis. Triggers on "analyze this video", "make a report from this video", "write up this YouTube link", "document what's in these videos", "analyze these clips", "video analysis", or any request that includes video URLs or local video paths and asks for a written deliverable.
allowed-tools: Bash, Read, Write, AskUserQuestion
homepage: https://github.com/evillollive/Analyze-Video-Skill
repository: https://github.com/evillollive/Analyze-Video-Skill
license: AGPL-3.0
user-invocable: true
---

# analyze-video

Self-contained pipeline that takes one or more video sources, downloads or resolves them locally, extracts frames, uses captions or Whisper for transcripts when available, tiles frames into contact sheets for cheap visual review, selects representative frames, and produces a polished Word document with timestamped analysis.

## Safety and privacy boundary

This skill runs `yt-dlp`, `ffmpeg`, and `ffprobe` locally. Source video files, frames, contact sheets, manifests, and the final `.docx` stay on the user's machine. Extracted audio is sent to Groq or OpenAI only when no native captions are available and a Whisper key is configured.

Do not try to bypass platform bot detection or access controls. If a site blocks unauthenticated downloads, the safe fallback is explicit user authorization: ask whether the user wants to use their own browser session via `--cookies-from-browser <browser>` or a cookies file via `--cookies <path>`. Do not spoof watch sessions, forge tokens, automate hidden playback to trick a site, or use unrelated hosting/services as an evasion layer.

## Token strategy

Do not read every frame. The pipeline emits per-chunk contact sheets and a lightweight manifest so you can preview the video at low cost:

1. Read `manifest_lite.json` first. It omits transcript text AND per-frame arrays but includes chunk metadata, timestamps, contact-sheet paths, `docx_image_dimensions`, `suggested_docx_name`, `transcript_path`, quick-mode flags, and the full manifest path. (`select_frames.py` pulls per-frame paths from the full manifest for you.)
2. Read contact sheets only when useful. For quick mode or very long videos, call `select_frames.py` directly and preview only the relevant chunks.
3. Read selected full-resolution frames in one parallel batch per video.
4. Read the full `manifest.json` only when transcript text is needed for direct quotes, section-writing, or transcript-boundary refinement.

For long videos, `process.py` auto-chunks unfocused videos over 12 minutes into about 10-minute chunks with overlap. If `manifest_lite.preview_cost_warning` is true and the user asked about a narrow moment, prefer re-running with `--start` and `--end` instead of reading every contact sheet.

## Step 0: Setup preflight

First, resolve the skill directory. Most runners set `CLAUDE_SKILL_DIR`, but some sandboxes don't. If it's unset, fall back to the directory this `SKILL.md` lives in:

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$(cd "$(dirname "$0")" 2>/dev/null && pwd)}"
# If you don't have $0 (e.g. pasting commands), locate it once:
#   SKILL_DIR=$(dirname "$(find / -name SKILL.md -path '*analyze-video*' 2>/dev/null | head -1)")
```

Use `"$SKILL_DIR/scripts/..."` consistently. Never hardcode `~/.cache/analyze-video/scripts/...`; runnable scripts live under `"$SKILL_DIR/scripts/"`.

Execution host contract:
- `setup.py` and `process.py` must run on the same host environment.
- If preflight/setup ran in a sandbox but processing will run host-side (for example via Desktop Commander), run preflight again on the host before processing:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --check
```

- Treat setup state as host-local. Do not assume a sandbox "ready" state applies to the host machine.

Run once per session:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --check
```

Exit-code contract: `0` means local dependencies are ready. Any non-zero exit means "not ready" (the script currently uses `2` for missing dependencies). Treat only `0` as ready; never assume a specific non-zero value. A Whisper API key is optional; without one, videos with native captions still get transcript analysis and captionless videos are processed frames-only.

If preflight exits non-zero, run:

```bash
python3 "${SKILL_DIR}/scripts/setup.py"
```

The installer:
- macOS with Homebrew: installs missing `ffmpeg`, `yt-dlp`, Node.js/npm dependencies, and the `docx` npm module.
- Linux: auto-installs the no-sudo dependencies (`yt-dlp` via `pipx`/`pip --user`, and the `docx` npm module into `~/.cache/analyze-video`). For packages that need root (`ffmpeg`, Node.js/npm), it prints exact install commands. If `yt-dlp` lands in a user-local bin that isn't on `PATH`, setup prints the exact `export PATH=...` line; run it before invoking the pipeline.
- Windows: prints exact install commands.
- Scaffolds `~/.config/analyze-video/.env` at mode 0600.
- Marks setup complete once required local dependencies are ready.

If the user wants transcript fallback for captionless videos, ask whether they have a Groq key (preferred) or OpenAI key, then write it with:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --set-key groq "<KEY>"
# or:
python3 "${SKILL_DIR}/scripts/setup.py" --set-key openai "<KEY>"
```

## Step 1: Parse the request

Extract:
- One or more video sources: HTTP(S) URLs or local video paths.
- Optional focus range, such as "2:30 to 3:15" or "the demo section at 1:30".
- Optional speed/quality intent. If the user asks for a quick scan, use `--quick`.

Infer focus ranges from the request and pass them with `--start` and `--end`. Do not ask about focus unless the request is ambiguous enough that processing the full video would likely waste time or tokens.

## Step 2: Ask the user only when needed

Ask once for the batch:

1. **Frames per video** if the user did not specify. This is a required question unless the user explicitly says "pick for me" or equivalent. Suggest:
   - Under 2 min: 6 to 8
   - 2 to 5 min: 8 to 12
   - 5 to 15 min: 12 to 20
   - Over 15 min: 16 to 25, or a focused range
   If the user explicitly delegates frame-count choice, default to 20 for videos over 15 minutes and state that assumption.
2. **Output format** only for 2+ videos:
   - One combined `.docx` (default)
   - Separate `.docx` per video

If a URL failed because the site requires login/bot verification and the user is authorized to view it, ask whether they want to retry with their own browser cookies. Do not ask for cookies proactively before a failure.

## Step 3: Process each video

Create one numbered output directory per video under the session outputs directory:

```bash
OUT_DIR="<absolute path to session outputs>"
VIDEO_DIR="$OUT_DIR/video_1"

python3 "${SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR"
```

Preferred guarded entrypoint (enforces preflight + frame intent + spec gates):

```bash
python3 "${SKILL_DIR}/scripts/run_guarded_pipeline.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR" \
  --frames 20
```

For focused processing:

```bash
python3 "${SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR" \
  --start 2:30 --end 3:15
```

For quick mode:

```bash
python3 "${SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR" \
  --quick
```

For user-authorized retry after a login/bot/access block:

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

If `--source` is a local file downloaded from a URL, also pass `--source-url "<original-url>"` so `process.py` can auto-recover the real title and captions transcript.

Tool-runtime constraint:
- If the execution shell has a short timeout budget (for example 45 seconds), do not run full-length processing there.
- For long videos, run `process.py` in a host-side shell tool that can complete long-running commands, then continue analysis from the produced manifests.

Process videos sequentially. Do not parallelize video processing; it can saturate network, CPU, disk, and token budget. (Within a single video, `process.py` already extracts its chunks in parallel — see below — so running one video at a time still uses the machine fully.) `process.py` prints the path to `manifest_lite.json` on stdout. Progress and warnings go to stderr.

Per-video outputs include:
- `manifest_lite.json`: lightweight default manifest, schema v3 minus transcript text and per-frame arrays.
- `manifest.json`: full schema v3 manifest with top-level `transcript_segments`.
- `transcript.txt`: human-readable transcript (`[mm:ss] text` per line), written whenever a transcript exists. Its path is also in `manifest_lite.transcript_path`.
- `report.md`: human-readable pipeline report.
- `status.json`: live stage marker (`downloading`, `extracting` chunk i of N, `complete`). Useful for checking progress mid-run.
- `manifest_partial.json`: a partial manifest written as chunks finish; present only while a run is in flight or after it was interrupted. Removed on success.
- `chunks/chunk_N/contact_sheet.jpg`: one contact sheet per processed chunk.
- `chunks/chunk_N/frames/<sig>/frame_NNNN.jpg`: full-resolution selected-frame candidates. The `<sig>` subfolder is keyed to the extraction settings; always use the `absolute_path` from the manifest rather than building this path yourself.
- `download/video.<ext>`: source video when downloaded with `--no-download-cache` or from a local file. By default a downloaded URL lives in the shared cache (see Resuming), not under the out-dir.
- `audio.mp3` or `audio_START_END.mp3`: only if Whisper was used.
- `status.json`: current pipeline stage, updated continuously (downloading, transcript_ready, extracting with `current_chunk`/`chunks_completed`, complete). Read it to see how far an interrupted run got.

### Parallel chunk extraction

When a video is auto-chunked, `process.py` extracts several chunks at once (frame extraction and contact-sheet tiling are independent per chunk). The default concurrency is auto-picked from the core count, capped at 4, and never exceeds the chunk count. Override it with `--jobs N`; `--jobs 1` forces the old sequential behavior (useful on a heavily loaded or memory-constrained host). Output is identical either way — chunks are always written to the manifest in chronological order.

### Resuming after a timeout
`process.py` is resumable. Re-running with the same `--source` and `--out-dir` reuses any chunk whose frames are still valid (matched by an extraction signature), so an interrupted long video continues instead of restarting from zero. Each distinct set of extraction settings writes into its own `frames/<sig>/` subfolder, so a re-run never has to delete a previous run's files (which some sandboxes forbid) and stale frames can't pollute the result. If a run is killed, check `status.json` to see where it stopped, then just re-run the same command. Pass `--force` to ignore cached output and re-download + re-extract everything.

Downloaded URLs are cached once per URL under `~/.cache/analyze-video/downloads/<url-hash>/` and reused across runs, so a focused `--start`/`--end` rerun (even in a different `--out-dir`) does not re-download the whole video. The full video is always fetched, so timestamps stay correct. Pass `--force` to refresh a cached download, or `--no-download-cache` to keep the source under the out-dir instead.

The download cache is self-managing: at the end of each run, `process.py` evicts entries older than 14 days and trims the cache back under a 5 GB total (least-recently-used first), never touching the file the current (or a concurrent) run is using. Tune the limits with `ANALYZE_VIDEO_CACHE_MAX_AGE_DAYS` and `ANALYZE_VIDEO_CACHE_MAX_GB` (set either to `0` to disable that limit). To wipe every cached download by hand, run `python3 "${SKILL_DIR}/scripts/setup.py" --clear-cache` (this leaves the `docx` module cache intact). `setup.py --json` reports the current cache size as `download_cache_bytes`.

### Trimming a trailing promo/outro

If a video ends with a repetitive promo or static "watch the full episode" card, `process.py` detects it and records a `trailing_promo` hint in the manifest (plus a note in `report.md`). It does not remove anything by default. To drop that block from frame extraction, re-run with `--trim-static-outro`, or target the real content with `--end`.

## Step 4: Read manifests and preview visuals

After each `process.py` run:

1. Read `manifest_lite.json`. It carries chunk metadata, contact-sheet paths, timestamps, and `transcript_slice` pointers, but NOT per-frame arrays or transcript text (kept out so the file stays well under the Read-tool size limit on long videos).
2. If `quick_mode` is true, skip contact-sheet preview unless the user asked for detailed visual analysis.
3. Otherwise, read each relevant chunk contact sheet from `manifest_lite.chunks[].contact_sheet.absolute_path`. For very long videos, read only the chunks matching the user focus or visibly useful time ranges.
4. Read `manifest.json` (the full manifest) when you need `transcript_segments` or per-frame paths.

Per-frame paths live in the full manifest at:

```text
manifest.json -> chunks[].frames[].absolute_path
```

`select_frames.py` reads them for you (it loads the full manifest automatically via the lite file's `manifest_path` pointer), so you rarely need to open `manifest.json` by hand just to pick frames.

Chunk schema field names (lite and full): `index`, `start_seconds`, `end_seconds`, `start_formatted`, `end_formatted`, `duration_seconds`, `frame_count`, `contact_sheet`, `transcript_slice`. (They are `index`/`start_formatted`/`end_formatted`, not `chunk_index`/`start_time_str`.)

Transcript text lives at:

```text
manifest.transcript_segments[]
```

Each chunk includes `transcript_slice` with `start_index`, `end_index`, and `segment_count` pointers into the top-level transcript list.

## Step 5: Select frames

Use the helper instead of re-deriving the frame-selection math:

```bash
python3 "${SKILL_DIR}/scripts/select_frames.py" "$VIDEO_DIR/manifest_lite.json" <N>
```

You can pass `manifest_lite.json` or `manifest.json`; the helper transparently loads the full manifest for the per-frame paths.

Always run `select_frames.py` in the current session before spec build. Do not reuse selected-frame paths from prior-session notes or summaries.

The output is a JSON list of selected frames with `chunk_index`, `frame_index`, `absolute_path`, and timestamps. Refine the picks after looking at contact sheets when needed:
- Shift toward visible scene transitions.
- Include opening and closing frames if visually distinct.
- Prefer frames that show concrete UI/text/action over near-duplicates.

Read selected full-resolution frames in one parallel Read batch per video. For batch processing, finish one video before reading frames for the next.

## Step 6: Write the analysis

Write time-based sections with descriptive headings, for example:
- "Opening setup (0:00 to 0:18)"
- "Live demo walkthrough (2:30 to 3:15)"

For each section:
- Describe what is visible: layout, people, on-screen text, expressions, UI, camera focus, motion, and visual transitions.
- Connect transcript evidence when available using timestamps from `manifest.transcript_segments`.
- Note what is significant or surprising.

Be concrete and observational. Avoid vague summaries such as "the presenter explains the feature" when the visual evidence supports a richer description.

For caption style, consult:

```bash
${SKILL_DIR}/templates/caption_guide.md
```

For combined multi-video docs, add an "Observations Across Videos" section covering shared structure, visual style, themes, and differences.

## Step 7: Build the docx

Do not write JavaScript at runtime. Build a JSON spec and pass it to the bundled builder:

```bash
python3 "${SKILL_DIR}/scripts/validate_spec_paths.py" --spec "$OUT_DIR/spec.json" &&
python3 "${SKILL_DIR}/scripts/lint_spec_quality.py" --spec "$OUT_DIR/spec.json" &&
node "${SKILL_DIR}/scripts/build-docx.js" --spec "$OUT_DIR/spec.json"
```

`validate_spec_paths.py` is mandatory. It verifies that all spec-referenced frame/contact-sheet/transcript paths are absolute and exist before doc build. `build-docx.js` now enforces the same checks and fails fast if stale or missing paths slip through.

Name the output document after the video and the word "analysis". For a single video, use the manifest's `suggested_docx_name` (already slug-safe and title-based, e.g. `how-to-bake-bread-analysis.docx`) and place it in the out-dir, so `out` is `"$OUT_DIR/<suggested_docx_name>"`. For a combined multi-video doc, build a similar name from the videos analyzed (for example the first video's title slug plus `-and-2-more`) and always end it with `-analysis.docx`.

Spec shape:

```json
{
  "out": "/absolute/path/<title-slug>-analysis.docx",
  "title": "Video Analysis",
  "subtitle": "Generated by /analyze-video",
  "frame_layout": "1up",
  "videos": [
    {
      "title": "Video title",
      "source": "https://youtu.be/abc123",
      "meta": "Uploader · Duration · Source URL",
      "image_dimensions": { "width": 480, "height": 270 },
      "frame_layout": "2up",
      "sections": [
        {
          "heading": "Opening (0:00 to 0:18)",
          "body": "Analysis prose.",
          "frame_layout": "2up",
          "frames": [
            {
              "path": "/absolute/path/frame_0001.jpg",
              "caption": "Concrete frame caption."
            }
          ]
        }
      ]
    }
  ],
  "observations": "Optional cross-video observations.",
  "appendix_contact_sheets": [
    {
      "path": "/absolute/path/chunks/chunk_1/contact_sheet.jpg",
      "heading": "Video title, chunk 1 (0:00 to 10:00)",
      "caption": "Chronological overview, 0:00 to 10:00.",
      "alt": "Grid of evenly spaced frames from the first ten minutes."
    }
  ],
  "appendix_transcript": [
    {
      "heading": "Video title",
      "path": "/absolute/path/transcript.txt"
    }
  ]
}
```

Always set each video's `source` to the original URL or local path the user gave (use `manifest_lite.source`, or `manifest_lite.url` for URLs). The builder renders it as a readable "Source:" line under the video title so the document records exactly what was analyzed. Do not put cache or download paths here.

`frame_layout` controls how section frames are arranged: `"1up"` (default) renders one full-width frame per row, while `"2up"` places frames side by side in a borderless two-column table (good for tighter, comparison-style layouts). Set it at the spec top level and optionally override it per video or per section. Captions and required alt text are preserved in both layouts.

Use `manifest_lite.docx_image_dimensions` as the per-video default. `build-docx.js` handles page sizing, image embedding, captions, and required alt text. Contact sheets in `appendix_contact_sheets` keep their own aspect ratio automatically (no `width`/`height` needed).

`appendix_transcript` adds a full-transcript appendix. Give each entry a `heading` and a `path` pointing at the video's `manifest_lite.transcript_path` (the `transcript.txt` the pipeline writes). The builder reads the file itself, so never paste the transcript text into the spec. Only include this when the user asked for the transcript in the document and a transcript exists (`transcript_segment_count > 0`).

**If `node` reports it can't find `docx` (EACCES / `Cannot find module 'docx'`):** the skill directory is read-only, so `npm install` there fails silently. The builder already tries `DOCX_NODE_MODULES`, `NODE_PATH`, `scripts/node_modules`, and finally installs into `~/.cache/analyze-video/node_modules`. To point it at an existing install instead, run:

```bash
NODE_PATH=/path/to/dir/containing/node_modules node "${SKILL_DIR}/scripts/build-docx.js" --spec "$OUT_DIR/spec.json"
```

Do **not** try to `npm install` into `${SKILL_DIR}/scripts`; it may be mounted read-only.

## Step 8: Validate and deliver

**Mandatory gate, do not skip:** appendices are OFF by default. You MUST ask the user the delivery question below and receive an explicit answer *before* you build the `.docx`. Never auto-add the contact-sheet appendix or the transcript appendix on your own initiative. If you build without asking, that's a defect. There is exactly one build, and it happens *after* these answers.

Ask once for the batch using `AskUserQuestion` (skip any option that doesn't apply, and only offer the transcript options when a transcript exists, i.e. `transcript_segment_count > 0`):

> "A few delivery options before I build the document:
> 1. Include the contact sheet(s) as a visual appendix *inside* the document?
> 2. Include the full transcript as an appendix *inside* the document?
> 3. Keep standalone copies of the contact sheet(s) and/or the transcript as separate files next to the document?
> 4. Also want a PDF version?
> 5. Clean up the remaining working files afterward?"

Default every appendix answer to "no" unless the user says yes. If the user gives no answer or declines, build with no appendices.

Then build with only the appendices the user explicitly approved:

- **Contact sheets in the document (only if approved):** add an `appendix_contact_sheets` entry (one per chunk, or one for a single-chunk video), pulling each `contact_sheet.absolute_path` from the manifest's chunks and captioning each with its chunk time range. The builder sizes sheets so about two fit per page; you don't set width/height.
- **Transcript in the document (only if approved):** add an `appendix_transcript` entry per video, with `path` set to that video's `manifest_lite.transcript_path`.

Build the docx once, after the answers, so only the requested appendices are included.

Run the builder and confirm the `.docx` exists. If path validation fails, rebuild the spec from the current `select_frames.py` output and re-run validation before build-docx. If a docx validator is available, run it; otherwise skip validation silently. Present the document with a `computer://` link.

If PDF requested:

```bash
libreoffice --headless --convert-to pdf "$OUT_DIR/<filename>.docx" --outdir "$OUT_DIR/"
```

**Keeping standalone files:** if the user wants to keep the contact sheet(s) and/or the transcript as separate files, copy them next to the final document *before* any cleanup, using clear, collision-safe names:

- Transcript: copy each video's `transcript_path` to `<out-dir>/<video-slug>-transcript.txt`.
- Contact sheets: copy each `contact_sheet.absolute_path` to `<out-dir>/contact_sheets/<video-slug>-chunk-N.jpg`.

Report the kept file paths to the user.

If cleanup requested, remove per-video working directories and any spec/build scratch files, but keep the `.docx`, the PDF, and any standalone files you just preserved above. Note that a downloaded URL's source video lives in the shared cache (`~/.cache/analyze-video/downloads/<url-hash>/`), not under the out-dir, so removing the out-dir won't delete it; that cache is intentionally reused across runs and is auto-pruned by age and size (see Resuming). Use `--no-download-cache` if you need the source kept inside the out-dir for self-contained cleanup, or `setup.py --clear-cache` to wipe the whole download cache now.

## Failure modes

- **Setup preflight failed**: run installer. Missing Whisper keys are optional; required local dependencies are not.
- **Setup/process host mismatch**: if setup was run in one environment (for example Linux sandbox) and processing will run in another (for example Mac host), re-run `python3 "${SKILL_DIR}/scripts/setup.py" --check` on the actual execution host before `process.py`.
- **Runner timeout on long videos**: if the current shell has a short timeout budget, run `process.py` in a host-side shell tool that supports long-running commands, then resume from generated manifests.
- **Download blocked by login, age gate, bot check, members-only, or private access**: explain the specific access issue. For public YouTube URLs, `download.py` already tries the android player client first (it bypasses YouTube's n-challenge without a JavaScript runtime and avoids the 403s the web client hits from server/cloud IPs), then falls back to the web client automatically. If access still fails and the user can view the video and authorizes it, retry with `--cookies-from-browser <browser>` or `--cookies <file>`. Otherwise ask for a local file. Note: `--cookies-from-browser` only works when yt-dlp runs on the SAME OS as the browser. In a Linux sandbox it cannot read a macOS/Windows browser's cookie store, so run yt-dlp host-side (e.g. via a Mac/Windows shell tool) for cookie-based access, then point `process.py` at the resulting local file.
- **Rate limited**: wait before retrying. User-authorized browser cookies may help if the content is accessible in their browser.
- **Geo restricted**: ask for a local file or another source the user can access from this environment.
- **No transcript**: proceed frames-only and note it in the docx. If the source is a YouTube URL but the video was processed from a separately downloaded local file (so the caption pass never ran), retrofit the transcript without re-downloading: `python3 "${SKILL_DIR}/scripts/process.py" --captions-only --source <url> --out-dir <video-dir>`. This fetches auto-subs (android client), writes `transcript.txt`, and patches any existing `manifest(_lite).json` transcript fields.
- **Whisper backend failed**: when both keys exist and `--whisper` was not pinned, `process.py` tries Groq then OpenAI. If both fail, proceed frames-only.
- **Whisper audio too large**: rerun with a focused `--start`/`--end` range or use a source with native captions.
- **Long-video preview warning**: prefer focused reruns or quick mode rather than reading every contact sheet.
- **yt-dlp "No supported JavaScript runtime" warning**: harmless for most sources (including any with native captions) and for public YouTube URLs (the android-first path needs no JS runtime). Some sites need JS-based extraction; if a download fails for that reason, install a JS runtime yt-dlp supports (e.g. Deno) or use a local file. This is a yt-dlp requirement, not a skill bug.
- **yt-dlp can't find Node / n-challenge fails**: prefer a pipx/pip-installed `yt-dlp` over a frozen standalone binary. The standalone binary cannot reliably locate a system Node.js for subprocess-based extraction even with `PATH` exported, whereas the pip-installed version uses the system interpreter. `setup.py` installs the pip version.
- **yt-dlp installed but not on PATH (Linux)**: apply the exact `export PATH=...` line from setup in the same shell invocation that runs `process.py`, or run by absolute tool path.
- **Spec has stale/missing frame paths**: re-run `select_frames.py` in the current session and re-generate `spec.json`; do not reuse prior-session frame paths.

## Security notes

The skill does not upload source video, persist cookies, post to platform accounts, or access platform accounts by default. Cookie-based retries must be initiated only after user consent and should use the user's own authorized browser/session.

Bundled runtime: `scripts/process.py`, `download.py`, `frames.py`, `transcribe.py`, `whisper.py`, `setup.py`, `select_frames.py`, `validate_spec_paths.py`, `lint_spec_quality.py`, `run_guarded_pipeline.py`, and `build-docx.js`.
