# Sharing /analyze-video

Use this page as the launch checklist and copy bank for sharing `/analyze-video` broadly.

## Positioning

**One-liner:** Paste a video link and get a timestamped Word report with screenshots.

**Short pitch:** `/analyze-video` is a Claude Code skill that turns YouTube, Vimeo, TikTok, X, Twitch, or local videos into polished `.docx` reports. It downloads or resolves the video locally, extracts frames, builds token-efficient contact sheets, uses captions or optional Whisper, and writes a concrete timestamped analysis with embedded screenshots.

**Best audience:**
- researchers summarizing long videos
- teams documenting demos, webinars, talks, or product walkthroughs
- creators turning videos into written deliverables
- AI-agent users looking for a practical multimodal workflow
- Claude Code users looking for skills/plugins

## Launch checklist

### Repository

- [ ] Keep the README opening concise: one-line value prop, preview image, install link.
- [ ] Keep the latest `.skill` artifact attached to the newest GitHub release.
- [ ] Add or confirm GitHub topics:
  - `claude-code`
  - `claude-skill`
  - `video-analysis`
  - `youtube-analysis`
  - `yt-dlp`
  - `whisper`
  - `docx`
  - `ai-agents`
  - `multimodal`
  - `ffmpeg`
- [ ] Add a real demo recording when available. The README currently uses a lightweight SVG preview so the repo stays small.
- [ ] Add a sample generated `.docx` or PDF as a release asset when you have a public-domain or self-owned demo video.

### Demo assets

Minimum viable demo:

1. Show a video URL/local path.
2. Show `process.py` producing `manifest_lite.json`.
3. Show a contact sheet.
4. Show selected frames.
5. Show the generated Word report.

Suggested demo script:

```text
I paste a video link into Claude Code and ask for a report.
The skill downloads the video locally, grabs captions or uses Whisper, extracts frames, and creates a contact sheet.
Claude scans the contact sheet instead of reading every frame, picks the most useful screenshots, and writes a timestamped analysis.
The final output is a Word document with screenshots, captions, and concrete observations.
```

Recording tips:

- Keep it under 90 seconds.
- Use a video you own or a public-domain sample.
- Blur or avoid API keys, local usernames, private URLs, and cookies.
- Show the final `.docx` first or last; it is the payoff.

### Places to share

- GitHub topics and README
- Claude Code / Claude skills communities
- Awesome Claude Code or AI-agent lists
- Hacker News "Show HN"
- LinkedIn/X demo thread
- Reddit communities where it is relevant and allowed:
  - r/ClaudeAI
  - r/LocalLLaMA
  - r/youtubedl
  - r/artificial
- Product Hunt, if there is a polished landing page or demo video

## Copy/paste posts

### Short social post

```text
I built /analyze-video, a Claude Code skill that turns a video link into a timestamped Word report with screenshots.

It uses yt-dlp + ffmpeg locally, scans contact sheets to save tokens, uses captions or optional Whisper, and exports a polished .docx.

Repo + release:
https://github.com/evillollive/Analyze-Video-Skill
```

### Technical post

```text
New Claude Code skill: /analyze-video

Paste a YouTube/Vimeo/TikTok/X/Twitch/local video and get a Word report with:
- selected screenshots
- timestamped sections
- transcript-aware analysis when captions or Whisper are available
- contact sheets to avoid reading every frame
- local-first processing

The interesting bit is token control: the skill tiles extracted frames into contact sheets, has the model inspect those cheaply, then reads only the selected full-res frames for the final report.

https://github.com/evillollive/Analyze-Video-Skill
```

### Show HN draft

```text
Show HN: A Claude Code skill that turns videos into Word reports

I built /analyze-video, a Claude Code skill for producing timestamped .docx reports from videos. It uses yt-dlp and ffmpeg locally, pulls captions when available, optionally falls back to Whisper, extracts frames, builds contact sheets to reduce image-token cost, and embeds selected screenshots in a Word document.

The goal is not just "summarize a video"; it is to create a useful written deliverable with visual evidence, timestamps, and frame captions.

Repo: https://github.com/evillollive/Analyze-Video-Skill
```

## Trust and safety language

Use this wording when people ask about blocked videos or privacy:

```text
The skill processes video locally. Source video files and frames stay on your machine. Audio is sent to Groq/OpenAI only if captions are missing and you configure a Whisper key.

For blocked sites, the supported path is user-authorized access: if you can watch the video in your own browser, yt-dlp can optionally use your browser cookies with your explicit consent. The skill does not spoof watch sessions, forge tokens, or automate hidden playback to bypass bot detection.
```

## Follow-up backlog

- [ ] Record and add a real short demo video.
- [ ] Add a sample generated `.docx` or PDF to the next release.
- [ ] Submit PRs to relevant "awesome" lists.
- [ ] Consider a small landing page if the project gets traction.
- [ ] Explore a native GitHub Copilot App wrapper or canvas for source input, progress, frame selection, and docx download.
