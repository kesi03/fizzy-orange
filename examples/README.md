# Playable example packages

These ZIPs are **reduced, firmware-ready subsets** of public SFZ instruments from
[sfzinstruments](https://github.com/sfzinstruments). They are small enough to open
directly in the FIZZY ORANGE custom Editor page: drop the ZIP on the upload zone
and wait for the status line to return to `ready`.

For orchestral sections plus piano, harpsichord, organ and vibraphone, see the
[Sonatina Symphonic Orchestra examples](sso/README.md).

They are teaching examples, not full copies of the original instruments. Each was
built by selecting a small note/velocity subset, converting to **16-bit mono WAV
at 22.05 kHz**, trimming the long samples, rewriting the SFZ to the
[firmware-supported subset](../docs/PROTOCOL.md#3-sfz-subset-understood-by-the-firmware),
and packing with `sfzpkg pack --validate`.

Every velocity layer in these melodic packages is mapped across MIDI 0–127, so
there are no gaps when playing up and down the keyboard. Neighbouring sampled
notes share the keys between them; no extra samples were added. The eight SSO
packages use the same coverage scheme, except the percussion pack, which plays
its drum hits from the pads instead.

| ZIP | Instrument | Samples | Decoded PCM | Source | License |
|---|---|---:|---:|---|---|
| [`jrhodes3c-mini.zip`](jrhodes3c-mini.zip) | jRhodes3c looped Rhodes | 16 | 1.14 MB | [jlearman.jRhodes3c](https://github.com/sfzinstruments/jlearman.jRhodes3c) | Samples CC BY 4.0; control files CC0 |
| [`jrhodes3d-mini.zip`](jrhodes3d-mini.zip) | jRhodes3d full Rhodes | 16 | 2.02 MB | [jlearman.jRhodes3d](https://github.com/sfzinstruments/jlearman.jRhodes3d) | Samples CC BY 4.0; control files CC0 |
| [`steeldrum-mini.zip`](steeldrum-mini.zip) | Steel Drum | 24 | 1.93 MB | [jlearman.SteelDrum](https://github.com/sfzinstruments/jlearman.SteelDrum) | Unlicense / public domain |
| [`osiris-piano-mini.zip`](osiris-piano-mini.zip) | Osiris upright piano (MicA) | 16 | 2.02 MB | [Osiris_Piano](https://github.com/sfzinstruments/Osiris_Piano) | CC0 1.0 Universal |

All four packages are below the firmware limits of 64 files/samples and 16 MB
decoded PCM. Each ZIP also contains a `README.txt` with the same provenance and
attribution information, because that note travels with the package when it is
uploaded.

## Attribution notes

* **jRhodes3c / jRhodes3d**: per the source `LICENSE`, samples are from (or based
  on, if that's the case) **jRhodes3d —
  <https://github.com/sfzinstruments/jlearman.jRhodes3d>**. The samples are
  licensed CC BY 4.0; control files and example clips are CC0.
* **SteelDrum**: released under the Unlicense / public domain dedication by
  Jeff Learman. No attribution is required, but the source is credited above.
* **Osiris_Piano**: released under CC0 1.0 Universal by Versilian Studios.

## What was changed

* jRhodes3c: 8 sampled notes × 2 velocity layers; original looped FLAC samples
  shortened to at most 2 s. Loop points were regenerated because ffmpeg does not
  carry FLAC loop metadata into WAV.
* jRhodes3d: 8 sampled notes × 2 velocity layers; full-length FLAC samples
  trimmed to 3 s.
* SteelDrum: 12 chromatic notes (C4–B4) × 2 dynamics; stereo FLAC downmixed to
  mono and trimmed to 2.5 s. One main take was kept from each round-robin set.
* Osiris Piano: MicA velocity layers 1 and 2 at 8 notes (C3–E5); stereo FLAC
  downmixed to mono and trimmed to 3 s. The original `#include` mapping modules
  were flattened into one SFZ.

## Checking the examples

From the repository root:

```sh
python scripts/full_keyboard.py --check
PYTHONPATH=tools python -m sfzpkg inspect examples/jrhodes3c-mini.zip
```

The coverage check reports 11 melodic ZIPs with full MIDI 0–127 coverage and
`sso-percussion.zip` as the pad-mode package. All 12 example ZIPs pass
`sfzpkg inspect` with 0 errors and 0 warnings.
