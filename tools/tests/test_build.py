"""The build convenience command converts and packs in one step."""

from __future__ import annotations

import json
import tempfile
import unittest
import zlib
import zipfile
from pathlib import Path

from sfzpkg.presets import FORMAT

from .support import make_wav, run_cli
from .test_kontakt import XML


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_build_creates_valid_zip(self):
        patch = self.root / "piano.nki"
        patch.write_bytes(b"\x00" * 170 + zlib.compress(XML.encode("utf-8")))
        samples = self.root / "samples"
        make_wav(samples / "Piano_C3.wav")
        out = self.root / "piano.zip"

        code, stdout, stderr = run_cli(
            "build", "--input", patch, "--samples", samples,
            "--out", out, "--name", "PIANO",
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(out.is_file())
        self.assertIn(str(out), stdout)
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertIn("fizzy.json", names)
            self.assertIn("PIANO.sfz", names)
            self.assertIn("samples/Piano_C3.wav", names)
            manifest = json.loads(zf.read("fizzy.json"))
            self.assertEqual(manifest["format"], FORMAT)
            self.assertEqual(manifest["name"], "PIANO")


if __name__ == "__main__":
    unittest.main()
