#!/usr/bin/env python3
"""Host fingerprint helpers for setup/process host-consistency checks."""
from __future__ import annotations

import hashlib
import json
import platform
import socket
import time
from pathlib import Path


CONFIG_DIR = Path.home() / ".config" / "analyze-video"
SETUP_STATE_FILE = CONFIG_DIR / "setup_state.json"


def current_host_fingerprint() -> dict:
    data = {
        "platform": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "hostname": socket.gethostname(),
    }
    raw = "|".join(str(data[k]) for k in ("platform", "release", "machine", "hostname"))
    data["id"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return data


def read_setup_state(path: Path = SETUP_STATE_FILE) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def write_setup_state(path: Path = SETUP_STATE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "written_at": time.time(),
        "host": current_host_fingerprint(),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def host_matches(state: dict, current: dict | None = None) -> bool:
    if not state:
        return False
    current = current or current_host_fingerprint()
    recorded = (state.get("host") or {})
    return (
        recorded.get("platform") == current.get("platform")
        and recorded.get("machine") == current.get("machine")
        and recorded.get("hostname") == current.get("hostname")
    )
