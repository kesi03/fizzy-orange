# FIZZY ORANGE

An SFZ sample player for **VIBE FM**. It boots with a playable sine demo, and a
custom Editor page lets you drop a ZIP containing `fizzy.json`, SFZ files and
audio. Packages are stored in the firmware's own storage, rebuilt on every
start, and played with up to 32 voices from the keys, pads, MIDI or a DAW.

The same `.wasm` runs in the VIBE FM VST, the browser emulator and on the VIBE
hardware. The firmware is plain C built with the VIBE FM SDK; the Editor page is
plain HTML/CSS/JS and talks only through `window.vibe`.

## What is in here

```
firmware/fizzy-orange/    firmware C, manifest, icon, Editor page, wasmtest script
tools/sfzpkg/             stdlib-only Python CLI to pack and convert packages
tools/README.md           sfzpkg reference and conversion limitations
docs/PROTOCOL.md          ZIP layout, Editor byte protocol, SFZ subset
examples/                 reduced, playable SFZ example packages (ZIPs)
scripts/build.ps1         Windows build wrapper (Zig + python3 shim)
scripts/full_keyboard.py  example coverage check / pad-mapping updater
sdk/                      VIBE FM SDK (vendored from bar-fly)
sdk/build/                built firmware: fizzy-orange.wasm / .zip / .editor/
```

Ready-to-install artifact: **`sdk/build/fizzy-orange.zip`**. Drop it onto the
VIBE FM VST window (or load it in the emulator). No setup is needed to make a
sound: the built-in `FIZZY DEMO` sine is already selected.

## Loading a package

1. Make a ZIP with `sfzpkg pack` (stored ZIPs work everywhere; deflated ZIPs need
   a host with `DecompressionStream('deflate-raw')`):

   ```sh
   python -m pip install -e ./tools
   sfzpkg pack --dir instruments/piano --name "FIZZY PIANO" --validate --out piano.zip
   ```

   Without installing:

   ```sh
   PYTHONPATH=tools python -m sfzpkg pack --dir instruments/piano --out piano.zip --validate
   ```

2. Open the firmware's custom Editor page, drop `piano.zip` on the upload zone,
   and wait for the status line to return to `ready`. The page decodes WAV, MP3,
   FLAC, OGG, AIFF and Opus with Web Audio, uploads 16-bit PCM WAV in 2048-byte
   chunks, then asks the firmware to rebuild.

3. Alternatively use the Editor's **LOAD PATH** command with an unpacked folder
   on host media. The firmware walks the folder for `.sfz`, `.json` and audio
   files, stores them, and rebuilds. Files survive a restart.

The package contract is documented in [`docs/PROTOCOL.md`](docs/PROTOCOL.md).
The firmware understands `<global>`, `<group>` and `<region>` with the common
key/velocity, sample, loop and `ampeg_*` opcodes; unknown opcodes are counted
and shown in the status instead of breaking playback.

## Ready-made examples

[`examples/`](examples/README.md) contains four playable, trimmed-down packages
made from public sfzinstruments SFZ libraries: jRhodes3c, jRhodes3d, SteelDrum
and Osiris Piano. It also links to eight
[Sonatina Symphonic Orchestra packs](examples/sso/README.md): strings, brass,
woodwinds, percussion, grand piano, harpsichord, organ and vibraphone. Each is a
small subset (9–24 samples, about 1–3 MB decoded PCM) with attribution embedded
in the ZIP; drop any ZIP onto the Editor page to try it. The 11 melodic packages
have no key gaps: every SFZ velocity layer covers MIDI 0–127 by sharing the
nearest sampled note. `sso-percussion.zip` opts into pad mode instead: pads 1–15
play the drum hits on notes 36–50, pad 16 remains all-notes-off, and presets are
selected with F2 / A / B.

## Controls

| Control | Action |
|---|---|
| Keys / MIDI / DAW notes | Play the selected preset |
| Pads 1–15 | Select preset 1–15; on `pads: true` packages, play drum hits 36–50 |
| Pad 16 / REC / STOP | All notes off |
| F1 / F2 | Next page / next preset |
| F3 / F4 | Audition C4 / all notes off |
| F5 / F6 | Controls overview / rebuild stored package |
| K1–K6 | SOUND · ENV · TONE · VOICE · FILES · HELP pages |
| Encoders 1–4 | The four values shown above F1–F6 |
| A / B | Volume / previous–next preset |
| DATA + ENTER | FOCUS: walk a value; press to act |
| HOME (tap) / RETURN | Show / close the controls overview |
| AUD button on the display | Audition C4 |

