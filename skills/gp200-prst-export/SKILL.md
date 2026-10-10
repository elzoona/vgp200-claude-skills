---
name: "gp200-prst-export"
description: "Build ready-to-upload Valeton GP-200 .prst preset files from a description of amps, pedals and parameter values, using only real documented effect names. Use this whenever a GP-200 or Valeton patch should end up as a file rather than a table of knob settings — 'export this as a .prst', 'give me a file I can load in gp200-studio', 'make me a patch for this song I can upload to my GP-200', 'build presets for each section' — and also right after a GP-200 tone has been designed in conversation, since handing over a loadable file is almost always more useful than making the user dial 15 parameters by hand. Covers effect and parameter name lookup, EXP pedal and CTRL footswitch assignments, and batch-building a whole song's sections in one pass. Also flashes finished presets directly to the pedalboard over USB MIDI — use it for 'upload this to my GP-200', 'put these on the device', 'write this to slot 3B'."
---

# GP-200 .prst preset export

Turns a finished preset design (real effect/amp names, real documented
parameter values) into an actual binary `.prst` file that loads straight into
gp200-studio, the Valeton GP-200 editor, or the device. The user never types a
parameter by hand.

This does not design tones for you — it's the export step. Pick the effects and
values first (song analysis, amp choice, gain staging), then use this to make
the file.

## Run scripts by plugin root

The scripts live in this plugin, not in the user's project, and the working
directory is rarely the plugin directory. Use `${CLAUDE_PLUGIN_ROOT}`:

```bash
SCRIPTS="${CLAUDE_PLUGIN_ROOT}/skills/gp200-prst-export/scripts"
python3 "$SCRIPTS/build_preset.py" --validate my_preset.json
```

The spec JSON and the output directory belong wherever the user is working.
Write specs somewhere the user can find them, never into the plugin directory.

## Workflow

### 1. Look up real names before writing the spec

Every effect and parameter name has to match the device's own catalog for the
module slot you're putting it in. Names that look obvious are frequently wrong
("Green Overdrive" is `Green OD`; "Vox AC30" is an `AC` amp model with a
specific name), and the same display name can appear under several modules with
different parameters.

Query the catalog instead of reading the full 1700-line reference:

```bash
python3 "$SCRIPTS/lookup_effects.py" --modules          # the 11 slots, in chain order
python3 "$SCRIPTS/lookup_effects.py" DST                # every drive pedal available
python3 "$SCRIPTS/lookup_effects.py" AMP "AC Pre"       # params, ranges, defaults
python3 "$SCRIPTS/lookup_effects.py" --search tape      # find by partial name
```

The per-effect view is the one that matters most: it gives you each parameter's
exact name, its numeric range, and its default, so the values you write are in
range and named correctly on the first attempt. When a name doesn't resolve the
script suggests close matches and tells you if the effect lives under a
different module — take those suggestions rather than substituting something
plausible. Reading `reference/effects_by_module.md` directly is a fallback for
when you genuinely need to browse a whole module's catalog at once.

### 2. Write the JSON spec

One spec object = one preset = one song section. The full field list is
documented at the top of `scripts/build_preset.py`; `examples/example_preset.json`
is a working minimal spec. A realistic one:

```json
{
  "name": "Verse Clean",
  "author": "Your Name",
  "slot": "01-A",
  "modules": {
    "PRE": { "effect": "COMP", "enabled": true, "params": { "Sustain": 35, "Volume": 55 } },
    "WAH": { "effect": "V-Wah", "enabled": false },
    "DST": { "effect": "Green OD", "enabled": false },
    "AMP": { "effect": "AC Pre", "enabled": true, "params": { "Volume": 60, "Tone": 55 } },
    "NR":  { "effect": "Gate 1", "enabled": true, "params": { "Threshold": 25 } },
    "CAB": { "effect": "UK GRN 2", "enabled": true },
    "EQ":  { "effect": "Guitar EQ 1", "enabled": true },
    "MOD": { "effect": "Detune", "enabled": false },
    "DLY": { "effect": "Tape", "enabled": true, "params": { "Time": 380, "Feedback": 25, "Mix": 20 } },
    "RVB": { "effect": "Room", "enabled": true, "params": { "Mix": 18 } },
    "VOL": { "effect": "Volume", "enabled": true, "params": { "Volume": 100 } }
  }
}
```

