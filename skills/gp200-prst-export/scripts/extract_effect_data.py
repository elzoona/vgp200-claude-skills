#!/usr/bin/env python3
"""
Regenerate data/effect_data.json from a gp200-studio checkout.

data/effect_data.json is a snapshot of three TypeScript tables in
gp200-studio (which in turn generates them from Valeton's official
algorithm.xml — see reference/SOURCES.md for the full chain):

    src/core/effectNames.ts    -> EFFECT_MAP, SLOT_MODULES
    src/core/effectParams.ts   -> EFFECT_PARAMS
    src/core/defaultPreset.ts  -> MODULE_DEFAULTS

Usage:
    extract_effect_data.py --studio ~/git/gp200-studio            # rewrite the data file
    extract_effect_data.py --studio ~/git/gp200-studio --check    # report drift, write nothing

Run --check after pulling gp200-studio to see whether a firmware update
changed the effect catalog. After writing, rerun generate_reference.py so
reference/effects_by_module.md matches, and revalidate the examples.

The parser handles the small subset of TypeScript these generated files use:
object/array literals with unquoted keys, single-quoted strings, hex numbers,
line and block comments, and trailing commas. It deliberately does not try to
be a general TS parser — if gp200-studio ever changes how these tables are
written, this fails loudly rather than producing half-correct effect data.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
DATA_PATH = REPO_ROOT / "data" / "effect_data.json"


class ExtractError(RuntimeError):
    """Raised when the TypeScript sources don't look the way we expect."""


def _find_literal(src: str, const_name: str) -> str:
    """Return the raw text of `export const <const_name> ... = <literal>`,
    brace-matched so nested objects and arrays come along."""
    match = re.search(rf"\bconst\s+{re.escape(const_name)}\b[^=]*=\s*", src)
    if not match:
        raise ExtractError(f"couldn't find `const {const_name} = ...` — did the file layout change?")

    start = match.end()
    if src[start] not in "{[":
        raise ExtractError(f"{const_name} isn't an object or array literal")

    openers = {"{": "}", "[": "]"}
    stack = [openers[src[start]]]
    i = start + 1
    while i < len(src) and stack:
        c = src[i]
        if c in "'\"`":
            i = _skip_string(src, i)
            continue
        if src.startswith("//", i):
            i = src.find("\n", i)
            if i == -1:
                break
            continue
        if src.startswith("/*", i):
            end = src.find("*/", i)
            i = len(src) if end == -1 else end + 2
            continue
        if c in openers:
            stack.append(openers[c])
        elif c in "}]":
            if c != stack[-1]:
                raise ExtractError(f"unbalanced brackets while reading {const_name}")
            stack.pop()
        i += 1

    if stack:
        raise ExtractError(f"unterminated literal for {const_name}")
    return src[start:i]


def _skip_string(src: str, i: int) -> int:
    quote = src[i]
    i += 1
    while i < len(src):
        if src[i] == "\\":
            i += 2
            continue
        if src[i] == quote:
            return i + 1
        i += 1
    raise ExtractError("unterminated string literal")


def _to_json(literal: str) -> str:
    """Convert a TS object/array literal to JSON text.

    Strings are pulled out first and re-encoded, so the regex cleanups that
    follow only ever touch code, never string contents (effect names contain
    characters like '+' and '/' that would otherwise be at risk).
    """
    out: list[str] = []
    i = 0
    while i < len(literal):
        c = literal[i]
        if c in "'\"`":
            end = _skip_string(literal, i)
            raw = literal[i + 1 : end - 1].replace("\\'", "'")
            out.append(json.dumps(raw))
            i = end
            continue
        if literal.startswith("//", i):
            nl = literal.find("\n", i)
            i = len(literal) if nl == -1 else nl
            continue
        if literal.startswith("/*", i):
            end = literal.find("*/", i)
            i = len(literal) if end == -1 else end + 2
            continue
        out.append(c)
        i += 1

    text = "".join(out)
    text = re.sub(r"\bas const\b", "", text)
    text = re.sub(r"0[xX]([0-9a-fA-F]+)", lambda m: str(int(m.group(1), 16)), text)
    text = re.sub(r"([A-Za-z_$][\w$]*)\s*:", r'"\1":', text)   # bare keys
    text = re.sub(r"(?<![\w\"])(\d+)\s*:", r'"\1":', text)      # numeric keys
    text = re.sub(r",(\s*[}\]])", r"\1", text)                  # trailing commas
    return text


