"""Command-line interface for sfzpkg.

Commands:

    pack      build a FIZZY ORANGE package ZIP from a folder
    convert   read a Kontakt/NNXT patch (or an explicit --map) into SFZ + samples
    inspect   list a package and report validation problems
    build     convert a patch and pack the result in one step
"""

from __future__ import annotations

import argparse
import json
import posixpath
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from . import ConversionError, SfzPkgError, ValidationError
from . import archive, audio, kontakt, nnxt, presets, sfz


def info(message: str) -> None:
    print(message, file=sys.stderr)


def warn(message: str) -> None:
    info("warning: %s" % message)


def slugify(text: str, limit: int = 60) -> str:
    """Make a compact, portable name for output filenames."""
    text = re.sub(r"[^\w.-]+", "-", str(text), flags=re.UNICODE).strip("-.")
    return (text or "instrument")[:limit]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise SfzPkgError("cannot read %s: %s" % (path, exc)) from exc


def _resolve_input(root: Path, value: str) -> Path:
    """Resolve a CLI file path relative to the package directory or CWD."""
    path = Path(value).expanduser()
    if path.is_absolute() and path.is_file():
        return path.resolve()
    in_root = root / path
    if in_root.is_file():
        return in_root.resolve()
    return path.resolve()


def _path_rel(root: Path, path: Path) -> Optional[str]:
    """Return path relative to root in ZIP form, or None if outside root."""
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return None


def _add_source(root: Path, sources: Dict[str, Path], path: Path, preferred: str) -> str:
    """Add a file to the source map, reusing its existing relative path."""
    for rel, existing in sources.items():
        if existing.resolve() == path.resolve():
            return rel
    rel = archive.safe_rel(preferred)
    sources[rel] = path
    return rel


def _unique_rel(rel: str, used: Sequence[str]) -> str:
    """Add ``-2``, ``-3``... before the suffix until the name is unused."""
    rel = archive.safe_rel(rel)
    taken = {name.casefold() for name in used}
    if rel.casefold() not in taken:
        return rel
    path = Path(rel)
    number = 2
    while True:
        candidate = path.with_name("%s-%d%s" % (path.stem, number, path.suffix)).as_posix()
        if candidate.casefold() not in taken:
            return archive.safe_rel(candidate)
        number += 1


def _wav_rel(rel: str) -> str:
    path = Path(rel)
    return path.with_suffix(".wav").as_posix()


def _resolve_local_ref(ref: str, sfz_rel: str, sources: Dict[str, Path]) -> Optional[str]:
    """Resolve an SFZ sample reference against the source files on disk."""
    ref = sfz.normalize_ref(ref)
    if not ref:
        return None
    candidate = posixpath.normpath(posixpath.join(posixpath.dirname(sfz_rel), ref))
    by_rel = {rel.casefold(): rel for rel in sources}
    if candidate.casefold() in by_rel:
        return by_rel[candidate.casefold()]
    base = posixpath.basename(candidate).casefold()
    matches = sorted(
        (
            rel
            for rel in sources
            if posixpath.basename(rel).casefold() == base
            and Path(rel).suffix.lower() in audio.AUDIO_SUFFIXES
        ),
        key=str.casefold,
    )
    return matches[0] if matches else None


# ---------------------------------------------------------------------------
# pack
# ---------------------------------------------------------------------------


