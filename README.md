# 🎬 /analyze-video

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/evillollive/Analyze-Video-Skill)](https://github.com/evillollive/Analyze-Video-Skill/releases)

**Drop a video link. Get a beautifully formatted Word doc back — with screenshots, timestamps, and a written breakdown of everything that happens.**

This is a skill for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that watches videos so you don't have to (or helps you watch them better). It works with YouTube, Vimeo, TikTok, X, Twitch, local files — basically anything [yt-dlp](https://github.com/yt-dlp/yt-dlp) can handle.

## How it works

You paste a link. The skill does the rest:

1. **Downloads** the video and grabs any existing captions
2. **Pulls frames** at smart intervals — not too many, not too few
3. **Builds a contact sheet** so the AI can see the whole video at a glance without burning through your context window
4. **Reads the transcript** (or creates one via Whisper if there are no captions)
5. **Picks the best frames** and writes a detailed, timestamped analysis
6. **Exports a polished `.docx`** with embedded screenshots, captions, and prose

One video or ten — it handles batches too.

## The clever bits

### Contact sheets save you money

Every frame the AI reads costs tokens. Instead of reading 100+ frames individually, the skill tiles them into a single contact sheet image. The AI scans that overview, picks the frames that actually matter, and only reads *those* at full resolution. Same quality, fraction of the cost.

### Long videos don't get lost

A 60-minute video with 120 frames means one frame every 30 seconds — huge gaps where important things could happen. So videos over 12 minutes automatically split into 10-minute chunks, each with its own set of frames. Coverage stays tight (~1 frame every 7 seconds) no matter how long the video is.

For really long videos (5+ chunks), the skill gives you a heads-up about preview costs and offers a focus mode to zoom into just the parts you care about.

## Getting started

### What you'll need

- **Python 3.9+** and **Node.js** (for the Word doc builder)
- **ffmpeg**, **ffprobe**, **yt-dlp** — on macOS these auto-install via Homebrew; on Linux/Windows the skill prints the commands for you
- Optionally, a **Whisper API key** for transcribing videos that don't have captions:
  - [Groq](https://console.groq.com/keys) — faster and cheaper, recommended
  - [OpenAI](https://platform.openai.com/api-keys) — solid fallback

> No API key? No problem. The skill still works great on videos that have captions — you just won't get transcripts for ones that don't.

### Install

Drop this folder into your Claude Code skills directory, or install it as a plugin. The first time the skill runs, `setup.py` takes care of dependencies, the npm `docx` module, and scaffolding your config.

Your settings live at `~/.config/analyze-video/.env`. To add a Whisper key:

```bash
python3 scripts/setup.py --set-key groq <YOUR_KEY>
# or:
python3 scripts/setup.py --set-key openai <YOUR_KEY>
```

## Try it

Just talk naturally:

- *"Analyze this video and write me a report: https://youtu.be/abc"*
- *"Make a doc from these three videos with screenshots"*
- *"Just look at the 2:30–3:15 section of this clip"*
- *"Quick TL;DR with a few screenshots"* (triggers quick mode — fewer frames, faster turnaround)

The skill picks up on what you're asking and handles the details.

## What's in the box

```
Analyze-Video-Skill/
├── SKILL.md                     # How Claude uses this skill
├── CHANGELOG.md                 # What changed and when
├── LICENSE                      # GPL-3.0 — always open source
├── analyze-video.skill          # Pre-built bundle for claude.ai
├── commands/
│   └── analyze-video.md         # Slash-command definition
├── hooks/                       # Auto-setup on session start
├── scripts/
│   ├── process.py               # The main pipeline — one video at a time
│   ├── select_frames.py         # Smart frame picker (proportional + boundary-aware)
│   ├── download.py              # yt-dlp wrapper with error handling
│   ├── frames.py                # Frame extraction + contact sheet builder
│   ├── transcribe.py            # VTT caption parser + deduplicator
│   ├── whisper.py               # Groq/OpenAI Whisper client with auto-fallback
│   ├── env_utils.py             # Shared config reader
│   ├── setup.py                 # First-run installer + preflight checks
│   ├── build-docx.js            # JSON → Word doc renderer
│   └── build-skill.sh           # Packages everything into a .skill bundle
├── tests/                       # 48 tests covering parsing, math, and security
└── templates/
    └── caption_guide.md         # Writing style guide for frame captions
```

## Privacy & security

Everything runs locally on your machine. Here's exactly what goes where:

**Stays on your computer:**
- The video file, all extracted frames, contact sheets, and the final `.docx`
- Your config at `~/.config/analyze-video/.env` (locked to owner-only permissions)

**Sent to an API (only when needed):**
- Extracted audio → Groq or OpenAI Whisper, *only* when the video has no captions and you've set up a key. The video itself never leaves your machine.

**Never happens:**
- No video uploads to any service
- No platform logins, cookies, or account access
- No data stored anywhere except your output folder and config directory

## Contributing

Found a bug? Have an idea? PRs and issues are always welcome.

## License

[GPL-3.0](LICENSE) — this project will always be open source. Fork it, improve it, share it — just keep it open.
