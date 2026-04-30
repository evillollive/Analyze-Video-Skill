# analyze-video

A [Cowork](https://claude.ai) skill that picks up where [/watch](https://github.com/bradautomates/claude-video) left off. Run `/watch` on one or more videos, then run `/analyze-video` to get a polished Word document with a detailed visual analysis, embedded still frames, and timestamped captions.

---

## What it does

`/watch` gives Claude eyes on a video: extracted frames and a timestamped transcript. `analyze-video` takes that material and turns it into a deliverable.

- Handles 1 to many videos in the same session
- Asks how many frames per video to include
- Selects frames intelligently across the full duration (not just the first N)
- Writes a detailed timestamp-based analysis with rich visual descriptions
- Embeds selected frames with captions directly in the document
- Exports as `.docx`, with a PDF option on request

---

## Requirements

- [Cowork](https://claude.ai) (or Claude Code with skill support)
- The [/watch skill](https://github.com/bradautomates/claude-video) installed and run first
- Node.js (for the docx builder, installed automatically if missing)

---

## Install

| Surface | Install |
|---------|---------|
| **Claude Code** | `/plugin marketplace add evillollive/Analyze-Video-Skill` then `/plugin install analyze-video@Analyze-Video-Skill` |
| **claude.ai** (web) | [Download `analyze-video.skill`](https://github.com/evillollive/Analyze-Video-Skill/releases/latest) → Settings → Capabilities → Skills → `+` |
| **Codex** | `git clone https://github.com/evillollive/Analyze-Video-Skill.git ~/.codex/skills/analyze-video` |
| **Manual / dev** | `git clone https://github.com/evillollive/Analyze-Video-Skill.git ~/.claude/skills/analyze-video` |

### Claude Code

```
/plugin marketplace add evillollive/Analyze-Video-Skill
/plugin install analyze-video@Analyze-Video-Skill
```

Update later with `/plugin update analyze-video@Analyze-Video-Skill`.

### claude.ai (web)

1. [Download `analyze-video.skill`](https://github.com/evillollive/Analyze-Video-Skill/releases/latest) from the latest release.
2. Go to Settings → Capabilities → Skills.
3. Click `+` and drop the file in.

### Codex

```bash
git clone https://github.com/evillollive/Analyze-Video-Skill.git ~/.codex/skills/analyze-video
```

### Manual (developer)

```bash
git clone https://github.com/evillollive/Analyze-Video-Skill.git ~/.claude/skills/analyze-video
```

---

## Usage

1. Run `/watch` on one or more videos:
   ```
   /watch https://youtu.be/your-video-here
   ```

2. Once `/watch` finishes, run:
   ```
   /analyze-video
   ```

3. Claude will ask:
   - How many frames per video to include
   - If you have multiple videos: one combined document or separate files

4. It builds the `.docx` and asks if you also want a PDF.

---

## Output

A Word document (`.docx`) containing:

- Title and video metadata
- Time-based sections with detailed visual descriptions
- Still frames embedded inline with descriptive captions
- A production notes section when multiple videos are analyzed

---

## How frame selection works

Rather than picking frames mechanically, the skill divides the video into N equal time segments and selects one frame from each. It then cross-references the transcript to shift selections toward segment boundaries where something visually meaningful is happening, and always tries to include the opening and closing of the video.

---

## Structure

```
.
├── SKILL.md                   # skill contract — loaded by all surfaces
├── scripts/
│   ├── build-docx.js          # Node.js docx builder template with helpers
│   └── build-skill.sh         # build dist/analyze-video.skill for claude.ai upload
├── commands/                  # slash command shim (Claude Code only)
├── hooks/                     # SessionStart Node.js check (Claude Code only)
├── .claude-plugin/            # plugin.json + marketplace.json (Claude Code)
├── .codex-plugin/             # codex packaging
└── .github/workflows/         # release.yml — auto-builds .skill on tag push
```

---

## Develop

```bash
# Build the claude.ai upload bundle:
bash scripts/build-skill.sh      # → dist/analyze-video.skill
```

Releasing: tag `vX.Y.Z`, push the tag. The workflow builds `dist/analyze-video.skill` and attaches it to the GitHub release.

See [CHANGELOG.md](CHANGELOG.md) for version history.

---

## Pair with

- [/watch](https://github.com/bradautomates/claude-video) — required prerequisite that downloads and processes the video

---

## License

Apache-2.0