def pack_dir(
    root,
    out,
    *,
    name: Optional[str] = None,
    sfz_file: Optional[str] = None,
    presets_file: Optional[str] = None,
    wav: bool = False,
    inline_includes: bool = False,
    validate: bool = False,
    deflate: bool = False,
    pads: bool = False,
    force: bool = True,
) -> dict:
    """Pack a directory into a FIZZY ORANGE ZIP; returns a short summary."""
    root = Path(root).expanduser().resolve()
    if not root.is_dir():
        raise SfzPkgError("not a directory: %s" % root)
    out = Path(out).expanduser()
    if out.exists() and not force:
        raise SfzPkgError("refusing to overwrite %s (use --force)" % out)

    sources: Dict[str, Path] = dict(archive.collect_files(root))
    sfz_rels = [rel for rel in sources if rel.lower().endswith(".sfz")]
    sfz_bases: Dict[str, Path] = {rel: sources[rel].parent for rel in sfz_rels}
    if sfz_file:
        selected = _resolve_input(root, sfz_file)
        if not selected.is_file():
            raise SfzPkgError("--sfz file not found: %s" % selected)
        rel = _path_rel(root, selected) or archive.safe_rel(selected.name)
        sources.setdefault(rel, selected)
        sfz_bases[rel] = selected.parent
        sfz_rels = [rel]
    if not sfz_rels:
        raise SfzPkgError("no .sfz files found under %s (use --sfz FILE)" % root)

    # Read (and optionally expand) each instrument before transcoding so sample
    # rewrites also cover text coming from included files.
    sfz_texts: Dict[str, str] = {}
    for rel in sfz_rels:
        text = _read_text(sources[rel])
        if inline_includes:
            text = sfz.inline_includes(text, sfz_bases[rel])
        sfz_texts[rel] = text

    payloads: Dict[str, bytes] = {}
    converted: Dict[str, str] = {}
    if wav:
        with tempfile.TemporaryDirectory(prefix="sfzpkg-wav-") as temp:
            tempdir = Path(temp)
            used = set(sources)
            for rel, path in sorted(sources.items()):
                if audio.needs_transcode(path):
                    new_rel = _unique_rel(_wav_rel(rel), used)
                    used.add(new_rel)
                    target = tempdir / new_rel
                    audio.transcode_to_wav(path, target)
                    payloads[new_rel] = target.read_bytes()
                    converted[rel.casefold()] = new_rel
                else:
                    payloads[rel] = path.read_bytes()
    else:
        for rel, path in sources.items():
            payloads[rel] = path.read_bytes()

    if converted:
        mapping: Dict[str, str] = {}
        for rel, text in sfz_texts.items():
            for _, ref in sfz.sample_refs(text):
                resolved = _resolve_local_ref(ref, rel, sources)
                if resolved and resolved.casefold() in converted:
                    mapping[sfz.ref_key(ref)] = converted[resolved.casefold()]
        for rel in sfz_texts:
            sfz_texts[rel] = sfz.rewrite_sample_paths(sfz_texts[rel], mapping)
    for rel, text in sfz_texts.items():
        payloads[rel] = text.encode("utf-8")

    extra_presets: List[dict] = []
    if presets_file:
        path = _resolve_input(root, presets_file)
        if not path.is_file():
            raise SfzPkgError("--presets file not found: %s" % path)
        rel = _add_source(root, sources, path, "presets/" + path.name)
        payloads[rel] = path.read_bytes()
        extra_presets = presets.load_presets(path)

    manifest_name = archive.find_member(list(payloads), "fizzy.json")
    if manifest_name is not None:
        manifest = presets.load_manifest(payloads[manifest_name])
    else:
        manifest = presets.build_manifest(name or root.name, sfz_rels)
    if name:
        manifest["name"] = name
    instrument_sfzs = presets.instrument_sfz_paths(manifest)
    known = {rel.casefold() for rel in instrument_sfzs}
    for rel in sfz_rels:
        if rel.casefold() not in known:
            manifest.setdefault("instruments", []).append(
                {"name": Path(rel).stem, "sfz": rel}
            )
    if extra_presets:
        manifest["presets"] = presets.merge_presets(
            manifest.get("presets") or [], extra_presets
        )
    if pads:
        manifest["pads"] = True
    payloads["fizzy.json"] = presets.dumps_manifest(manifest)

    if archive.find_member(list(payloads), "README.txt") is None:
        sample_count = sum(
            1 for rel in payloads if Path(rel).suffix.lower() in audio.AUDIO_SUFFIXES
        )
        lines = [
            str(manifest.get("name") or root.name),
            "",
            "Open this ZIP in the FIZZY ORANGE Editor page, or unpack it onto host media.",
            "",
        ]
        lines += ["instrument: %s" % rel for rel in sfz_rels]
        lines.append("samples: %d" % sample_count)
        if converted:
            lines.append("audio: converted to 16-bit PCM WAV by sfzpkg")
        payloads["README.txt"] = ("\n".join(lines) + "\n").encode("utf-8")

    problems = archive.validate_package(payloads) if validate else []
    if problems:
        for problem in problems:
            info(str(problem))
        errors = [p for p in problems if p.level == "error"]
        if errors:
            raise ValidationError("validation failed with %d error(s)" % len(errors))

    archive.write_zip(out, payloads, deflate)
    sample_count = sum(
        1 for rel in payloads if Path(rel).suffix.lower() in audio.AUDIO_SUFFIXES
    )
    return {
        "out": out,
        "entries": len(payloads),
        "instruments": len(sfz_rels),
        "samples": sample_count,
        "converted": len(converted),
        "problems": problems,
    }


