---
name: analyze-video
description: Use when the user wants to analyze one or more videos (URLs or local files) and produce a Word document with embedded frames and a written timestamp-based analysis. Triggers on "analyze this video", "make a report from this video", "write up this YouTube link", "document what's in these videos", "analyze these clips", "video analysis", or any request that includes video URLs or local video paths and asks for a written deliverable.
allowed-tools: Bash, Read, Write, AskUserQuestion
homepage: https://github.com/evillollive/Analyze-Video-Skill
repository: https://github.com/evillollive/Analyze-Video-Skill
license: MIT
user-invocable: true
---

# analyze-video

Self-contained pipeline that takes one or more video sources, downloads them, extracts frames, transcribes them (captions or Whisper API), tiles all frames into a contact sheet for cheap visual scanning, and produces a polished Word document with selected frames embedded and a timestamp-based written analysis.

## Token strategy (why the contact sheet matters)

Reading every extracted frame burns 50–80k image tokens per video. Instead:

1. The script tiles each chunk's frames into a `contact_sheet.jpg`.
2. You Read the contact sheet(s) once (~5–10k tokens each) to see the whole video at a glance.
3. You decide which N frames matter, and Read only those at full resolution.

The pipeline also writes a `manifest_lite.json` (no transcript text) for default reads and a full `manifest.json` (with the full transcript) you only Read when you need raw quotes.

## Auto-chunking for long videos