Points worth getting right:

- **`name` is capped at 16 characters, and the builder rejects anything longer**
  with a clear `PresetBuildError` rather than trimming it for you — `"Bridge
  Solo Lead Tone"` fails the build outright. Keep section names to 16
  characters up front so you don't get the error at build time. Same for
  `author`.
- **List every module the section touches, including the off ones.** A module
  you omit falls back to its slot default effect at default values, which is a
  guess by absence. Setting `"enabled": false` explicitly records that you meant
  the block to be bypassed, and it survives review by the user.
- **`slot`** is `"BB-L"` (bank 01-64, letter A-D) and controls the target
  location on the device plus the output filename. Give sections of one song
  consecutive slots so they're in footswitch order.
- **Effect names shared by several catalog entries take a number.** CAB has 20
  entries all displayed as `User IR`, and `SnapTone` appears 5 times under both
  AMP and DST. Address them as `"User IR 1"` ... `"User IR 20"` (1-based, in id
  order); the bare name is rejected as ambiguous rather than silently resolving
  to one of them. Unique names that merely end in a digit (`Guitar EQ 1`,
  `AC Pre 2`) are unaffected. `lookup_effects.py CAB` prints the numbered form.
- **A User IR cab cannot be set over USB.** `build_effect_change()` transmits
  only the effect id's top and bottom bytes and drops the `0x10` User-IR
  sub-category, so `upload_preset.py` lands on a regular cab instead and the
  read-back reports the mismatch (`want User IR, got SUP ZEP`). The `.prst`
  file stores the full 32-bit id and is correct — load it in gp200-studio, or
  select the IR slot on the unit by hand.
- **Values are in each parameter's own units**, which is why the lookup step
  matters: delay `Time` is milliseconds with a range in the hundreds, while
  `Mix` is 0-100. Out-of-range values are rejected with the valid range.

For a whole song, put the section objects in a JSON array in one file and pass
`--batch`.

### 3. Build

```bash
python3 "$SCRIPTS/build_preset.py" --validate song.json --batch   # check names/ranges
python3 "$SCRIPTS/build_preset.py" song.json --batch -o ./presets
```

Validate first on anything non-trivial: it resolves every name and range without
writing files, so you fix a typo in one round instead of shipping a file and
finding out later. Errors name the offending effect, parameter and valid range —
feed that back into `lookup_effects.py` rather than trying a different guess.
In batch mode each preset is reported independently and a failure in one doesn't
stop the others, so re-read the output for `FAIL` lines even when files appear.

### 4. Hand over the files

Give the user the actual `.prst` paths (via `present_files` where available).
Tell them the slot each file targets. They upload in gp200-studio or the Valeton
editor — no manual parameter entry.

## Uploading to the device

`upload_preset.py` flashes a built `.prst` straight into a device slot over
USB MIDI, so the user never opens an editor at all.

```bash
python3 "$SCRIPTS/upload_preset.py" --list-ports
python3 "$SCRIPTS/upload_preset.py" presets/verse_clean.prst --slot 03-B --dry-run
python3 "$SCRIPTS/upload_preset.py" presets/verse_clean.prst --slot 03-B
```

It needs `mido` and `python-rtmidi` (`pip install mido python-rtmidi`) — the
only third-party dependency anywhere in this skill. If they are missing the
script says so and exits; it does not traceback.