def cmd_pack(args) -> None:
    result = pack_dir(
        args.dir,
        args.out,
        name=args.name,
        sfz_file=args.sfz,
        presets_file=args.presets,
        wav=args.wav,
        inline_includes=args.inline_includes,
        validate=args.validate,
        deflate=args.deflate,
        pads=args.pads,
    )
    print(result["out"])
    info(
        "packed %d entries (%d instrument(s), %d sample(s), %d converted audio)"
        % (result["entries"], result["instruments"], result["samples"], result["converted"])
    )


# ---------------------------------------------------------------------------
# convert
# ---------------------------------------------------------------------------


def _index_samples(dirs: Sequence[str]) -> Tuple[dict, dict]:
    """Index audio files by relative path and by basename (case-insensitive)."""
    by_rel: Dict[str, Tuple[Path, Path]] = {}
    by_base: Dict[str, List[Tuple[Path, Path]]] = defaultdict(list)
    for raw in dirs:
        directory = Path(raw).expanduser().resolve()
        if not directory.is_dir():
            raise SfzPkgError("sample directory not found: %s" % directory)
        for path in sorted(directory.rglob("*")):
            if path.is_file() and audio.is_audio(path):
                rel = path.relative_to(directory).as_posix()
                by_rel.setdefault(rel.casefold(), (directory, path))
                by_base[path.name.casefold()].append((directory, path))
    return by_rel, by_base


def _resolve_sample(ref: str, by_rel: dict, by_base: dict) -> Optional[Tuple[Path, Path]]:
    """Resolve a region's sample reference to ``(sample_root, file)``."""
    candidate = posixpath.normpath(sfz.normalize_ref(ref))
    found = by_rel.get(candidate.casefold())
    if found:
        return found
    matches = by_base.get(posixpath.basename(candidate).casefold(), [])
    return matches[0] if matches else None


