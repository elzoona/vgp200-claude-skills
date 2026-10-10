"""
GP-200 .prst binary encoder — pure-Python port of gp200-studio's
PRSTEncoder.ts / BufferGenerator.ts / controlRecords.ts.

This module builds a *synthetic* preset from scratch (the "no rawSource"
path of the TypeScript encoder). It does not support round-tripping an
existing .prst file's unmodeled bytes (controller assignment tweaks,
capture metadata) — for that, use gp200-studio itself. It's meant for
generating brand-new presets from a friendly, name-based description.

Byte-for-byte verified against gp200-studio's PRSTEncoder output and
against real Valeton-exported .prst sample files (see the Verification
section of README.md).
"""
from __future__ import annotations

import json
import struct
from pathlib import Path
from typing import Any

DATA_PATH = Path(__file__).parent.parent / "data" / "effect_data.json"

with open(DATA_PATH, "r", encoding="utf-8") as f:
    _DATA = json.load(f)

SLOT_MODULES: list[str] = _DATA["slotModules"]  # index 0..10 -> module name
MODULE_DEFAULTS: dict[str, int] = {k: int(v) for k, v in _DATA["moduleDefaults"].items()}
EFFECT_MAP: dict[int, dict[str, str]] = {int(k): v for k, v in _DATA["effectMap"].items()}
EFFECT_PARAMS: dict[int, list[dict[str, Any]]] = {
    int(k): v for k, v in _DATA["effectParams"].items()
}

# Modules that are active-by-default in a from-scratch INIT preset
# (mirrors defaultPreset.ts DEFAULT_ENABLED).
_DEFAULT_ENABLED_MODULES = {"AMP", "CAB", "VOL"}

# ── name -> effect id lookup ────────────────────────────────────────────
# Build (module, lowercased name) -> id, plus a name-only index used when
# the caller doesn't disambiguate by module (only safe when unambiguous).
# Three (module, name) pairs are duplicated in Valeton's catalog -- CAB/User IR
# (x20) and SnapTone under both AMP and DST. Keying a plain dict on the name
# collapsed each group onto whichever id was inserted last, so a request for
# the first User IR slot silently returned the twentieth. Duplicates are kept
# in id order and addressed as "<Name> <n>" (1-based); the bare name is
# rejected rather than guessed at.
_BY_MODULE_NAME: dict[tuple[str, str], int] = {}
_BY_NAME: dict[str, list[int]] = {}
_DUPLICATE_NAMES: dict[tuple[str, str], list[int]] = {}
for _id, _info in sorted(EFFECT_MAP.items()):
    _key = (_info["module"], _info["name"].lower())
    _DUPLICATE_NAMES.setdefault(_key, []).append(_id)
    _BY_NAME.setdefault(_info["name"].lower(), []).append(_id)
_BY_MODULE_NAME = {_k: _v[0] for _k, _v in _DUPLICATE_NAMES.items() if len(_v) == 1}
_DUPLICATE_NAMES = {_k: _v for _k, _v in _DUPLICATE_NAMES.items() if len(_v) > 1}


class PresetBuildError(ValueError):
    """Raised when a preset spec can't be resolved to valid GP-200 data."""


