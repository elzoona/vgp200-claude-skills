#!/usr/bin/env python3
"""Regenerate reference/effects_by_module.md from data/effect_data.json.

Run this only if data/effect_data.json is refreshed from a newer gp200-studio
checkout (e.g. after a GP-200 firmware update changes algorithm.xml).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from prst_lib import EFFECT_MAP, EFFECT_PARAMS, SLOT_MODULES  # noqa: E402

OUT_PATH = Path(__file__).parent.parent / "reference" / "effects_by_module.md"


def format_param(p: dict) -> str:
    if p["type"] == "knob":
        return f"  - {p['name']}: knob, {p['min']}-{p['max']} (default {p['default']})"
    options = ", ".join(o["name"] for o in p["options"])
    return f"  - {p['name']}: {p['type']}, options: [{options}] (default id {p['default']})"


def main() -> None:
    by_module: dict[str, list[tuple[int, str]]] = {m: [] for m in SLOT_MODULES}
    for effect_id, info in EFFECT_MAP.items():
        by_module.setdefault(info["module"], []).append((effect_id, info["name"]))

    lines = [
        "# GP-200 Effects by Module",
        "",
        "Auto-generated from data/effect_data.json — do not hand-edit.",
        "Regenerate with `python3 scripts/generate_reference.py` after refreshing the data file.",
        "",
        "Use the exact `name` shown here as the `effect` value in a preset spec, scoped to",
        "the listed module (the same display name can exist under more than one module).",
        "",
    ]
    for module in SLOT_MODULES:
        effects = sorted(by_module.get(module, []), key=lambda t: t[1].lower())
        lines.append(f"## {module}")
        lines.append("")
        for effect_id, name in effects:
            lines.append(f"- **{name}** (id {effect_id})")
            for p in EFFECT_PARAMS.get(effect_id, []):
                lines.append(format_param(p))
        lines.append("")

    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
