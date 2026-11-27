"""Pack tests: stored ZIP, generated manifest, validation."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from sfzpkg import archive
from sfzpkg.presets import FORMAT

from .support import make_wav, run_cli

SFZ = """<global>
<region>
sample=samples/Piano_C4.wav
key=60
loop_mode=no_loop
"""


class PackTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "piano.sfz").write_text(SFZ, encoding="utf-8")
        make_wav(self.root / "samples" / "Piano_C4.wav")

    def tearDown(self):
        self.temp.cleanup()

    def test_pack_creates_stored_zip_with_manifest_and_samples(self):
        out = self.root / "out.zip"
        code, stdout, stderr = run_cli(
            "pack", "--dir", self.root, "--out", out,
            "--name", "FIZZY PIANO", "--validate",
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(out.is_file())
        with zipfile.ZipFile(out) as zf:
            names = zf.namelist()
            self.assertIn("fizzy.json", names)
            self.assertIn("piano.sfz", names)
            self.assertIn("samples/Piano_C4.wav", names)
            self.assertEqual(zf.getinfo("piano.sfz").compress_type, zipfile.ZIP_STORED)
            self.assertEqual(zf.getinfo("samples/Piano_C4.wav").compress_type, zipfile.ZIP_STORED)
            manifest = json.loads(zf.read("fizzy.json"))
            self.assertEqual(manifest["format"], FORMAT)
            self.assertEqual(manifest["name"], "FIZZY PIANO")
            self.assertEqual(manifest["instruments"][0]["sfz"], "piano.sfz")
            self.assertEqual(manifest["presets"], [])
        with zipfile.ZipFile(out) as zf:
            payloads = {name: zf.read(name) for name in names}
        self.assertFalse(
            [p for p in archive.validate_package(payloads) if p.level == "error"]
        )

    def test_pack_deflate_is_opt_in(self):
        out = self.root / "out.zip"
        code, _, stderr = run_cli(
            "pack", "--dir", self.root, "--out", out, "--deflate"
        )
        self.assertEqual(code, 0, stderr)
        with zipfile.ZipFile(out) as zf:
            self.assertEqual(zf.getinfo("piano.sfz").compress_type, zipfile.ZIP_DEFLATED)

    def test_pack_validate_reports_missing_sample(self):
        (self.root / "piano.sfz").write_text(
            SFZ.replace("Piano_C4.wav", "Missing.wav"), encoding="utf-8"
        )
        out = self.root / "out.zip"
        code, _, stderr = run_cli(
            "pack", "--dir", self.root, "--out", out, "--validate"
        )
        self.assertEqual(code, 1)
        self.assertIn("missing sample", stderr)
        self.assertIn("Missing.wav", stderr)
        self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
