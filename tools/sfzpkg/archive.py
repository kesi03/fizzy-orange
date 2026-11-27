"""ZIP package collection, writing and validation.

Package layout (docs/PROTOCOL.md)::

    fizzy.json
    piano.sfz
    samples/Piano_C4.wav
    presets/extra.json
    README.txt

Entries are stored uncompressed by default so the firmware can read them from
media without a decompression step.
"""

from __future__ import annotations

import os
import posixpath
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from . import SfzPkgError
from .audio import AUDIO_SUFFIXES
from .presets import (
    FORMAT,
    instrument_sfz_paths,
    load_manifest,
    preset_instrument_indexes,
)
from .sfz import sample_refs, unsupported_opcodes

SAMPLE_SUFFIXES = frozenset(AUDIO_SUFFIXES)
_SAFE_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.\-/ ]*$")


@dataclass
class Problem:
    """One validation finding."""

    level: str  # "error" or "warning"
    message: str

    def __str__(self) -> str:
        return "%s: %s" % (self.level.upper(), self.message)

    def as_dict(self) -> dict:
        return {"level": self.level, "message": self.message}


def safe_rel(path) -> str:
    """Convert *path* to a safe, forward-slash ZIP member name."""
    text = str(path).replace("\\", "/").strip()
    if (
        not text
        or text.startswith("/")
        or re.match(r"^[A-Za-z]:", text)
        or not _SAFE_RE.match(text)
    ):
        raise SfzPkgError("unsafe package path: %r" % str(path))
    parts: List[str] = []
    for part in text.split("/"):
        if part in ("", ".", ".."):
            if part == "..":
                raise SfzPkgError("unsafe package path: %r" % str(path))
            continue
        parts.append(part)
    if not parts:
        raise SfzPkgError("empty package path")
    return "/".join(parts)


def zip_name(name: str) -> str:
    """Normalize a ZIP member name for case-insensitive comparison."""
    return str(name).replace("\\", "/").strip().lstrip("./")


