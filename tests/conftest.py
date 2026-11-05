"""Shared test setup."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import ytdlp  # noqa: E402


@pytest.fixture(autouse=True)
def _no_real_ytdlp_updates(monkeypatch):
    """Never let a test update the machine's yt-dlp; tests opt back in explicitly."""
    monkeypatch.setenv(ytdlp.NO_UPDATE_ENV, "1")
    ytdlp._reset_for_tests()
    yield
    ytdlp._reset_for_tests()
