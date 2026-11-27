# sfzpkg

`sfzpkg` builds and inspects **FIZZY ORANGE** instrument packages. A package is
a plain ZIP following `docs/PROTOCOL.md`:

```
fizzy.json                  package manifest (format "fizzy-orange/1")
piano.sfz                   one or more SFZ instruments
samples/Piano_C4.wav        samples referenced by the SFZ files
presets/extra.json          optional additional preset banks
README.txt                  optional notes
```

Only the Python standard library is required (3.8+). `ffmpeg` is optional and
is only needed for `--wav`. Install the CLI with:

```sh
python -m pip install -e ./tools
sfzpkg --help
```

From the repository root you can also run it without installing:

```sh
PYTHONPATH=tools python -m sfzpkg --help
```

The examples below use the installed `sfzpkg` command.

## Pack

Collect `.sfz` files, samples (`.wav`, `.mp3`, `.flac`, `.ogg`, `.aiff`,
`.aif`), `fizzy.json`, `presets/*.json` and `README.txt` from a folder:

```sh
sfzpkg pack --dir instruments/piano --out piano.zip --name "FIZZY PIANO"
sfzpkg pack --dir instruments/piano --out piano.zip --validate
sfzpkg pack --dir instruments/piano --sfz piano.sfz --out one.zip
sfzpkg pack --dir instruments/piano --presets banks/extra.json --out piano.zip
sfzpkg pack --dir instruments/drums --out drums.zip --pads
```

* ZIP entries are **stored uncompressed by default** so the firmware/Editor can
  read them directly; `--deflate` opts into compression.
* If `fizzy.json` is absent it is generated with the instrument list and an
  empty preset list. `--name` overrides the manifest name. `--pads` adds
  `"pads": true` so pads 1–15 play notes 36–50 instead of choosing presets
  (for drum packages).
* `--validate` checks that every `sample=` path resolves to a ZIP entry
  (case-insensitive relative path, then basename) and reports unsupported
  opcodes as warnings. Errors make `pack` fail.
* `--inline-includes` recursively expands `#include "file.sfz"` directives;
  include cycles and missing files are errors.
* `--wav` transcodes non-WAV audio to 16-bit PCM WAV with `ffmpeg` and rewrites
  the SFZ sample paths. It fails clearly if ffmpeg is not installed.
* Sample files are limited by the firmware to 64 files, 512 regions and 16 MB
  total PCM; `pack` does not enforce those limits for you.

## Convert sampler patches

```sh
sfzpkg convert --input Piano.nki --samples samples/ --out converted/
sfzpkg convert --input Guitar.nkm --samples samples/ --samples more/ \
               --out converted/ --name "GUITAR" --wav
sfzpkg convert --input Bass.nnxt --samples samples/ --out converted/ --force
```

The result is `OUT/<name>.sfz`, `OUT/fizzy.json` and `OUT/samples/*`. Sample
directories are searched by relative path and by case-insensitive basename.
Missing samples are **warnings**: the `sample=` line is retained so you can put
the file in place later. Use `--force` to overwrite a non-empty output folder.

### Explicit mapping (`--map`)

```sh
sfzpkg convert --input patch.nnxt --samples samples/ --out converted/ \
               --map zones.json
```

```json
{
  "name": "MY PIANO",
  "regions": [
    {
      "sample": "Piano_C4.wav",
      "key": 60, "lokey": 0, "hikey": 127,
      "lovel": 0, "hivel": 127,
      "volume_db": 0, "pan": 0, "tune": 0,
      "loop_start": 0, "loop_end": 0, "loop_mode": "no_loop"
    }
  ]
}
```

Every field except `sample` has a default. `offset` and `end` (sample start/end
in frames) are accepted as optional extras. Valid `loop_mode` values are
`no_loop`, `one_shot`, `loop_continuous` and `loop_sustain`.

## Inspect

```sh
sfzpkg inspect piano.zip
sfzpkg inspect piano.zip --json
```

Lists entries with sizes/compression, checks the manifest and reports missing
sample paths, unsupported opcodes and invalid preset instrument indexes.

## Build (convert + pack)

```sh
sfzpkg build --input Piano.nki --samples samples/ --out piano.zip --name "PIANO"
sfzpkg build --input patch.nnxt --samples samples/ --out piano.zip --map zones.json
```

Runs `convert` in a temporary directory, then `pack --validate` into one ZIP.
`--wav`, `--deflate`, `--presets`, `--inline-includes` and `--force` are also
accepted.

## Conversion limitations

* **Kontakt:** only files with a zlib/gzip-compressed `K2_Container` XML payload
  (typically produced by Translator 7) are supported. Those are found by
  scanning the first 256 bytes for `78 01`/`78 9c`/`78 da`. Native modern NI
  containers are proprietary and **cannot be parsed**; the tool reports the
  offending filename and suggests `--map`. Group/program inheritance is not
  evaluated; only zone-level `V` values are mapped. Kontakt `loopMode` 1 maps to
  `loop_sustain` and 2 to `loop_continuous`; `zoneVolume` (linear) is converted
  to dB.
* **Reason NNXT / `.pch`:** the patch format is proprietary and undocumented.
  The binary scanner only finds printable sample filenames with adjacent small
  integer key/velocity fields, or embedded XML/JSON text. It is inherently
  best-effort and may miss or mis-map zones. **`--map` is the reliable
  fallback** for NNXT and for any patch the scanner cannot read.
* Only the SFZ opcodes listed in `docs/PROTOCOL.md` are understood by the
  firmware. Unknown opcodes are ignored and reported as warnings; `inspect` and
  `pack --validate` do not remove them.
* `--wav` preserves the source sample rate and writes 16-bit PCM; it does not
  enforce 1–2 channels or the firmware's size limits. Conversion is only as
  good as the installed ffmpeg build (some codecs may be unavailable).
* Sample path matching is case-insensitive, but ambiguous basename matches
  pick the first result in sorted order; use relative paths in SFZ files when
  duplicate filenames exist.

## Tests

From the repository root:

```sh
PYTHONPATH=tools python -m unittest discover -s tools/tests -t tools
```