def resolve_effect_id(module: str, effect_name: str) -> int:
    """Resolve a display name (as shown in the Valeton editor / manual,
    e.g. 'Green OD', 'AC Sim', 'UK GRN 2') to its numeric effect id,
    scoped to the given module slot (PRE/WAH/DST/AMP/NR/CAB/EQ/MOD/DLY/RVB/VOL).
    """
    name = effect_name.strip().lower()
    key = (module, name)
    if key in _BY_MODULE_NAME:
        return _BY_MODULE_NAME[key]

    # "<Name> <n>" addressing for duplicated catalog entries, e.g. "User IR 1".
    # Checked after the exact match so genuinely unique names that end in a
    # digit ("Guitar EQ 1", "AC Pre 2", "Workman 2") keep resolving normally.
    base, _, index = name.rpartition(" ")
    if base and index.isdigit() and (module, base) in _DUPLICATE_NAMES:
        ids = _DUPLICATE_NAMES[(module, base)]
        n = int(index)
        if not 1 <= n <= len(ids):
            raise PresetBuildError(
                f"{effect_name!r} is out of range for module {module}: the "
                f"catalog has {len(ids)} entries named "
                f"{EFFECT_MAP[ids[0]]['name']!r} (1-{len(ids)})."
            )
        return ids[n - 1]

    if key in _DUPLICATE_NAMES:
        ids = _DUPLICATE_NAMES[key]
        display = EFFECT_MAP[ids[0]]["name"]
        raise PresetBuildError(
            f"Effect name {effect_name!r} is ambiguous for module {module}: "
            f"the catalog has {len(ids)} entries with that name. Use a "
            f"numbered form, {display} 1 through {display} {len(ids)}."
        )

    candidates = _BY_NAME.get(name)
    if not candidates:
        raise PresetBuildError(
            f"Unknown effect name {effect_name!r} for module {module}. "
            f"Check reference/effects_by_module.md for valid names."
        )
    other_modules = sorted({EFFECT_MAP[c]["module"] for c in candidates})
    raise PresetBuildError(
        f"Effect {effect_name!r} is not cataloged under module {module}. "
        f"It's listed under: {', '.join(other_modules)}. "
        f"Check reference/effects_by_module.md and put it in the matching module slot."
    )


def default_params(effect_id: int) -> list[float]:
    params = [0.0] * 15
    for d in EFFECT_PARAMS.get(effect_id, []):
        idx = d["idx"]
        if 0 <= idx < 15:
            params[idx] = float(d["default"])
    return params


def resolve_params(effect_id: int, overrides: dict[str, Any] | None) -> list[float]:
    """Resolve a {paramName: value} dict to the 15-slot float array,
    starting from documented defaults. `value` may be a number (knob) or,
    for switch/combox params, either the numeric option id or the option's
    display name (string).
    """
    params = default_params(effect_id)
    if not overrides:
        return params
    defs = {d["name"]: d for d in EFFECT_PARAMS.get(effect_id, [])}
    for name, value in overrides.items():
        if name not in defs:
            valid = ", ".join(defs.keys())
            raise PresetBuildError(
                f"Unknown parameter {name!r} for effect id {effect_id} "
                f"({EFFECT_MAP.get(effect_id, {}).get('name', '?')}). "
                f"Valid params: {valid}"
            )
        d = defs[name]
        idx = d["idx"]
        if d["type"] == "knob":
            num = float(value)
            if not (d["min"] <= num <= d["max"]):
                raise PresetBuildError(
                    f"{name}={value} out of range for effect "
                    f"{EFFECT_MAP.get(effect_id, {}).get('name', '?')} "
                    f"(expected {d['min']}..{d['max']})"
                )
            params[idx] = num
        else:  # switch / combox
            if isinstance(value, str):
                match = next(
                    (o for o in d["options"] if o["name"].lower() == value.lower()), None
                )
                if match is None:
                    names = ", ".join(o["name"] for o in d["options"])
                    raise PresetBuildError(
                        f"Unknown option {value!r} for {name!r} "
                        f"(effect {EFFECT_MAP.get(effect_id, {}).get('name', '?')}). "
                        f"Valid options: {names}"
                    )
                params[idx] = float(match["id"])
            else:
                params[idx] = float(value)
    return params


# ── .prst binary layout (mirrors PRSTEncoder.ts) ────────────────────────
PRST_MAGIC = b"TSRP"
DEVICE_ID = b"2-PG"
TOTAL_SIZE = 1224

OFFSET_MAGIC = 0x00
OFFSET_DEVICE_ID = 0x10
OFFSET_FW_VERSION = 0x14
OFFSET_TIMESTAMP = 0x1C
OFFSET_MRAP_PTR = 0x20
OFFSET_MRAP = 0x28
OFFSET_MRAP_SIZE = 0x2C
MRAP_CONTENT_SIZE = 1172

