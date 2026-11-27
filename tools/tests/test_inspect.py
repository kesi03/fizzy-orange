"""inspect reports missing samples and package metadata."""

from __future__ import annotations

import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from sfzpkg.presets import FORMAT

from .support import run_cli


class InspectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def _package(self, sample: str) -> Path:
        manifest = {
            "format": FORMAT,
            "name": "TEST PIANO",
            "instruments": [{"name": "PIANO", "sfz": "piano.sfz"}],
            "presets": [],
        }
        sfz = "<region>\nsample=samples/%s\nkey=60\nunknown_opcode=1\n" % sample
        path = self.root / "package.zip"
        with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("fizzy.json", json.dumps(manifest))
            zf.writestr("piano.sfz", sfz)
        return path

    def test_inspect_reports_missing_sample(self):
        package = self._package("Missing.wav")
        code, stdout, stderr = run_cli("inspect", package)
        self.assertEqual(code, 0, stderr)
        self.assertIn("ERROR", stdout)
        self.assertIn("Missing.wav", stdout)
        self.assertIn("unknown_opcode", stdout)
        self.assertIn("stored", stdout)

    def test_inspect_json_has_structured_problems(self):
        package = self._package("Missing.wav")
        code, stdout, stderr = run_cli("inspect", package, "--json")
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertEqual(report["manifest"]["name"], "TEST PIANO")
        self.assertTrue(
            any(
                p["level"] == "error" and "Missing.wav" in p["message"]
                for p in report["problems"]
            )
        )
        self.assertTrue(
            any(
                p["level"] == "warning" and "unknown_opcode" in p["message"]
                for p in report["problems"]
            )
        )

    def test_valid_package_has_no_errors(self):
        package = self.root / "valid.zip"
        manifest = {
            "format": FORMAT,
            "name": "OK",
            "instruments": [{"name": "PIANO", "sfz": "piano.sfz"}],
            "presets": [],
        }
        with zipfile.ZipFile(package, "w", zipfile.ZIP_STORED) as zf:
            zf.writestr("fizzy.json", json.dumps(manifest))
            zf.writestr("piano.sfz", "<region>\nsample=samples/C4.wav\n")
            zf.writestr("samples/C4.wav", "fake wav")
        code, stdout, stderr = run_cli("inspect", package, "--json")
        self.assertEqual(code, 0, stderr)
        report = json.loads(stdout)
        self.assertFalse(any(p["level"] == "error" for p in report["problems"]))


if __name__ == "__main__":
    unittest.main()
