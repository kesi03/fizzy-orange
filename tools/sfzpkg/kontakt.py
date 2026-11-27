"""Legacy Kontakt (NKI/NKM) reader.

Files produced by Translator 7 contain a short binary header followed by a
zlib-compressed UTF-8 ``K2_Container`` XML document.  We locate the compressed
stream by scanning the first 256 bytes (usually at offset 170), decompress it
and map each ``<Zone>``'s ``<V name="..." value="..."/>`` pairs to SFZ regions.

Native modern NI containers are proprietary and cannot be parsed here; those
inputs produce a clear error that suggests the explicit ``--map`` fallback.
"""

from __future__ import annotations

import gzip
import math
import zlib
from pathlib import Path
from typing import List, Optional, Tuple
from xml.etree import ElementTree as ET

from . import ConversionError
from .sfz import Region

ZLIB_MAGICS = ((0x78, 0x01), (0x78, 0x9C), (0x78, 0xDA))
SCAN_BYTES = 256
MAP_HINT = "native Kontakt containers are not supported; provide an explicit --map JSON file"


def _local(tag: str) -> str:
    """Strip an optional XML namespace prefix from an element tag."""
    return tag.rsplit("}", 1)[-1]


def _decompress(data: bytes) -> Optional[bytes]:
    """Return decompressed XML bytes, or None when no known stream is found."""
    if data[:2] == b"\x1f\x8b":  # gzip start magic (some Kontakt versions)
        try:
            return gzip.decompress(data)
        except (OSError, EOFError, zlib.error):
            pass
    for offset in range(min(SCAN_BYTES, max(0, len(data) - 1))):
        if (data[offset], data[offset + 1]) not in ZLIB_MAGICS:
            continue
        try:
            obj = zlib.decompressobj()
            payload = obj.decompress(data[offset:]) + obj.flush()
        except zlib.error:
            continue
        if payload.strip().startswith((b"<?xml", b"<K2_Container", b"<")):
            return payload
    return None


def detect_kontakt(path) -> bool:
    """True for .nki/.nkm files or files with a compressed K2 payload."""
    path = Path(path)
    if path.suffix.lower() in (".nki", ".nkm"):
        return True
    try:
        with path.open("rb") as handle:
            return _decompress(handle.read(SCAN_BYTES + 4096)) is not None
    except OSError:
        return False


def _parse_int(raw, default: int) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(float(str(raw).strip()))
    except (TypeError, ValueError):
        return None


def _midi(values: dict, name: str, default: int) -> Optional[int]:
    value = _parse_int(values.get(name), default)
    if value is None or not (0 <= value <= 127):
        return None
    return value


def _number(raw, default: float = 0.0) -> float:
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return float(str(raw).strip())
    except (TypeError, ValueError):
        return default


def _loop_mode(raw) -> Optional[str]:
    """Kontakt: 0=off, 1=until_release, 2=until_end."""
    mode = _parse_int(raw, 0)
    return {0: "no_loop", 1: "loop_sustain", 2: "loop_continuous"}.get(mode)


def _zone_values(zone: ET.Element) -> dict:
    """Collect V name/value pairs from a Zone (direct children or nested)."""
    values: dict = {}
    candidates = list(zone.findall("V")) + list(zone.iter("V"))
    for elem in candidates:
        name = elem.get("name")
        if not name:
            continue
        values[name] = elem.get("value", "")
    return values


def parse_nki(path) -> Tuple[str, List[Region]]:
    """Parse a legacy K2_Container NKI/NKM file.

    Returns:
        ``(instrument_name, regions)``.

    Raises:
        ConversionError: if the file is a native modern container, is
            corrupted, or contains no usable zones.
    """
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise ConversionError("cannot read %s: %s" % (path, exc)) from exc

    payload = _decompress(data)
    if payload is None:
        raise ConversionError(
            "%s: no zlib/gzip compressed K2_Container XML found in the first %d bytes; %s"
            % (path.name, SCAN_BYTES, MAP_HINT)
        )
    try:
        root = ET.fromstring(payload.decode("utf-8-sig", errors="replace"))
    except ET.ParseError as exc:
        raise ConversionError(
            "%s: compressed payload is not valid K2_Container XML (%s); %s"
            % (path.name, exc, MAP_HINT)
        ) from exc
    if _local(root.tag) != "K2_Container":
        raise ConversionError(
            "%s: unexpected compressed payload root <%s>; %s"
            % (path.name, _local(root.tag), MAP_HINT)
        )

    name = ""
    for elem in root.iter():
        if _local(elem.tag) == "K2_Program" and elem.get("name"):
            name = elem.get("name") or ""
            break
    if not name or name == "(null)":
        name = path.stem

    regions: List[Region] = []
    for elem in root.iter():
        if _local(elem.tag) != "Zone":
            continue
        values = _zone_values(elem)
        sample = str(values.get("sampleFile") or "").strip()
        if not sample:
            continue  # empty zones are not useful
        root_key = _midi(values, "rootKey", 60)
        lokey = _midi(values, "lowKey", 0)
        hikey = _midi(values, "highKey", 127)
        lovel = _midi(values, "lowVelo", 0)
        hivel = _midi(values, "highVelo", 127)
        if None in (root_key, lokey, hikey, lovel, hivel) or lokey > hikey:
            continue  # discard zones with invalid MIDI ranges
        try:
            linear = float(values.get("zoneVolume") or 1.0)
        except (TypeError, ValueError):
            linear = 1.0
        volume_db = 20.0 * math.log10(linear) if linear > 0 else -144.0
        regions.append(
            Region(
                sample=sample,
                key=root_key,
                lokey=lokey,
                hikey=hikey,
                lovel=lovel,
                hivel=hivel,
                volume_db=volume_db,
                pan=_number(values.get("zonePan")),
                tune=_number(values.get("zoneTune")),
                offset=int(_parse_int(values.get("sampleStart"), 0) or 0),
                end=int(_parse_int(values.get("sampleEnd"), 0) or 0),
                loop_start=int(_parse_int(values.get("loopStart"), 0) or 0),
                loop_end=int(_parse_int(values.get("loopEnd"), 0) or 0),
                loop_mode=_loop_mode(values.get("loopMode")) or "no_loop",
            )
        )
    if not regions:
        raise ConversionError(
            "%s: no zones with sampleFile were found in the K2_Container XML; %s"
            % (path.name, MAP_HINT)
        )
    return name, regions
