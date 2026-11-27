#!/usr/bin/env python3
"""Give every bundled example package complete play coverage.

Melodic packages get full MIDI-keyboard coverage: each SFZ velocity layer is
rewritten so MIDI 0..127 has no gaps, while the original samples, velocity
bands, loops and envelopes are kept. A note at a sample's own
``pitch_keycenter`` still plays that sample at its original pitch.

Percussion is better on the pads, so ``sso-percussion.zip`` is treated
differently. Its ``fizzy.json`` gets ``"pads": true`` and each drum sample is
mapped to one pad note (36..50), so hitting a pad plays that sound at its
native pitch. Pad 16 remains all-notes-off and preset selection moves to F2/A-B.

Usage::

    python scripts/full_keyboard.py            # rewrite the example ZIPs
    python scripts/full_keyboard.py --check    # only verify coverage
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

LOW_NOTE = 0
HIGH_NOTE = 127
PAD_BASE = 36
PAD_LAST = 50
COVERAGE_MARKER = "// FIZZY ORANGE: full MIDI keyboard coverage (0-127)"
PAD_MARKER = "// FIZZY ORANGE: pads 1-15 play notes 36-50"
_NOTE_IN_NAME = re.compile(r"_n(\d{2,3})_")

Block = Dict[str, object]


def _split_blocks(lines: Sequence[str]) -> List[Block]:
    """Split SFZ text into a preamble and header blocks."""
    blocks: List[Block] = []
    current: Optional[Block] = None
    for line in lines:
        if line.lstrip().startswith("<"):
            if current is not None:
                blocks.append(current)
            current = {"header": line.strip(), "lines": []}
        else:
            if current is None:
                current = {"header": None, "lines": []}
            current["lines"].append(line)
    if current is not None:
        blocks.append(current)
    return blocks


def _opcodes(lines: Sequence[str]) -> List[Tuple[str, str, str]]:
    """Return ``(key, value, original_line)`` for simple ``key=value`` lines."""
    out: List[Tuple[str, str, str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("//") or stripped.startswith(";"):
            continue
        if "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        out.append((key.strip().lower(), value.strip(), line))
    return out


def _as_int(value: str, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _region_info(lines: Sequence[str]) -> Tuple[str, int, int, int]:
    """Return ``(sample, root, lovel, hivel)`` for one region block."""
    ops = {key: value for key, value, _ in _opcodes(lines)}
    sample = ops.get("sample", "")
    root_s = ops.get("pitch_keycenter", ops.get("key"))
    if root_s is None and "lokey" in ops and ops["lokey"] == ops.get("hikey"):
        root_s = ops["lokey"]
    if root_s is None:
        match = _NOTE_IN_NAME.search(sample)
        root_s = match.group(1) if match else "60"
    root = _as_int(root_s, 60)
    lovel = _as_int(ops.get("lovel", "1"), 1)
    hivel = _as_int(ops.get("hivel", "127"), 127)
    return sample, max(0, min(127, root)), lovel, hivel


def _ranges_for_roots(roots: Iterable[int]) -> Dict[int, Tuple[int, int]]:
    """Partition 0..127 into contiguous ranges centred on the sampled roots."""
    ordered = sorted(set(roots))
    if not ordered:
        return {}
    result: Dict[int, Tuple[int, int]] = {}
    for i, root in enumerate(ordered):
        lo = LOW_NOTE if i == 0 else (ordered[i - 1] + root) // 2 + 1
        hi = HIGH_NOTE if i + 1 == len(ordered) else (root + ordered[i + 1]) // 2
        result[root] = (lo, hi)
    return result


def _rewrite_region(
    lines: Sequence[str], lo: int, hi: int, root: int, center: Optional[int] = None
) -> List[str]:
    """Replace key bounds without disturbing the rest of a region block.

    ``center`` is only given for pad packages, where the sample must be
    retrigered at the pad note's own pitch instead of its original root.
    """
    center = root if center is None else center
    out: List[str] = []
    wrote_lo = wrote_hi = wrote_center = False
    for line in lines:
        stripped = line.strip()
        if "=" not in stripped or stripped.startswith("//") or stripped.startswith(";"):
            out.append(line)
            continue
        key, value = stripped.split("=", 1)
        key = key.strip().lower()
        if key == "key":
            # `key` overrides lokey/hikey/pitch_keycenter in the firmware, so
            # replace it with explicit opcodes we control below.
            out.append("pitch_keycenter=%d" % center)
            continue
        if key == "lokey":
            if not wrote_lo:
                out.append("lokey=%d" % lo)
                wrote_lo = True
            continue
        if key == "hikey":
            if not wrote_hi:
                out.append("hikey=%d" % hi)
                wrote_hi = True
            continue
        if key == "pitch_keycenter" and center != root:
            if not wrote_center:
                out.append("pitch_keycenter=%d" % center)
                wrote_center = True
            continue
        out.append(line)
    insert_at = 0
    for i, line in enumerate(out):
        if line.strip().lower().startswith("sample="):
            insert_at = i + 1
            break
    additions = []
    if not wrote_lo:
        additions.append("lokey=%d" % lo)
    if not wrote_hi:
        additions.append("hikey=%d" % hi)
    if center != root and not wrote_center:
        additions.append("pitch_keycenter=%d" % center)
    if additions:
        out[insert_at:insert_at] = additions
    return out


def expand_sfz(text: str) -> str:
    """Return SFZ text with every velocity layer spanning MIDI 0..127."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = _split_blocks(lines)

    info: Dict[int, Tuple[str, int, int, int]] = {}
    by_velocity: Dict[Tuple[int, int], List[int]] = {}
    for index, block in enumerate(blocks):
        if block["header"] != "<region>":
            continue
        _, root, lovel, hivel = _region_info(block["lines"])  # type: ignore[arg-type]
        info[index] = (_, root, lovel, hivel)
        by_velocity.setdefault((lovel, hivel), []).append(index)

    ranges = {
        velocity: _ranges_for_roots(info[i][1] for i in indices)
        for velocity, indices in by_velocity.items()
    }

    rewritten: List[str] = []
    for index, block in enumerate(blocks):
        header = block["header"]
        body = [str(line) for line in block["lines"]]  # type: ignore[list-item]
        if header == "<region>" and index in info:
            _, root, lovel, hivel = info[index]
            lo, hi = ranges[(lovel, hivel)][root]
            body = _rewrite_region(body, lo, hi, root)
        if header is None:
            if COVERAGE_MARKER not in body:
                body.insert(0, COVERAGE_MARKER)
        rewritten.extend([header] if header else [])
        rewritten.extend(body)
    return "\n".join(rewritten).rstrip() + "\n"