OFFSET_PRE_META = 0x30
OFFSET_PATCH_SLOT = 0x34    # u8: target slot 0..255 (mirrored at 0x90)
OFFSET_PATCH_TEMPO = 0x36   # u16 LE
OFFSET_PATCH_VOLUME = 0x38  # u16 LE, but 0..100 so only the low byte is written
OFFSET_PATCH_PAN = 0x3A     # s16 LE
OFFSET_PATCH_STYLE = 0x3C   # u16 LE
OFFSET_FX_MODE = 0x42       # u16 LE: 0 = parallel, 1 = serial

OFFSET_PATCH_NAME = 0x44
PATCH_NAME_MAX = 16
OFFSET_AUTHOR = 0x54
AUTHOR_MAX = 16
OFFSET_NOTE = 0x64
NOTE_MAX = 40

OFFSET_ROUTING = 0x8C
OFFSET_ROUTING_SLOT = 0x90
OFFSET_FX_SEND = 0x92
OFFSET_FX_RETURN = 0x93

EFFECT_BLOCK_COUNT = 11
EFFECT_BLOCK_START = 0xA0
EFFECT_BLOCK_SIZE = 0x48
PARAMS_OFFSET = 0x0C
PARAMS_COUNT = 15

OFFSET_CHECKSUM = 0x4C6

# ── controls tail (0x3B0-0x4C5), mirrors controlRecords.ts ─────────────
CONTROL_RECORDS_FILE_OFFSET = 0x3B0
_TYPE_EXP = 0x000C
_TYPE_UNKNOWN10 = 0x0010
_TYPE_CTRL = 0x000F
_HEADER_SIZE = 4
_EXP_PAYLOAD_SIZE = 12
_CTRL_PAYLOAD_SIZE = 8
_UNKNOWN10_PAYLOAD_SIZE = 4
_EXP_RECORD_COUNT = 9
_CTRL_RECORD_COUNT = 8


def _default_exp_assignments() -> list[dict[str, Any]]:
    """EXP1 Mode A Para1 -> VOL(10) param0; EXP1 Mode B Para1 -> WAH(1) param3;
    everything else unassigned, range 0..100 (matches every observed fw 1.8.0 export)."""
    out = []
    for page in range(3):
        for item in range(3):
            block_index = None
            param_index = 0
            if page == 0 and item == 0:
                block_index = 10
            if page == 1 and item == 0:
                block_index = 1
                param_index = 3
            out.append(
                {"page": page, "item": item, "blockIndex": block_index,
                 "paramIndex": param_index, "min": 0, "max": 100}
            )
    return out


def _default_ctrl_assignments() -> list[dict[str, Any]]:
    return [{"ctrlIndex": i, "blockMask": 0} for i in range(_CTRL_RECORD_COUNT)]


