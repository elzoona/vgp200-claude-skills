#!/usr/bin/env python3
"""
Look up GP-200 effect and parameter names without reading the whole
reference doc (305 effects, ~1700 lines).

Usage:
    lookup_effects.py --modules              # module slots, in signal-chain order
    lookup_effects.py DST                    # every effect name in the DST slot
    lookup_effects.py DST "Green OD"         # that effect's params, ranges, defaults
    lookup_effects.py --search tape          # name search across all modules
    lookup_effects.py --search "tube" -m AMP # ...restricted to one module

Names are matched case-insensitively. When a name doesn't resolve, close
matches are printed so you can correct it instead of guessing — a guessed
name either fails the build or, worse, silently lands on a different effect.
"""
from __future__ import annotations

import argparse
import difflib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from prst_lib import (  # noqa: E402
    EFFECT_MAP,
    EFFECT_PARAMS,
    MODULE_DEFAULTS,
    SLOT_MODULES,
    PresetBuildError,
    resolve_effect_id,
)


def effects_in(module: str) -> list[tuple[int, str]]:
    found = [(eid, i["name"]) for eid, i in EFFECT_MAP.items() if i["module"] == module]
    return sorted(found, key=lambda t: t[1].lower())


def format_param(p: dict) -> str:
    if p["type"] == "knob":
        return f"  {p['name']}: knob {p['min']}-{p['max']} (default {p['default']})"
    options = ", ".join(o["name"] for o in p["options"])
    current = next((o["name"] for o in p["options"] if o["id"] == p["default"]), p["default"])
    return f"  {p['name']}: {p['type']} [{options}] (default {current})"


def print_module(module: str) -> None:
    effects = effects_in(module)
    default_id = MODULE_DEFAULTS.get(module)
    default_name = EFFECT_MAP.get(default_id, {}).get("name", "?")
    print(f"{module} — {len(effects)} effects (slot default: {default_name})")
    seen: dict[str, int] = {}
    total: dict[str, int] = {}
    for _, name in effects:
        total[name] = total.get(name, 0) + 1
    for _, name in effects:
        # Names shared by several catalog entries (User IR, SnapTone) are
        # addressed as "<Name> <n>" -- print the form that actually resolves.
        if total[name] > 1:
            seen[name] = seen.get(name, 0) + 1
            print(f"  {name} {seen[name]}")
        else:
            print(f"  {name}")


def print_effect(module: str, name: str) -> int:
    match = None
    try:
        eid = resolve_effect_id(module, name)
        match = (eid, EFFECT_MAP[eid]["name"])
    except PresetBuildError as exc:
        # Ambiguous or out-of-range numbered name: say so rather than fall
        # through to "no effect named ...", which would be misleading.
        if "ambiguous" in str(exc) or "out of range" in str(exc):
            print(str(exc), file=sys.stderr)
            return 1
    if match is None:
        names = [n for _, n in effects_in(module)]
        close = difflib.get_close_matches(name, names, n=5, cutoff=0.5)
        print(f"No effect named {name!r} in module {module}.", file=sys.stderr)
        if close:
            print("Closest names in this module: " + ", ".join(close), file=sys.stderr)
        elsewhere = sorted(
            {i["module"] for i in EFFECT_MAP.values() if i["name"].lower() == name.lower()}
        )
        if elsewhere:
            print(
                f"That name does exist, but under: {', '.join(elsewhere)} — "
                "place it in that module slot instead.",
                file=sys.stderr,
            )
        return 1

    effect_id, real_name = match
    print(f"{module} / {real_name} (id {effect_id})")
    params = EFFECT_PARAMS.get(effect_id, [])
    if not params:
        print("  (no adjustable parameters)")
    for p in params:
        print(format_param(p))
    return 0


def print_search(term: str, module: str | None) -> int:
    term_l = term.lower()
    hits = [
        (i["module"], i["name"])
        for i in EFFECT_MAP.values()
        if term_l in i["name"].lower() and (module is None or i["module"] == module)
    ]
    if not hits:
        scope = f" in {module}" if module else ""
        print(f"No effect name contains {term!r}{scope}.", file=sys.stderr)
        pool = [i["name"] for i in EFFECT_MAP.values() if module is None or i["module"] == module]
        close = difflib.get_close_matches(term, pool, n=5, cutoff=0.5)
        if close:
            print("Did you mean: " + ", ".join(close), file=sys.stderr)
        return 1
    for mod, name in sorted(set(hits), key=lambda t: (SLOT_MODULES.index(t[0]), t[1].lower())):
        print(f"{mod:4} {name}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("module", nargs="?", help=f"Module slot: {', '.join(SLOT_MODULES)}")
    parser.add_argument("effect", nargs="?", help="Effect display name to describe")
    parser.add_argument("--search", metavar="TERM", help="Substring search over effect names")
    parser.add_argument("-m", "--module-filter", help="Restrict --search to one module")
    parser.add_argument("--modules", action="store_true", help="List module slots and defaults")
    args = parser.parse_args()

    def check_module(value: str) -> str | None:
        upper = value.upper()
        if upper not in SLOT_MODULES:
            print(
                f"Unknown module {value!r}. Valid slots: {', '.join(SLOT_MODULES)}",
                file=sys.stderr,
            )
            return None
        return upper

    if args.modules:
        print("Signal chain order (slot index 0-10):")
        for i, module in enumerate(SLOT_MODULES):
            default_name = EFFECT_MAP.get(MODULE_DEFAULTS[module], {}).get("name", "?")
            count = len(effects_in(module))
            print(f"  {i:2}  {module:4} {count:3} effects, default {default_name}")
        return 0

    if args.search:
        module = None
        if args.module_filter:
            module = check_module(args.module_filter)
            if module is None:
                return 2
        return print_search(args.search, module)

    if not args.module:
        parser.print_help()
        return 2

    module = check_module(args.module)
    if module is None:
        return 2
    if args.effect:
        return print_effect(module, args.effect)
    print_module(module)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
