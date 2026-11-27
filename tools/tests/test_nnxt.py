"""NNXT conversion via --map and via the best-effort binary scanner."""

from __future__ import annotations

import json
import struct
import tempfile
import unittest
from pathlib import Path

from .support import make_wav, run_cli

MAP = {
    "name": "MAPPED BASS",
    "regions": [
        {
            "sample": "Bass_C2.wav",
            "key": 36,
            "lokey": 24,
            "hikey": 48,
            "lovel": 1,
            "hivel": 100,
            "volume_db": -3.5,
            "pan": 10,
            "loop_mode": "loop_continuous",
            "loop_start": 100,
            "loop_end": 1000,
        }
    ],
}


class NnxtTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.samples = self.root / "samples"
        make_wav(self.samples / "Bass_C2.wav")
        make_wav(self.samples / "Piano_C4.wav")

    def tearDown(self):
        self.temp.cleanup()

    def test_map_conversion(self):
        map_path = self.root / "zones.json"
        map_path.write_text(json.dumps(MAP), encoding="utf-8")
        out = self.root / "converted"
        patch = self.root / "patch.nnxt"
        patch.write_bytes(b"proprietary reason patch")

        code, _, stderr = run_cli(
            "convert", "--input", patch, "--samples", self.samples,
            "--out", out, "--map", map_path, "--name", "bass",
        )
        self.assertEqual(code, 0, stderr)
        text = (out / "bass.sfz").read_text(encoding="utf-8")
        self.assertIn("sample=samples/Bass_C2.wav", text)
        self.assertIn("key=36", text)
        self.assertIn("lokey=24", text)
        self.assertIn("volume=-3.5", text)
        self.assertIn("loop_mode=loop_continuous", text)
        manifest = json.loads((out / "fizzy.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], "bass")

    def test_binary_scanner_finds_sample_and_key_fields(self):
        patch = self.root / "scan.nnxt"
        patch.write_bytes(
            b"NNXT\x00\x00\x00\x00"
            + b"Piano_C4.wav\x00"
            + struct.pack("<IIIII", 60, 48, 72, 0, 127)
        )
        out = self.root / "scanned"
        code, _, stderr = run_cli(
            "convert", "--input", patch, "--samples", self.samples, "--out", out
        )
        self.assertEqual(code, 0, stderr)
        text = (out / "scan.sfz").read_text(encoding="utf-8")
        self.assertIn("sample=samples/Piano_C4.wav", text)
        self.assertIn("key=60", text)
        self.assertIn("lokey=48", text)
        self.assertIn("hikey=72", text)
        self.assertIn("best-effort", stderr)

    def test_unreadable_patch_reports_map_hint(self):
        patch = self.root / "empty.nnxt"
        patch.write_bytes(b"\x00" * 128)
        code, _, stderr = run_cli(
            "convert", "--input", patch, "--samples", self.samples,
            "--out", self.root / "empty-out",
        )
        self.assertEqual(code, 1)
        self.assertIn("--map", stderr)


if __name__ == "__main__":
    unittest.main()