def collect_files(root) -> List[Tuple[str, Path]]:
    """Collect package candidate files below *root* as ``(relpath, path)``."""
    root = Path(root)
    found: List[Tuple[str, Path]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(
            d for d in dirnames if not d.startswith(".") and d != "__pycache__"
        )
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            rel = path.relative_to(root).as_posix()
            lower = rel.casefold()
            suffix = path.suffix.lower()
            keep = (
                suffix == ".sfz"
                or suffix in SAMPLE_SUFFIXES
                or lower == "readme.txt"
                or lower == "fizzy.json"
                or (lower.startswith("presets/") and suffix == ".json")
            )
            if keep:
                found.append((safe_rel(rel), path))
    return sorted(found, key=lambda pair: pair[0].casefold())


def read_payloads(files: Iterable[Tuple[str, Path]]) -> Dict[str, bytes]:
    """Read collected files into a ``{zip_name: bytes}`` mapping."""
    payloads: Dict[str, bytes] = {}
    for rel, path in files:
        rel = safe_rel(rel)
        if rel.casefold() in payloads:
            raise SfzPkgError("duplicate package path: %s" % rel)
        try:
            payloads[rel] = Path(path).read_bytes()
        except OSError as exc:
            raise SfzPkgError("cannot read %s: %s" % (path, exc)) from exc
    return payloads


def write_zip(out, payloads: Dict[str, bytes], deflate: bool = False) -> Path:
    """Write *payloads* to *out* (ZIP_STORED unless *deflate*)."""
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    compression = zipfile.ZIP_DEFLATED if deflate else zipfile.ZIP_STORED
    with zipfile.ZipFile(out, "w", compression=compression) as archive:
        for rel in sorted(payloads, key=str.casefold):
            archive.writestr(safe_rel(rel), payloads[rel])
    return out


def find_member(names: Sequence[str], wanted: str) -> Optional[str]:
    """Find a ZIP member by case-insensitive relative path."""
    target = zip_name(wanted).casefold()
    for name in names:
        if zip_name(name).casefold() == target:
            return name
    return None


def resolve_member(sfz_rel: str, ref: str, names: Sequence[str]) -> Optional[str]:
    """Resolve an SFZ sample reference against ZIP member names.

    Relative paths are resolved against the SFZ's directory first; a unique
    basename match is accepted as a fallback.  All comparisons are
    case-insensitive.
    """
    ref = ref.replace("\\", "/").strip()
    if not ref or re.match(r"^[A-Za-z]:", ref) or ref.startswith("/"):
        return None
    rel = posixpath.normpath(posixpath.join(posixpath.dirname(sfz_rel), ref))
    if rel == ".." or rel.startswith("../"):
        return None
    normalized = {zip_name(name).casefold(): name for name in names}
    direct = normalized.get(rel.casefold())
    if direct is not None:
        return direct
    base = posixpath.basename(rel).casefold()
    matches = sorted(
        (name for name in names if posixpath.basename(zip_name(name)).casefold() == base),
        key=str.casefold,
    )
    return matches[0] if matches else None


def validate_package(payloads: Dict[str, bytes]) -> List[Problem]:
    """Validate manifest, SFZ sample paths and opcodes in an in-memory package."""
    names = [zip_name(name) for name in payloads]
    problems: List[Problem] = []

    manifest_name = find_member(names, "fizzy.json")
    if manifest_name is None:
        problems.append(Problem("error", "missing fizzy.json manifest"))
        manifest = {}
    else:
        try:
            manifest = load_manifest(payloads[manifest_name])
        except SfzPkgError as exc:
            problems.append(Problem("error", str(exc)))
            manifest = {}
        else:
            if manifest.get("format") != FORMAT:
                problems.append(
                    Problem(
                        "error",
                        "fizzy.json format is %r, expected %r" % (manifest.get("format"), FORMAT),
                    )
                )

    sfz_names: List[str] = []
    for rel in instrument_sfz_paths(manifest):
        member = find_member(names, rel)
        if member is None:
            problems.append(Problem("error", "instrument SFZ not in package: %s" % rel))
        elif member not in sfz_names:
            sfz_names.append(member)
    for name in names:
        if name.lower().endswith(".sfz"):
            member = find_member(names, name) or name
            if member not in sfz_names:
                sfz_names.append(member)
    if not sfz_names:
        problems.append(Problem("warning", "package has no SFZ instruments"))

    for sfz_name in sfz_names:
        text = payloads[sfz_name].decode("utf-8", errors="replace")
        for lineno, ref in sample_refs(text):
            member = resolve_member(sfz_name, ref, names)
            if member is None:
                problems.append(
                    Problem(
                        "error",
                        "%s:%d: missing sample %r (sample=%s)" % (sfz_name, lineno, ref, ref),
                    )
                )
            elif Path(member).suffix.lower() not in SAMPLE_SUFFIXES:
                problems.append(
                    Problem(
                        "warning",
                        "%s:%d: sample %r has an unsupported audio type" % (sfz_name, lineno, member),
                    )
                )
        for lineno, opcode in unsupported_opcodes(text):
            problems.append(
                Problem(
                    "warning",
                    "%s:%d: opcode %r is not supported by the firmware (ignored)"
                    % (sfz_name, lineno, opcode),
                )
            )

    instruments = manifest.get("instruments")
    instrument_count = len(instruments) if isinstance(instruments, list) else 0
    for preset, index in preset_instrument_indexes(manifest).items():
        if index is not None:
            try:
                number = int(index)
            except (TypeError, ValueError):
                number = -1
            if not (0 <= number < instrument_count):
                problems.append(
                    Problem(
                        "warning",
                        "preset %r references instrument %r which is not in the manifest"
                        % (preset, index),
                    )
                )
    return problems


def inspect_zip(path) -> dict:
    """Open a ZIP and return a structured inspection report."""
    path = Path(path)
    if not path.is_file():
        raise SfzPkgError("not a ZIP file: %s" % path)
    try:
        with zipfile.ZipFile(path) as archive:
            infos = [i for i in archive.infolist() if not i.is_dir()]
            payloads = {}
            for info in infos:
                try:
                    payloads[zip_name(info.filename)] = archive.read(info)
                except (OSError, zipfile.BadZipFile) as exc:
                    payloads[zip_name(info.filename)] = b""
                    raise SfzPkgError("cannot read %s from ZIP: %s" % (info.filename, exc)) from exc
    except zipfile.BadZipFile as exc:
        raise SfzPkgError("%s: not a valid ZIP: %s" % (path, exc)) from exc

    manifest = {}
    manifest_name = find_member(list(payloads), "fizzy.json")
    if manifest_name is not None:
        try:
            manifest = load_manifest(payloads[manifest_name])
        except SfzPkgError as exc:
            manifest = {"error": str(exc)}
    compression_names = {
        zipfile.ZIP_STORED: "stored",
        zipfile.ZIP_DEFLATED: "deflated",
    }
    return {
        "package": str(path),
        "entries": [
            {
                "name": zip_name(info.filename),
                "size": info.file_size,
                "compression": compression_names.get(info.compress_type, str(info.compress_type)),
            }
            for info in infos
        ],
        "manifest": manifest,
        "problems": [p.as_dict() for p in validate_package(payloads)],
    }
