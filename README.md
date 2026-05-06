# /analyze-video

A Claude skill that turns one or more videos into a polished Word document with timestamp-based prose analysis and embedded still frames. Download, frame extraction, contact-sheet preview, transcription, intelligent frame selection, and `.docx` export, all in one workflow.

## What it does

Given a video URL (YouTube, Vimeo, X, TikTok, Twitch, most yt-dlp-supported sites) or a local file path, the skill:

1. Downloads the video and any native captions
2. Extracts auto-scaled frames (capped at 100 frames, 2 fps)
3. Tiles all frames into a single contact-sheet image for cheap visual preview
4. Transcribes the audio (captions first, Whisper API as fallback)
5. Asks how many frames to embed in the final document
6. Reads only the selected frames and writes a detailed time-based analysis
7. Builds a polished Word document with embedded frames and captions

Handles single videos and batches.

## Why the contact sheet

Reading every extracted frame burns 50 to 80k image tokens per video. Instead, the script tiles all frames into one `contact_sheet.jpg`. Claude Reads the contact sheet once (~5 to 10k tokens), decides which frames matter, and Reads only those at full resolution.

Typical token budget: 20 to 30k per video instead of 50 to 80k.

## Requirements

- Python 3.9+
- `ffmpeg`, `ffprobe`, `yt-dlp` (auto-installed via Homebrew on macOS; install commands printed for Linux/Windows)
- A Whisper API key for the audio fallback (free tier works):
  - Groq: https://console.groq.com/keys (preferred: cheaper, faster)
  - OpenAI: https://platform.openai.com/api-keys (fallback)

If neither key is set, the skill works frames-only on videos without native captions.

## Installation

This is a Claude Code skill. Drop the entire folder under your skills directory, or install it as a plugin per your Claude Code setup. The `setup.py` script handles dependency installation and API-key scaffolding the first time the skill runs.

Configuration lives at `~/.config/analyze-video/.env`.

## Usage

Just ask Claude something like:

- "Analyze this video: https://youtu.be/abc and write me a report"
- "Make a doc from these three videos with screenshots"
- "Analyze the demo at 2:30-3:15 in this clip"

The skill triggers automatically and walks through the workflow.

## Files

```
analyze-video/
├── SKILL.md                      # Skill instructions for Claude
├── README.md                     # This file
├── LICENSE                       # MIT
├── scripts/
│   ├── process.py                # Per-video pipeline orchestrator
│   ├── download.py               # yt-dlp wrapper
│   ├── frames.py                 # ffmpeg frame extraction + contact sheet
│   ├── transcribe.py             # WebVTT caption parser
│   ├── whisper.py                # Groq / OpenAI Whisper client
│   └── setup.py                  # Preflight + installer
└── templates/
    └── build.js.template         # docx builder template (uses npm `docx` package)
```

## License

MIT. See `LICENSE`.
