---
name: analyze-video
description: Use when the user wants to analyze one or more videos (URLs or local files) and produce a Word document with embedded frames and a written timestamp-based analysis. Triggers on "analyze this video", "make a report from this video", "write up this YouTube link", "document what's in these videos", "analyze these clips", "video analysis", or any request that includes video URLs or local video paths and asks for a written deliverable.
allowed-tools: Bash, Read, Write, AskUserQuestion
homepage: https://github.com/evillollive/Analyze-Video-Skill
repository: https://github.com/evillollive/Analyze-Video-Skill
license: MIT
user-invocable: true
---

# analyze-video (v2)

Self-contained pipeline that takes one or more video sources, downloads them, extracts frames, transcribes them (captions or Whisper API), tiles all frames into a contact sheet for cheap visual scanning, and produces a polished Word document with selected frames embedded and a timestamp-based written analysis.

This is a v2 merge of the earlier `/watch` and `/analyze-video` skills into one workflow. There is no separate `/watch` step.

## Token strategy (why the contact sheet matters)

Reading every extracted frame burns 50 to 80k image tokens per video. Instead:

1. The script tiles all frames into a `contact_sheet.jpg` per chunk (8 columns wide, row-major, chronological).
2. You Read the contact sheet(s) once (~5 to 10k tokens each) to see the whole video at a glance.
3. You decide which N frames matter for the document, and Read only those at full resolution.

This typically lands at 20 to 30k tokens per chunk instead of 50 to 80k.

## Auto-chunking for long videos

