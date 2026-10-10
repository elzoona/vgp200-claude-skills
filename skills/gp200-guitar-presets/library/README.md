# Your reference library

`gp200-guitar-presets` ships with no reference material. This folder is where
you put your own — the skill reads it at the start of every preset request and
prefers what it finds here over general knowledge.

Everything you add here except this README and `setup.example.md` is ignored by
git, so tabs, articles and manuals you're not allowed to redistribute stay on
your machine.

## Layout

```
library/
├── setup.md          your guitars, monitor/amp, Global EQ, standing rules
├── context.md        decisions from earlier sessions ("song X's main patch uses…")
├── manual/           the Valeton GP-200 manual (PDF)
├── tabs/             one file per song — spreadsheet, PDF, .gp, text
├── docs/             tone research, rig write-ups (Markdown or text)
└── articles/         saved articles (PDF)
```

None of it is required, and you can organise it differently — the skill lists
the whole folder and reads whatever looks relevant. These conventions just make
it find things faster:

- **`setup.md`** — start from `setup.example.md`. It's the one file the skill
  reads every time, so put anything that should apply to *every* preset here:
  which guitars you own, your FRFR/amp settings, your Global EQ, and rules like
  "for this artist's 90s era, default to heavier drive".
- **`tabs/`** — name files after the song title. Add the version if you keep
  several ("Song Title - Live 2005.xlsx"); the skill distinguishes live and
  studio arrangements.
- **`manual/`** — download the GP-200 user manual from Valeton's website. The
  skill uses it for global settings, routing and menus; parameter names and
  ranges come from the bundled effect catalog instead.

## Keeping it somewhere else

When the plugin is installed from a marketplace, this folder lives in Claude
Code's plugin cache and can be replaced on update. To keep your library safe,
put it anywhere you like and point the skill at it in `~/.claude/settings.json`:

```json
{
  "env": { "GP200_LIBRARY": "/Users/you/Music/gp200-library" }
}
```

When `GP200_LIBRARY` is set, this folder is ignored.
