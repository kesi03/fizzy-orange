# FIZZY ORANGE internal contracts

This file is the contract between the firmware, the custom Editor page and the
`tools/sfzpkg` packer. Keep all three in step when changing a byte or a field.

## 1. Package layout (the ZIP handled by the Editor and by `sfzpkg pack`)

```
fizzy.json                  package manifest (required)
piano.sfz                   one or more SFZ instruments
samples/Piano_C4.wav        samples referenced by the SFZ files
presets/extra.json          optional additional preset banks
README.txt                  optional human-readable notes
```

`fizzy.json`:

```json
{
  "format": "fizzy-orange/1",
  "name": "FIZZY PIANO",
  "instruments": [
    { "name": "PIANO", "sfz": "piano.sfz" }
  ],
  "presets": [
    { "name": "SOFT", "instrument": 0, "description": "low velocity layer" }
  ],
  "pads": true
}
```

`"pads": true` is optional. It makes pads 1–15 play notes 36–50 instead of
selecting presets; the selected preset still comes from F2 / A / B, and pad 16
remains all-notes-off. Pad velocity comes from the `VIBE_EV_PAD` event value,
just like MIDI note velocity. It is intended for drum/percussion packages. The
demo and all ordinary packages default to pad-select behaviour when the field
is absent.

Sample audio may be `.wav`, `.mp3`, `.flac`, `.ogg`, `.aif`, `.opus`, `.m4a` or
`.mp4` inside the ZIP. The Editor decodes every audio entry with Web Audio and
uploads it as 16-bit PCM WAV. The packer can transcode to WAV with `ffmpeg` so
the package is portable.

## 2. Editor ⇄ firmware byte protocol

All commands arrive as `VIBE_EV_EDITOR_DATA`; replies are ASCII lines sent with
`vibe_editor_post()` and start with byte `0x02`. All integers are little-endian.

| Byte | Payload | Meaning |
|---|---|---|
| `0x10` | `len:u32`, `slot:u8`, `kind:u8`, `name_len:u8`, name | begin one file upload |
| `0x11` | `offset:u32`, data | next chunk of that file (max 2048 data bytes) |
| `0x12` | — | commit the file to firmware storage |
| `0x13` | — | discard the whole uploaded package |
| `0x14` | — | build instruments from committed files |
| `0x20` | — | request a status snapshot |
| `0x21` | `index:u32` | select an instrument/preset |
| `0x22` | — | all notes off |
| `0x23` | `note:u8`, `vel:u8` | audition a note |
| `0x30` | UTF-8 path | load an unpacked package from host media |

`kind`: `1` = SFZ text, `2` = WAV sample, `3` = `fizzy.json` / preset JSON.
`slot` is 0…63. The firmware stores the bytes under `fizz/f<slot>.bin` and keeps
the uploaded name for sample-path matching.

Status lines:

```
02 OK\n
02 STATUS <text>\n
02 ERROR <text>\n
02 PKG <name>\n
02 FILE <slot> <kind> <name>\n
02 INSTR <index> <regions> <samples> <name>\n
02 PRESET <index> <name>\n
02 SAMPLE <index> <name>\n
02 SELECTED <index>\n
```

The SDK outbox holds one message per event. A reply with several lines is
therefore posted as one `0x02`-prefixed message with newline-separated lines;
`0x02` appears only at the start of the message, not on every line. The Editor
splits `bytes.subarray(1)` on `\n`. This is the same batching pattern used by
the tested naminator Editor: queue all lines, post once, flush in `on_draw`.

## 3. SFZ subset understood by the firmware

Comments (`//`, `/* */`) and blank lines are ignored. `#include` is ignored by
the firmware; `sfzpkg pack --inline-includes` expands it first.

Headers: `<global>`, `<group>`, `<region>`. Group and global values are
inherited. Supported opcodes:

```
sample, key, lokey, hikey, pitch_keycenter, lovel, hivel,
volume, gain, pan, tune, finetune,
offset, end, loop_mode, loop_start, loop_end,
ampeg_attack, ampeg_decay, ampeg_sustain, ampeg_release, ampeg_hold
```

`loop_mode`: `no_loop`, `one_shot`, `loop_continuous`, `loop_sustain`.
Note names such as `C4`, `C#4`, `Db4` are accepted wherever a key is expected.
Unknown opcodes are ignored, counted and reported in the status line.

Limits: 64 sample files, 512 regions, 16 MB total PCM, 32 voices.

## 4. Limits that the Editor must respect

* one `vibe.send()` call ≤ 4096 bytes — use 2048-byte chunks;
* Editor folder ≤ 2 MB — plain HTML/CSS/JS only, no network and no external fonts;
* upload one file at a time, then send `0x14`;
* audio is uploaded as WAV, 16-bit, 1 or 2 channels, at the decoded sample rate.
