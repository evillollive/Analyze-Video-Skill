---
name: analyze-video
description: After /watch processes one or more videos, this skill writes a detailed visual analysis with embedded still frames and exports it as a Word document (.docx). Use immediately after running /watch when the user wants a written analysis, video report, or document with frames. Also triggers on "analyze this video", "write up the video", "make a report from the video", "document what's in the video", "summarize with screenshots", "export the analysis", "turn this into a document", or any request to produce a deliverable from video content already processed in the session.
allowed-tools: Bash, Read, Write, AskUserQuestion
---

# analyze-video

This skill picks up where /watch left off. /watch gives Claude eyes on a video: frames extracted as JPEGs plus a timestamped transcript. This skill takes that material and produces a polished Word document with a detailed visual analysis organized by time, selected still frames embedded and captioned, and a clean export.

It handles 1 to many videos in the same session.

## What this skill does (for humans)

Run `/watch` on however many videos you want, then invoke `/analyze-video`. It inventories all the `/watch` outputs in context, asks how many frames per video and whether to combine into one document or keep them separate, copies frames to an accessible location, selects them intelligently (spread across the full duration, biased toward moments where something is visually changing), reads them, writes the full timestamp-based analysis, and builds the `.docx`. PDF export is a one-question follow-up after delivery.

A few design decisions worth knowing:

- **Frame selection** is explained explicitly so Claude does not just take the first N frames. Selection is spread across the video duration and weighted toward moments where the visual content is actually changing.
- **Captions** have a writing guide with two concrete patterns, so they describe what is in the frame rather than restating the section heading.
- **Aspect ratio handling** is included (16:9, 4:3, vertical) so the embedded images are sized correctly regardless of source format.
- **Cowork compatibility** is built in: /watch puts frames in /tmp which is not directly readable in Cowork, so the skill copies them to the outputs folder before reading.

---

## Prerequisites

This skill requires /watch to have already run in the current session. Before proceeding, confirm that at least one "watch: video report" block is present in the conversation context, with frame paths and a transcript listed.

If /watch has not been run yet, tell the user to run it first:
```
/watch <url-or-path>
```

---

## Step 1: Inventory what is in context

Scan the conversation for all /watch reports. For each video, note:

- **Title** — from the report header (e.g., "Turning a codebase into an 80s dungeon crawler")
- **Duration** — total length of the video
- **Frame directory** — the path listed under "Frames live at:" (typically `/tmp/watch-XXXXXXXX/frames`)
- **Total frame count** — count the listed frame paths in the report
- **Transcript** — all segments with timestamps
- **Working directory** — listed at the bottom of the report (e.g., `/tmp/watch-XXXXXXXX`)

If there are multiple videos, list them briefly so the user knows what you found before proceeding.

---

## Step 2: Ask the user

Use `AskUserQuestion` to collect two things:

**1. How many frames per video** should be included in the document?

Suggest a sensible default based on duration:
- Under 2 min: suggest 6-8 frames
- 2-5 min: suggest 8-12 frames
- Over 5 min: suggest 12-20 frames

**2. If there are multiple videos:** Should all videos go into one combined document, or should each get its own separate .docx file?

If there is only one video, skip question 2.

---

## Step 3: Copy frames to an accessible location

The /watch script writes frames to `/tmp`, which is not directly readable by the Read tool in Cowork. Copy the selected frames to the outputs directory before reading them.

First, determine how many frames exist and calculate which ones to select (see Frame Selection below). Then copy only those frames:

```bash
# Copy full frame directory for a video, then you can Read from the outputs path
cp -r /tmp/watch-XXXXXXXX/frames /path/to/outputs/frames_video_1/

# For multiple videos, use numbered subdirectories
cp -r /tmp/watch-XXXXXXXX/frames /path/to/outputs/frames_video_2/
```

The outputs directory in Cowork is the path shown in your environment context. Use it consistently throughout.

---

## Step 4: Select frames intelligently

Given N frames to include from M available frames, the goal is maximum coverage and visual variety, not mechanical spacing.

**Selection algorithm:**

Divide the video into N equal time segments and pick one frame from each segment. In practice with sequential frame files numbered 0001 to M:

```
step = M / N
selected_indices = [max(1, round(step * i + step/2)) for i in range(N)]
```

Clamp all indices to the valid range [1, M].

**Refine based on content:** Cross-reference the selected frame timestamps against the transcript. If a selected frame falls in the middle of a sentence with no visual change happening, shift it by 1-2 positions toward the nearest transcript segment boundary. Prefer frames where something new appears on screen (a new speaker, a UI change, a visual transition) over frames where nothing has changed since the last selected frame.

Always try to include at least one frame from the opening and one from the closing if the video has distinct intro/outro moments.

---

## Step 5: Read the selected frames

Read all selected frames in a single parallel batch: one `Read` call per frame, all in the same turn. Frames are now in the outputs directory, so they are accessible.

You now have both visual content (the frames) and spoken content (the transcript) to work from.

---

## Step 6: Write the analysis

For each video, write a detailed analysis organized into time-based sections. Structure it as flowing prose, not bullet points.

**Section format:** Name each section by the time range and a short descriptive label (e.g., "Opening (0:00-0:15)" or "Live Demo (0:30-0:55)"). Within each section:

- Describe what is visually on screen: layout, color palette, text visible, people, expressions, UI elements, what the camera is focused on
- Describe what is being said at that moment (from the transcript)
- Note what is significant, surprising, or worth calling out

Be specific and observational. A good section reads like detailed scene description: someone who has not watched the video should come away with a clear mental image of what was on screen and what was happening. Avoid vague summary language like "the presenter explains X" in favor of concrete description: "Lee, wearing a black GitHub hoodie, stands in front of a green screen and holds a laptop. He gestures toward the camera as a Copilot conversation is projected large behind him."

