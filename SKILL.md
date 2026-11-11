---
name: analyze-video
description: >-
  Analyze video URLs and local files into timestamped Word reports and
  Open Filmmaker Toolkit project files. Also handle caption downloads and
  best-quality local video copies. Triggers include
  "analyze this video", "make a report from this video", "download the captions",
  "get the subtitles", "export this analysis as an Open Filmmaker Toolkit project",
  "download this video", "get me a local copy", "best quality copy", and
  "save this YouTube video".
allowed-tools: Bash, Read, Write, AskUserQuestion
homepage: https://github.com/evillollive/Analyze-Video-Skill
repository: https://github.com/evillollive/Analyze-Video-Skill
license: AGPL-3.0
user-invocable: true
---

# analyze-video

Self-contained pipeline that takes one or more video sources, downloads or resolves them locally, extracts frames, uses captions or Whisper for transcripts when available, tiles frames into contact sheets for cheap visual review, selects representative frames, and produces a polished Word document with timestamped analysis. It can also download caption files, save a best-quality local copy, and export the analysis as an Open Filmmaker Toolkit project file.

The skill works with any agent that can run shell commands, on macOS, Linux or Windows. Two conventions used below:

- **Ask the user** means use your environment's question tool if it has one, otherwise ask in plain text.
- **Deliver** means give the user the absolute path of the file, as a clickable file link if your environment supports them.

## Reference files

Read these only when the situation calls for them. They live in `${SKILL_DIR}/references/`.

| File | Read it when |
|---|---|
| `downloads.md` | A download fails or comes back degraded, the user wants a best-quality copy, or you need the full caption options |
| `environments.md` | Commands time out, setup and processing run in different places, a download is blocked only from this machine, tools are installed but not found, or you are on Windows |
| `docx-spec.md` | You need layouts, appendices, a PDF, standalone files, cleanup, or the `docx` module cannot be found |
| `pipeline-options.md` | A run was interrupted, or you need output files, manifest fields, caching, parallelism or promo trimming |
| `oft-export.md` | The user wants an Open Filmmaker Toolkit project file |

## Safety and privacy boundary

This skill runs `yt-dlp`, `ffmpeg`, and `ffprobe` locally. Source video files, frames, contact sheets, manifests, and the final `.docx` stay on the user's machine. Extracted audio is sent to Groq or OpenAI only when no native captions are available and a Whisper key is configured.

Do not try to bypass platform bot detection or access controls. If a site blocks unauthenticated downloads, the safe fallback is explicit user authorization: ask whether the user wants to use their own browser session via `--cookies-from-browser <browser>` or a cookies file via `--cookies <path>`. Do not spoof watch sessions, forge tokens, automate hidden playback to trick a site, or use unrelated hosting/services as an evasion layer.

## Choose the mode

- **Analysis** (default): the user wants a written document about one or more videos. Follow Steps 0 to 8.
- **Captions only**: the user wants subtitle or transcript files and no analysis. Follow "Captions-only mode" and stop.
- **Download only**: the user wants a local copy of the video and no analysis. Follow "Download-only mode" and stop.

In every mode, first resolve the skill directory as shown in Step 0.

## Captions-only mode

Skip the report pipeline: no setup preflight, frame questions, `process.py`, or `.docx`. Only `yt-dlp` is needed; if it is missing, `captions.py` prints install guidance (or run `setup.py`).

```bash
python3 "${SKILL_DIR}/scripts/captions.py" \
  --source "<url>" [--source "<url2>" ...] \
  --out-dir "<absolute output dir>" \
  [--langs en] [--format srt] [--no-auto] [--list]
```

Defaults are English and SRT. `--format` also takes `vtt`, `txt`, a comma list, or `all`; `--list` shows the available languages without downloading. Full options and the output format are in `references/downloads.md`.

Local video files are not supported in this mode; point the user to any sidecar `.srt`/`.vtt` next to the file instead. Do not offer cleanup or PDF steps. Report the file paths and stop.

## Download-only mode

Skip setup of the docx stack, frame extraction, contact sheets, and the report pipeline. Before downloading, ask only for the choices the user has not already made:

1. Deliverable: video file only, or video file plus analysis.
2. Container: MKV (any compatible source codec), edit-friendly MP4 (H.264 video + AAC audio), or both.
3. Captions: none, SRT, or SRT plus a timestamped transcript.
4. Destination folder.

The script needs only yt-dlp, ffprobe, and ffmpeg:

```bash
python3 "${SKILL_DIR}/scripts/download.py" --best \
  --source "<url>" --out-dir "<destination>" \
  --container mkv --audio-lang original --subs none \
  --temp-dir "<a local folder that is not synced by a cloud service>"
```

