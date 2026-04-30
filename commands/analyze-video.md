---
description: After /watch processes one or more videos, write a detailed visual analysis with embedded still frames and export it as a Word document (.docx).
allowed-tools: [Bash, Read, Write, AskUserQuestion]
---

Invoke the `analyze-video` skill (defined in SKILL.md) with the user's arguments: $ARGUMENTS

Follow the skill's full pipeline: inventory /watch outputs in context → ask user for frame count and document preferences → copy frames → select frames intelligently → Read frames → write analysis → build .docx → validate and deliver. If /watch has not been run yet, tell the user to run it first.