**For multiple videos:** Analyze each one fully before moving to the next. Finish the document with a "Production Notes" or "Observations Across Videos" section that notes shared patterns: format, visual style, themes, structure.

---

## Step 7: Build the Word document

Set up the docx builder if not already done:

```bash
mkdir -p /path/to/outputs/docx_build
cd /path/to/outputs/docx_build
npm init -y 2>/dev/null && npm install docx 2>&1 | tail -3
```

Write a `build.js` script to the docx_build directory. Use this structure as the foundation:

```javascript
const { Document, Packer, Paragraph, TextRun, ImageRun } = require('docx');
const fs = require('fs');

const OUT = '/path/to/outputs';

// Helper functions
function imgPara(filePath) {
  return new Paragraph({
    spacing: { before: 220, after: 0 },
    children: [new ImageRun({
      type: 'jpg',
      data: fs.readFileSync(filePath),
      transformation: { width: 480, height: 270 }, // 16:9 default; adjust if source is different ratio
      altText: { title: 'frame', description: 'video frame', name: 'frame' }
    })]
  });
}

function cap(text) {
  return new Paragraph({
    spacing: { before: 60, after: 280 },
    children: [new TextRun({ text, italics: true, size: 18, color: '777777', font: 'Arial' })]
  });
}

function body(text) {
  return new Paragraph({
    spacing: { before: 0, after: 180 },
    children: [new TextRun({ text, size: 22, font: 'Arial' })]
  });
}

function h1(text) {
  return new Paragraph({
    spacing: { before: 440, after: 180 },
    children: [new TextRun({ text, bold: true, size: 36, font: 'Arial', color: '1A1A1A' })]
  });
}

function h2(text) {
  return new Paragraph({
    spacing: { before: 300, after: 120 },
    children: [new TextRun({ text, bold: true, size: 24, font: 'Arial', color: '2D6A9F' })]
  });
}

function meta(text) {
  return new Paragraph({
    spacing: { before: 0, after: 260 },
    children: [new TextRun({ text, italics: true, size: 20, color: '888888', font: 'Arial' })]
  });
}

// Build children array
const children = [
  // Title
  new Paragraph({
    spacing: { before: 0, after: 120 },
    children: [new TextRun({ text: 'Document Title Here', bold: true, size: 44, font: 'Arial', color: '0D0D0D' })]
  }),
  // ... add content here
];

const doc = new Document({
  sections: [{
    properties: {
      page: {
        size: { width: 12240, height: 15840 }, // US Letter
        margin: { top: 1440, right: 1440, bottom: 1440, left: 1440 }
      }
    },
    children
  }]
});

Packer.toBuffer(doc).then(buf => {
  fs.writeFileSync(`${OUT}/video_analysis.docx`, buf);
  console.log('Done');
}).catch(err => { console.error(err); process.exit(1); });
```

**Critical docx rules:**

- `type: 'jpg'` is required on every ImageRun (never omit it)
- `altText` with all three fields (title, description, name) is required on every ImageRun
- Never use `\n` inside TextRun: use separate Paragraph elements
- Page size is always set explicitly: 12240 x 15840 DXA for US Letter
- Content width with 1-inch margins = 9360 DXA

**Document structure:**

1. Title: the video title (or a collection title for multiple videos)
2. Metadata line: uploader, duration, source URL
3. For each video: an H1 section, then H2 subsections by time period, with body text and frames interspersed
4. Each selected frame appears directly after the paragraph it illustrates, followed by a 1-2 sentence caption (what is shown and why it is notable)
5. For multiple videos: a closing "Observations" or "Production Notes" section

**Naming the output file:**

For a single video, derive the filename from the video title (e.g., `gh_dungeons_analysis.docx`). For a combined multi-video document, use a descriptive collection name. For separate files, name each after its video.

---

## Step 8: Validate and deliver

Validate the document before presenting it:

```bash
python /path/to/docx/scripts/office/validate.py /path/to/outputs/video_analysis.docx
```

If the validate.py script is not available, skip validation and proceed.

Present the file link to the user.

Then ask: "Want a PDF version as well?" If yes:

```bash
libreoffice --headless --convert-to pdf /path/to/outputs/video_analysis.docx \
  --outdir /path/to/outputs/
```

If LibreOffice is not available, note that the .docx can be opened in Word or Google Docs and exported to PDF from there.

---

## Step 9: Offer cleanup

After delivering, offer to delete the copied frame directories from outputs (they add bulk to the workspace). The original /watch working directories in /tmp can be removed with `rm -rf /tmp/watch-XXXXXXXX` if the user is done with them.

---

## Frame-to-caption writing guide

A good caption is concrete and adds something the image alone does not make explicit. Two patterns that work well:

**Describe + contextualize:**
"The dungeon game running live in a terminal. Status bar reads 'HP: 13/20 | Level: 2/5 | Kills: 4.' The dungeon layout is seeded by the repository's commit SHA."

**Describe + note significance:**
"A Merge Conflict appears as an in-game trap, announced in red at the bottom of the screen: 'WARNING: MERGE CONFLICT DETECTED. TREAD CAREFULLY.'"

Avoid captions that just restate the section heading or say "presenter talks about X." Describe what is visible in the frame.

---

## Notes on aspect ratio

The /watch skill defaults to 512px wide frames. For standard 16:9 video, the correct document dimensions are `width: 480, height: 270`. For 4:3 video, use `width: 480, height: 360`. For vertical/9:16 video (TikTok, Reels), use `width: 240, height: 427`.

Check the resolution field in the /watch report header to determine the source aspect ratio before setting ImageRun dimensions.
