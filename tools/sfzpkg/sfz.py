"""SFZ parsing helpers for the firmware's documented subset.

Only the opcodes and headers listed in ``docs/PROTOCOL.md`` are understood by
the firmware.  This module provides:

* comment stripping that preserves line numbers (for useful diagnostics),
* ``sample=`` path discovery and rewriting (needed by ``--wav``),
* recursive ``#include`` expansion with cycle detection,
* serialization of converted sampler zones as a new SFZ instrument.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Tuple

from . import ConversionError, SfzPkgError

SUPPORTED_OPCODES = frozenset(
    """
    sample key lokey hikey pitch_keycenter lovel hivel
    volume gain pan tune finetune
    offset end loop_mode loop_start loop_end
    ampeg_attack ampeg_decay ampeg_sustain ampeg_release ampeg_hold
    """.split()
)
SUPPORTED_HEADERS = frozenset(("global", "group", "region"))
LOOP_MODES = frozenset(("no_loop", "one_shot", "loop_continuous", "loop_sustain"))

_OPCODE_RE = re.compile(
    r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s]+)"
)
_INCLUDE_RE = re.compile(r"^\s*#include\s+(?:\"([^\"]+)\"|'([^']+)'|(\S+))", re.IGNORECASE)
_SAMPLE_RE = re.compile(r"(?i)(\bsample\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s]+)")
_HEADER_RE = re.compile(r"^\s*<([A-Za-z_][\w]*)>\s*$")


def strip_comments(text: str) -> str:
    """Remove ``//`` and ``/* ... */`` comments, preserving newlines/offsets."""
    chars = list(text)
    i = 0
    in_block = False
    size = len(text)
    while i < size:
        if in_block:
            if text.startswith("*/", i):
                chars[i] = chars[i + 1] = " "
                i += 2
                in_block = False
            else:
                if text[i] != "\n":
                    chars[i] = " "
                i += 1
        elif text.startswith("/*", i):
            chars[i] = chars[i + 1] = " "
            i += 2
            in_block = True
        elif text.startswith("//", i):
            while i < size and text[i] != "\n":
                chars[i] = " "
                i += 1
        else:
            i += 1
    return "".join(chars)


