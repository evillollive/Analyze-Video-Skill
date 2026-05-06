# /analyze-video

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/evillollive/Analyze-Video-Skill)](https://github.com/evillollive/Analyze-Video-Skill/releases)

An agentic AI skill for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that turns one or more videos into a polished Word document with timestamp-based prose analysis and embedded still frames. Download, frame extraction, contact-sheet preview, transcription, intelligent frame selection, and `.docx` export — all in one workflow.

## What It Does

Given a video URL (YouTube, Vimeo, X, TikTok, Twitch, most yt-dlp-supported sites) or a local file path, the skill:

1. Downloads the video and any native captions
2. Extracts auto-scaled frames (up to 120 per chunk, 2 fps cap)
3. Auto-chunks long videos: anything over 12 minutes splits into 10-minute chunks (5-second overlap)
4. Tiles each chunk's frames into a contact-sheet image for cheap visual preview
5. Transcribes the audio (captions first, Whisper API as fallback)
6. Asks how many frames to embed in the final document
7. Reads only the selected frames and writes a detailed time-based analysis
8. Builds a polished Word document with embedded frames and captions

Handles single videos and batches.

## Why a Contact Sheet

Reading every extracted frame burns tens of thousands of image tokens per video. Instead, the script tiles each chunk's frames into a `contact_sheet.jpg`. AI reads the contact sheet, decides which frames matter, and reads only those at full resolution.

## Why Auto-Chunking

A 60-minute video would have one frame every 36 seconds, most of the video would be invisible to the agent. Auto-chunking splits long videos into 10-minute sections, each with its own ~80–100 frames, so coverage stays at roughly one frame every 7 seconds regardless of total length.

The trade-off is more contact sheets to preview; for very long videos (5+ chunks), the skill warns about the preview cost and offers focus mode as an alternative.

## Requirements

- **Python** 3.9+
- **Node.js** (for the `.docx` builder)
- **ffmpeg**, **ffprobe**, **yt-dlp** — auto-installed via Homebrew on macOS; install commands printed for Linux/Windows
- A **Whisper API key** for audio transcription fallback (free tier works):
  - [Groq](https://console.groq.com/keys) (preferred — cheaper, faster)
  - [OpenAI](https://platform.openai.com/api-keys) (fallback)

> If neither key is set, the skill works frames-only on videos without native captions.

## Installation

This is a Claude Code skill. Drop the entire folder under your skills directory, or install it as a plugin per your Claude Code setup. The `setup.py` script handles dependency installation, the npm `docx` module, and API-key scaffolding the first time the skill runs.

Configuration lives at `~/.config/analyze-video/.env`. To set a Whisper key from the command line without editing the file:

```bash
python3 scripts/setup.py --set-key groq <YOUR_KEY>
# or:
python3 scripts/setup.py --set-key openai <YOUR_KEY>
```

## Usage

Just ask Claude something like:

- *"Analyze this video: https://youtu.be/abc and write me a report"*
- *"Make a doc from these three videos with screenshots"*
- *"Analyze the demo at 2:30–3:15 in this clip"*
- *"Quick TL;DR with a few screenshots from this clip"* (triggers `--quick` mode)

The skill triggers automatically and walks through the workflow.

## Project Structure

```
Analyze-Video-Skill/
├── SKILL.md                        # Skill instructions for Claude
├── README.md                       # This file
├── CHANGELOG.md                    # Release history
├── LICENSE                         # MIT
├── analyze-video.skill             # Packaged skill bundle for claude.ai
├── commands/
│   └── analyze-video.md            # Slash-command definition
├── hooks/
│   ├── hooks.json                  # SessionStart hook config
│   └── scripts/                    # Hook helper scripts
├── scripts/
│   ├── process.py                  # Per-video pipeline orchestrator
│   ├── select_frames.py            # Frame-selection helper (proportional + boundary-aware)
│   ├── download.py                 # yt-dlp wrapper
│   ├── frames.py                   # ffmpeg frame extraction + contact sheet
│   ├── transcribe.py               # WebVTT caption parser
│   ├── whisper.py                  # Groq / OpenAI Whisper client (with auto-fallback)
│   ├── setup.py                    # Preflight + installer (deps, npm docx, --set-key)
│   ├── build-docx.js               # JSON-spec docx builder (no per-session npm install)
│   └── build-skill.sh              # Builds .skill bundle for distribution
└── templates/
    └── caption_guide.md            # Caption style guide (read at write-time)
```

## Security & Privacy

This skill:

- Runs `yt-dlp` locally to download videos and pull native captions.
- Runs `ffmpeg`/`ffprobe` locally to extract frames, audio, and the contact sheet.
- Sends extracted **audio only** (not the video) to Groq or OpenAI Whisper API when no captions are available and Whisper is enabled.
- Writes everything under the session outputs folder you provide via `--out-dir`.
- Reads/writes `~/.config/analyze-video/.env` (mode `0600`) for the Whisper key.

It does **not**:

- Upload the source video to any API.
- Access any platform account (no login, no cookies, no posting).
- Persist anything outside the session outputs folder and `~/.config/analyze-video/`.

## Contributing

Contributions are welcome! Please open an issue or submit a pull request.

## License

[MIT](LICENSE)
