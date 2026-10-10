#!/usr/bin/env python3
"""
Build one or more Valeton GP-200 .prst preset files from a friendly,
name-based JSON description.

Usage:
    python3 build_preset.py preset.json                     # -> ./out/<name>.prst
    python3 build_preset.py preset.json -o /path/to/out
    python3 build_preset.py song_presets.json --batch        # spec file is a JSON array
    python3 build_preset.py --validate preset.json           # check without writing

Spec format (single preset object):
{
  "name": "Song Intro",             // required, <=16 chars -> becomes the patch name
  "author": "Your Name",            // optional, <=16 chars
  "slot": "01-A",                   // optional target slot "BB-L" (bank 1-64, letter A-D)
  "patchVolume": 50,                // optional, 0-100 (default 50)
  "patchPan": 0,                    // optional, -50..50 (default 0)
  "patchTempo": 120,                // optional, BPM (default 120)
  "fxLoopSend": 4,                  // optional, 1-10 (default 4)
  "fxLoopReturn": 4,                // optional, 1-10 (default 4)
  "modules": {
    "PRE": {"effect": "COMP", "enabled": true, "params": {"Sustain": 20, "Volume": 50}},
    "WAH": {"effect": "V-Wah", "enabled": false},
    "DST": {"effect": "Green OD", "enabled": true, "params": {...}},
    "AMP": {"effect": "AC Sim", "enabled": true, "params": {...}},
    "NR":  {"effect": "Gate 1", "enabled": true, "params": {"Threshold": 30}},
    "CAB": {"effect": "UK GRN 2", "enabled": true, "params": {...}},
    "EQ":  {"effect": "Guitar EQ 1", "enabled": true, "params": {...}},
    "MOD": {"effect": "Detune", "enabled": false},
    "DLY": {"effect": "Tape", "enabled": true, "params": {...}},
    "RVB": {"effect": "Room", "enabled": true, "params": {...}},
    "VOL": {"effect": "Volume", "enabled": true, "params": {"Volume": 100}}
  },
  "controllers": {                  // optional, overrides the default pedal wiring
    "exp1ModeA": [                  // EXP1, bank A (built-in pedal)
      {"para": 1, "block": "VOL", "param": "Volume", "min": 0, "max": 100}
    ],
    "exp1ModeB": [                  // EXP1, bank B (built-in pedal)
      {"para": 1, "block": "WAH", "param": "Position"}
    ],
    "exp2": [],                     // EXP2 (external pedal jack)
    "ctrl": {                       // footswitch -> block on/off toggles
      "1": ["WAH"],
      "3": ["AMP", "DLY"]
    }
  }
}

Any module left out of "modules" falls back to its documented default effect
at documented default parameter values (matching a blank GP-200 INIT patch).
Valid effect/parameter names per module: see reference/effects_by_module.md
(regenerate with scripts/generate_reference.py).

"controllers" is entirely optional. Omit it to get the documented factory
default (EXP1 Mode A Para 1 -> VOL Volume, EXP1 Mode B Para 1 -> WAH
Position, everything else unassigned, footswitches untouched) — that's what
every preset built before this field existed still gets. Include it to
override that wiring:
  - exp1ModeA / exp1ModeB / exp2 are lists of Para assignments (up to 3 each,
    "para" 1-3; omitted paras stay unassigned). "block" is a module name
    (PRE/WAH/DST/AMP/NR/CAB/EQ/MOD/DLY/RVB/VOL); "param" is that block's
    knob/switch name as resolved from its assigned effect (or a raw integer
    param index). "min"/"max" default to 0/100 (the pedal's heel/toe values).
  - "ctrl" maps a 1-8 footswitch number to the list of blocks it toggles
    on/off; footswitches left out stay untouched (mask 0, same as factory).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from prst_lib import (  # noqa: E402
    SLOT_MODULES,
    MODULE_DEFAULTS,
    EFFECT_PARAMS,
    EFFECT_MAP,
    _DEFAULT_ENABLED_MODULES,
    EffectSlot,
    Preset,
    PresetBuildError,
    encode,
    resolve_effect_id,
    resolve_params,
    default_params,
    slot_label_to_index,
    slot_index_to_label,
)

# EXP bank name -> page index, matching the SysEx protocol / ExpAssignment.page
# (see gp200-studio's ControllerPanel.tsx EXP_PAGES and controlRecords.ts).
_EXP_PAGES = {"exp1ModeA": 0, "exp1ModeB": 1, "exp2": 2}


def _resolve_module_effect_id(module: str, modules_spec: dict) -> int:
    m = modules_spec.get(module)
    if m is None:
        return MODULE_DEFAULTS[module]
    effect_name = m.get("effect")
    if not effect_name:
        raise PresetBuildError(f"modules.{module} is missing required 'effect' name")
    return resolve_effect_id(module, effect_name)


def build_effect_slots(modules_spec: dict) -> list[EffectSlot]:
    slots = []
    for slot_index, module in enumerate(SLOT_MODULES):
        m = modules_spec.get(module)
        effect_id = _resolve_module_effect_id(module, modules_spec)
        if m is None:
            enabled = module in _DEFAULT_ENABLED_MODULES
            params = default_params(effect_id)
        else:
            enabled = bool(m.get("enabled", True))
            params = resolve_params(effect_id, m.get("params"))
        slots.append(EffectSlot(slot_index, effect_id, enabled, params))
    return slots


def _resolve_param_index(module: str, effect_id: int, param: Any) -> int:
    if isinstance(param, int):
        return param
    defs = {d["name"]: d for d in EFFECT_PARAMS.get(effect_id, [])}
    if param not in defs:
        effect_name = EFFECT_MAP.get(effect_id, {}).get("name", "?")
        valid = ", ".join(defs.keys())
        raise PresetBuildError(
            f"controllers: unknown parameter {param!r} for block {module} "
            f"(effect {effect_name}). Valid params: {valid}"
        )
    return defs[param]["idx"]


def build_exp_assignments(controllers_spec: dict, modules_spec: dict) -> list[dict]:
    """Build the 9-record EXP assignment list (3 banks x 3 Para slots) from
    the friendly 'controllers' spec. Unlisted banks/paras are unassigned."""
    by_slot: dict[int, dict] = {}
    for bank_name, page in _EXP_PAGES.items():
        entries = controllers_spec.get(bank_name, [])
        for i, entry in enumerate(entries):
            para = entry.get("para", i + 1)
            if not (1 <= para <= 3):
                raise PresetBuildError(f"controllers.{bank_name}: 'para' must be 1-3, got {para}")
            item = para - 1
            block = entry.get("block")
            if block is None:
                raise PresetBuildError(f"controllers.{bank_name}[{i}] is missing required 'block'")
            if block not in SLOT_MODULES:
                raise PresetBuildError(
                    f"controllers.{bank_name}[{i}]: unknown block {block!r} "
                    f"(expected one of {SLOT_MODULES})"
                )
            block_index = SLOT_MODULES.index(block)
            param = entry.get("param")
            if param is None:
                raise PresetBuildError(f"controllers.{bank_name}[{i}] is missing required 'param'")
            effect_id = _resolve_module_effect_id(block, modules_spec)
            param_index = _resolve_param_index(block, effect_id, param)
            by_slot[(page << 4) | item] = {
                "page": page,
                "item": item,
                "blockIndex": block_index,
                "paramIndex": param_index,
                "min": entry.get("min", 0),
                "max": entry.get("max", 100),
            }

    assignments = []
    for page in range(3):
        for item in range(3):
            slot_id = (page << 4) | item
            assignments.append(
                by_slot.get(
                    slot_id,
                    {"page": page, "item": item, "blockIndex": None, "paramIndex": 0, "min": 0, "max": 100},
                )
            )
    return assignments


def build_ctrl_assignments(controllers_spec: dict) -> list[dict]:
    """Build the 8-record CTRL footswitch assignment list from the friendly
    {'1': ['WAH', ...], ...} 1-8 numbered spec. Unlisted footswitches get
    mask 0 (untouched), same as the factory default."""
    ctrl_spec = controllers_spec.get("ctrl", {})
    masks = [0] * 8
    for key, blocks in ctrl_spec.items():
        ctrl_num = int(key)
        if not (1 <= ctrl_num <= 8):
            raise PresetBuildError(f"controllers.ctrl: footswitch number must be 1-8, got {ctrl_num}")
        mask = 0
        for block in blocks:
            if block not in SLOT_MODULES:
                raise PresetBuildError(
                    f"controllers.ctrl['{key}']: unknown block {block!r} "
                    f"(expected one of {SLOT_MODULES})"
                )
            mask |= 1 << SLOT_MODULES.index(block)
        masks[ctrl_num - 1] = mask
    return [{"ctrlIndex": i, "blockMask": masks[i]} for i in range(8)]


def spec_to_preset(spec: dict) -> Preset:
    name = spec.get("name")
    if not name:
        raise PresetBuildError("spec is missing required 'name'")
    slot_index = None
    if "slot" in spec and spec["slot"] is not None:
        slot_index = slot_label_to_index(spec["slot"])
    modules_spec = spec.get("modules", {})
    effects = build_effect_slots(modules_spec)

    exp_assignments = None
    ctrl_assignments = None
    if "controllers" in spec and spec["controllers"] is not None:
        controllers_spec = spec["controllers"]
        exp_assignments = build_exp_assignments(controllers_spec, modules_spec)
        ctrl_assignments = build_ctrl_assignments(controllers_spec)

    return Preset(
        patch_name=name,
        effects=effects,
        author=spec.get("author"),
        version=str(spec.get("version", "1")),
        fx_loop_send=spec.get("fxLoopSend", 4),
        fx_loop_return=spec.get("fxLoopReturn", 4),
        patch_volume=spec.get("patchVolume", 50),
        patch_pan=spec.get("patchPan", 0),
        patch_tempo=spec.get("patchTempo", 120),
        slot_index=slot_index,
        exp_assignments=exp_assignments,
        ctrl_assignments=ctrl_assignments,
    )


def safe_filename(name: str) -> str:
    keep = "".join(c if c.isalnum() or c in " ._-" else "_" for c in name)
    return keep.strip() or "preset"


def write_preset(spec: dict, out_dir: Path) -> Path:
    preset = spec_to_preset(spec)
    data = encode(preset)
    label = slot_index_to_label(preset.slot_index) if preset.slot_index is not None else None
    filename = f"{label} {safe_filename(preset.patch_name)}.prst" if label else f"{safe_filename(preset.patch_name)}.prst"
    out_path = out_dir / filename
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(data)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("spec", help="Path to a preset JSON spec, or a JSON array of specs with --batch")
    parser.add_argument("-o", "--out-dir", default="out", help="Output directory (default: ./out)")
    parser.add_argument("--batch", action="store_true", help="Spec file is a JSON array of presets")
    parser.add_argument("--validate", action="store_true", help="Validate only, don't write files")
    args = parser.parse_args()

    spec_path = Path(args.spec)
    raw = json.loads(spec_path.read_text(encoding="utf-8"))
    specs = raw if args.batch else [raw]
    if args.batch and not isinstance(specs, list):
        print("error: --batch requires the spec file to contain a JSON array", file=sys.stderr)
        return 2

    out_dir = Path(args.out_dir)
    had_error = False
    for i, spec in enumerate(specs):
        label = spec.get("name", f"#{i}")
        try:
            if args.validate:
                spec_to_preset(spec)
                print(f"OK   {label}")
            else:
                path = write_preset(spec, out_dir)
                print(f"OK   {label} -> {path}")
        except PresetBuildError as e:
            had_error = True
            print(f"FAIL {label}: {e}", file=sys.stderr)

    return 1 if had_error else 0


if __name__ == "__main__":
    raise SystemExit(main())