Videos longer than 12 minutes are auto-split into 10-minute chunks (5-second overlap so transitions don't fall in the gap). Each chunk gets its own contact sheet and frame set.

For very long videos (5+ chunks), `manifest_lite.preview_cost_warning` is true. Tell the user the chunk count and offer focus mode (`--start HH:MM:SS --end HH:MM:SS`) before reading every contact sheet.

## Step 0: Setup preflight

Run on the first invocation each session:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check
```

Silent on success (exit 0). On non-zero, run the installer:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py"
```

The installer:
- Auto-installs `ffmpeg`, `yt-dlp`, `node` via Homebrew on macOS (prints commands on Linux/Windows).
- Installs the npm `docx` package once into `${CLAUDE_SKILL_DIR}/scripts/node_modules/` so the docx step never has to scaffold per session.
- Scaffolds `~/.config/analyze-video/.env` at mode 0600.

If a Whisper API key is still missing, use `AskUserQuestion` to ask the user whether they have a Groq key (preferred: cheaper, faster) or an OpenAI key, then save it via:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --set-key groq <KEY>
```

If they don't want Whisper, pass `--no-whisper` to `process.py` and proceed frames-only.

After Step 0 returns 0 once in a session, skip it on subsequent invocations.

## Step 1: Parse the request

Extract from the user's message:
- A list of video sources (URLs or local file paths). One or more.
- Optional question or focus area ("analyze the demo at 2:30 to 3:15").
- "Quick" intent ("just a few screenshots", "TL;DR with some frames"). Quick mode is `--quick` to `process.py`: lower frame budget, skip the contact-sheet preview step (select frames directly from the manifest).

If section focus is implied, plan to pass `--start` / `--end` to `process.py` for that video.

## Step 2: Ask the user (only when needed)

Use the heuristic defaults; only call `AskUserQuestion` when the user's intent is genuinely ambiguous.

**Frames per video.** Default by longest video duration — only ask if the user mentioned wanting "a lot" or "just a few":
- Under 2 min: 6
- 2–5 min: 10
- 5–15 min: 15
- Over 15 min: 20

**Output format.** Only ask if there are 2 or more videos:
- One combined .docx (default)
- Separate .docx per video

Don't ask about section focus. Infer it from the user's request.

## Step 3: Process each video

Determine the session outputs directory from your environment. Create one numbered subdirectory per video:

```bash
OUT_DIR="<absolute path to session outputs>"
VIDEO_DIR="$OUT_DIR/video_1"

python3 "${CLAUDE_SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR"
```

For section-focused processing add `--start` / `--end`. For quick mode add `--quick`.

Per-video outputs in `$VIDEO_DIR`:
- `manifest_lite.json` — slim pipeline summary (no transcript text). **Read this by default.**
- `manifest.json` — full output including `transcript_segments[]`. Read only when you need raw quotes.
- `report.md` — human-readable summary
- `chunks/chunk_N/contact_sheet.jpg` — tiled overview per chunk
- `chunks/chunk_N/frames/frame_NNNN.jpg` — individual frames per chunk
- `download/video.<ext>` — source video
- `audio.mp3` — only if Whisper was used (reused on re-runs)

Stdout from `process.py` is the path to `manifest_lite.json`. Progress goes to stderr.

Top-level fields you care about in the lite manifest:
- `chunked`, `chunk_count`, `chunks[]` (each with `start_seconds`, `end_seconds`, `frames[]`, `contact_sheet`)
- `docx_image_dimensions: {width, height}` — pre-computed for the docx step. **Use as-is.**
- `aspect_ratio` — if you need it for prose
- `preview_cost_warning` — true when 5+ chunks; warn user and offer focus mode
- `quick_mode` — true when the user opted in; skip the contact-sheet preview step
- `transcript_source`, `transcript_segment_count` — `transcript_segments[]` lives only in the full `manifest.json`
- `chunks[i].transcript_slice: {start_index, end_index, segment_count}` — index pointers into `manifest.transcript_segments` if you load it

For batch mode: process videos sequentially, one at a time. After each video, Read its `manifest_lite.json` and contact sheets, then move on. **Per-video failure handling:** if `process.py` exits non-zero (download blocked, file not found, etc.), log the failure to the user, record the source + error, and continue with the remaining videos. At the end of the docx, add a "Failed videos" section listing each source and the reason. Don't retry within the batch.

To disable chunking explicitly, pass `--no-chunking`.

## Step 4: Plan the analysis from each contact sheet

For each video:

1. If `manifest_lite.preview_cost_warning` is true (5+ chunks), tell the user the chunk count and offer focus mode first.
2. Skip this whole step in `quick_mode`. Otherwise Read each `chunks[i].contact_sheet.absolute_path`.
3. If you need transcript context, Read `manifest.json` once and slice via `chunks[i].transcript_slice`.
4. Identify distinct visual sections (intro, demo, outro, scene changes).

**Pick which N frames go in the document.** Don't compute this by hand — call:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/select_frames.py" "$VIDEO_DIR/manifest_lite.json" <N>
```

It returns a JSON list of `{chunk_index, frame_index, absolute_path, timestamp_seconds, timestamp_formatted}` distributed proportionally across chunks (with at least 1 per chunk). Refine afterward only if needed: shift a pick by 1–2 positions toward a transcript boundary or a visible transition. Always include the opening and the closing.

Each contact sheet tile is row-major chronological with 8 columns. Tile (row R, col C) corresponds to that chunk's frame index `(R * 8) + C + 1`.

## Step 5: Read selected full-res frames

For each selected frame, Read its `absolute_path`. Do all Reads for one video in a single parallel batch. In batch mode, only read frames for the video you're currently writing about.

## Step 6: Write the analysis

For each video, write the analysis as time-based sections with descriptive labels (e.g., "Opening (0:00–0:15)" or "Live Demo (2:30–3:15)"). Within each section:

- Describe what is visually on screen: layout, color, on-screen text, people, expressions, UI elements, camera focus.
- Describe what is being said at that moment (cite transcript timestamps).
- Note what is significant or surprising.

Be concrete and observational. Avoid "the presenter explains X" in favor of "Lee, in a black GitHub hoodie, gestures at a Copilot conversation projected behind him."

For multi-video runs, finish with an "Observations Across Videos" section noting shared format, visual style, themes, and structure.

For caption style, see `${CLAUDE_SKILL_DIR}/templates/caption_guide.md` (read it once before writing the first caption).

## Step 7: Build the docx

The docx builder consumes a JSON spec — no JS to write at runtime, no per-session `npm install`.

1. Build the spec in memory and write it to `$OUT_DIR/docx_spec.json`. Schema:

   ```json
   {
     "out": "/abs/path/output.docx",
     "title": "Visual Analysis: <title>",
     "subtitle": "Generated by /analyze-video",
     "image_dimensions": { "width": 480, "height": 270 },
     "videos": [
       {
         "title": "Video Title",
         "meta": "Uploader · Duration · URL",
         "image_dimensions": { ... },          // optional, overrides global
         "sections": [
           {
             "heading": "Opening (0:00–0:15)",
             "body": "Multi-paragraph prose. Blank lines split paragraphs.",
             "frames": [
               { "path": "/abs/.../frame_0001.jpg", "caption": "..." }
             ]
           }
         ]
       }
     ],
     "observations": "Cross-video observations (batch only)."
   }
   ```

   Use each video's `manifest_lite.docx_image_dimensions` as the per-video `image_dimensions`. Don't compute pixel dimensions yourself.

2. Run the builder:

   ```bash
   node "${CLAUDE_SKILL_DIR}/scripts/build-docx.js" --spec "$OUT_DIR/docx_spec.json"
   ```

   It uses the `docx` package installed at setup time. If it errors with `Cannot find module 'docx'`, run `python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --install-docx` and retry.

The full schema (including all rendering rules and the image/altText requirements) lives in the header of `scripts/build-docx.js`.

## Step 8: Validate and deliver

If a docx validator is available, run it:

```bash
python /path/to/docx-skill/scripts/validate.py "$OUT_DIR/<filename>.docx"
```

If unavailable, skip silently. Present the docx via a `computer://` link.

## Step 9: Soft checkpoint (PDF + cleanup)

Ask once at the end:

> "Want a PDF version too, and should I clean up the working files (frames, audio, source video) but keep the docx?"

If PDF requested:

```bash
libreoffice --headless --convert-to pdf "$OUT_DIR/<filename>.docx" --outdir "$OUT_DIR/"
```

If cleanup requested: remove `$OUT_DIR/video_*` and `$OUT_DIR/docx_spec.json`. Keep the .docx (and PDF).

## Failure modes

- **Setup preflight failed** — run installer; ask user for Whisper key and `setup.py --set-key …`; or pass `--no-whisper`.
- **Download fails** (yt-dlp error) — tell the user plainly. Login-required and region-locked videos won't work. In batch mode, log and continue.
- **Whisper backend fails** — `process.py` automatically falls back from Groq to OpenAI when both keys exist (no user action needed). On total failure, the pipeline proceeds frames-only and notes it.
- **No transcript** — proceed frames-only; note this in the docx.
- **Preview cost warning** (5+ chunks) — tell the user the chunk count and offer focus mode (`--start`/`--end`).
- **`Cannot find module 'docx'`** at build time — run `setup.py --install-docx`.

## Token efficiency notes

- Lite manifest read: ~3–5k tokens per video (vs ~30k for full manifest on long videos).
- Contact sheet for one chunk: ~5–10k image tokens.
- Selected frame at 512px: ~600–1000 tokens each.
- Typical per-video budget: 15–25k image tokens.

If the user asks a follow-up about a video already processed in this session, you have its manifest and frames in context. Don't re-run.

## Security & permissions

This skill:
- Runs `yt-dlp` locally to download videos and pull native captions.
- Runs `ffmpeg`/`ffprobe` locally to extract frames, audio, and the contact sheet.
- Sends extracted audio (not the video) to Groq or OpenAI Whisper API only when no captions are available and Whisper is enabled.
- Writes everything under the session outputs folder you provide via `--out-dir`.
- Reads/writes `~/.config/analyze-video/.env` at mode 0600 for the Whisper key and `SETUP_COMPLETE` marker.

It does NOT:
- Upload the source video to any API. Only extracted audio leaves the machine.
- Access any platform account (no login, no cookies, no posting).
- Persist anything outside the session outputs folder and `~/.config/analyze-video/`.

**Bundled scripts** in `scripts/`: `process.py` (entry point), `select_frames.py` (frame picker), `download.py`, `frames.py`, `transcribe.py`, `whisper.py`, `setup.py`, `build-docx.js`. Sidecar in `templates/caption_guide.md`. Review before first use.
