# Sonatina Symphonic Orchestra examples

Reduced, playable subsets of [Sonatina Symphonic Orchestra](https://github.com/peastman/sso).
Drop any ZIP onto the FIZZY ORANGE Editor page to load it. These are teaching
examples, not full copies of the original library. The seven melodic packs have
no key gaps: every SFZ velocity layer covers MIDI 0–127. `sso-percussion.zip`
is the exception by design — it sets `"pads": true` so pads 1–15 play the drum
hits on notes 36–50, with pad 16 still all-notes-off and presets selected by
F2 / A / B.

Source: <https://github.com/peastman/sso/tree/master/Sonatina%20Symphonic%20Orchestra>
License: **Creative Commons Sampling Plus 1.0** (noncommercial, attribution required).
Original library by Mattias Westlund; repository maintained by peastman.

| ZIP | Instruments | Samples | Decoded PCM |
|---|---|---:|---:|
| [`sso-strings.zip`](sso-strings.zip) | 1ST VIOLINS, CELLI, BASSES, VIOLAS | 24 | 3.21 MB |
| [`sso-brass.zip`](sso-brass.zip) | TRUMPETS, FRENCH HORNS, TROMBONES, TUBA | 24 | 3.03 MB |
| [`sso-woodwinds.zip`](sso-woodwinds.zip) | FLUTES, OBOES, CLARINETS, BASSOONS | 24 | 3.03 MB |
| [`sso-percussion.zip`](sso-percussion.zip) | TIMPANI, GLOCKENSPIEL, XYLOPHONE, BASS + SNARE | 19 | 1.30 MB |
| [`sso-grand-piano.zip`](sso-grand-piano.zip) | GRAND PIANO | 16 | 2.56 MB |
| [`sso-harpsichord.zip`](sso-harpsichord.zip) | HARPSICHORD 8FT, HARPSICHORD 4FT | 16 | 1.35 MB |
| [`sso-organ.zip`](sso-organ.zip) | OPEN DIAPASON, GEDACT 8FT | 16 | 2.02 MB |
| [`sso-vibraphone.zip`](sso-vibraphone.zip) | VIBRAPHONE | 9 | 1.32 MB |

Each ZIP contains a `README.txt` with the source and the CC Sampling Plus 1.0
license note, so that attribution travels with the package when it is uploaded.

Run `python scripts/full_keyboard.py --check` from the repository root to verify
the coverage and pad mapping. The percussion package was also played through
the real Editor byte protocol with `VIBE_EV_PAD` events: `STATUS` reported
`pad play`, with no errors and no NaNs. All eight ZIPs pass `sfzpkg inspect`.