def _build_default_tail(
    exp: list[dict[str, Any]] | None = None,
    ctrl: list[dict[str, Any]] | None = None,
) -> bytes:
    exp = exp if exp is not None else _default_exp_assignments()
    ctrl = ctrl if ctrl is not None else _default_ctrl_assignments()

    size = (
        8
        + _EXP_RECORD_COUNT * (_HEADER_SIZE + _EXP_PAYLOAD_SIZE)
        + 3 * (_HEADER_SIZE + _UNKNOWN10_PAYLOAD_SIZE)
        + _CTRL_RECORD_COUNT * (_HEADER_SIZE + _CTRL_PAYLOAD_SIZE)
        + 6
    )
    tail = bytearray(size)
    off = 8  # leading zero padding

    exp_by_slot = {(a["page"] << 4) | a["item"]: a for a in exp}
    for page in range(3):
        for item in range(3):
            slot_id = (page << 4) | item
            struct.pack_into("<HH", tail, off, _TYPE_EXP, _EXP_PAYLOAD_SIZE)
            a = exp_by_slot.get(slot_id)
            tail[off + 4] = slot_id
            raw_block = 0xFF
            param_index = 0
            max_v = 100.0
            min_v = 0.0
            if a:
                if a["blockIndex"] is not None:
                    raw_block = a["blockIndex"]
                param_index = a["paramIndex"]
                max_v = a["max"]
                min_v = a["min"]
            tail[off + 5] = raw_block
            struct.pack_into("<H", tail, off + 6, param_index)
            struct.pack_into("<f", tail, off + 8, max_v)
            struct.pack_into("<f", tail, off + 12, min_v)
            off += _HEADER_SIZE + _EXP_PAYLOAD_SIZE

    # Three unknown 0x0010 records: constant values in every observed export.
    for i, val in enumerate((0xFF, 0xFF, 0x0B)):
        struct.pack_into("<HH", tail, off, _TYPE_UNKNOWN10, _UNKNOWN10_PAYLOAD_SIZE)
        tail[off + 4] = i
        tail[off + 5] = val
        off += _HEADER_SIZE + _UNKNOWN10_PAYLOAD_SIZE

    ctrl_by_index = {a["ctrlIndex"]: a for a in ctrl}
    for ctrl_index in range(_CTRL_RECORD_COUNT):
        struct.pack_into("<HH", tail, off, _TYPE_CTRL, _CTRL_PAYLOAD_SIZE)
        tail[off + 4] = ctrl_index
        struct.pack_into("<H", tail, off + 8, ctrl_by_index.get(ctrl_index, {}).get("blockMask", 0))
        off += _HEADER_SIZE + _CTRL_PAYLOAD_SIZE

    tail[off] = 0xC0
    tail[off + 1] = 0x04
    return bytes(tail)


def _write_ascii(buf: bytearray, offset: int, text: str, field_len: int) -> None:
    encoded = text.encode("ascii", errors="replace")[:field_len]
    buf[offset:offset + len(encoded)] = encoded
    buf[offset + len(encoded):offset + field_len] = bytes(field_len - len(encoded))


class EffectSlot:
    __slots__ = ("slot_index", "effect_id", "enabled", "params")

    def __init__(self, slot_index: int, effect_id: int, enabled: bool, params: list[float]):
        self.slot_index = slot_index
        self.effect_id = effect_id
        self.enabled = enabled
        self.params = params


class Preset:
    """In-memory preset model, analogous to GP200Preset (types.ts) for the
    synthetic (no-rawSource) case."""

    def __init__(
        self,
        patch_name: str,
        effects: list[EffectSlot],
        author: str | None = None,
        version: str = "1",
        fx_loop_send: int = 4,
        fx_loop_return: int = 4,
        patch_volume: int = 50,
        patch_pan: int = 0,
        patch_tempo: int = 120,
        slot_index: int | None = None,
        exp_assignments: list[dict[str, Any]] | None = None,
        ctrl_assignments: list[dict[str, Any]] | None = None,
        timestamp: int | None = None,
    ):
        if len(patch_name) > 16:
            raise PresetBuildError(f"patchName {patch_name!r} exceeds 16 characters")
        if author and len(author) > 16:
            raise PresetBuildError(f"author {author!r} exceeds 16 characters")
        if len(effects) != 11:
            raise PresetBuildError("exactly 11 effect slots are required")
        self.patch_name = patch_name
        self.author = author
        self.effects = effects
        self.version = version
        self.fx_loop_send = fx_loop_send
        self.fx_loop_return = fx_loop_return
        self.patch_volume = patch_volume
        self.patch_pan = patch_pan
        self.patch_tempo = patch_tempo
        self.slot_index = slot_index
        self.exp_assignments = exp_assignments
        self.ctrl_assignments = ctrl_assignments
        self.timestamp = timestamp