Rules that always apply:

- Write final files to the destination the user chose. Never assume one.
- If the best available stream is below 720p, stop and ask whether the user accepts it; only then retry with `--allow-low-quality`.
- After downloading, report the actual codecs, resolution, fps and video bitrate the script measured, plus the decode-test result.
- If the user also wants analysis, download first, then run the normal flow against the local file. Do not analyze or build a `.docx` when the user asked for the file only.

Audio language, MP4 behavior and failure handling are in `references/downloads.md`.

## Step 0: Setup preflight

Set `SKILL_DIR` to the absolute path of the folder this `SKILL.md` lives in. Some agents expose it as an environment variable (for example `CLAUDE_SKILL_DIR` or `COPILOT_SKILL_DIR`). No particular agent is required: if none is set, fill in the path you loaded this file from. Then check it, because a wrong value makes every later command fail with "file not found":

```bash
SKILL_DIR="${SKILL_DIR:-${CLAUDE_SKILL_DIR:-${COPILOT_SKILL_DIR:-}}}"
# If that is empty, set it yourself:
#   SKILL_DIR="/absolute/path/to/analyze-video"
# or locate it once:
#   SKILL_DIR=$(dirname "$(find / -name SKILL.md -path '*analyze-video*' 2>/dev/null | head -1)")
test -f "$SKILL_DIR/scripts/process.py" && echo "skill dir ok" || echo "SKILL_DIR is wrong: '$SKILL_DIR'"
```

Shells that start fresh on every command do not remember the variable; set it again at the start of each command.

Use `"$SKILL_DIR/scripts/..."` consistently. Never hardcode `~/.cache/analyze-video/scripts/...`.

Run once per session, on the machine where processing will run:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --check
```

Exit code `0` means local dependencies are ready. Treat any non-zero exit as "not ready" and run the installer:

```bash
python3 "${SKILL_DIR}/scripts/setup.py"
```

The installer adds what it can without administrator rights and prints exact install commands for the rest:

- macOS with Homebrew: installs missing `ffmpeg`, `yt-dlp`, Node.js, Deno and the `docx` npm module.
- Linux: installs `yt-dlp`, Deno and the `docx` npm module without sudo; prints commands for `ffmpeg` and Node.js.
- Windows: installs `yt-dlp` with pip and missing `ffmpeg`, Node.js and Deno with winget, plus the `docx` npm module; prints commands for anything that fails.

If setup prints `export PATH=...` lines, apply them before running the pipeline. On Windows, newly installed tools are found by the scripts right away, but need a new terminal before you can call them by name. If `--check` notes that yt-dlp is more than 45 days old, `setup.py --update-ytdlp` updates it.

A Whisper API key is optional. Without one, videos with native captions still get transcript analysis and captionless videos are processed frames-only. If the user wants transcripts for captionless videos, ask whether they have a Groq key (preferred) or an OpenAI key, then store it:

```bash
python3 "${SKILL_DIR}/scripts/setup.py" --set-key groq "<KEY>"
# or:
python3 "${SKILL_DIR}/scripts/setup.py" --set-key openai "<KEY>"
```

Setup state belongs to the machine it ran on. If setup and processing happen in different places, or commands are cut off by a time limit, read `references/environments.md`.

## Step 1: Parse the request

Extract:
- One or more video sources: HTTP(S) URLs or local video paths.
- Optional focus range, such as "2:30 to 3:15" or "the demo section at 1:30".
- Optional speed/quality intent. If the user asks for a quick scan, use `--quick`.

Infer focus ranges from the request and pass them with `--start` and `--end`. Do not ask about focus unless the request is ambiguous enough that processing the full video would likely waste time or tokens.

## Step 2: Ask the user only when needed

Ask the user once for the batch:

1. **Frames per video** if the user did not specify. This is a required question unless the user explicitly says "pick for me" or equivalent. Suggest:
   - Under 2 min: 6 to 8
   - 2 to 5 min: 8 to 12
   - 5 to 15 min: 12 to 20
   - Over 15 min: 16 to 25, or a focused range
   If the user delegates the choice, use the middle of the range for the video's length (20 for videos over 15 minutes) and say which number you used.
   If you do not know the video's length yet, you may ask this after Step 3, when `manifest_lite.json` gives the duration. The count is not needed until Step 5.
2. **Output format** only for 2+ videos:
   - One combined `.docx` (default)
   - Separate `.docx` per video
3. **Where to save the document**, if the user has not said and your environment has no standard place for deliverables.

If a URL failed because the site requires login/bot verification and the user is authorized to view it, ask whether they want to retry with their own browser cookies. Do not ask for cookies proactively before a failure.

## Step 3: Process each video

Create one numbered output directory per video inside the working folder for this job (the folder the user chose, or your environment's output folder):

```bash
OUT_DIR="<absolute path to the working folder>"
VIDEO_DIR="$OUT_DIR/video_1"