def unquote(value: str) -> str:
    """Strip matching single or double quotes from an opcode value."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def quote_ref(ref: str) -> str:
    """Quote an SFZ path only when it contains whitespace or quotes."""
    ref = str(ref).replace('"', "")
    if any(ch.isspace() for ch in ref):
        return '"%s"' % ref
    return ref


def normalize_ref(ref: str) -> str:
    """Canonical form used to match SFZ sample references."""
    ref = unquote(ref).replace("\\", "/").strip()
    while ref.startswith("./"):
        ref = ref[2:]
    return ref


def ref_key(ref: str) -> str:
    """Case-insensitive key for matching references (paths are case-folded)."""
    return normalize_ref(ref).casefold()


def iter_opcodes(text: str) -> Iterator[Tuple[int, str, str]]:
    """Yield ``(line_number, opcode, unquoted_value)`` for each opcode."""
    for lineno, line in enumerate(strip_comments(text).splitlines(), 1):
        for match in _OPCODE_RE.finditer(line):
            yield lineno, match.group(1).lower(), unquote(match.group(2))


def sample_refs(text: str) -> Iterator[Tuple[int, str]]:
    """Yield ``(line_number, sample_path)`` for every ``sample=`` opcode."""
    for lineno, opcode, value in iter_opcodes(text):
        if opcode == "sample":
            yield lineno, value


def unsupported_opcodes(text: str) -> Iterator[Tuple[int, str]]:
    """Yield ``(line_number, opcode)`` for opcodes the firmware ignores."""
    for lineno, opcode, _ in iter_opcodes(text):
        if opcode not in SUPPORTED_OPCODES:
            yield lineno, opcode


def rewrite_sample_paths(text: str, mapping: Dict[str, str]) -> str:
    """Replace sample references using a ``ref_key() -> new_path`` mapping.

    Line numbers and all non-sample text are preserved verbatim.
    """
    if not mapping:
        return text
    out: List[str] = []
    for line in text.splitlines(keepends=True):
        def repl(match):
            new = mapping.get(ref_key(match.group(2)))
            if new is None:
                return match.group(0)
            return match.group(1) + quote_ref(new)

        out.append(_SAMPLE_RE.sub(repl, line))
    return "".join(out)


def _find_include(base: Path, ref: str) -> Path:
    """Resolve an include path, with a case-insensitive fallback."""
    ref = unquote(ref).replace("\\", "/")
    candidate = (base / ref).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    # Windows and many user-supplied paths disagree about letter case.
    parts = [p for p in ref.split("/") if p not in ("", ".")]
    root = base
    wrong_case = False
    for part in parts:
        if part == "..":
            root = root.parent
            continue
        try:
            entries = list(root.iterdir())
        except OSError:
            entries = []
        found = next((p for p in entries if p.name == part), None)
        if found is None:
            found = next((p for p in entries if p.name.casefold() == part.casefold()), None)
            wrong_case = wrong_case or found is not None
        if found is None:
            raise ConversionError("include not found: %s (from %s)" % (ref, base))
        root = found
    if not root.is_file():
        raise ConversionError("include is not a file: %s (from %s)" % (ref, base))
    if wrong_case:
        return root.resolve()
    return root.resolve()


def inline_includes(
    text: str, base_dir, stack: Optional[List[Path]] = None
) -> str:
    """Recursively expand ``#include "file.sfz"`` directives.

    Args:
        text: SFZ source text to expand.
        base_dir: directory that relative include paths are resolved against.
        stack: internal list of already-opened include paths (cycle detection).

    Raises:
        ConversionError: on a missing include or an include cycle.
    """
    base = Path(base_dir).resolve()
    stack = list(stack or [])
    out: List[str] = []
    for line in text.splitlines(keepends=True):
        match = _INCLUDE_RE.match(line)
        if not match:
            out.append(line)
            continue
        ref = next((g for g in match.groups() if g), "")
        path = _find_include(base, ref)
        if path in stack:
            chain = " -> ".join(p.name for p in stack + [path])
            raise ConversionError("include cycle: %s" % chain)
        included = path.read_text(encoding="utf-8-sig", errors="replace")
        expanded = inline_includes(included, path.parent, stack + [path])
        if expanded and not expanded.endswith(("\n", "\r")):
            expanded += "\n"
        out.append(expanded)
    return "".join(out)


# ---------------------------------------------------------------------------
# Region serialization
# ---------------------------------------------------------------------------


def _number(value, default: float) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return default
    return result


def _fmt(value: float) -> str:
    if isinstance(value, int) or float(value).is_integer():
        return str(int(value))
    return format(float(value), ".6g")


@dataclass
class Region:
    """One sampler zone, normalized to the SFZ subset used by the firmware."""

    sample: str
    key: Optional[int] = None
    lokey: int = 0
    hikey: int = 127
    lovel: int = 0
    hivel: int = 127
    volume_db: float = 0.0
    pan: float = 0.0
    tune: float = 0.0
    offset: int = 0
    end: int = 0
    loop_start: int = 0
    loop_end: int = 0
    loop_mode: str = "no_loop"

    @classmethod
    def from_dict(cls, data: dict, where: str = "region") -> "Region":
        """Build a Region from an explicit ``--map`` JSON entry."""
        if not isinstance(data, dict):
            raise ConversionError("%s: region must be a JSON object" % where)
        sample = str(data.get("sample") or "").strip()
        if not sample:
            raise ConversionError("%s: region has no 'sample' path" % where)

        def integer(name: str, default: int, minimum=0, maximum=127) -> int:
            raw = data.get(name, default)
            try:
                value = int(raw)
            except (TypeError, ValueError):
                raise ConversionError("%s: %s must be an integer, got %r" % (where, name, raw))
            if value < minimum or value > maximum:
                raise ConversionError(
                    "%s: %s must be between %d and %d, got %d" % (where, name, minimum, maximum, value)
                )
            return value

        mode = str(data.get("loop_mode") or "no_loop").strip()
        if mode not in LOOP_MODES:
            raise ConversionError(
                "%s: loop_mode %r is not one of %s" % (where, mode, ", ".join(sorted(LOOP_MODES)))
            )
        key = data.get("key")
        if key is not None and str(key).strip() != "":
            key = integer("key", 60)
        else:
            key = None
        return cls(
            sample=sample,
            key=key,
            lokey=integer("lokey", 0),
            hikey=integer("hikey", 127),
            lovel=integer("lovel", 0),
            hivel=integer("hivel", 127),
            volume_db=_number(data.get("volume_db", 0.0), 0.0),
            pan=_number(data.get("pan", 0.0), 0.0),
            tune=_number(data.get("tune", 0.0), 0.0),
            offset=integer("offset", 0, 0, 2**31 - 1),
            end=integer("end", 0, 0, 2**31 - 1),
            loop_start=integer("loop_start", 0, 0, 2**31 - 1),
            loop_end=integer("loop_end", 0, 0, 2**31 - 1),
            loop_mode=mode,
        )

    def with_sample(self, sample: str) -> "Region":
        """Return a copy with a different sample path."""
        return replace(self, sample=sample)


def regions_to_sfz(
    regions: Iterable[Region], name: Optional[str] = None, sample_prefix: Optional[str] = None
) -> str:
    """Render converted regions as an SFZ 2 instrument."""
    lines: List[str] = ["; Generated by sfzpkg"]
    if name:
        lines.append("; %s" % str(name).replace("\n", " ").replace("\r", " "))
    lines.append("<global>")
    for region in regions:
        sample = str(region.sample).replace("\\", "/")
        if sample_prefix:
            sample = posixpath.join(str(sample_prefix).replace("\\", "/"), sample)
        lines.append("")
        lines.append("<region>")
        lines.append("sample=%s" % quote_ref(sample))
        key = region.key if region.key is not None else (
            region.lokey if region.lokey == region.hikey else None
        )
        if key is not None:
            lines.append("key=%d" % key)
        lines.append("lokey=%d" % region.lokey)
        lines.append("hikey=%d" % region.hikey)
        lines.append("lovel=%d" % region.lovel)
        lines.append("hivel=%d" % region.hivel)
        if region.volume_db:
            lines.append("volume=%s" % _fmt(region.volume_db))
        if region.pan:
            lines.append("pan=%s" % _fmt(region.pan))
        if region.tune:
            lines.append("tune=%s" % _fmt(region.tune))
        if region.offset:
            lines.append("offset=%d" % region.offset)
        if region.end:
            lines.append("end=%d" % region.end)
        lines.append("loop_mode=%s" % region.loop_mode)
        if region.loop_start:
            lines.append("loop_start=%d" % region.loop_start)
        if region.loop_end:
            lines.append("loop_end=%d" % region.loop_end)
    return "\n".join(lines) + "\n"