Parameters are exposed to the DAW with stable ids (`select`, `volume`,
`octave`, `transpose`, `attack`, `release`, `sustain`, `velocity`, `tune`,
`spread`) and grouped as SOUND / ENV / TONE.

## Converting Kontakt and Reason patches

```sh
sfzpkg convert --input Piano.nki --samples samples/ --out converted/
sfzpkg convert --input Bass.nki --samples samples/ --out converted/ --wav
sfzpkg build   --input patch.nnxt --samples samples/ --out piano.zip --map zones.json
sfzpkg inspect piano.zip --json
```

* **Kontakt:** legacy zlib-compressed `K2_Container` XML (typically from
  Translator 7) is parsed into SFZ zones. Modern/encrypted NI containers cannot
  be read; the tool fails clearly and suggests an explicit `--map` JSON file.
* **Reason NNXT / `.pch`:** proprietary. The binary/XML scanner is best-effort;
  `--map` is the reliable path.
* `--wav` uses `ffmpeg` if installed. Only the firmware's SFZ subset is
  guaranteed to be understood; `pack --validate` and `inspect` report warnings.

See [`tools/README.md`](tools/README.md) for the full CLI reference.

## Build from source

Requirements: Zig 0.13.0 and Python 3. On Windows, use the wrapper because the
SDK's `python3` name can collide with the Microsoft Store alias:

```powershell
pwsh -File scripts/build.ps1
```

On macOS/Linux, from the repository root:

```sh
(cd sdk && sh tools/build.sh ../firmware/fizzy-orange)
```

Outputs go to `sdk/build/`: `fizzy-orange.wasm`, `fizzy-orange.zip`,
`fizzy-orange.editor/`, plus the generated JSON manifest and PNG icon.

## Validation

```powershell
# Windows: vibe-check.mjs needs HOME set
$env:HOME = $env:USERPROFILE
cd sdk
node tools/vibe-check.mjs build/fizzy-orange.wasm --manifest ../firmware/fizzy-orange/firmware.json
node tools/wasmtest.mjs build/fizzy-orange.wasm ../firmware/fizzy-orange/wasmtest.json out/
cd ..
python scripts/full_keyboard.py --check
$env:PYTHONPATH = 'tools'
python -B -m unittest discover -s tools/tests -t tools
python -B -m sfzpkg inspect examples/sso/sso-percussion.zip
```

Current results:

* `vibe-check` — **PASSED** (stage 1): format, manifest, icon, Editor page,
  two flows, sound without drawing, HOME, 60 s random events, no NaN, realtime
  limits, storage and Editor checks. WABT extension validation was skipped
  locally because `wasm-validate` is not installed.
* `wasmtest` — peak `0.16`, RMS `0.0374`, `nan: 0`, audio CPU `0.5%`,
  `draw_ms: 0.27`, `draw_max_ms: 3.35`.
* `full_keyboard.py --check` — 11 melodic ZIPs cover MIDI 0–127 in every
  velocity layer; `sso-percussion.zip` is the only pad-mode ZIP.
* `sfzpkg inspect` — all 12 example ZIPs: 0 errors, 0 warnings.
* End-to-end Editor protocol — `sso-organ.zip` (2 instruments, 16 regions,
  16 samples), `sso-vibraphone.zip` (9 regions/samples), `jrhodes3c-mini.zip`
  (16 regions/samples) and pad-mode `sso-percussion.zip` (4 instruments,
  19 samples) were unzipped, uploaded with `0x10`/`0x11`/`0x12`, built with
  `0x14`, then played. The percussion run used `VIBE_EV_PAD` events for pads
  1–3 and reported `pad play` in `STATUS`; all runs had no `ERROR` replies,
  no NaNs and nonzero audio.
* `tools/tests` — 16 tests, **OK**.

## Limits

64 uploaded files / samples, 512 regions, 16 MB decoded PCM, 32 voices, 4 MB
per uploaded file, one `vibe.send()` per message with 2048-byte chunks. The
Editor folder is ~45 KB and has no network access or external assets. The
firmware must remain fully playable without the Editor page; it does.

The SDK under `sdk/` is Apache-2.0 (see `sdk/LICENSE` and `sdk/NOTICE`). The
firmware declares Apache-2.0 in `firmware/fizzy-orange/firmware.json`.
