# Changelog

All notable changes to `/analyze-video` are documented here.

## [0.1.0] — 2026-04-30

Initial release.

### Added
- `/analyze-video` slash command for Claude Code plugin mode.
- Plugin packaging: `.claude-plugin/`, `.codex-plugin/`, `commands/`.
- `scripts/build-docx.js` — reusable Node.js docx builder template with helpers for headings, body text, image embedding, and captions.
- `scripts/build-skill.sh` — builds `dist/analyze-video.skill` for claude.ai upload.
- SessionStart hook to verify Node.js availability.
- `.github/workflows/release.yml` — auto-builds and attaches `.skill` to GitHub releases on tag push.
- `.gitignore`, `.gitattributes`, `CHANGELOG.md`.
- Multi-surface install instructions in README.
