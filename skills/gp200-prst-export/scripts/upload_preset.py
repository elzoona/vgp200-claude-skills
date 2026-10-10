#!/usr/bin/env python3
"""Flash a .prst preset to a Valeton GP-200 slot over USB MIDI.

The only script in this skill with third-party dependencies:

    pip install mido python-rtmidi

Wire format lives in prst_upload.py and is byte-verified against
gp200-studio's TypeScript. This file is the transport and the safety gate.

CAUTION: writing a slot destroys whatever preset was in it. The device has no
undo. Verify on a scratch slot before trusting this with work you care about.
"""
from __future__ import annotations

import argparse
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).parent))

import struct

import prst_lib
import prst_upload
from prst_lib import slot_label_to_index, slot_index_to_label

DEVICE_PORT_HINT = "GP-200"
CHUNK_DELAY_S = 0.020
PARK_SETTLE_S = 0.300
FLASH_SETTLE_S = 0.800
COMMIT_SETTLE_S = 0.600

#: Per-message pacing for the live-edit write, keyed by SysEx sub-byte.
#: Established on hardware 2026-09-18. These are not arbitrary: the edit
#: session has a limited window after the preset-change, and pacing the
#: parameter writes SLOWER made things strictly worse — at 45ms per parameter
#: the EQ, MOD, DLY and RVB blocks (the later half of the chain) all reverted
#: to their defaults, while at 8ms only one parameter was lost.
_LIVE_DELAYS_S = {
    0x08: 0.200,   # preset change  - load the slot into the edit buffer
    0x14: 0.030,   # effect change
    0x18: 0.008,   # parameter change
    0x10: 0.015,   # block toggle / patch setting
    0x20: 0.030,   # routing reorder / author
}

#: Settle after the save-commit before reading the slot back. At 0.4s the
#: read races the flash write and returns the effect's DEFAULTS, which reads
#: exactly like a failed parameter write - that is what made a correct
#: DLY Time=375 look like a value-encoding bug for three experiments.
COMMIT_FLASH_SETTLE_S = 1.500

#: The device needs a moment after the handshake before the first write; the
#: first commit of a session is dropped without it.
WARMUP_S = 1.000

#: Device image index = file offset - 40. The READ image (1176 bytes, 8-byte
#: preamble) is NOT laid out like the write image build_upload_image produces
#: (1184 bytes, 16-byte preamble) - decoding one with the other's arithmetic
#: shifts every field by 8 bytes and yields plausible nonsense.
READ_IMAGE_SHIFT = 40
READ_IMAGE_LEN = 1176


def _require_mido():
    try:
        import mido  # noqa: F401
        return mido
    except ImportError:
        sys.exit(
            "upload_preset.py needs MIDI support, which is not installed.\n\n"
            "    pip install mido python-rtmidi\n\n"
            "Everything else in this skill is stdlib-only; only uploading "
            "needs these."
        )


def parse_slot(label: str) -> int:
    """'03-B' -> 9. Raises ValueError on anything out of range.

    slot_label_to_index() only validates the letter, not the bank: '00-A'
    resolves to -4 and '65-A' resolves to 256. Both are silently truncated
    by build_upload_chunks()'s `slot & 0x7F`, which turns an out-of-range
    --slot into a write to some *other*, in-range slot with no error at
    all. Reject anything outside 0-255 here instead.

    CRITICAL: also reject anything above index 127 (bank 33-A and up), even
    though it is a "valid" index by the device's own numbering. The read
    and prompt paths (build_read_request, build_preset_change) nibble-encode
    the full 0-255 range correctly, but build_upload_chunks()'s chunk header
    byte [10] is only 7 bits (`slot & 0x7F`), so a write to e.g. 33-A
    (index 128) silently flashes 01-A (128 & 0x7F == 0) instead. Because the
    read/prompt path is unaffected, the script reads 33-A's real occupant,
    asks the user to confirm overwriting 33-A, and then destroys 01-A while
    the device still shows 33-A's old, untouched preset — "nothing
    happened" to the user, but a different slot is now gone.

    The wire format for banks 33-64 has never been captured from real
    hardware (upstream's only USB capture wrote slot 9), so there is no
    verified encoding to extend build_upload_chunks() with. Guessing at
    extra bytes is not an option for a destructive write. Until a capture
    exists, only 01-A through 32-D (index 0-127) can be targeted for
    upload.
    """
    index = slot_label_to_index(label)
    if not 0 <= index <= 127:
        raise ValueError(
            f"slot {label!r} is out of range (resolves to index {index}); "
            "slots 01-A through 32-D (index 0-127) can be targeted for "
            "upload. Slots 33-A through 64-D cannot currently be addressed: "
            "the on-wire chunk header only has a 7-bit slot byte, and the "
            "encoding for banks 33-64 has never been captured from real "
            "hardware, so extending it would mean guessing at a destructive "
            "write. 01-A through 32-D work normally."
        )
    return index


