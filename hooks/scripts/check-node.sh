#!/usr/bin/env bash
# SessionStart hook for /analyze-video — checks that Node.js is available.
# Silent when everything is ready to avoid spam.
set -euo pipefail

HAS_NODE=""
command -v node >/dev/null 2>&1 && HAS_NODE="yes"

if [[ -z "$HAS_NODE" ]]; then
  echo "/analyze-video: needs Node.js to build .docx files. Install Node.js (https://nodejs.org) before running /analyze-video."
  exit 0
fi

# Node is available — silent success.
exit 0
