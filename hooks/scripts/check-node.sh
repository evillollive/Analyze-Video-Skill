#!/usr/bin/env bash
# SessionStart hook for /analyze-video.
#
# Intentionally silent: the skill's `setup.py --check` (run on first invocation)
# covers all real preflight (ffmpeg, yt-dlp, node, npm, docx module, API key).
# Printing anything here would spam every session of every project, even ones
# that never invoke /analyze-video.
exit 0