def should_prompt_for_overwrite(current_name: str | None, force: bool,
                                interactive: bool) -> bool:
    """Decide whether to stop and ask before flashing over a slot.

    Always ask, unless --force. Flashing is destructive and the device has no
    undo, so every write gets a human look at what is about to be lost — it
    makes no difference what the slot currently holds, or whether the device
    answered the name read at all. Not knowing is a reason for more caution,
    not less.

    --force is the single deliberate exception: uploading a whole song means
    one write per section, and a policy that is right for one upload is
    hostile at eight.

    `interactive` is accepted but not consulted here. Whether a prompt is
    *possible* is the caller's problem: main() refuses outright rather than
    calling input() with no terminal attached. Keeping that out of the policy
    means this function answers one question only — should this write be
    confirmed by a human — and never conflates it with can it be.
    """
    if force:
        return False
    return True


def open_device(port_name: str | None):
    """Return (input, output) for the GP-200, matching on name substring."""
    mido = _require_mido()
    ins = mido.get_input_names()
    outs = mido.get_output_names()
    want = port_name or DEVICE_PORT_HINT

    def pick(names):
        exact = [n for n in names if n == want]
        if exact:
            return exact[0]
        loose = [n for n in names if want.lower() in n.lower()]
        return loose[0] if loose else None

    in_name, out_name = pick(ins), pick(outs)
    if not in_name or not out_name:
        sys.exit(
            f"No MIDI port matching {want!r}.\n"
            f"  inputs:  {ins or '(none)'}\n"
            f"  outputs: {outs or '(none)'}\n"
            "Is the GP-200 powered on and connected by USB? "
            "Use --list-ports to see everything."
        )
    return mido.open_input(in_name), mido.open_output(out_name)


def _send(out, raw: bytes):
    mido = _require_mido()
    out.send(mido.Message.from_bytes(list(raw)))


def _collect(inp, sub: int, timeout: float, quiet: float = 0.3) -> list[bytes]:
    """Gather sub-matching SysEx chunks until `quiet` seconds of silence."""
    got: list[bytes] = []
    deadline = time.time() + timeout
    last = time.time()
    while time.time() < deadline:
        for msg in inp.iter_pending():
            raw = bytes(msg.bytes())
            if len(raw) > 9 and raw[8] == 0x12 and raw[9] == sub:
                got.append(raw)
                last = time.time()
        if got and time.time() - last > quiet:
            break
        time.sleep(0.01)
    return got