def convert_patch(
    input_path,
    sample_dirs,
    out_dir,
    *,
    name: Optional[str] = None,
    map_file: Optional[str] = None,
    wav: bool = False,
    force: bool = False,
) -> dict:
    """Convert a Kontakt/NNXT patch into an SFZ folder; returns a summary."""
    patch = Path(input_path).expanduser().resolve()
    if not patch.is_file():
        raise SfzPkgError("patch file not found: %s" % patch)
    out = Path(out_dir).expanduser().resolve()
    if out.exists() and any(out.iterdir()) and not force:
        raise SfzPkgError("refusing to overwrite non-empty %s (use --force)" % out)
    out.mkdir(parents=True, exist_ok=True)

    if map_file:
        source_name, regions = nnxt.load_map(map_file)
        kind = "explicit --map"
    else:
        suffix = patch.suffix.lower()
        if suffix in (".nki", ".nkm"):
            source_name, regions = kontakt.parse_nki(patch)
            kind = "Kontakt K2_Container"
        elif suffix in (".nnxt", ".pch"):
            source_name, regions = nnxt.scan_patch(patch)
            kind = "NNXT binary scan (best-effort)"
        elif kontakt.detect_kontakt(patch):
            source_name, regions = kontakt.parse_nki(patch)
            kind = "Kontakt K2_Container"
        elif nnxt.detect_nnxt(patch):
            source_name, regions = nnxt.scan_patch(patch)
            kind = "NNXT binary scan (best-effort)"
        else:
            raise ConversionError(
                "unrecognized patch format for %s (expected .nki/.nkm or .nnxt/.pch); "
                "use --map JSON to provide the zones explicitly" % patch.name
            )

    instrument_name = slugify(name or source_name or patch.stem)
    by_rel, by_base = _index_samples(sample_dirs)
    used: set = set()
    copied_files: List[Tuple[str, Path]] = []
    unresolved: List[str] = []
    copied = 0
    regions_out = []

    for region in regions:
        found = _resolve_sample(region.sample, by_rel, by_base)
        if found is None:
            base = posixpath.basename(sfz.normalize_ref(region.sample)) or "sample.wav"
            try:
                expected = archive.safe_rel("samples/" + base)
            except SfzPkgError:
                expected = "samples/sample.wav"
            warn(
                "unresolved sample %r; keeping SFZ line sample=%s" % (region.sample, expected)
            )
            unresolved.append(region.sample)
            regions_out.append(region.with_sample(expected))
            continue
        root, src = found
        rel = _unique_rel("samples/" + path_rel(root, src), used)
        used.add(rel)
        dest = out / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        copied += 1
        copied_files.append((rel, dest))
        regions_out.append(region.with_sample(rel))

    if wav:
        ref_fix: Dict[str, str] = {}
        for rel, dest in copied_files:
            if dest.suffix.lower() == ".wav":
                continue
            wav_rel = _unique_rel(_wav_rel(rel), used)
            used.add(wav_rel)
            wav_dest = out / wav_rel
            audio.transcode_to_wav(dest, wav_dest)
            dest.unlink()
            ref_fix[rel.casefold()] = wav_rel
        if ref_fix:
            regions_out = [
                region.with_sample(ref_fix[region.sample.casefold()])
                if region.sample.casefold() in ref_fix
                else region
                for region in regions_out
            ]

    sfz_rel = instrument_name + ".sfz"
    sfz_path = out / sfz_rel
    sfz_path.write_text(
        sfz.regions_to_sfz(regions_out, name=instrument_name), encoding="utf-8"
    )
    (out / "fizzy.json").write_bytes(
        presets.dumps_manifest(presets.build_manifest(instrument_name, [sfz_rel]))
    )
    return {
        "kind": kind,
        "name": instrument_name,
        "sfz": sfz_path,
        "regions": len(regions_out),
        "copied": copied,
        "unresolved": unresolved,
    }


def path_rel(root: Path, path: Path) -> str:
    """Relative POSIX path used for copied samples."""
    return path.relative_to(root).as_posix()


def cmd_convert(args) -> None:
    result = convert_patch(
        args.input,
        args.samples,
        args.out,
        name=args.name,
        map_file=args.map,
        wav=args.wav,
        force=args.force,
    )
    print(result["sfz"])
    info(
        "%s: %d region(s), %d sample(s) copied, %d unresolved"
        % (result["kind"], result["regions"], result["copied"], len(result["unresolved"]))
    )


# ---------------------------------------------------------------------------
# inspect / build
# ---------------------------------------------------------------------------


def cmd_inspect(args) -> None:
    report = archive.inspect_zip(args.zip)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return
    print("package: %s" % report["package"])
    manifest = report["manifest"]
    print("format:  %s" % manifest.get("format", "unknown"))
    print("name:    %s" % manifest.get("name", "?"))
    print("entries:")
    for entry in report["entries"]:
        print(
            "  %s\t%d bytes\t%s"
            % (entry["name"], entry["size"], entry["compression"])
        )
    print("problems:")
    if not report["problems"]:
        print("  none")
    for problem in report["problems"]:
        print("  %s: %s" % (problem["level"].upper(), problem["message"]))


