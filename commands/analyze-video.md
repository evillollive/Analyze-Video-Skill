---
description: Analyze one or more videos (URLs or local files) and produce a Word document with embedded frames and a timestamp-based written analysis.
allowed-tools: [Bash, Read, Write, AskUserQuestion]
---

Invoke the `analyze-video` skill (defined in SKILL.md) with the user's arguments: $ARGUMENTS

Follow the full pipeline in SKILL.md: setup preflight → parse the request (sources, optional focus, quick intent) → ask the user only when needed → run `process.py` per video → preview each `manifest_lite.json` and contact sheet(s) → call `select_frames.py` to pick frames → Read selected frames → write the analysis → emit the docx spec → run `build-docx.js` → validate and deliver → offer PDF + cleanup.