def _await(inp, sub: int, timeout: float) -> bytes | None:
    """Wait for one SysEx reply with the given sub-command."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in inp.iter_pending():
            raw = bytes(msg.bytes())
            if len(raw) > 9 and raw[8] == 0x12 and raw[9] == sub:
                return raw
        time.sleep(0.01)
    return None


def handshake(inp, out, timeout: float = 3.0) -> int | None:
    """Run the connect handshake and return the device's current slot.

    This mirrors gp200-studio's connect sequence, and every step earns its
    place. Hardware testing on 2026-09-18 established that the device
    answers identity and version queries straight away but ignores every
    slot-read request until the STATE DUMP has been requested. Without it,
    read_slot_name returns None, the overwrite prompt shows "(unknown)",
    and a flash write appears to send cleanly while nothing reaches the
    slot. Skipping the handshake is what made the first real upload fail.

    Raises SystemExit if the device does not answer the identity query —
    matching a MIDI port by name only proves something is named GP-200, not
    that anything is listening. Flashing an unresponsive device is exactly
    the case worth refusing.
    """
    for msg in inp.iter_pending():
        pass  # drain anything left over from earlier traffic

    _send(out, prst_upload.build_identity_query())
    if _await(inp, 0x08, timeout) is None:
        sys.exit(
            "The GP-200 port is open but the device is not answering.\n"
            "Nothing was sent to any preset slot.\n"
            "  - Is the unit powered on and out of any menu/edit screen?\n"
            "  - Is another editor (gp200-studio, the Valeton editor) already\n"
            "    connected? Only one host can hold the port at a time.\n"
            "  - Try unplugging and reconnecting the USB cable."
        )

    _send(out, prst_upload.build_enter_editor_mode())
    time.sleep(0.1)

    # Not optional — see build_state_dump_request. This is what unlocks reads.
    _send(out, prst_upload.build_state_dump_request())
    current = prst_upload.parse_state_dump(_collect(inp, 0x4E, timeout))

    _send(out, prst_upload.build_version_check())
    _await(inp, 0x0A, timeout)

    return current


def read_slot_name(inp, out, slot: int, timeout: float = 2.5) -> str | None:
    """Ask the device what is in `slot`. None if it does not answer.

    Uses the full sub=0x10 read and NOTHING ELSE.

    Do not add a sub=0x20 "name-only" fast path here. It was tried, and on
    hardware (2026-09-18) it does not merely go unanswered — it WEDGES the
    device. After a single sub=0x20 the unit stops replying to everything,
    including the identity query, and a fresh handshake cannot recover it;
    the USB connection has to be re-established.

    That made the fallback strictly worse than having none. The sequence
    "try 0x20, fall back to 0x10" is exactly what this function used to do,
    and it is why the overwrite prompt showed "(unknown)" and the first
    real upload went into a device that had stopped listening. A lone
    sub=0x10 immediately after the handshake returns all 7 chunks.

    prst_upload.build_name_read_request still exists and is parity-tested
    because it is part of the documented format — but nothing may call it
    against real hardware until the wedge is understood.
    """
    for msg in inp.iter_pending():
        pass  # drain anything still arriving from the handshake
    _send(out, prst_upload.build_read_request(slot))
    deadline = time.time() + timeout
    while time.time() < deadline:
        for msg in inp.iter_pending():
            raw = bytes(msg.bytes())
            if len(raw) > 14 and raw[8] == 0x12 and raw[9] == 0x18:
                if raw[11] == 0 and raw[12] == 0:
                    return prst_upload.parse_preset_name(raw)
        time.sleep(0.01)
    return None


def read_slot_image(inp, out, slot: int, timeout: float = 4.0) -> bytearray | None:
    """Read a slot's full 1176-byte image, not just its name.

    read_slot_name() parses the name out of the offset-0 chunk and stops,
    which is why a write that persisted nothing but the name read back as a
    success. Verifying a write means comparing the whole payload.
    """
    for msg in inp.iter_pending():
        pass
    _send(out, prst_upload.build_read_request(slot))
    image = bytearray(READ_IMAGE_LEN)
    seen: dict[int, int] = {}
    deadline = time.time() + timeout
    while time.time() < deadline and len(seen) < 7:
        for msg in inp.iter_pending():
            raw = bytes(msg.bytes())
            if len(raw) > 14 and raw[8] == 0x12 and raw[9] == 0x18:
                offset = raw[12] * 128 + raw[11]
                decoded = prst_upload.nibble_decode(raw[13:len(raw) - 1])
                image[offset:offset + len(decoded)] = decoded
                seen[offset] = len(decoded)
        time.sleep(0.01)
    return image if len(seen) == 7 else None


def _blocks_from_image(image: bytearray) -> list[tuple[int, bool, list[float]]]:
    """Decode the 11 effect blocks out of a device read image."""
    base = prst_lib.EFFECT_BLOCK_START - READ_IMAGE_SHIFT
    blocks = []
    for i in range(prst_lib.EFFECT_BLOCK_COUNT):
        b = base + i * prst_lib.EFFECT_BLOCK_SIZE
        effect_id = struct.unpack_from("<I", image, b + 8)[0]
        enabled = bool(image[b + 5])
        params = [
            struct.unpack_from("<f", image, b + prst_lib.PARAMS_OFFSET + p * 4)[0]
            for p in range(prst_lib.PARAMS_COUNT)
        ]
        blocks.append((effect_id, enabled, params))
    return blocks


def diff_against_device(preset, image: bytearray) -> list[str]:
    """Human-readable differences between the preset and what the slot holds."""
    on_device = _blocks_from_image(image)
    problems = []
    for effect in preset.effects:
        i = effect.slot_index
        module = prst_lib.SLOT_MODULES[i]
        got_id, got_enabled, got_params = on_device[i]
        if got_id != effect.effect_id:
            want = prst_lib.EFFECT_MAP.get(effect.effect_id, {}).get("name", effect.effect_id)
            got = prst_lib.EFFECT_MAP.get(got_id, {}).get("name", got_id)
            problems.append(f"{module} effect: want {want}, got {got}")
            continue
        if got_enabled != effect.enabled:
            problems.append(
                f"{module} is {'on' if got_enabled else 'bypassed'}, "
                f"want {'on' if effect.enabled else 'bypassed'}")
        names = {d["idx"]: d["name"]
                 for d in prst_lib.EFFECT_PARAMS.get(effect.effect_id, [])}
        for p, want_value in prst_lib.defined_params(effect.effect_id,
                                                     effect.params):
            if abs(got_params[p] - want_value) > 0.01:
                problems.append(
                    f"{module}.{names[p]}: want {want_value:g}, "
                    f"got {got_params[p]:g}")
    return problems


def _send_plan(out, inp, plan: list[bytes]) -> None:
    """Send a live-edit plan, pacing each message by its sub-byte."""
    for index, msg in enumerate(plan):
        _send(out, msg)
        if index == len(plan) - 1:          # save-commit
            time.sleep(COMMIT_FLASH_SETTLE_S)
        else:
            time.sleep(_LIVE_DELAYS_S.get(msg[9], 0.030))
        for _ in inp.iter_pending():
            pass


def write_preset_live(path: pathlib.Path, slot: int, inp, out,
                      attempts: int = 4) -> list[str]:
    """Write `path` into `slot` by live-editing, then verify and repair.

    Returns the remaining differences, empty when the slot matches the file.

    The flash-write chunk burst this replaced does not work on this firmware
    (see prst_upload). Live editing does, but it is not perfectly reliable:
    an occasional block or parameter does not take. Rather than hope, this
    reads the whole slot back and re-sends only what differs, which converges
    in one or two passes and ends with evidence instead of an assumption.
    """
    preset = prst_lib.decode(path.read_bytes())
    blocks = [(e.effect_id, e.enabled,
               prst_lib.defined_params(e.effect_id, e.params))
              for e in preset.effects]

    print(f"  warming up ({WARMUP_S:g}s)")
    time.sleep(WARMUP_S)

    plan = prst_upload.build_live_write_plan(
        blocks, name=preset.patch_name, author=preset.author,
        tempo=preset.patch_tempo, volume=preset.patch_volume,
        pan=preset.patch_pan, fx_send=preset.fx_loop_send,
        fx_return=preset.fx_loop_return, slot=slot)
    print(f"  writing {len(plan)} messages "
          f"({prst_lib.EFFECT_BLOCK_COUNT} blocks, live edit)")
    _send_plan(out, inp, plan)

    problems: list[str] = []
    for attempt in range(1, attempts + 1):
        image = read_slot_image(inp, out, slot)
        if image is None:
            return ["the device did not answer a full read-back, "
                    "so this write is unverified"]
        problems = diff_against_device(preset, image)
        if not problems:
            print(f"  verified on pass {attempt}: slot matches the file")
            return []
        if attempt == attempts:
            break
        print(f"  pass {attempt}: {len(problems)} difference(s), repairing")
        repair = [prst_upload.build_preset_change(slot)]
        on_device = _blocks_from_image(image)
        for effect in preset.effects:
            i = effect.slot_index
            got_id, got_enabled, got_params = on_device[i]
            needs_all = got_id != effect.effect_id
            if needs_all:
                repair.append(prst_upload.build_effect_change(i, effect.effect_id))
            for p, want_value in prst_lib.defined_params(effect.effect_id,
                                                          effect.params):
                if needs_all or abs(got_params[p] - want_value) > 0.01:
                    repair.append(prst_upload.build_param_change(
                        i, p, effect.effect_id, want_value))
            if needs_all or got_enabled != effect.enabled:
                repair.append(prst_upload.build_toggle_effect(i, effect.enabled))
        repair.append(prst_upload.build_save_commit(preset.patch_name, slot))
        _send_plan(out, inp, repair)

    return problems


def upload(path: pathlib.Path, slot: int, inp, out) -> None:
    """Write `path` into `slot` and report what the device actually holds."""
    problems = write_preset_live(path, slot, inp, out)
    label = slot_index_to_label(slot)
    if not problems:
        print(f"  verified: {label} matches {path.name}")
        print("  Power-cycle the unit and re-check to confirm it "
              "persisted to flash.")
        return
    print(f"  WARNING: {label} does not match {path.name} "
          f"({len(problems)} difference(s)):")
    for problem in problems:
        print(f"    {problem}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("file", nargs="?", type=pathlib.Path, help="the .prst to upload")
    p.add_argument("--slot", help="target slot, e.g. 03-B (bank 01-32, letter A-D)")
    p.add_argument("--port", help="MIDI port name; default matches 'GP-200'")
    p.add_argument("--force", action="store_true", help="skip the overwrite prompt")
    p.add_argument("--dry-run", action="store_true",
                   help="report the plan without touching MIDI")
    p.add_argument("--list-ports", action="store_true", help="list MIDI ports and exit")
    args = p.parse_args(argv)

    if args.list_ports:
        mido = _require_mido()
        print("inputs: ", mido.get_input_names())
        print("outputs:", mido.get_output_names())
        return 0

    if not args.file or not args.slot:
        p.error("both a .prst file and --slot are required")
    if not args.file.is_file():
        p.error(f"no such file: {args.file}")

    slot = parse_slot(args.slot)
    label = slot_index_to_label(slot)

    if args.dry_run:
        image = prst_upload.build_upload_image(args.file.read_bytes())
        chunks = prst_upload.build_upload_chunks(image, slot)
        print(f"{args.file.name} -> slot {label}")
        print(f"  image: {len(image)} bytes")
        print(f"  {len(chunks)} chunks, {sum(len(c) for c in chunks)} bytes on the wire")
        print("  dry run: nothing sent")
        return 0

    inp, out = open_device(args.port)
    try:
        # Must run before any read or write: the device ignores slot reads
        # until the handshake's state dump has been requested, and exits
        # here rather than flashing a device that never answered.
        current = handshake(inp, out)
        print(f"{args.file.name} -> slot {label}")
        if current is not None:
            print(f"  device is on: {slot_index_to_label(current)}")
        occupant = read_slot_name(inp, out, slot)
        print(f"  currently: {occupant or '(unknown)'}")
        if should_prompt_for_overwrite(occupant, args.force, sys.stdin.isatty()):
            if not sys.stdin.isatty():
                print(
                    f"error: could not confirm overwriting {label} — no "
                    "terminal is attached to ask on, so this write was "
                    "refused.\n"
                    "--force exists only for a user who has already decided "
                    "to overwrite this named slot; it is not a way to get "
                    "past this refusal.",
                    file=sys.stderr,
                )
                return 2
            answer = input(f"  overwrite {label} ({occupant})? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                print("  aborted")
                return 1
        upload(args.file, slot, inp, out)
    finally:
        inp.close()
        out.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