def encode(preset: Preset) -> bytes:
    """Encode a Preset into a 1224-byte .prst file, byte-compatible with the
    Valeton GP-200 editor / device (mirrors PRSTEncoder.ts's no-rawSource path)."""
    buf = bytearray(TOTAL_SIZE)

    # ── File header (0x00-0x2F) ─────────────────────────────────────────
    buf[OFFSET_MAGIC:OFFSET_MAGIC + 4] = PRST_MAGIC
    buf[0x0B] = 0x06
    buf[OFFSET_DEVICE_ID:OFFSET_DEVICE_ID + 4] = DEVICE_ID
    try:
        ver_num = int(preset.version)
    except ValueError:
        ver_num = None
    buf[OFFSET_FW_VERSION] = 0x00
    buf[OFFSET_FW_VERSION + 1] = (ver_num & 0xFF) if ver_num is not None else 0x01
    buf[OFFSET_FW_VERSION + 2] = 0x01
    buf[OFFSET_FW_VERSION + 3] = 0x00

    import time
    ts = preset.timestamp if preset.timestamp is not None else int(time.time())
    struct.pack_into("<I", buf, OFFSET_TIMESTAMP, ts & 0xFFFFFFFF)

    struct.pack_into("<H", buf, 0x24, 0x0494)
    buf[OFFSET_MRAP:OFFSET_MRAP + 4] = b"MRAP"
    struct.pack_into("<I", buf, OFFSET_MRAP_SIZE, MRAP_CONTENT_SIZE)

    buf[OFFSET_PRE_META] = 0x02
    buf[OFFSET_PRE_META + 2] = 0x58

    struct.pack_into("<I", buf, OFFSET_MRAP_PTR, OFFSET_MRAP)

    # ── Patch name / author (0x44-0x63) ─────────────────────────────────
    _write_ascii(buf, OFFSET_PATCH_NAME, preset.patch_name, PATCH_NAME_MAX)
    _write_ascii(buf, OFFSET_AUTHOR, preset.author or "", AUTHOR_MAX)

    # ── Routing section (0x8C-0x9F) ─────────────────────────────────────
    buf[OFFSET_ROUTING] = 0x08
    buf[OFFSET_ROUTING + 1] = 0x00
    buf[OFFSET_ROUTING + 2] = 0x10
    buf[OFFSET_ROUTING + 3] = 0x00
    for i in range(EFFECT_BLOCK_COUNT):
        slot = preset.effects[i] if i < len(preset.effects) else None
        buf[OFFSET_ROUTING + 8 + i] = slot.slot_index if slot else i
    buf[OFFSET_FX_SEND] = preset.fx_loop_send
    buf[OFFSET_FX_RETURN] = preset.fx_loop_return

    struct.pack_into("<H", buf, OFFSET_PATCH_TEMPO, preset.patch_tempo)
    buf[OFFSET_PATCH_VOLUME] = preset.patch_volume
    # Pan is s16 LE at 0x3A. It used to be written as one byte at 0x3C, which
    # is the STYLE field — panned presets set style and left pan at default.
    struct.pack_into("<h", buf, OFFSET_PATCH_PAN, preset.patch_pan)

    if preset.slot_index is not None:
        buf[OFFSET_PATCH_SLOT] = preset.slot_index & 0xFF
        buf[OFFSET_ROUTING_SLOT] = preset.slot_index & 0xFF

    # ── Effect blocks (0xA0-0x3AF, 11 x 72 bytes) ───────────────────────
    written = set()
    for slot in preset.effects:
        base = EFFECT_BLOCK_START + slot.slot_index * EFFECT_BLOCK_SIZE
        buf[base + 0] = 0x14
        buf[base + 2] = 0x44
        buf[base + 6] = 0x0F
        buf[base + 7] = 0x00
        buf[base + 4] = slot.slot_index
        buf[base + 5] = 1 if slot.enabled else 0
        struct.pack_into("<I", buf, base + 8, slot.effect_id & 0xFFFFFFFF)
        for p in range(min(PARAMS_COUNT, len(slot.params))):
            val = slot.params[p]
            struct.pack_into("<f", buf, base + PARAMS_OFFSET + p * 4, val if val == val else 0.0)
        written.add(slot.slot_index)
    for i in range(EFFECT_BLOCK_COUNT):
        if i in written:
            continue
        base = EFFECT_BLOCK_START + i * EFFECT_BLOCK_SIZE
        buf[base + 0] = 0x14
        buf[base + 2] = 0x44
        buf[base + 4] = i
        buf[base + 6] = 0x0F

    # ── Controls tail (0x3B0-0x4C5) ──────────────────────────────────────
    tail = _build_default_tail(preset.exp_assignments, preset.ctrl_assignments)
    buf[CONTROL_RECORDS_FILE_OFFSET:CONTROL_RECORDS_FILE_OFFSET + len(tail)] = tail

    # ── Checksum (0x4C6-0x4C7), big-endian ───────────────────────────────
    checksum = sum(buf[:OFFSET_CHECKSUM]) & 0xFFFF
    buf[OFFSET_CHECKSUM] = (checksum >> 8) & 0xFF
    buf[OFFSET_CHECKSUM + 1] = checksum & 0xFF

    return bytes(buf)