Before writing, the script reads the target slot's current preset name (for
display) and **always** asks for confirmation — the prompt does not depend on
what is in the slot, or on the device answering the name read at all.
**Writing destroys whatever was in that slot and the device has no undo.**
`--force` is the single exception: it skips the prompt, which is what makes
uploading a whole song section-by-section practical instead of confirming
eight times. With no terminal attached and no `--force`, the script still
opens the MIDI port and queries the target slot's current name, then refuses
to proceed and exits 2 — nothing is written and no upload happens, but the
port is opened and the device is queried first. That refusal is a human gate,
not a hint: if you are an agent running non-interactively, get the user's
explicit confirmation in conversation — naming the specific slots that will
be overwritten — before you pass `--force`.

`--slot` takes a `BB-L` label, but only **01-A through 32-D** (banks 1-32) can
be targeted for upload — the on-wire chunk header has a single 7-bit slot
byte, and the encoding for banks 33-64 has never been captured from real
hardware, so extending it would mean guessing at a destructive write. A label
outside 01-A–32-D — a nonsense one like `65-A` or a real but currently
unreachable one like `33-A` — is rejected with a clear error rather than
silently writing some other, in-range slot.

### What the script does, and why each step is there

The write path is **live editing**, not a flash-write image. Established
against real hardware on 2026-09-18.

1. **Handshake** — identity query, enter editor mode, state dump, version
   check. The state dump looks like an optional query and is not: until it
   is sent the device ignores every slot read, so the overwrite prompt
   cannot see what it is about to destroy.
2. **Read the target slot** using the full `sub=0x10` read. Never the
   `sub=0x20` name-only variant — on this firmware that one *wedges the
   device*, after which it answers nothing at all until USB is
   re-established.
3. **Warm up.** The first write after the handshake is dropped without a
   pause.
4. **Select the target slot**, which loads it into the edit buffer.
5. **Per block, in chain order: effect type → parameters → bypass state.**
   The type must come first because changing it resets that block's
   parameters to the effect's defaults. Chain order matters because the
   device's Auto Cab Match rewrites CAB whenever the amp model changes, so
   CAB has to be written after AMP.
6. **Routing reorder, patch volume/pan/tempo, author**, then **save-commit**.
7. **Read the whole slot back, diff it against the file, and re-send only
   what differs**, up to four passes. Live editing is not perfectly
   reliable — an occasional block or parameter does not take — so the
   script converges instead of assuming, and reports any difference it
   could not fix.

Two traps worth knowing, because each one produced a *silent* wrong answer:

- **The 0x12/0x20 flash-write chunk burst does not work.** It persists
  nothing; the slot keeps its previous effect payload and the only thing
  that lands is the save-commit's 16-byte name. Because the old read-back
  checked only the name, every failed upload reported success. Three
  framings were tried (the 1184-byte/16-byte-preamble image; a 1176-byte
  image matching the device's own read format; chunks mirroring the read
  replies at `sub=0x18`/stride 185) and none persisted anything.
  `build_upload_chunks` is the one builder in gp200-studio's codec with no
  capture annotation, and its claim that the device "commits to flash
  directly, no save-commit follows" is false for this firmware.
- **Parameter indices are not 0..n-1.** 91 of the 305 catalogued effects
  have gaps, and every CAB model defines Volume/Low Cut/High Cut at indices
  1/5/6. Walking a block's parameters positionally reads and writes
  neighbouring fields. Use `prst_lib.defined_params()`.

Pacing is load-bearing and counter-intuitive: the edit session has a limited
window after the slot is selected, so sending parameters *slower* is worse.
At 45 ms per parameter the whole back half of the chain (EQ, MOD, DLY, RVB)
reverted to defaults; at 8 ms only one parameter was lost. Conversely the
settle *after* the save-commit must be generous — at 0.4 s the read-back
races the flash write and returns the effect's defaults, which looks exactly
like a failed parameter write.

Still worth telling the user:

