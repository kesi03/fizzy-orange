"""Kontakt conversion from a synthetic zlib-compressed K2_Container NKI."""

from __future__ import annotations

import json
import tempfile
import unittest
import zlib
from pathlib import Path

from .support import make_wav, run_cli

XML = """<?xml version="1.0"?>
<K2_Container index="0" name="(null)" type="single_program" version="0.50">
  <Programs>
    <K2_Program index="0" name="MY PIANO" version="0.80">
      <Groups>
        <K2_Group index="0" name="Group 1">
          <Zones>
            <Zone>
              <V name="rootKey" value="60"/>
              <V name="lowKey" value="48"/>
              <V name="highKey" value="72"/>
              <V name="lowVelo" value="0"/>
              <V name="highVelo" value="127"/>
              <V name="zoneVolume" value="1.304352"/>
              <V name="zonePan" value="-12"/>
              <V name="zoneTune" value="3"/>
              <V name="sampleFile" value="Piano_C3.wav"/>
              <V name="sampleStart" value="100"/>
              <V name="sampleEnd" value="105993"/>
              <V name="loopMode" value="1"/>
              <V name="loopStart" value="39914"/>
              <V name="loopEnd" value="54112"/>
            </Zone>
          </Zones>
        </K2_Group>
      </Groups>
    </K2_Program>
  </Programs>
</K2_Container>
"""


class KontaktTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_kontakt_zlib_zone_is_converted(self):
        patch = self.root / "piano.nki"
        patch.write_bytes(b"\x00" * 170 + zlib.compress(XML.encode("utf-8")))
        samples = self.root / "samples"
        make_wav(samples / "Piano_C3.wav")
        out = self.root / "converted"

        code, _, stderr = run_cli(
            "convert", "--input", patch, "--samples", samples, "--out", out
        )
        self.assertEqual(code, 0, stderr)
        sfz_path = out / "MY-PIANO.sfz"
        self.assertTrue(sfz_path.is_file())
        text = sfz_path.read_text(encoding="utf-8")
        self.assertIn("sample=samples/Piano_C3.wav", text)
        self.assertIn("key=60", text)
        self.assertIn("lokey=48", text)
        self.assertIn("hikey=72", text)
        self.assertIn("loop_mode=loop_sustain", text)
        self.assertIn("loop_start=39914", text)
        self.assertIn("offset=100", text)
        self.assertIn("pan=-12", text)
        self.assertIn("volume=2.3", text)
        self.assertTrue((out / "samples" / "Piano_C3.wav").is_file())
        manifest = json.loads((out / "fizzy.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["instruments"][0]["sfz"], "MY-PIANO.sfz")

    def test_native_container_error_suggests_map(self):
        patch = self.root / "modern.nki"
        patch.write_bytes(b"NI proprietary container" + b"\x00" * 512)
        code, _, stderr = run_cli(
            "convert", "--input", patch,
            "--samples", self.root / "samples", "--out", self.root / "converted",
        )
        self.assertEqual(code, 1)
        self.assertIn("modern.nki", stderr)
        self.assertIn("--map", stderr)


if __name__ == "__main__":
    unittest.main()
