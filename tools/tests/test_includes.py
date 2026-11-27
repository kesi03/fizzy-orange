"""#include expansion and cycle detection."""

from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from sfzpkg import ConversionError, sfz

from .support import make_wav, run_cli


class IncludeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def test_recursive_includes_are_expanded(self):
        (self.root / "parent.sfz").write_text(
            '#include "child.sfz"\n<region>\nsample=samples/B.wav\n', encoding="utf-8"
        )
        (self.root / "child.sfz").write_text(
            '#include "grandchild.sfz"\n<region>\nsample=samples/A.wav\n', encoding="utf-8"
        )
        (self.root / "grandchild.sfz").write_text(
            '<region>\nsample=samples/C.wav\n', encoding="utf-8"
        )
        for name in ("A.wav", "B.wav", "C.wav"):
            make_wav(self.root / "samples" / name)

        out = self.root / "out.zip"
        code, _, stderr = run_cli(
            "pack", "--dir", self.root, "--sfz", self.root / "parent.sfz",
            "--out", out, "--inline-includes", "--validate",
        )
        self.assertEqual(code, 0, stderr)
        with zipfile.ZipFile(out) as zf:
            text = zf.read("parent.sfz").decode("utf-8")
        self.assertNotIn("#include", text)
        for name in ("A.wav", "B.wav", "C.wav"):
            self.assertIn("sample=samples/%s" % name, text)

    def test_include_cycle_is_detected(self):
        (self.root / "a.sfz").write_text('#include "b.sfz"\n', encoding="utf-8")
        (self.root / "b.sfz").write_text('#include "a.sfz"\n', encoding="utf-8")
        with self.assertRaises(ConversionError) as ctx:
            sfz.inline_includes(
                (self.root / "a.sfz").read_text(encoding="utf-8"), self.root
            )
        self.assertIn("cycle", str(ctx.exception))

        code, _, stderr = run_cli(
            "pack", "--dir", self.root, "--out", self.root / "out.zip",
            "--inline-includes", "--sfz", self.root / "a.sfz",
        )
        self.assertEqual(code, 1)
        self.assertIn("cycle", stderr)


if __name__ == "__main__":
    unittest.main()