def pad_map_sfz(text: str) -> str:
    """Map one drum sample per pad note (36..50) for percussion packages."""
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks = _split_blocks(lines)

    info: Dict[int, Tuple[str, int, int, int]] = {}
    by_velocity: Dict[Tuple[int, int], List[int]] = {}
    for index, block in enumerate(blocks):
        if block["header"] != "<region>":
            continue
        sample, root, lovel, hivel = _region_info(block["lines"])  # type: ignore[arg-type]
        if not sample:
            continue
        info[index] = (sample, root, lovel, hivel)
        by_velocity.setdefault((lovel, hivel), []).append(index)

    pad_notes: Dict[int, int] = {}
    for velocity, indices in by_velocity.items():
        roots = sorted(set(info[i][1] for i in indices))
        if len(roots) > PAD_LAST - PAD_BASE + 1:
            raise ValueError("%s: more than 15 drum sounds for one velocity layer" % velocity)
        for i, root in enumerate(roots):
            pad_notes[(velocity, root)] = PAD_BASE + i

    rewritten: List[str] = []
    for index, block in enumerate(blocks):
        header = block["header"]
        body = [str(line) for line in block["lines"]]  # type: ignore[list-item]
        if header == "<region>" and index in info:
            _, root, lovel, hivel = info[index]
            note = pad_notes[(lovel, hivel), root]
            body = _rewrite_region(body, note, note, root, center=note)
        if header is None:
            if COVERAGE_MARKER in body:
                body.remove(COVERAGE_MARKER)
            if PAD_MARKER not in body:
                body.insert(0, PAD_MARKER)
        rewritten.extend([header] if header else [])
        rewritten.extend(body)
    return "\n".join(rewritten).rstrip() + "\n"


