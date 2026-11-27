"""Audio helpers: optional ffmpeg transcoding to 16-bit PCM WAV.

ffmpeg is deliberately not imported or required at module load time.  It is
looked up only when a command actually requests ``--wav``; if it is missing,
the user gets a clear, actionable error.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from . import ConversionError

# Audio extensions understood by the Editor (see docs/PROTOCOL.md).  aiff/aif
# are included because they are common in sampler patches and can be converted
# to WAV with ffmpeg.
AUDIO_SUFFIXES = frozenset(
    (".wav", ".mp3", ".flac", ".ogg", ".aiff", ".aif")
)
NON_WAV_SUFFIXES = frozenset(s for s in AUDIO_SUFFIXES if s != ".wav")


def is_audio(path) -> bool:
    """True when *path* has a sample extension recognised by the Editor."""
    return Path(str(path)).suffix.lower() in AUDIO_SUFFIXES


def needs_transcode(path) -> bool:
    """True when *path* is audio that is not already a WAV file."""
    return Path(str(path)).suffix.lower() in NON_WAV_SUFFIXES


def find_ffmpeg() -> str:
    """Return the ffmpeg executable path (``FFMPEG`` env var wins).

    Raises:
        ConversionError: when ffmpeg cannot be found on PATH.
    """
    exe = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not exe:
        raise ConversionError(
            "ffmpeg was not found on PATH; install ffmpeg (or set FFMPEG=/path/to/ffmpeg) "
            "to use --wav, or omit --wav to keep the original audio files"
        )
    return exe


def transcode_to_wav(src: Path, dst: Path) -> None:
    """Transcode *src* into a 16-bit PCM WAV at *dst*.

    The source sample rate is preserved, matching the Editor's upload contract.
    Any failure raises :class:`ConversionError` with ffmpeg's own error text.
    """
    src = Path(src)
    dst = Path(dst)
    if not src.is_file():
        raise ConversionError("cannot transcode missing audio file: %s" % src)
    dst.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        find_ffmpeg(),
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-vn",
        "-c:a",
        "pcm_s16le",
        "-f",
        "wav",
        str(dst),
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        raise ConversionError("could not run ffmpeg: %s" % exc) from exc
    if proc.returncode != 0 or not dst.is_file():
        detail = (proc.stderr or proc.stdout or "").strip()
        raise ConversionError(
            "ffmpeg failed for %s (exit %d)%s"
            % (src.name, proc.returncode, ": " + detail[:400] if detail else "")
        )
