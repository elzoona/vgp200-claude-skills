# Valeton GP-200 plugin

Two chained skills for the Valeton GP-200 multi-effects pedalboard.

| Skill | Does |
|---|---|
| `gp200-guitar-presets` | Analyses a song, designs one preset per sonic section using only effects that exist on the unit, and offers the result as an HTML sheet, `.prst` files, or a direct upload |
| `gp200-prst-export` | Builds `.prst` binaries from a JSON spec and flashes them to device slots over USB MIDI |

## Install

```
/plugin marketplace add elzoona/vgp200-claude-skills
/plugin install vgp200-claude-skills@vgp200-claude-skills
```

## Add your own reference material

The presets skill ships with no tabs, articles or manuals — you bring your own.
Put them in `skills/gp200-guitar-presets/library/` (or anywhere, pointed to by
the `GP200_LIBRARY` environment variable). Start by copying
`library/setup.example.md` to `setup.md` and filling in your guitars and Global
EQ. See [`library/README.md`](skills/gp200-guitar-presets/library/README.md)
for the layout.

## Upload dependencies

Only `upload_preset.py` needs third-party packages:

```
pip install mido python-rtmidi
```

## Provenance

Effect data is extracted from [gp200-studio](https://github.com/kabir0st/gp200-studio),
which derives it from Valeton's own `algorithm.xml`. The SysEx upload format is
ported from the same project, reverse-engineered from USB captures of the
official editor. See `skills/gp200-prst-export/reference/SOURCES.md`.

## License

[GPL-3.0-only](LICENSE). The export skill is a derivative of
[gp200-studio](https://github.com/kabir0st/gp200-studio) (GPL-3.0-only), so the
plugin as a whole carries the same license. GP-200 and Valeton are trademarks of
their owner; this project is not affiliated with or endorsed by Valeton.