def _pad_regions(text: str) -> List[Tuple[int, int]]:
    """Return ``(note, pitch_keycenter)`` for each region of a pad package."""
    blocks = _split_blocks(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    out: List[Tuple[int, int]] = []
    for block in blocks:
        if block["header"] != "<region>":
            continue
        ops = {key: value for key, value, _ in _opcodes(block["lines"])}  # type: ignore[arg-type]
        if "sample" not in ops:
            continue
        lo = _as_int(ops.get("lokey", "-1"), -1)
        hi = _as_int(ops.get("hikey", "-1"), -1)
        center = _as_int(ops.get("pitch_keycenter", ops.get("key", "-1")), -1)
        out.append((lo, center if hi == lo else -1))
    return out


def _sfz_coverage(text: str) -> Tuple[set, Dict[Tuple[int, int], set]]:
    blocks = _split_blocks(text.replace("\r\n", "\n").replace("\r", "\n").split("\n"))
    all_notes: set = set()
    bands: Dict[Tuple[int, int], set] = {}
    for block in blocks:
        if block["header"] != "<region>":
            continue
        ops = {key: value for key, value, _ in _opcodes(block["lines"])}  # type: ignore[arg-type]
        if "sample" not in ops:
            continue
        lo = _as_int(ops.get("lokey", "0"), 0)
        hi = _as_int(ops.get("hikey", "127"), 127)
        notes = set(range(max(0, lo), min(127, hi) + 1))
        all_notes.update(notes)
        band = (_as_int(ops.get("lovel", "1"), 1), _as_int(ops.get("hivel", "127"), 127))
        bands.setdefault(band, set()).update(notes)
    return all_notes, bands


def _manifest_name(zf: zipfile.ZipFile) -> Optional[str]:
    for name in zf.namelist():
        if name.casefold() == "fizzy.json":
            return name
    return None


def _manifest_doc(zf: zipfile.ZipFile) -> Optional[dict]:
    name = _manifest_name(zf)
    if name is None:
        return None
    doc = json.loads(zf.read(name).decode("utf-8-sig"))
    return doc if isinstance(doc, dict) else None


def _wants_pads(path: Path, doc: Optional[dict]) -> bool:
    if not doc:
        return False
    return bool(doc.get("pads")) or "percussion" in path.name.casefold() \
        or "percussion" in str(doc.get("name") or "").casefold()


def check_zip(path: Path) -> List[str]:
    """Return a list of play-coverage problems in one example ZIP."""
    problems: List[str] = []
    with zipfile.ZipFile(path) as zf:
        doc = _manifest_doc(zf)
        pad_mode = _wants_pads(path, doc)
        if pad_mode and doc is not None and not doc.get("pads"):
            problems.append("%s: fizzy.json is missing \"pads\": true" % path.name)
        for name in zf.namelist():
            if not name.lower().endswith(".sfz"):
                continue
            text = zf.read(name).decode("utf-8", "replace")
            if pad_mode:
                regions = _pad_regions(text)
                bad = [(note, center) for note, center in regions
                       if note < PAD_BASE or note > PAD_LAST or center != note]
                if bad:
                    problems.append("%s: %s has %d region(s) outside pad notes 36-50" % (
                        path.name, name, len(bad)))
                used = sorted(set(note for note, _ in regions))
                expected = list(range(PAD_BASE, PAD_BASE + len(used)))
                if used and used != expected:
                    problems.append("%s: %s should use pads 1-%d, got %s" % (
                        path.name, name, len(used), [n - PAD_BASE + 1 for n in used]))
                continue
            notes, bands = _sfz_coverage(text)
            missing = sorted(set(range(LOW_NOTE, HIGH_NOTE + 1)) - notes)
            if missing:
                problems.append("%s: %s misses %d notes (first: %s)" % (
                    path.name, name, len(missing), missing[:8]))
            for band, band_notes in sorted(bands.items()):
                band_missing = set(range(LOW_NOTE, HIGH_NOTE + 1)) - band_notes
                if band_missing:
                    problems.append("%s: %s velocity %d-%d misses %d notes" % (
                        path.name, name, band[0], band[1], len(band_missing)))
    return problems


def _example_zips(root: Path) -> List[Path]:
    return sorted(list((root / "examples").glob("*.zip")) + list((root / "examples" / "sso").glob("*.zip")))


def rewrite_zip(path: Path) -> int:
    """Rewrite the SFZ files in one ZIP. Returns the number of files changed."""
    with zipfile.ZipFile(path) as zf:
        entries = [(info, zf.read(info.filename)) for info in zf.infolist()]
        doc = _manifest_doc(zf)
    pad_mode = _wants_pads(path, doc)
    changed = 0
    new_entries = []
    for info, data in entries:
        if info.filename.lower().endswith(".sfz"):
            text = data.decode("utf-8")
            rewritten = pad_map_sfz(text) if pad_mode else expand_sfz(text)
            if rewritten != text:
                data = rewritten.encode("utf-8")
                changed += 1
        elif _manifest_name_from_entries(entries) == info.filename and pad_mode and doc is not None:
            if not doc.get("pads"):
                doc["pads"] = True
                data = (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")
                changed += 1
        new_entries.append((info, data))
    if changed:
        tmp = path.with_suffix(path.suffix + ".tmp")
        with zipfile.ZipFile(tmp, "w") as zf:
            for info, data in new_entries:
                zf.writestr(info, data)
        os.replace(tmp, path)
    return changed


def _manifest_name_from_entries(entries: Sequence[Tuple[object, bytes]]) -> Optional[str]:
    for info, _ in entries:
        name = getattr(info, "filename", "")
        if str(name).casefold() == "fizzy.json":
            return str(name)
    return None


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="only verify keyboard coverage")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent.parent,
                        help="repository root (default: parent of scripts/)")
    args = parser.parse_args(argv)

    zips = _example_zips(args.root)
    if not zips:
        print("no example ZIPs found under %s" % args.root, file=sys.stderr)
        return 2

    problems: List[str] = []
    for path in zips:
        if not args.check:
            count = rewrite_zip(path)
            print("%s: updated %d SFZ file(s)" % (path.relative_to(args.root), count))
        problems.extend(check_zip(path))

    if problems:
        for problem in problems:
            print("ERROR " + problem, file=sys.stderr)
        return 1
    pad_zips = []
    for path in zips:
        with zipfile.ZipFile(path) as zf:
            if _wants_pads(path, _manifest_doc(zf)):
                pad_zips.append(path.name)
    print("OK: %d melodic ZIP(s) cover MIDI %d-%d; pad ZIP(s): %s" % (
        len(zips) - len(pad_zips), LOW_NOTE, HIGH_NOTE,
        ", ".join(pad_zips) or "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
