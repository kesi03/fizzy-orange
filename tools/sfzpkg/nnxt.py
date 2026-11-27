"""Reason NNXT / .pch reader.

Reason's patch format is proprietary and has no public sample-zone schema.
This module therefore does two things:

1. always accept an explicit ``--map`` JSON file (the reliable path), and
2. provide a best-effort binary scanner that finds printable sample filenames
   with adjacent small integer key/velocity fields, plus XML/JSON-like text
   embedded in a patch.

The scanner can never be guaranteed to recover every mapping; see tools/README.md.
"""

from __future__ import annotations

import json
import re
import struct
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

from . import ConversionError
from .sfz import Region

MAP_HINT = "Reason NNXT patches are proprietary; use --map JSON for a reliable conversion"

_SAMPLE_BYTES_RE = re.compile(
    rb"(?i)(?:[A-Za-z0-9_\-\\./()\[\] ]{1,180})\.(?:wav|aif|aiff|mp3|flac|ogg)(?![A-Za-z0-9])"
)
_ASCII_INTS_RE = re.compile(rb"(?<!\d)(\d{1,3})(?!\d)")

_SAMPLE_KEYS = ("sample", "sampleFile", "sample_file", "file", "filename", "path")
_KEY_KEYS = ("key", "rootKey", "root_key")
_LOKEY_KEYS = ("lokey", "lowKey", "low_key")
_HIKEY_KEYS = ("hikey", "highKey", "high_key")
_LOVEL_KEYS = ("lovel", "lowVelo", "low_velo")
_HIVEL_KEYS = ("hivel", "highVelo", "high_velo")


def detect_nnxt(path) -> bool:
    """True for .nnxt/.pch files or patches with recognizable Reason markers."""
    path = Path(path)
    if path.suffix.lower() in (".nnxt", ".pch"):
        return True
    try:
        text = path.read_bytes()[:4096].decode("ascii", errors="ignore")
    except OSError:
        return False
    return "NNXT" in text or "Reason" in text


def _first(mapping: dict, keys: Tuple[str, ...]):
    """Return the first present, non-empty value for any of *keys*."""
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return None


