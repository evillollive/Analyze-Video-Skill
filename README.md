# 🎬 /analyze-video

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](LICENSE)
[![GitHub release](https://img.shields.io/github/v/release/evillollive/Analyze-Video-Skill)](https://github.com/evillollive/Analyze-Video-Skill/releases)

**Drop a video link. Get a beautifully formatted Word doc back, complete with screenshots, timestamps, and a written breakdown of everything that happens.**

This is a skill for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that watches videos so you don't have to (or helps you watch them better). It works with YouTube, Vimeo, TikTok, X, Twitch, local files... basically anything [yt-dlp](https://github.com/yt-dlp/yt-dlp) can handle.

## How it works

You paste a link. The skill does the rest:

1. **Downloads** the video and grabs any existing captions
2. **Pulls frames** at smart intervals (not too many, not too few)
3. **Builds a contact sheet** so the AI can see the whole video at a glance without burning through your context window
4. **Reads the transcript** (or creates one via Whisper if there are no captions)
5. **Picks the best frames** and writes a detailed, timestamped analysis
6. **Exports a polished `.docx`** with embedded screenshots, captions, and prose

One video or ten, it handles batches too.

## The clever bits

### Contact sheets save you money

Every frame the AI reads costs tokens. Instead of reading 100+ frames individually, the skill tiles them into a single contact sheet image. The AI scans that overview, picks the frames that actually matter, and only reads *those* at full resolution. Same quality, fraction of the cost.

### Unified workflow

`/watch` and `/analyze-video` are merged into one skill flow. You run one command, the skill handles setup, processing, frame selection, analysis, and docx output end to end.

### Built for long videos

Big videos used to time out and restart from zero. Now the skill picks up where it left off: extracted frames are reused when the source and settings haven't changed (pass `--force` to redo them), each extraction config gets its own directory so a resume never has to delete or clobber a prior run's frames (even in locked-down sandboxes), and a `status.json` plus a rolling `manifest_partial.json` record progress so an interrupted run is never a black box. A downloaded video is cached once per URL and reused across runs, so a focused rerun doesn't re-download the whole thing. Repeated end-card promos or static outros can be detected and trimmed with `--trim-static-outro`.

## Getting started

### What you'll need

- **Python 3.9+** and **Node.js** (for the Word doc builder)
- **ffmpeg**, **ffprobe**, **yt-dlp** (on macOS these auto-install via Homebrew; on Linux/Windows the skill prints the commands for you)
- Optionally, a **Whisper API key** for transcribing videos that don't have captions:
  - [Groq](https://console.groq.com/keys) (faster and cheaper, recommended)
  - [OpenAI](https://platform.openai.com/api-keys) (solid fallback)

> No API key? No problem. The skill still works on videos that have captions, and captionless videos fall back to frames-only analysis.

### Install

Drop this folder into your Claude Code skills directory, or install it as a plugin. The first time the skill runs, `setup.py` takes care of required local dependencies, the npm `docx` module, and scaffolding your config. Whisper keys are optional and are not required for setup to complete.

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
- *"Just look at the 2:30 to 3:15 section of this clip"*

The skill picks up on what you're asking and handles the details.

## What's in the box

```
Analyze-Video-Skill/
├── SKILL.md                     # How Claude uses this skill
├── CHANGELOG.md                 # What changed and when
├── LICENSE                      # AGPL-3.0, always open source
├── analyze-video.skill          # Pre-built bundle for claude.ai
├── commands/
│   └── analyze-video.md         # Slash-command definition
├── hooks/                       # Auto-setup on session start
├── scripts/
│   ├── process.py               # Main pipeline entry point (download, frames, transcript)
│   ├── download.py              # yt-dlp wrapper with error handling
│   ├── frames.py                # Frame extraction + contact sheet builder
│   ├── transcribe.py            # VTT caption parser + deduplicator
│   ├── whisper.py               # Groq/OpenAI Whisper client
│   ├── env_utils.py             # Shared config reader
│   ├── setup.py                 # First-run installer + preflight checks
│   ├── build-docx.js            # Word document renderer
│   ├── select_frames.py         # Frame selection helper
│   └── build-skill.sh           # Packages everything into a .skill bundle
├── tests/                       # tests covering parsing, math, setup, and security
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
- No platform logins, cookies, or account access by default
- No data stored anywhere except your output folder and config directory

### When a site blocks the download

Some sites block unauthenticated download tools with login prompts, bot checks, age gates, members-only access, rate limits, or regional restrictions. The skill now classifies those failures and gives a specific next step instead of repeatedly retrying.

If you can already watch the video in your own browser and want the skill to use that authorized session, retry with one of yt-dlp's cookie options:

```bash
python3 scripts/process.py --source "<url>" --out-dir /tmp/video \
  --cookies-from-browser safari
```

or:

```bash
python3 scripts/process.py --source "<url>" --out-dir /tmp/video \
  --cookies /path/to/cookies.txt
```

The skill should not spoof watch sessions, forge tokens, or automate hidden browser playback to bypass bot detection. If browser cookies are not appropriate, download or screen-record the video yourself and pass the local file path.

## Contributing

Found a bug? Have an idea? PRs and issues are always welcome.

## License

[AGPL-3.0](LICENSE). This project will always be open source. Fork it, improve it, share it. Just keep it open.
