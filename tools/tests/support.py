"""Shared helpers for sfzpkg tests (stdlib only)."""

from __future__ import annotations

import wave
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path

from sfzpkg.cli import main


def run_cli(*argv) -> tuple:
    """Run the CLI in-process; return ``(exit_code, stdout, stderr)``."""
    out, err = StringIO(), StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main([str(arg) for arg in argv])
    return code, out.getvalue(), err.getvalue()


def make_wav(path: Path, frames: int = 512, rate: int = 22050) -> Path:
    """Write a tiny valid mono 16-bit WAV for tests."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * frames)
    return path
