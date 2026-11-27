"""Manifest and preset helpers for the ``fizzy-orange/1`` package format."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from . import SfzPkgError

FORMAT = "fizzy-orange/1"


def build_manifest(name: str, sfz_files: Iterable[str], presets: Optional[Iterable[dict]] = None) -> dict:
    """Create a new ``fizzy.json`` document.

    Instrument names default to the SFZ file's stem; the firmware/Editor uses
    the ``sfz`` path to find the actual file inside the ZIP.
    """
    instruments = []
    for rel in sfz_files:
        rel = str(rel).replace("\\", "/")
        instruments.append({"name": Path(rel).stem, "sfz": rel})
    return {
        "format": FORMAT,
        "name": str(name or "FIZZY ORANGE"),
        "instruments": instruments,
        "presets": list(presets or []),
    }


def normalize_presets(data: Any, source: str = "presets") -> List[dict]:
    """Normalize a JSON preset bank into a list of preset dictionaries.

    Accepts either a bare list of presets or an object with a ``presets`` key.
    Extra fields are preserved so future firmware fields are not lost.
    """
    if isinstance(data, dict):
        raw = data.get("presets", [])
    elif isinstance(data, list):
        raw = data
    else:
        raise SfzPkgError("%s: expected a JSON list or an object with a 'presets' list" % source)
    if not isinstance(raw, list):
        raise SfzPkgError("%s: 'presets' must be a list" % source)
    out: List[dict] = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SfzPkgError("%s: preset %d is not a JSON object" % (source, i))
        preset = dict(item)
        if not str(preset.get("name") or "").strip():
            raise SfzPkgError("%s: preset %d has no name" % (source, i))
        out.append(preset)
    return out


def load_presets(path) -> List[dict]:
    """Read and normalize a preset JSON file."""
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise SfzPkgError("cannot read %s: %s" % (path, exc)) from exc
    except json.JSONDecodeError as exc:
        raise SfzPkgError("%s: invalid JSON: %s" % (path, exc)) from exc
    return normalize_presets(data, source=str(path))


def merge_presets(existing: Iterable[dict], extra: Iterable[dict]) -> List[dict]:
    """Append presets from *extra*, skipping names already present."""
    result: List[dict] = []
    seen = set()
    for preset in list(existing) + list(extra):
        if not isinstance(preset, dict):
            continue
        key = str(preset.get("name") or "").casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(preset)
    return result


def load_manifest(data: bytes) -> dict:
    """Parse ``fizzy.json`` bytes; malformed manifests are an error."""
    try:
        doc = json.loads(data.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SfzPkgError("fizzy.json: invalid JSON: %s" % exc) from exc
    if not isinstance(doc, dict):
        raise SfzPkgError("fizzy.json: expected a JSON object")
    return doc


def dumps_manifest(doc: dict) -> bytes:
    """Serialize a manifest consistently with a trailing newline."""
    return (json.dumps(doc, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def instrument_sfz_paths(doc: dict) -> List[str]:
    """Return the SFZ paths referenced by a manifest."""
    paths: List[str] = []
    instruments = doc.get("instruments")
    if not isinstance(instruments, list):
        return paths
    for item in instruments:
        if isinstance(item, dict) and item.get("sfz"):
            paths.append(str(item["sfz"]).replace("\\", "/"))
    return paths


def preset_instrument_indexes(doc: dict) -> Dict[str, Any]:
    """Map preset names to their (possibly missing) instrument index."""
    result: Dict[str, Any] = {}
    presets = doc.get("presets")
    if not isinstance(presets, list):
        return result
    for item in presets:
        if isinstance(item, dict):
            result[str(item.get("name") or "?")] = item.get("instrument")
    return result