def load_map(path) -> Tuple[str, List[Region]]:
    """Load an explicit ``--map`` JSON file (the reliable conversion path)."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise ConversionError("cannot read --map %s: %s" % (path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise ConversionError("--map %s: invalid JSON: %s" % (path, exc)) from exc
    if not isinstance(data, dict):
        raise ConversionError("--map JSON must be an object with 'name' and 'regions'")
    raw = data.get("regions")
    if not isinstance(raw, list) or not raw:
        raise ConversionError("--map JSON needs a non-empty 'regions' list")
    name = str(data.get("name") or path.stem)
    regions = [
        Region.from_dict(item, where="--map region %d" % i)
        for i, item in enumerate(raw)
    ]
    return name, regions


def _regions_from_mapping(data, source: str) -> Tuple[Optional[str], List[Region]]:
    """Read a JSON-like mapping already parsed from a patch or --map file."""
    if isinstance(data, dict):
        raw = data.get("regions", [])
        name = data.get("name")
    elif isinstance(data, list):
        raw, name = data, None
    else:
        raise ConversionError("%s: expected a JSON object or list" % source)
    if not isinstance(raw, list):
        raise ConversionError("%s: 'regions' must be a list" % source)
    name = str(name) if name else None
    regions = []
    for i, item in enumerate(raw):
        try:
            regions.append(Region.from_dict(item, where="%s region %d" % (source, i)))
        except ConversionError:
            continue
    return name, regions


def _regions_from_xml(text: str) -> Tuple[Optional[str], List[Region]]:
    """Best-effort extraction from XML-like patch text."""
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return None, []
    name = root.get("name")
    regions: List[Region] = []
    for elem in root.iter():
        values = dict(elem.attrib)
        for child in elem.findall("V"):
            if child.get("name"):
                values[child.get("name")] = child.get("value", "")
        sample = _first(values, _SAMPLE_KEYS)
        if not sample:
            continue
        region_dict = {
            "sample": str(sample),
            "key": _first(values, _KEY_KEYS),
            "lokey": _first(values, _LOKEY_KEYS),
            "hikey": _first(values, _HIKEY_KEYS),
            "lovel": _first(values, _LOVEL_KEYS),
            "hivel": _first(values, _HIVEL_KEYS),
            "volume_db": _first(values, ("volume_db", "volume", "volumeDb")),
            "pan": values.get("pan"),
            "tune": values.get("tune"),
            "offset": values.get("offset"),
            "end": values.get("end"),
            "loop_start": _first(values, ("loop_start", "loopStart")),
            "loop_end": _first(values, ("loop_end", "loopEnd")),
            "loop_mode": _first(values, ("loop_mode", "loopMode")),
        }
        try:
            regions.append(Region.from_dict(region_dict, where="XML region"))
        except ConversionError:
            continue
    return (str(name) if name else None), regions


def _integer_fields(chunk: bytes) -> List[int]:
    """Find plausible key/velocity integers after (or before) a filename."""
    # Filenames are often NUL-terminated, so the integer run may start at a
    # non-zero alignment.  Try the first few alignments and keep the longest.
    best: List[int] = []
    for start in range(min(8, max(0, len(chunk) - 3))):
        values: List[int] = []
        for offset in range(start, min(start + 32, len(chunk) - 3), 4):
            value = struct.unpack_from("<I", chunk, offset)[0]
            if value > 127:
                break
            values.append(value)
        if len(values) > len(best):
            best = values[:5]
        if len(best) >= 5:
            break
    if len(best) >= 3:
        return best
    values = [int(m.group(1)) for m in _ASCII_INTS_RE.finditer(chunk[:96])]
    return values[:5]


def _region_from_fields(sample: str, values: List[int]) -> Region:
    """Convert a scanned integer run into key/velocity ranges."""
    if len(values) >= 5:
        key, lokey, hikey, lovel, hivel = values[:5]
    elif len(values) >= 3:
        lokey, hikey, lovel = values[:3]
        key, hivel = lokey, 127
    elif values:
        key = values[0]
        lokey = hikey = key
        lovel, hivel = 0, 127
    else:
        key, lokey, hikey, lovel, hivel = 60, 0, 127, 0, 127
    key = max(0, min(127, key))
    lokey = max(0, min(127, lokey))
    hikey = max(0, min(127, hikey))
    if lokey > hikey:
        lokey, hikey = hikey, lokey
    return Region(
        sample=sample,
        key=key,
        lokey=lokey,
        hikey=hikey,
        lovel=max(0, min(127, lovel)),
        hivel=max(0, min(127, hivel)),
    )


def scan_patch(path) -> Tuple[str, List[Region]]:
    """Best-effort scan of a binary NNXT/.pch patch.

    Raises:
        ConversionError: if no sample mappings can be recovered.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConversionError("cannot read %s: %s" % (path, exc)) from exc

    text = data.decode("utf-8", errors="replace")
    stripped = text.strip()
    if stripped.startswith(("{", "[")):
        try:
            name, regions = _regions_from_mapping(json.loads(stripped), str(path))
        except json.JSONDecodeError:
            name, regions = None, []
        if regions:
            return name or path.stem, regions
    if "<" in text and ">" in text:
        name, regions = _regions_from_xml(text)
        if regions:
            return name or path.stem, regions

    regions: List[Region] = []
    seen = set()
    for match in _SAMPLE_BYTES_RE.finditer(data):
        sample = match.group(0).decode("ascii", errors="replace").strip(" \t\r\n\x00")
        if not sample:
            continue
        key = sample.casefold()
        if key in seen:
            continue
        seen.add(key)
        tail = data[match.end():match.end() + 128]
        head = data[max(0, match.start() - 64):match.start()]
        values = _integer_fields(tail) or _integer_fields(head)
        regions.append(_region_from_fields(sample, values))
    if not regions:
        raise ConversionError(
            "%s: no sample mappings found by the NNXT scanner; %s" % (path.name, MAP_HINT)
        )
    return path.stem, regions
