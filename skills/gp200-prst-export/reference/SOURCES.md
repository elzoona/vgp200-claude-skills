# Sources and credits

This tool didn't reverse-engineer the GP-200 `.prst` format from scratch. It
stands on two open-source community editors and Valeton's own documentation.
This file records who did what, and — just as importantly — where to go when
the effect catalog or the file format needs updating.

## gp200-studio — primary source

**https://github.com/kabir0st/gp200-studio** · GPL-3.0-only · ported at commit
`8be5ef1` (2026-07-18), last synced at `7bd2427`

A browser-based editor, patch manager and loop station for the GP-200. This is
the project this tool is derived from, in two distinct ways:

**The encoder is a port.** `scripts/prst_lib.py` is a pure-Python translation of
the "no rawSource" (build-from-scratch) path through:

| gp200-studio file | What it provides |
| --- | --- |
| `src/core/PRSTEncoder.ts` | The 1224-byte file layout, field offsets, checksum |
| `src/core/BufferGenerator.ts` | Buffer assembly and the MRAP chunk |
| `src/core/controlRecords.ts` | The controls tail (0x3B0–0x4C5): EXP and CTRL records |
| `src/core/defaultPreset.ts` | INIT-patch behaviour: module defaults, which blocks start enabled |
| `src/core/PRSTDecoder.ts` | Used for verification — round-tripping our output back |

**The effect database is a snapshot.** `data/effect_data.json` is extracted from:

| gp200-studio file | What it provides |
| --- | --- |
| `src/core/effectNames.ts` | `EFFECT_MAP` (305 effect ids → name + module), `SLOT_MODULES` |
| `src/core/effectParams.ts` | `EFFECT_PARAMS` (1352 parameter definitions: ranges, defaults, switch options) |
| `src/core/defaultPreset.ts` | `MODULE_DEFAULTS` (the default effect per slot) |

gp200-studio in turn generates `effectParams.ts` from Valeton's `algorithm.xml`
via its own `scripts/generate-effect-params.mjs`, so the parameter values are
the device manufacturer's, not anyone's guesses.

gp200-studio added a GPL-3.0-only `LICENSE` (and `"license": "GPL-3.0-only"` in
`package.json`) in commit `1218246`, just after the original port commit; every
commit synced since carries it. This tool is a derivative work — a modified
Python translation of its encoder and codec plus an extract of its effect
tables — and is therefore distributed under GPL-3.0-only as well (see
`LICENSE` at the repository root).

## gp200editor / Preset Forge — format documentation

**https://github.com/phash/gp200editor**
· GPL-3.0 · referenced at commit `47a1b76` (2026-07-04)

An independent browser-based GP-200 preset editor. No code was taken from it —
it served as a second, independently-derived description of the binary format,
which is what made it possible to trust the layout rather than infer it from a
single implementation:

- `docs/prst-format.md` — the `.prst` binary layout in German: magic `TSRP`,
  device id `2-PG`, 1224-byte user presets (1176 for factory), name at 0x44 and
  author at 0x54 (16 ASCII bytes each), 11 × 72-byte effect blocks from 0xA0,
  15 × LE float32 parameters at +12, and the effect-code high-byte → module
  mapping. Every offset in `scripts/prst_lib.py` agrees with this document.
- `docs/sysex-protocol.md` — the USB MIDI SysEx protocol, relevant if this tool
  ever grows the ability to push presets to the device directly.
- `prst/*.prst` — real Valeton-exported preset files, useful as byte-level
  comparison targets.

## Valeton official documentation

Ships inside a gp200-studio checkout under `valeton-docs/`:

- `GP200 - Effects v1.8.0.pdf` — the official effect list with parameter
  descriptions. The authority when a parameter's *meaning* is unclear (the JSON
  gives you the range; the PDF tells you what the knob does).
- `Manual_EN_Firmware V1.8.0.pdf` — device manual, firmware 1.8.0.

The ultimate upstream is `algorithm.xml`, shipped inside the Windows-only
Valeton editor. On a Wine install it lives at:

```
~/.wine/drive_c/Program Files/Valeton/GP-200/Resource/GP-200/File/algorithm.xml
```

## gp200-guitar-presets — the companion skill

`gp200-guitar-presets` — the skill that *designs* presets (song analysis, amp
and effect choice, real documented values), reading the user's own gear notes
and reference library. This tool is its export
step: that skill decides the tone, this one turns the decision into a file.
Keeping them separate means preset design can improve without touching the
binary encoder.

## Refreshing the effect data

When gp200-studio updates its effect tables — usually because a GP-200 firmware
release changed `algorithm.xml` — pull it and re-extract:

```bash
git -C ~/git/gp200-studio pull

# See whether anything actually changed, without writing:
python3 scripts/extract_effect_data.py --studio ~/git/gp200-studio --check

# Apply it, then bring the generated reference and examples back in line:
python3 scripts/extract_effect_data.py --studio ~/git/gp200-studio
python3 scripts/generate_reference.py
python3 scripts/build_preset.py --validate examples/example_preset.json
python3 scripts/build_preset.py --validate examples/example_preset_swapped_pedal.json
```

`--check` reports added, removed and renamed effects plus which effects had
parameter changes, so a firmware bump is reviewable rather than a 133 KB diff.
It exits non-zero when the snapshot is stale, which makes it usable as a
periodic freshness check.

The extractor parses the TypeScript literals directly and refuses to write a
partial result. If gp200-studio restructures those files it will fail with a
message naming the table it couldn't read — fix the extractor rather than
hand-editing `data/effect_data.json`, since the hand edit is invisible the next
time anyone re-extracts.

## How the encoder was verified

1. Byte-for-byte diff against gp200-studio's own TypeScript encoder for the same
   preset spec — with default controller wiring and with custom EXP/CTRL
   assignments. Zero differences in both cases.
2. Round-trip through gp200-studio's `PRSTDecoder`: the generated file parses
   back with the same name, author, slot, effects and EXP/CTRL assignments.
3. Cross-check of every offset against gp200editor's independently-written
   `docs/prst-format.md`.

## Upstream sync log

- **2026-09-17** — synced against gp200-studio `7bd2427` and gp200editor
  `0c90654`. Corrected the patch-pan offset from `0x3C` to `0x3A` and
  documented `OFFSET_PATCH_STYLE` (`0x3C`), `OFFSET_FX_MODE` (`0x42`) and
  `OFFSET_NOTE` (`0x64`, 40 bytes), all per `PRSTEncoder.ts`. The SysEx
  upload path (`buildUploadImage`, `buildUploadChunks`, `nibbleEncode`,
  `buildReadRequest`, `buildPresetChange`) was verified unchanged across
  that range.
- **2026-10-10** — re-checked licensing: gp200-studio is GPL-3.0-only (added in
  `1218246`) and gp200editor remains GPL-3.0. This tool adopted GPL-3.0-only.