def parse_literal(src: str, const_name: str):
    text = _to_json(_find_literal(src, const_name))
    try:
        return json.loads(text)
    except json.JSONDecodeError as e:
        raise ExtractError(
            f"{const_name} didn't convert to valid JSON ({e}). The TypeScript in "
            "gp200-studio probably uses syntax this extractor doesn't handle — "
            "check the literal by hand rather than trusting a partial result."
        ) from e


def build_data(studio: Path) -> dict:
    core = studio / "src" / "core"
    missing = [p.name for p in (core / "effectNames.ts", core / "effectParams.ts", core / "defaultPreset.ts") if not p.exists()]
    if missing:
        raise ExtractError(
            f"{', '.join(missing)} not found under {core}. "
            "Point --studio at the root of a gp200-studio checkout."
        )

    names_src = (core / "effectNames.ts").read_text(encoding="utf-8")
    params_src = (core / "effectParams.ts").read_text(encoding="utf-8")
    default_src = (core / "defaultPreset.ts").read_text(encoding="utf-8")

    effect_map = parse_literal(names_src, "EFFECT_MAP")
    slot_modules = parse_literal(names_src, "SLOT_MODULES")
    effect_params = parse_literal(params_src, "EFFECT_PARAMS")
    module_defaults = parse_literal(default_src, "MODULE_DEFAULTS")

    # Sanity checks: a silently-empty table would produce a data file that
    # still loads but rejects every real effect name.
    if len(slot_modules) != 11:
        raise ExtractError(f"expected 11 module slots, got {len(slot_modules)}: {slot_modules}")
    if not effect_map or not effect_params:
        raise ExtractError("effect map or parameter table came out empty")
    for module, effect_id in module_defaults.items():
        if str(effect_id) not in effect_map:
            raise ExtractError(f"default effect {effect_id} for module {module} isn't in the effect map")

    return {
        "slotModules": slot_modules,
        "moduleDefaults": module_defaults,
        "effectMap": effect_map,
        "effectParams": effect_params,
    }


def summarize(data: dict) -> str:
    return (
        f"{len(data['effectMap'])} effects, "
        f"{sum(len(v) for v in data['effectParams'].values())} parameter definitions"
    )


def report_drift(old: dict, new: dict) -> bool:
    """Print what changed between two snapshots. Returns True if identical."""
    if old == new:
        print(f"data/effect_data.json is up to date ({summarize(new)})")
        return True

    old_ids = set(old.get("effectMap", {}))
    new_ids = set(new["effectMap"])
    added = sorted(new_ids - old_ids)
    removed = sorted(old_ids - new_ids)
    renamed = [
        (i, old["effectMap"][i]["name"], new["effectMap"][i]["name"])
        for i in sorted(old_ids & new_ids)
        if old["effectMap"][i]["name"] != new["effectMap"][i]["name"]
    ]
    param_changed = sorted(
        i for i in new["effectParams"]
        if old.get("effectParams", {}).get(i) != new["effectParams"][i]
    )

    print(f"data/effect_data.json differs from the gp200-studio checkout ({summarize(new)})")
    if added:
        print(f"  added effects:   {', '.join(new['effectMap'][i]['name'] for i in added)}")
    if removed:
        print(f"  removed effects: {', '.join(old['effectMap'][i]['name'] for i in removed)}")
    for i, was, now in renamed:
        print(f"  renamed: {was} -> {now} (id {i})")
    if param_changed:
        shown = ", ".join(new["effectMap"].get(i, {}).get("name", i) for i in param_changed[:12])
        more = f" (+{len(param_changed) - 12} more)" if len(param_changed) > 12 else ""
        print(f"  parameter changes on {len(param_changed)} effects: {shown}{more}")
    if old.get("moduleDefaults") != new["moduleDefaults"]:
        print("  module defaults changed")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--studio", default="~/git/gp200-studio", help="Path to a gp200-studio checkout"
    )
    parser.add_argument(
        "--check", action="store_true", help="Report differences without writing"
    )
    parser.add_argument("-o", "--out", default=str(DATA_PATH), help="Output path")
    args = parser.parse_args()

    try:
        data = build_data(Path(args.studio).expanduser())
    except ExtractError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    out_path = Path(args.out).expanduser()
    existing = json.loads(out_path.read_text(encoding="utf-8")) if out_path.exists() else {}

    if args.check:
        return 0 if report_drift(existing, data) else 1

    if existing == data:
        print(f"no change — {out_path} already matches ({summarize(data)})")
        return 0

    report_drift(existing, data)
    out_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"wrote {out_path}")
    print("next: python3 scripts/generate_reference.py && "
          "python3 scripts/build_preset.py --validate examples/example_preset.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