def cmd_build(args) -> None:
    with tempfile.TemporaryDirectory(prefix="sfzpkg-build-") as temp:
        converted_dir = Path(temp) / "converted"
        convert_patch(
            args.input,
            args.samples,
            converted_dir,
            name=args.name,
            map_file=args.map,
            wav=args.wav,
            force=True,
        )
        result = pack_dir(
            converted_dir,
            args.out,
            name=args.name,
            presets_file=args.presets,
            wav=False,  # convert_patch already transcoded when requested
            inline_includes=args.inline_includes,
            validate=True,
            deflate=args.deflate,
            force=args.force,
        )
    print(result["out"])
    info(
        "built %s: %d instrument(s), %d sample(s), %d validation warning(s)"
        % (
            result["out"],
            result["instruments"],
            result["samples"],
            sum(1 for p in result["problems"] if p.level == "warning"),
        )
    )


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sfzpkg", description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="cmd", required=True)

    pack = sub.add_parser("pack", help="build a FIZZY ORANGE package ZIP")
    pack.add_argument("--dir", required=True, help="folder containing SFZ files and samples")
    pack.add_argument("--out", required=True, help="output .zip path")
    pack.add_argument("--name", help="package name (overrides fizzy.json)")
    pack.add_argument("--sfz", help="use only this SFZ file instead of all .sfz files in --dir")
    pack.add_argument("--presets", help="preset JSON file to include and add to fizzy.json")
    pack.add_argument("--wav", action="store_true", help="transcode non-WAV audio with ffmpeg")
    pack.add_argument(
        "--inline-includes", action="store_true", help="recursively expand #include directives"
    )
    pack.add_argument(
        "--validate", action="store_true", help="check sample paths and opcodes before writing"
    )
    pack.add_argument(
        "--deflate", action="store_true", help="deflate entries (stored is the default)"
    )
    pack.add_argument(
        "--pads", action="store_true", help="pads play notes 36-50 instead of selecting presets"
    )
    pack.set_defaults(func=cmd_pack)

    convert = sub.add_parser("convert", help="convert a sampler patch into SFZ + samples")
    convert.add_argument("--input", required=True, help=".nki/.nkm or .nnxt/.pch patch")
    convert.add_argument(
        "--samples", action="append", required=True, metavar="DIR",
        help="directory to search for samples (repeatable)",
    )
    convert.add_argument("--out", required=True, help="output directory")
    convert.add_argument("--name", help="instrument/package name")
    convert.add_argument("--map", help="explicit region mapping JSON (reliable fallback)")
    convert.add_argument("--wav", action="store_true", help="transcode copied samples with ffmpeg")
    convert.add_argument("--force", action="store_true", help="allow overwriting files in --out")
    convert.set_defaults(func=cmd_convert)

    inspect = sub.add_parser("inspect", help="list a ZIP and validate its contents")
    inspect.add_argument("zip", help="package .zip to inspect")
    inspect.add_argument("--json", action="store_true", help="print a JSON report")
    inspect.set_defaults(func=cmd_inspect)

    build = sub.add_parser("build", help="convert a patch and pack it into one ZIP")
    build.add_argument("--input", required=True)
    build.add_argument(
        "--samples", action="append", required=True, metavar="DIR",
        help="directory to search for samples (repeatable)",
    )
    build.add_argument("--out", required=True, help="output .zip")
    build.add_argument("--name")
    build.add_argument("--map", help="explicit region mapping JSON")
    build.add_argument("--presets", help="preset JSON file to include")
    build.add_argument("--wav", action="store_true", help="transcode audio with ffmpeg")
    build.add_argument("--inline-includes", action="store_true")
    build.add_argument("--deflate", action="store_true")
    build.add_argument("--force", action="store_true", help="allow overwriting the output ZIP")
    build.set_defaults(func=cmd_build)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entry point used by both ``python -m sfzpkg`` and the console script."""
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
        return 0
    except SfzPkgError as exc:
        print("sfzpkg: %s" % exc, file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        info("interrupted")
        return 130