# ── slot label helpers (mirrors SysExCodec.slotToLabel usage in App.tsx) ─
_LETTERS = "ABCD"


def slot_label_to_index(label: str) -> int:
    """'01-A' -> 0, '63-D' -> 251, etc."""
    bank_str, letter = label.split("-")
    bank = int(bank_str)
    if letter.upper() not in _LETTERS:
        raise PresetBuildError(f"invalid slot letter {letter!r} (expected A-D)")
    return (bank - 1) * 4 + _LETTERS.index(letter.upper())


def slot_index_to_label(slot_index: int) -> str:
    bank = slot_index // 4 + 1
    letter = _LETTERS[slot_index % 4]
    return f"{bank:02d}-{letter}"


def decode(file_bytes: bytes) -> Preset:
    """Read a .prst back into a Preset.

    The inverse of encode() for everything the live-edit write path needs:
    the 11 effect blocks and the patch metadata. Parameters are trimmed to the
    count the effect actually defines, because the write path sends them one
    at a time and a parameter the effect does not have would land in a
    neighbouring field.
    """
    def _ascii(offset: int, length: int) -> str:
        raw = bytes(file_bytes[offset:offset + length])
        return raw.split(b"\x00")[0].decode("ascii", "replace")

    effects = []
    for slot_index in range(EFFECT_BLOCK_COUNT):
        base = EFFECT_BLOCK_START + slot_index * EFFECT_BLOCK_SIZE
        effect_id = struct.unpack_from("<I", file_bytes, base + 8)[0]
        enabled = bool(file_bytes[base + 5])
        params = [
            struct.unpack_from("<f", file_bytes, base + PARAMS_OFFSET + i * 4)[0]
            for i in range(PARAMS_COUNT)
        ]
        effects.append(EffectSlot(slot_index, effect_id, enabled, params))

    return Preset(
        patch_name=_ascii(OFFSET_PATCH_NAME, PATCH_NAME_MAX),
        author=_ascii(OFFSET_AUTHOR, PATCH_NAME_MAX),
        effects=effects,
        slot_index=file_bytes[OFFSET_PATCH_SLOT],
        patch_tempo=struct.unpack_from("<H", file_bytes, OFFSET_PATCH_TEMPO)[0],
        patch_volume=struct.unpack_from("<H", file_bytes, OFFSET_PATCH_VOLUME)[0],
        patch_pan=struct.unpack_from("<h", file_bytes, OFFSET_PATCH_PAN)[0],
        fx_loop_send=file_bytes[OFFSET_FX_SEND],
        fx_loop_return=file_bytes[OFFSET_FX_RETURN],
    )


def defined_params(effect_id: int, params: list[float]) -> list[tuple[int, float]]:
    """The (parameter index, value) pairs an effect actually defines.

    Parameter indices are NOT always 0..n-1: 91 of the 305 catalogued effects
    have gaps, and every CAB model puts Volume/Low Cut/High Cut at 1/5/6. So a
    parameter's position in the catalog listing is not its index in the block's
    15-float array, and walking the array positionally reads a neighbouring
    field. encode() writes at these indices; anything reading or transmitting
    parameters has to use them too.
    """
    pairs = []
    for definition in EFFECT_PARAMS.get(effect_id, []):
        idx = definition["idx"]
        if 0 <= idx < PARAMS_COUNT and idx < len(params):
            pairs.append((idx, params[idx]))
    return pairs
