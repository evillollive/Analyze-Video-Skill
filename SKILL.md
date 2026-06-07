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

1. Read `manifest_lite.json` first. It omits transcript text but includes chunk/frame paths, timestamps, contact-sheet paths, `docx_image_dimensions`, quick-mode flags, and the full manifest path.
2. Read contact sheets only when useful. For quick mode or very long videos, call `select_frames.py` directly and preview only the relevant chunks.
3. Read selected full-resolution frames in one parallel batch per video.
4. Read the full `manifest.json` only when transcript text is needed for direct quotes, section-writing, or transcript-boundary refinement.

For long videos, `process.py` auto-chunks unfocused videos over 12 minutes into about 10-minute chunks with overlap. If `manifest_lite.preview_cost_warning` is true and the user asked about a narrow moment, prefer re-running with `--start` and `--end` instead of reading every contact sheet.

## Step 0: Setup preflight

Run once per session:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check
```

Exit 0 means local dependencies are ready. A Whisper API key is optional; without one, videos with native captions still get transcript analysis and captionless videos are processed frames-only.

If preflight exits non-zero, run:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py"
```

The installer:
- macOS with Homebrew: installs missing `ffmpeg`, `yt-dlp`, Node.js/npm dependencies, and the `docx` npm module.
- Linux/Windows: prints exact install commands.
- Scaffolds `~/.config/analyze-video/.env` at mode 0600.
- Marks setup complete once required local dependencies are ready.

If the user wants transcript fallback for captionless videos, ask whether they have a Groq key (preferred) or OpenAI key, then write it with:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --set-key groq "<KEY>"
# or:
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --set-key openai "<KEY>"
```

## Step 1: Parse the request

Extract:
- One or more video sources: HTTP(S) URLs or local video paths.
- Optional focus range, such as "2:30 to 3:15" or "the demo section at 1:30".
- Optional speed/quality intent. If the user asks for a quick scan, use `--quick`.

Infer focus ranges from the request and pass them with `--start` and `--end`. Do not ask about focus unless the request is ambiguous enough that processing the full video would likely waste time or tokens.

## Step 2: Ask the user only when needed

Ask once for the batch:

1. **Frames per video** if the user did not specify. Suggest:
   - Under 2 min: 6 to 8
   - 2 to 5 min: 8 to 12
   - 5 to 15 min: 12 to 20
   - Over 15 min: 16 to 25, or a focused range
2. **Output format** only for 2+ videos:
   - One combined `.docx` (default)
   - Separate `.docx` per video

If a URL failed because the site requires login/bot verification and the user is authorized to view it, ask whether they want to retry with their own browser cookies. Do not ask for cookies proactively before a failure.

## Step 3: Process each video

Create one numbered output directory per video under the session outputs directory:

```bash
OUT_DIR="<absolute path to session outputs>"
VIDEO_DIR="$OUT_DIR/video_1"

python3 "${CLAUDE_SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR"
```

For focused processing:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR" \
  --start 2:30 --end 3:15
```

For quick mode:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR" \
  --quick
```

For user-authorized retry after a login/bot/access block:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/process.py" \
  --source "<url>" \
  --out-dir "$VIDEO_DIR" \
  --cookies-from-browser safari
```

or:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/process.py" \
  --source "<url>" \
  --out-dir "$VIDEO_DIR" \
  --cookies "/path/to/cookies.txt"