- **Use a scratch slot for the first upload of a session.** A write is
  destructive and has no undo, and confirming the whole chain on a slot you
  don't need costs one minute.
- **Power-cycle and re-check after the first write.** A read-back proves
  the device reports the new preset; a power cycle proves it reached flash.

Building the files and loading them in gp200-studio remains a fine
alternative if the user would rather not write from the CLI at all.

## EXP pedal and CTRL footswitch assignments

Omit `controllers` entirely and the preset gets factory wiring: EXP1 Mode A
controls VOL Volume, EXP1 Mode B controls WAH Position (every wah type exposes
"Position" at the same slot), EXP2 and all 8 footswitches unassigned. That's the
right default for most presets.

Override it when the song needs it — a volume-swell section wanting the pedal on
volume in both modes, or a footswitch that kicks in the solo boost
(`examples/example_preset_swapped_pedal.json`):

```json
"controllers": {
  "exp1ModeA": [{ "para": 1, "block": "WAH", "param": "Position" }],
  "exp1ModeB": [{ "para": 1, "block": "VOL", "param": "Volume" }],
  "exp2": [],
  "ctrl": { "1": ["WAH"], "3": ["DST", "DLY"] }
}
```

`exp1ModeA` / `exp1ModeB` / `exp2` each take up to 3 Para assignments (`para`
1-3). `block` is a module name; `param` is a knob name on whatever effect that
block holds in *this* spec — so if you change the block's effect, recheck the
param name. `min`/`max` default to 0/100 (heel/toe). `ctrl` maps footswitch 1-8
to the blocks it toggles; unlisted footswitches stay untouched.

## Scope and limits

- Builds complete standalone presets from scratch: 11 effect blocks, name,
  author, target slot, patch volume/pan/tempo, FX loop points, EXP/CTRL
  assignments. Byte-for-byte identical to gp200-studio's own TypeScript encoder
  for the same input, and its output decodes cleanly there.
- **Does not edit an existing `.prst` in place.** It always builds from scratch,
  so unmodeled bytes in a real exported file (capture metadata and similar) are
  not preserved. If the user wants to change one field of a file they already
  have while keeping everything else byte-identical, that's a gp200-studio job —
  say so rather than round-tripping it through here and quietly dropping data.
- Effect data comes from `data/effect_data.json`, extracted from gp200-studio's
  TypeScript tables, which come from Valeton's own `algorithm.xml` — so the
  parameter ranges and defaults are the manufacturer's, not approximations. If a
  firmware update changes the catalog, refresh it rather than hand-editing:

  ```bash
  python3 "$SCRIPTS/extract_effect_data.py" --studio /path/to/gp200-studio --check
  ```

  `reference/SOURCES.md` has the full provenance chain, the credits for the two
  community editors this is built on, and the refresh procedure.
- Never invent an effect or parameter name to get a build to pass. A preset that
  builds with the wrong effect is worse than an error message, because the user
  discovers it by ear on stage.

## Files

- `scripts/lookup_effects.py` — effect/parameter name and range lookup
- `scripts/build_preset.py` — CLI entry point (spec format documented in its docstring)
- `scripts/prst_lib.py` — binary encoder (pure Python, no dependencies)
- `scripts/generate_reference.py` — regenerates the reference doc from the data file
- `scripts/extract_effect_data.py` — refreshes the data file from a gp200-studio checkout
- `scripts/upload_preset.py` — flash a built preset to a device slot over USB MIDI
- `scripts/prst_upload.py` — SysEx wire format for slot read/write (no I/O)
- `data/effect_data.json` — effect/parameter database
- `reference/effects_by_module.md` — full catalog, grouped by module
- `reference/SOURCES.md` — where the format and effect data came from, and how to update them
- `examples/example_preset.json` — minimal working spec
- `examples/example_preset_swapped_pedal.json` — same, with custom EXP/CTRL wiring