python3 "${SKILL_DIR}/scripts/process.py" \
  --source "<url-or-path>" \
  --out-dir "$VIDEO_DIR"
```

Add flags as the request calls for them:

- `--start 2:30 --end 3:15`: process only a focus range.
- `--quick`: a faster, cheaper pass with a smaller frame budget.
- `--source-url "<original-url>"`: when `--source` is a local file that was downloaded from a URL, so the real title and captions can be recovered.
- `--force`: ignore cached output and redo everything.

`process.py` prints the path to `manifest_lite.json` on stdout. Progress and warnings go to stderr.

A video downloaded from a URL is kept in a shared cache (`~/.cache/analyze-video/downloads/`), not in the out-dir, so later runs do not download it again. Add `--no-download-cache` if the user wants the video file kept with the other working files.

Process videos one at a time. Do not run several videos in parallel; a single run already uses the machine fully.

If a run is interrupted, re-run the exact same command. It resumes instead of starting over. If a download is blocked, explain the specific access problem and read `references/downloads.md` before retrying. Offer the user's own browser cookies only after a failure, and only with their agreement.

## Step 4: Read the manifest and preview visuals

Do not read every frame. The pipeline produces contact sheets and a lightweight manifest so you can preview the video at low cost:

1. Read `manifest_lite.json` first. It has chunk metadata, timestamps, contact-sheet paths, `docx_image_dimensions`, `suggested_docx_name`, `transcript_path` and quick-mode flags, but no transcript text or per-frame arrays.
2. If `quick_mode` is true, skip contact-sheet preview unless the user asked for detailed visual analysis.
3. Otherwise read each relevant contact sheet from `manifest_lite.chunks[].contact_sheet.absolute_path`. For very long videos, read only the chunks that match the user's focus.
4. Read the full `manifest.json` only when you need transcript text (`manifest.transcript_segments[]`) for quotes, section writing, or refining section boundaries.

Videos over 12 minutes are split into chunks of about 10 minutes that overlap by a few seconds, so the last tiles of one sheet and the first tiles of the next can show the same moment. Each chunk has its own contact sheet and its own frame spacing. If `manifest_lite.preview_cost_warning` is true and the user asked about a narrow moment, prefer re-running with `--start` and `--end` over reading every contact sheet.

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
- Do not spend more than one frame on end credits or a static end card unless the user asked about them.

To swap in a different frame, count tiles on the chunk's contact sheet left to right, top to bottom, starting at 1. Tile N is `chunks[].frames[N-1]` in the full `manifest.json`; use that entry's `absolute_path`, `timestamp_seconds` and `timestamp_formatted`.

Read selected full-resolution frames in one parallel Read batch per video. For batch processing, finish one video before reading frames for the next.

## Step 6: Write the analysis

Write time-based sections with descriptive headings, for example:
- "Opening setup (0:00 to 0:18)"
- "Live demo walkthrough (2:30 to 3:15)"

For each section:
- Describe what is visible: layout, people, on-screen text, expressions, UI, camera focus, motion, and visual transitions.
- Connect transcript evidence when available using timestamps from `manifest.transcript_segments`.
- Note what is significant or surprising.

Quoting from captions needs care. `manifest_lite.transcript_source` says where the transcript came from, but not whether a person or a machine wrote it. Machine captions often have no punctuation, misrecognized words, and sound markers such as `[Applause]` in the middle of a sentence, and consecutive segments can repeat the same words.

- Quote only words that appear together, in order, in the transcript. Do not join text across a sound marker or silently drop one.
- When a phrase appears in two segments, use the timestamp of the first.
- If a word looks misrecognized, paraphrase instead of quoting it.
- Add punctuation and capitals for readability, but do not change words.

Be concrete and observational. Avoid vague summaries such as "the presenter explains the feature" when the visual evidence supports a richer description.

For caption style, consult:

```bash
${SKILL_DIR}/templates/caption_guide.md
```

For combined multi-video docs, add an "Observations Across Videos" section covering shared structure, visual style, themes, and differences.

## Step 7: Ask the delivery questions

Ask before building. Appendices are off by default, and there is exactly one build, after the answers. Never add the contact-sheet appendix or the transcript appendix on your own initiative.

Ask the user once for the batch. Skip any option that does not apply, and offer the transcript options only when a transcript exists (`transcript_segment_count > 0`):

> "A few delivery options before I build the document:
> 1. Include the contact sheet(s) as a visual appendix *inside* the document?
> 2. Include the full transcript as an appendix *inside* the document?
> 3. Keep standalone copies of the contact sheet(s) and/or the transcript as separate files next to the document?
> 4. Also want a PDF version?
> 5. Clean up the remaining working files afterward?
> 6. Also export the analysis as an Open Filmmaker Toolkit project file?"

Treat no answer as "no" to every option. For each "yes", follow `references/docx-spec.md` (appendices, PDF, standalone files, cleanup) or `references/oft-export.md`.

## Step 8: Build and deliver

Do not write JavaScript at runtime. Write a JSON spec and pass it to the bundled builder:

```bash
python3 "${SKILL_DIR}/scripts/validate_spec_paths.py" --spec "$OUT_DIR/spec.json" &&
python3 "${SKILL_DIR}/scripts/lint_spec_quality.py" --spec "$OUT_DIR/spec.json" &&
node "${SKILL_DIR}/scripts/build-docx.js" --spec "$OUT_DIR/spec.json"
```

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
      "meta": "Uploader · Duration",
      "image_dimensions": { "width": 480, "height": 270 },
      "frame_layout": "2up",
      "sections": [
        {
          "heading": "Opening (0:00 to 0:18)",
          "time_range": "0:00 to 0:18",
          "body": "Analysis prose.",
          "frame_layout": "2up",
          "frames": [
            {
              "path": "/absolute/path/frame_0001.jpg",
              "timestamp_seconds": 3.5,
              "timestamp_formatted": "0:03",
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

Rules for the spec:

- `out`: an absolute path in the folder where the document should be saved: the one the user chose, otherwise `$OUT_DIR`. For one video, use the manifest's `suggested_docx_name` as the file name. For a combined document, build a similar name from the videos (for example the first title slug plus `-and-2-more`) and end it with `-analysis.docx`.
- `source`: the original URL or local path the user gave (`manifest_lite.source`, or `manifest_lite.url` for URLs). Never a cache or download path.
- `image_dimensions`: use `manifest_lite.docx_image_dimensions`.
- Frame paths: from this session's `select_frames.py` output, or from this session's `manifest.json` for frames you swapped in (Step 5). Never from an earlier session's notes.
- `meta`: a short line under the title. Do not repeat the source URL there; the builder prints it on its own "Source:" line.
- `body`: separate paragraphs with a blank line.
- `time_range`, `timestamp_seconds` and `timestamp_formatted` are recorded for other tools and are not printed in the document. Put the time range in the section heading, and put a timestamp in a caption when the reader should see it.
- `frame_layout`: `"1up"` (default) or `"2up"`, settable at the top level, per video, or per section.
- `appendix_contact_sheets` and `appendix_transcript`: include only what the user approved in Step 7. Leave them out otherwise.

`validate_spec_paths.py` is mandatory. If it fails, a path is stale or mistyped: take the paths again from this session's `select_frames.py` output or `manifest.json` and validate again. Confirm the `.docx` exists, then deliver it, along with any extras the user approved.

## When something fails

- **Setup preflight failed**: run the installer. Whisper keys are optional; the local dependencies are not.
- **Anything about downloading** (blocked, rate limited, geo restricted, low quality, yt-dlp errors or updates): `references/downloads.md`.
- **Timeouts, setup done on a different machine, tools not found, Windows**: `references/environments.md`.
- **No transcript**: proceed frames-only and say so in the document. If the video came from a URL but was processed from a separately downloaded local file, add the transcript without re-downloading: `python3 "${SKILL_DIR}/scripts/process.py" --captions-only --source <url> --out-dir <video-dir>`.
- **Whisper backend failed**: when both keys exist, `process.py` tries Groq then OpenAI. If both fail, proceed frames-only.
- **Whisper audio too large**: re-run with a focused `--start`/`--end` range, or use a source with native captions.
- **Long-video preview warning**: prefer a focused re-run or quick mode over reading every contact sheet.
- **Spec has stale or missing frame paths**: re-run `select_frames.py` in the current session and regenerate `spec.json`.
- **`docx` module not found**: `references/docx-spec.md`.

## Security notes

The skill does not upload source video, persist cookies, post to platform accounts, or access platform accounts by default. Cookie-based retries must be initiated only after user consent and should use the user's own authorized browser/session.

Bundled runtime: `scripts/process.py`, `captions.py`, `download.py`, `ytdlp.py`, `frames.py`, `transcribe.py`, `whisper.py`, `setup.py`, `health_check.py`, `select_frames.py`, `validate_spec_paths.py`, `lint_spec_quality.py`, `run_guarded_pipeline.py`, `export_oft.py`, `mcp_server.py`, and `build-docx.js`.