```

Process videos sequentially. Do not parallelize video processing; it can saturate network, CPU, disk, and token budget. `process.py` prints the path to `manifest_lite.json` on stdout. Progress and warnings go to stderr.

Per-video outputs include:
- `manifest_lite.json`: lightweight default manifest, schema v3 minus transcript text.
- `manifest.json`: full schema v3 manifest with top-level `transcript_segments`.
- `report.md`: human-readable pipeline report.
- `status.json`: live stage marker (`downloading`, `extracting` chunk i of N, `complete`). Useful for checking progress mid-run.
- `manifest_partial.json`: a partial manifest written as chunks finish; present only while a run is in flight or after it was interrupted. Removed on success.
- `chunks/chunk_N/contact_sheet.jpg`: one contact sheet per processed chunk.
- `chunks/chunk_N/frames/frame_NNNN.jpg`: full-resolution selected-frame candidates.
- `download/video.<ext>`: source video, when downloaded.
- `audio.mp3` or `audio_START_END.mp3`: only if Whisper was used.

### Resuming after a timeout

`process.py` is resumable. Re-running with the same `--source` and `--out-dir` reuses the already-downloaded video and any chunk whose frames are still valid (matched by an extraction signature), so an interrupted long video continues instead of restarting from zero. If a run is killed, check `status.json` to see where it stopped, then just re-run the same command. Pass `--force` to ignore cached output and re-download + re-extract everything.

### Trimming a trailing promo/outro

If a video ends with a repetitive promo or static "watch the full episode" card, `process.py` detects it and records a `trailing_promo` hint in the manifest (plus a note in `report.md`). It does not remove anything by default. To drop that block from frame extraction, re-run with `--trim-static-outro`, or target the real content with `--end`.

## Step 4: Read manifests and preview visuals

After each `process.py` run:

1. Read `manifest_lite.json`.
2. If `quick_mode` is true, skip contact-sheet preview unless the user asked for detailed visual analysis.
3. Otherwise, read each relevant chunk contact sheet from `manifest_lite.chunks[].contact_sheet.absolute_path`. For very long videos, read only the chunks matching the user focus or visibly useful time ranges.
4. Read `manifest.json` only when you need `transcript_segments`.

Chunk frame paths live at:

```text
manifest_lite.chunks[].frames[].absolute_path
```

Transcript text lives at:

```text
manifest.transcript_segments[]
```

Each chunk includes `transcript_slice` with `start_index`, `end_index`, and `segment_count` pointers into the top-level transcript list.

## Step 5: Select frames

Use the helper instead of re-deriving the frame-selection math:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/select_frames.py" "$VIDEO_DIR/manifest_lite.json" <N>
```

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
${CLAUDE_SKILL_DIR}/templates/caption_guide.md
```

For combined multi-video docs, add an "Observations Across Videos" section covering shared structure, visual style, themes, and differences.

## Step 7: Build the docx

Do not write JavaScript at runtime. Build a JSON spec and pass it to the bundled builder:

```bash
node "${CLAUDE_SKILL_DIR}/scripts/build-docx.js" --spec "$OUT_DIR/spec.json"
```

Spec shape:

```json
{
  "out": "/absolute/path/output.docx",
  "title": "Video Analysis",
  "subtitle": "Generated by /analyze-video",
  "videos": [
    {
      "title": "Video title",
      "meta": "Uploader · Duration · Source URL",
      "image_dimensions": { "width": 480, "height": 270 },
      "sections": [
        {
          "heading": "Opening (0:00 to 0:18)",
          "body": "Analysis prose.",
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
      "heading": "Video title — chunk 1 (0:00 to 10:00)",
      "caption": "Chronological overview, 0:00 to 10:00.",
      "alt": "Grid of evenly spaced frames from the first ten minutes."
    }
  ]
}
```

Use `manifest_lite.docx_image_dimensions` as the per-video default. `build-docx.js` handles page sizing, image embedding, captions, and required alt text. Contact sheets in `appendix_contact_sheets` keep their own aspect ratio automatically (no `width`/`height` needed).

**If `node` reports it can't find `docx` (EACCES / `Cannot find module 'docx'`):** the skill directory is read-only, so `npm install` there fails silently. The builder already tries `DOCX_NODE_MODULES`, `NODE_PATH`, `scripts/node_modules`, and finally installs into `~/.cache/analyze-video/node_modules`. To point it at an existing install instead, run:

```bash
NODE_PATH=/path/to/dir/containing/node_modules node "${CLAUDE_SKILL_DIR}/scripts/build-docx.js" --spec "$OUT_DIR/spec.json"
```

Do **not** try to `npm install` into `${CLAUDE_SKILL_DIR}/scripts`; it may be mounted read-only.

## Step 8: Validate and deliver

Run the builder and confirm the `.docx` exists. If a docx validator is available, run it; otherwise skip validation silently. Present the document with a `computer://` link.

At the end, ask once:

> "Want a PDF version too, should I append the contact sheet(s) as a visual appendix, and should I clean up the working files (frames, audio, source video) but keep the docx?"

If the contact-sheet appendix is requested, add an `appendix_contact_sheets` entry (one per chunk, or one for a single-chunk video) before building the docx. Pull each `contact_sheet.absolute_path` from the manifest's chunks, and caption each with its chunk time range. Skip this by default unless the user asks.

If PDF requested:

```bash
libreoffice --headless --convert-to pdf "$OUT_DIR/<filename>.docx" --outdir "$OUT_DIR/"
```

If cleanup requested, remove per-video working directories and any spec/build scratch files, but keep the `.docx` and PDF.

## Failure modes

- **Setup preflight failed**: run installer. Missing Whisper keys are optional; required local dependencies are not.
- **Download blocked by login, age gate, bot check, members-only, or private access**: explain the specific access issue. If the user can view the video and authorizes it, retry with `--cookies-from-browser <browser>` or `--cookies <file>`. Otherwise ask for a local file.
- **Rate limited**: wait before retrying. User-authorized browser cookies may help if the content is accessible in their browser.
- **Geo restricted**: ask for a local file or another source the user can access from this environment.
- **No transcript**: proceed frames-only and note it in the docx.
- **Whisper backend failed**: when both keys exist and `--whisper` was not pinned, `process.py` tries Groq then OpenAI. If both fail, proceed frames-only.
- **Whisper audio too large**: rerun with a focused `--start`/`--end` range or use a source with native captions.
- **Long-video preview warning**: prefer focused reruns or quick mode rather than reading every contact sheet.

## Security notes

The skill does not upload source video, persist cookies, post to platform accounts, or access platform accounts by default. Cookie-based retries must be initiated only after user consent and should use the user's own authorized browser/session.

Bundled runtime: `scripts/process.py`, `download.py`, `frames.py`, `transcribe.py`, `whisper.py`, `setup.py`, `select_frames.py`, and `build-docx.js`.
