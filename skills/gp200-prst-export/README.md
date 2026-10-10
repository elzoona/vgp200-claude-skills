# gp200-prst-export

Builds Valeton GP-200 `.prst` preset files from a plain JSON description of
amps, effects and parameter values — no per-parameter entry on the device or in
an editor.

This directory is a **Claude Skill** (`SKILL.md`), shipped as part of the
`vgp200-claude-skills` plugin, so a Claude session can take a preset it just designed
and hand back an uploadable file. It works equally well as a standalone
command-line tool with no Claude involved.

Pure Python 3, no dependencies, no network access, no Node runtime.

## Install as a Claude skill

The easiest route is the plugin, which installs this skill together with
`gp200-guitar-presets` — see the repository's top-level README.

To install only this skill, copy this directory to where Claude looks for
skills — `~/.claude/skills/` for personal skills, `<project>/.claude/skills/`
for one project — keeping the directory name `gp200-prst-export` to match the
`name` in the frontmatter:

```bash
git clone https://github.com/elzoona/vgp200-claude-skills.git
cp -R vgp200-claude-skills/skills/gp200-prst-export ~/.claude/skills/gp200-prst-export
```

When working on the tool itself, symlink instead so edits take effect without
reinstalling:

```bash
ln -s "$PWD/vgp200-claude-skills/skills/gp200-prst-export" ~/.claude/skills/gp200-prst-export
```


### Verify it's installed

Start (or restart) Claude Code and run `/skills` — `gp200-prst-export` should be
listed. A quick functional check:

```bash
python3 ~/.claude/skills/gp200-prst-export/scripts/build_preset.py --validate \
  ~/.claude/skills/gp200-prst-export/examples/example_preset.json
# -> OK   EXAMPLE
```

Then ask for something like *"build me a GP-200 preset for a clean Vox-ish verse
tone and give me the .prst"* and Claude will look up real effect names, write a
spec and produce the file.

## Standalone command-line use

```bash
# what's available
python3 scripts/lookup_effects.py --modules              # the 11 module slots
python3 scripts/lookup_effects.py DST                    # drive pedals
python3 scripts/lookup_effects.py AMP "AC Pre"           # params, ranges, defaults
python3 scripts/lookup_effects.py --search tape          # search by partial name

# build
python3 scripts/build_preset.py --validate examples/example_preset.json
python3 scripts/build_preset.py examples/example_preset.json -o out/
python3 scripts/build_preset.py song_sections.json --batch -o out/
```

The spec format is documented at the top of `scripts/build_preset.py`;
`SKILL.md` walks through the full workflow, including EXP pedal and CTRL
footswitch assignments. `reference/effects_by_module.md` is the complete
catalog of valid effect and parameter names (auto-generated from
`data/effect_data.json`).

Output files are named `<slot> <patch name>.prst` and go to `./out` unless `-o`
says otherwise. Load them in gp200-studio, the Valeton GP-200 editor, or copy
them to the device.

## Repository layout

| Path | What it is |
| --- | --- |
| `SKILL.md` | Skill definition: workflow, spec format, controller wiring |
| `scripts/prst_lib.py` | Binary `.prst` encoder |
| `scripts/build_preset.py` | CLI: JSON spec → `.prst` |
| `scripts/lookup_effects.py` | Effect/parameter name and range lookup |
| `scripts/generate_reference.py` | Regenerates the reference doc from the data file |
| `scripts/extract_effect_data.py` | Refreshes the data file from a gp200-studio checkout |
| `data/effect_data.json` | Effect/parameter database (static snapshot) |
| `reference/effects_by_module.md` | Full catalog, grouped by module |
| `reference/SOURCES.md` | Credits, provenance, and how to update the effect data |
| `examples/` | Working preset specs, plain and with custom EXP/CTRL wiring |

## Credits

This tool exists because two people reverse-engineered the GP-200's file format
in the open. Full detail — which files, which commits, what came from where —
is in [`reference/SOURCES.md`](reference/SOURCES.md).

- **[gp200-studio](https://github.com/kabir0st/gp200-studio)** by kabir0st — the
  browser-based GP-200 editor this tool is ported from. `scripts/prst_lib.py` is
  a Python translation of its `PRSTEncoder.ts` / `BufferGenerator.ts` /
  `controlRecords.ts`, and `data/effect_data.json` is a snapshot of its effect
  tables. gp200-studio is GPL-3.0-only, so this tool is too.
- **[gp200editor / Preset Forge](https://github.com/phash/gp200editor)** by phash
  (GPL-3.0) — no code taken, but its `docs/prst-format.md` is an independently
  derived description of the same binary layout, which is what made the offsets
  trustworthy rather than merely consistent with one implementation.
- **Valeton** — the effect catalog, parameter ranges and defaults ultimately come
  from the official `algorithm.xml` and the firmware 1.8.0 documentation.

## License

[GPL-3.0-only](LICENSE). This tool is a derivative of gp200-studio (GPL-3.0-only):
`scripts/prst_lib.py` and `scripts/prst_upload.py` are Python translations of its
TypeScript encoder and SysEx codec, modified and extended, and
`data/effect_data.json` is extracted from its effect tables. GP-200 and Valeton
are trademarks of their owner; this project is not affiliated with or endorsed
by Valeton.

## Verification

The encoder (`scripts/prst_lib.py`) has been checked three ways:

1. Byte-for-byte diff against gp200-studio's TypeScript encoder for the same
   preset spec, both with default and with custom EXP/CTRL controller
   assignments (0 differences either way).
2. Round-trip through gp200-studio's `PRSTDecoder` (parses back with the same
   name, author, slot, effects and EXP/CTRL assignments).
3. Every offset cross-checked against gp200editor's independently written
   format documentation.

## Limits

- Builds presets **from scratch** only. It does not edit an existing `.prst` in
  place, so unmodeled bytes in a real exported file (capture metadata and
  similar) are not preserved — use gp200-studio for that.
- Patch names and author strings are capped at 16 ASCII characters by the file
  format and are silently truncated beyond that.
- `data/effect_data.json` is a static snapshot of gp200-studio's effect
  database. If a firmware update changes the catalog, re-extract it:

  ```bash
  git -C ~/git/gp200-studio pull
  python3 scripts/extract_effect_data.py --studio ~/git/gp200-studio --check   # what changed?
  python3 scripts/extract_effect_data.py --studio ~/git/gp200-studio           # apply
  python3 scripts/generate_reference.py                                        # rebuild the doc
  ```

  `--check` writes nothing and exits non-zero when the snapshot is stale,
  listing added, removed and renamed effects rather than a 133 KB diff.
