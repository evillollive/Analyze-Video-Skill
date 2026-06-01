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

1. The script tiles all frames into one `contact_sheet.jpg` (8 columns wide, row-major, chronological).
2. You Read the contact sheet once (around 5 to 10k tokens) to see the whole video at a glance.
3. You decide which N frames matter for the document, and Read only those at full resolution.

This typically lands at 20 to 30k tokens per video instead of 50 to 80k.

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
- `manifest.json`: structured pipeline output, schema_version 1
- `report.md`: human-readable summary
- `contact_sheet.jpg`: tiled overview of all frames
- `frames/frame_NNNN.jpg`: individual frames
- `download/video.<ext>`: source video
- `audio.mp3`: only if Whisper was used

The script's stdout is just the manifest path. All progress and warnings go to stderr.

For batch mode: process videos sequentially, one at a time. Don't parallelize. Don't accumulate full frame dumps in context between videos. After each video completes, Read its `manifest.json` and `contact_sheet.jpg`, then move on.

## Step 4: Plan the analysis from each contact sheet

For each video, after reading its manifest and contact sheet:

1. Cross-reference the contact sheet against the transcript in `manifest.transcript.segments`.
2. Identify distinct visual sections (intro, demo, outro, scene changes).
3. Pick which N frames go in the document (where N is the user's answer from Step 2).

**Frame selection algorithm:**

```
total = len(manifest.frames)
N = user's chosen frame count
step = total / N
selected_indices = [max(1, round(step * i + step/2)) for i in range(N)]
```

Then refine: if a selected index falls in the middle of a transcript segment with no visual change, shift it 1 to 2 positions toward the nearest transcript boundary or visible transition. Always include something from the opening and closing if distinct.

The contact sheet is in row-major chronological order with 8 columns. Tile (row R, col C) corresponds to frame index `(R * 8) + C + 1`. Use this to locate visual transitions on the sheet.

## Step 5: Read selected full-res frames

For each selected frame index, read the corresponding `frames/frame_NNNN.jpg` (use `manifest.frames[i].absolute_path`). Do all Reads for one video in a single parallel batch. For batch mode, only read frames for the video you're currently writing about, not all videos at once.

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

Push paragraphs into `children` using the helpers (`title`, `meta`, `h1`, `h2`, `body`, `imgPara`, `cap`). For each video: `h1`, `meta`, then per-section `h2` + `body` + frames.

**Two-column layout (default for 20+ frames):**

When the document will contain 20 or more frames, use side-by-side two-column tables instead of single-column `imgPara`+`cap` pairs. This cuts page count roughly in half and produces a much more readable dense document. Always use this layout for long-form series analyses or any request where the user asks for many frames.

Add these imports to build.js:
```javascript
const {
  ..., Table, TableRow, TableCell, WidthType, BorderStyle, VerticalAlign
} = require("docx");
```

Use these helpers:

```javascript
const NO_BORDER = { style: BorderStyle.NONE, size: 0, color: "FFFFFF" };

function makeCell(frameNum, capText, imgW, imgH, data) {
  return new TableCell({
    width: { size: 50, type: WidthType.PERCENTAGE },
    borders: { top: NO_BORDER, bottom: NO_BORDER, left: NO_BORDER, right: NO_BORDER },
    verticalAlign: VerticalAlign.TOP,
    children: [
      new Paragraph({
        children: [new ImageRun({
          data,
          transformation: { width: imgW, height: imgH },
          type: "jpg",
          altText: { title: `Frame ${frameNum}`, description: `${ts(frameNum)}`, name: `frame_${frameNum}` }
        })],
        alignment: AlignmentType.CENTER,
        spacing: { before: 60, after: 30 }
      }),
      new Paragraph({
        children: [new TextRun({ text: capText, italics: true, size: 16, color: "444444" })],
        alignment: AlignmentType.CENTER,
        spacing: { after: 140 }
      })
    ]
  });
}

function emptyCell() {
  return new TableCell({
    width: { size: 50, type: WidthType.PERCENTAGE },
    borders: { top: NO_BORDER, bottom: NO_BORDER, left: NO_BORDER, right: NO_BORDER },
    children: [new Paragraph({ text: "" })]
  });
}

// Pass an array of [frameNum, capText] pairs; renders two per row
function imgRows(pairs, imgW, imgH) {
  const rows = [];
  for (let i = 0; i < pairs.length; i += 2) {
    const [f1, c1] = pairs[i];
    const cells = [makeCell(f1, c1, imgW, imgH, loadFrame(f1))];
    if (i + 1 < pairs.length) {
      const [f2, c2] = pairs[i + 1];
      cells.push(makeCell(f2, c2, imgW, imgH, loadFrame(f2)));
    } else {
      cells.push(emptyCell());
    }
    rows.push(new Table({
      width: { size: 100, type: WidthType.PERCENTAGE },
      borders: {
        top: NO_BORDER, bottom: NO_BORDER, left: NO_BORDER,
        right: NO_BORDER, insideH: NO_BORDER, insideV: NO_BORDER
      },
      rows: [new TableRow({ children: cells })]
    }));
  }
  return rows;
}
```

Call it like this inside a section:
```javascript
children.push(...imgRows([
  [frameNum1, "Caption for frame 1."],
  [frameNum2, "Caption for frame 2."],
  // all frames for this section
], IMG_W, IMG_H));
```

**Image dimensions — pick from `manifest.aspect_ratio`:**

Single-column layout (fewer than 20 frames):
- "16:9" -> `{ width: 480, height: 270 }`
- "4:3"  -> `{ width: 480, height: 360 }`
- "9:16" -> `{ width: 240, height: 427 }`
- "1:1"  -> `{ width: 360, height: 360 }`

Two-column layout (20+ frames):
- "16:9" -> `{ width: 300, height: 169 }`
- "4:3"  -> `{ width: 300, height: 225 }`
- "9:16" -> `{ width: 210, height: 373 }`
- "1:1"  -> `{ width: 240, height: 240 }`
- Otherwise: compute height from width using `manifest.width`/`manifest.height` ratio.

**Critical docx rules (don't remove from build.js):**
- `type: 'jpg'` is required on every `ImageRun`.
- `altText` with all three fields (`title`, `description`, `name`) is required.
- Never use `\n` inside a `TextRun`. Use separate `Paragraph` elements.
- Page size 12240 x 15840 DXA (US Letter), 1440 DXA margins (1 inch).
- In two-column mode, all table borders must use `NO_BORDER` (top, bottom, left, right, insideH, insideV) or Word will render visible cell lines.

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
- **Long video warning** (manifest.long_video_warning is true) -> acknowledge it. Offer to re-run focused via `--start`/`--end`.
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
