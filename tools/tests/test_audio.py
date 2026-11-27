"""ffmpeg-backed WAV transcoding (skipped when ffmpeg is unavailable)."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from .support import make_wav, run_cli

FFMPEG = shutil.which("ffmpeg")


def make_aiff(wav: Path, aiff: Path) -> None:
    """Convert *wav* to AIFF with ffmpeg; skip the test when unsupported."""
    proc = subprocess.run(
        [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(wav), str(aiff)],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0 or not aiff.is_file():
        raise unittest.SkipTest("ffmpeg cannot encode AIFF: %s" % proc.stderr.strip())


@unittest.skipUnless(FFMPEG, "ffmpeg not available on PATH")
class WavTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_pack_wav_transcodes_and_rewrites_sfz(self):
        wav = make_wav(self.root / "samples" / "Loop.wav")
        aiff = self.root / "samples" / "Loop.aiff"
        make_aiff(wav, aiff)
        wav.unlink()
        (self.root / "piano.sfz").write_text(
            "<region>\nsample=samples/Loop.aiff\nkey=60\n", encoding="utf-8"
        )
        out = self.root / "out.zip"

        code, _, stderr = run_cli(
            "pack", "--dir", self.root, "--out", out, "--wav", "--validate"
        )
        self.assertEqual(code, 0, stderr)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertIn("samples/Loop.wav", names)
            self.assertNotIn("samples/Loop.aiff", names)
            self.assertIn("sample=samples/Loop.wav", zf.read("piano.sfz").decode("utf-8"))
            self.assertTrue(zf.read("samples/Loop.wav").startswith(b"RIFF"))

    def test_convert_wav_transcodes_copied_samples(self):
        wav = make_wav(self.root / "srcdir" / "Loop.wav")
        aiff = self.root / "srcdir" / "Loop.aiff"
        make_aiff(wav, aiff)
        map_path = self.root / "map.json"
        map_path.write_text(
            json.dumps(
                {"name": "LOOP", "regions": [{"sample": "Loop.aiff", "key": 60}]}
            ),
            encoding="utf-8",
        )
        patch = self.root / "patch.nnxt"
        patch.write_bytes(b"Reason NNXT patch")
        out = self.root / "converted"

        code, _, stderr = run_cli(
            "convert", "--input", patch, "--samples", self.root / "srcdir",
            "--out", out, "--map", map_path, "--wav",
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue((out / "samples" / "Loop.wav").is_file())
        self.assertFalse((out / "samples" / "Loop.aiff").exists())
        text = (out / "loop.sfz").read_text(encoding="utf-8")
        self.assertIn("sample=samples/Loop.wav", text)


if __name__ == "__main__":
    unittest.main()