Videos longer than 12 minutes are automatically split into 10-minute chunks (5-second overlap so transitions don't fall in the gap). Each chunk gets its own contact sheet and frame set, giving roughly one frame every 7 seconds within a chunk instead of one frame every 36+ seconds across an unchunked hour-long video.

Chunking is bypassed when the user passes `--start`/`--end` (focus mode is its own targeted scan).

For very long videos (5+ chunks), the manifest sets `preview_cost_warning: true`. The Read-every-contact-sheet preview cost grows linearly with chunk count, so a 90-minute video has ~9 chunks and ~70k tokens of preview before any frame is selected. When this warning fires, briefly tell the user the cost and offer focus mode (`--start HH:MM:SS --end HH:MM:SS`) as a cheaper alternative.

## Step 0: Setup preflight

Run on the first invocation each session:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py" --check
```

Silent on success (exit 0). On non-zero, run the installer:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/setup.py"
```

Installer behavior:
- macOS with Homebrew: auto-installs `ffmpeg` and `yt-dlp`
- Linux/Windows: prints exact install commands for you to relay to the user
- Scaffolds `~/.config/analyze-video/.env` at mode 0600
- Writes `SETUP_COMPLETE=true` once deps + a key are in place

If a Whisper API key is still missing after install, use `AskUserQuestion` to ask the user whether they have a Groq key (preferred: cheaper, faster) or an OpenAI key. Write it to `~/.config/analyze-video/.env`. If they don't want Whisper, you can pass `--no-whisper` to `process.py` and proceed frames-only.

After Step 0 returns 0 once in a session, skip it on subsequent invocations.

## Step 1: Parse the request

Extract from the user's message:
- A list of video sources (URLs or local file paths). One or more.
- Optional question or focus area ("analyze the demo at 2:30 to 3:15", "what's distinctive about the visuals?").

Examples:
- "Analyze https://youtu.be/abc and write me a report" -> 1 source, no focus
- "Make a docx from these three videos: A, B, C" -> 3 sources, no focus
- "Document what happens at 1:30 in this clip: D" -> 1 source, focus = 1:30 onward

If section focus is implied, plan to pass `--start` and `--end` to `process.py` for that video.

## Step 2: Ask the user (once, for the whole batch)

Use `AskUserQuestion` to gather:

1. **Frames per video** to include in the document. Suggest based on the longest video's duration:
   - Under 2 min: 6 to 8
   - 2 to 5 min: 8 to 12
   - 5 to 15 min: 12 to 20
   - Over 15 min: 16 to 25 (and consider asking if they want a section focus)

2. **Output format** (only ask if there are 2 or more videos):
   - One combined .docx (default)
   - Separate .docx per video

Don't ask about section focus. Infer it from the user's request.

## Step 3: Process each video

Determine the session outputs directory from your environment (the path under `local-agent-mode-sessions/.../outputs`). Create one numbered subdirectory per video:

```bash
OUT_DIR="<absolute path to session outputs>"
VIDEO_DIR="$OUT_DIR/video_1"

python3 "${CLAUDE_SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR"
```

For section-focused processing, add `--start` and/or `--end`:

```bash
python3 "${CLAUDE_SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$OUT_DIR/video_1" \
  --start 2:30 --end 3:15
```

Per-video outputs (in `$VIDEO_DIR`):
- `manifest.json`: structured pipeline output, schema_version 2
- `report.md`: human-readable summary
- `chunks/chunk_N/contact_sheet.jpg`: tiled overview per chunk (always at least one chunk)
- `chunks/chunk_N/frames/frame_NNNN.jpg`: individual frames per chunk
- `download/video.<ext>`: source video
- `audio.mp3`: only if Whisper was used

The script's stdout is just the manifest path. All progress and warnings go to stderr.

The manifest schema (v2) always has a `chunks: []` array, even for single-chunk videos. Iterate it uniformly. Top-level fields you care about:
- `chunked`: boolean, true when auto-chunking activated
- `chunk_count`: integer
- `chunks[]`: each entry has `start_seconds`, `end_seconds`, `frames[]`, `contact_sheet`, `transcript`
- `preview_cost_warning`: true when 5+ chunks (warn the user and offer focus mode)
- `transcript`: full-video transcript at the top level for convenience (each chunk has its own filtered slice)

For batch mode: process videos sequentially, one at a time. Don't parallelize. Don't accumulate full frame dumps in context between videos. After each video completes, Read its `manifest.json` and the contact sheet(s), then move on.

To disable chunking explicitly, pass `--no-chunking`. Rarely needed.

## Step 4: Plan the analysis from each contact sheet

For each video:

1. If `manifest.preview_cost_warning` is true (5+ chunks), tell the user the chunk count and approximate preview cost first, and offer focus mode. If they want full coverage anyway, proceed.
2. Read each `chunks[i].contact_sheet.absolute_path` (one Read per chunk). Each contact sheet covers up to ~10 minutes of video at 8 cols × N rows, row-major chronological.
3. Cross-reference each contact sheet against `chunks[i].transcript.segments` (or the top-level full transcript).
4. Identify distinct visual sections (intro, demo, outro, scene changes) across the whole video.
5. Pick which N frames go in the document (where N is the user's answer from Step 2).

**Frame selection across chunks:**

For unchunked videos (chunk_count == 1), select N frames from the single chunk:

```
total = len(manifest.chunks[0].frames)
N = user's chosen frame count
step = total / N
selected_indices = [max(1, round(step * i + step/2)) for i in range(N)]
```

For chunked videos, distribute N frames across chunks proportionally to chunk duration:

```
for each chunk:
    chunk_share = round(N * chunk.duration_seconds / video.duration_seconds)
    select chunk_share frames from chunk.frames using the same step formula
```

Make sure every chunk gets at least 1 frame (rounding can otherwise zero out short final chunks).

**Refine based on content:** if a selected frame falls in the middle of a transcript segment with no visual change, shift it 1 to 2 positions toward the nearest transcript boundary or visible transition. Always include something from the opening and closing if distinct.

Each contact sheet tile is in row-major chronological order with 8 columns. Within a chunk, tile (row R, col C) corresponds to that chunk's frame index `(R * 8) + C + 1`. Use this to locate visual transitions visually.

## Step 5: Read selected full-res frames

For each selected frame, read its `absolute_path` from `chunks[i].frames[j].absolute_path`. Do all Reads for one video in a single parallel batch. For batch mode, only read frames for the video you're currently writing about, not all videos at once.

## Step 6: Write the analysis

For each video, write the analysis as time-based sections with descriptive labels (e.g., "Opening (0:00 to 0:15)" or "Live Demo (2:30 to 3:15)"). Within each section:

- Describe what is visually on screen: layout, color, on-screen text, people, expressions, UI elements, camera focus.
- Describe what is being said at that moment (cite transcript timestamps).
- Note what is significant or surprising.

Be concrete and observational. Avoid "the presenter explains X" in favor of "Lee, in a black GitHub hoodie, gestures at a Copilot conversation projected behind him."

For multiple videos in a combined doc, finish with an "Observations Across Videos" section noting shared format, visual style, themes, and structure.

## Step 7: Build the docx

Set up the docx builder once per session:

```bash
mkdir -p "$OUT_DIR/docx_build"
cd "$OUT_DIR/docx_build"
npm init -y >/dev/null 2>&1
npm install docx 2>&1 | tail -3
```

Write `build.js` to `$OUT_DIR/docx_build/build.js` based on the template at `${CLAUDE_SKILL_DIR}/templates/build.js.template`. Replace placeholders:
- `__OUT_DIR__` -> the absolute outputs path
- `__DOC_TITLE__` -> e.g., "Visual Analysis: <video title>" or for batch "GitHub Films Visual Analysis"
- `__FILENAME__` -> e.g., `gh_dungeons_analysis.docx` (single) or `github_films_analysis.docx` (batch)

Push paragraphs into `children` using the helpers (`title`, `meta`, `h1`, `h2`, `body`, `imgPara`, `cap`). For each video: `h1`, `meta`, then per-section `h2` + `body` + `imgPara` + `cap`.

**Image dimensions: pick from `manifest.aspect_ratio`:**
- "16:9" -> `{ width: 480, height: 270 }`
- "4:3"  -> `{ width: 480, height: 360 }`
- "9:16" -> `{ width: 240, height: 427 }`
- "1:1"  -> `{ width: 360, height: 360 }`
- Otherwise: compute height from width=480 and `manifest.width`/`manifest.height`.

**Critical docx rules (don't remove from build.js):**
- `type: 'jpg'` is required on every `ImageRun`.
- `altText` with all three fields (`title`, `description`, `name`) is required.
- Never use `\n` inside a `TextRun`. Use separate `Paragraph` elements.
- Page size 12240 x 15840 DXA (US Letter), 1440 DXA margins (1 inch).

Run the build:

```bash
node "$OUT_DIR/docx_build/build.js"
```

## Step 8: Validate and deliver

If a docx validator is available, run it:

```bash
python /path/to/docx-skill/scripts/validate.py "$OUT_DIR/<filename>.docx"
```

If unavailable, skip silently. Present the docx via a `computer://` link to the user.

## Step 9: Soft checkpoint (PDF + cleanup)

Ask once at the end:

> "Want a PDF version too, and should I clean up the working files (frames, audio, source video) but keep the docx?"

If PDF requested:

```bash
libreoffice --headless --convert-to pdf "$OUT_DIR/<filename>.docx" --outdir "$OUT_DIR/"
```

If cleanup requested: remove `$OUT_DIR/video_*` and `$OUT_DIR/docx_build`. Keep the .docx (and PDF if generated).

## Frame-to-caption guide

A good caption is concrete and adds something the image alone doesn't show. Two patterns:

**Describe + contextualize:**
"The dungeon game running live in a terminal. Status bar reads 'HP: 13/20 | Level: 2/5 | Kills: 4.' The dungeon layout is seeded by the repository's commit SHA."

**Describe + note significance:**
"A Merge Conflict appears as an in-game trap, announced in red: 'WARNING: MERGE CONFLICT DETECTED. TREAD CAREFULLY.'"

Avoid captions that restate the section heading or summarize what the speaker said. Describe what is visible in the frame.

## Failure modes

- **Setup preflight failed** -> run installer; ask user for Whisper key if missing; or pass `--no-whisper`.
- **Download fails** (yt-dlp error to stderr) -> tell the user plainly. Login-required and region-locked videos won't work. Don't keep retrying.
- **No transcript** (no captions, no Whisper key, or Whisper failed) -> proceed frames-only; note this in the docx.
- **Preview cost warning** (`manifest.preview_cost_warning` is true; 5+ chunks) -> tell the user the chunk count and offer focus mode (`--start`/`--end`) as a cheaper alternative before reading every contact sheet.
- **Whisper request fails** -> retry with the other backend (`--whisper openai` if Groq failed, vice versa).

## Token efficiency notes

- Contact sheet for one video: around 5 to 10k image tokens.
- Selected frame at 512px: around 600 to 1000 tokens each.
- 12 selected frames per video: around 12 to 15k tokens.
- Typical full per-video budget: 20 to 30k image tokens.

If the user asks a follow-up about a video already processed in this session, you have its manifest, contact sheet, and selected frames in context. Don't re-run.

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

**Bundled scripts** in `scripts/`: `process.py` (entry point), `download.py`, `frames.py`, `transcribe.py`, `whisper.py`, `setup.py`. Template in `templates/build.js.template`. Review before first use.
