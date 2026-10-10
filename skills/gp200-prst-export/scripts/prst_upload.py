"""SysEx wire format for reading and writing GP-200 preset slots.

Pure byte manipulation — no MIDI I/O, no third-party imports, so it stays
testable without hardware. The transport lives in upload_preset.py.

Ported from gp200-studio's src/core/SysExCodec.ts, which was reverse-
engineered from USB captures of the official Valeton editor
(dumps/patch-upload.pcapng, official editor writing slot 9, 2026-07-18).
Byte-for-byte parity with that implementation is enforced by
tests/test_upload_parity.py.
"""
from __future__ import annotations

import math
import struct

SYSEX_HEADER = bytes([0xF0, 0x21, 0x25, 0x7E, 0x47, 0x50, 0x2D, 0x32])
SYSEX_END = 0xF7

#: Raw bytes per flash-write chunk; each becomes 366 nibble bytes on the wire.
CHUNK_RAW = 183

#: The upload image starts at this offset in the .prst file, skipping the
#: file header the device does not want.
CONTENT_START = 0x30

#: Trailing footer in the file (C0 04 00 00 00 00 + BE16 checksum), excluded
#: from the image.
FOOTER_LEN = 8

#: 16-byte write preamble the image carries and the file does not.
_IMAGE_PREAMBLE = bytes([
    0x00, 0x00, 0x04, 0x00, 0x01, 0x00, 0xFF, 0x00,
    0x01, 0x00, 0x04, 0x00, 0xFF, 0x00, 0xFF, 0x00,
])


def nibble_encode(data: bytes) -> bytes:
    """Split each byte into two 4-bit bytes, high nibble first.

    SysEx data bytes must be <= 0x7F, so every payload travels nibble-encoded.
    """
    out = bytearray(len(data) * 2)
    for i, b in enumerate(data):
        out[2 * i] = (b >> 4) & 0x0F
        out[2 * i + 1] = b & 0x0F
    return bytes(out)


def nibble_decode(data: bytes) -> bytes:
    """Inverse of nibble_encode. A trailing odd byte is discarded."""
    out = bytearray(len(data) // 2)
    for i in range(len(out)):
        out[i] = ((data[2 * i] & 0x0F) << 4) | (data[2 * i + 1] & 0x0F)
    return bytes(out)


def build_upload_image(file_bytes: bytes) -> bytes:
    """Assemble the flash-upload image from .prst file bytes.

    The image is not the whole file: a 16-byte write preamble, then file
    content from 0x30 up to but excluding the 8-byte footer. A 1224-byte
    user file yields a 1184-byte image.

    image[20] is the in-file slot mirror (file offset 0x34). The capture shows
    the official editor blanking it to 0xFF even when writing to slot 9 — the
    real target slot travels in each chunk's SysEx header instead. Getting
    this byte wrong produces a preset that uploads cleanly to the wrong place.
    """
    content = file_bytes[CONTENT_START:len(file_bytes) - FOOTER_LEN]
    image = bytearray(_IMAGE_PREAMBLE + content)
    image[20] = 0xFF
    return bytes(image)


# CAUTION -- upload slot range is 0-127 only. msg[10] below (the chunk
# header's slot byte) is a single 7-bit SysEx data byte: `slot & 0x7F`.
# Slots 128-255 (banks 33-64) are NOT addressable through this function —
# they alias onto slot & 0x7F, i.e. some other, lower, in-range slot, and
# that slot gets silently flashed instead. This is unlike build_read_request()
# and build_preset_change() below, which nibble-encode the slot across two
# bytes and cover the full 0-255 range correctly, so read/prompt operations
# do NOT alias the way writes here do.
#
# There is no known-good encoding for banks 33-64 in this function: the only
# USB capture this port (and gp200-studio's SysExCodec.ts, which has the same
# 7-bit mask) was ever verified against wrote slot 9. Do not guess at extra
# bytes to extend the range — upload_preset.parse_slot() enforces the 0-127
# limit at the CLI boundary so this function is never called with a slot it
# cannot address correctly.
def build_upload_chunks(image: bytes, slot: int) -> list[bytes]:
    """Frame an image as 0x12/0x20 flash-write chunks for `slot`.

    183 raw bytes per chunk. The RAW-image offset is 7-bit-split across
    bytes [11] (low) and [12] (high): offset = b12 * 128 + b11. A 1184-byte
    image yields 7 chunks (6 x 380B + 1 x 186B frames).

    The chunks alone do NOT persist. gp200-studio's comment claimed the
    device commits to flash directly with no save-commit; hardware testing
    on 2026-09-18 disproved that for this firmware. The burst sends
    cleanly, the device stays responsive, and the target slot is
    unchanged — the write is staged and discarded. build_save_commit must
    follow. See upload() in upload_preset.py for the full sequence.

    `slot` MUST be 0-127. See the module-level caution above this function.
    """
    chunks: list[bytes] = []
    for off in range(0, len(image), CHUNK_RAW):
        raw = image[off:off + CHUNK_RAW]
        nibble = nibble_encode(raw)
        msg = bytearray(13 + len(nibble) + 1)
        msg[0:10] = SYSEX_HEADER + bytes([0x12, 0x20])
        msg[10] = slot & 0x7F  # 7-bit only -- see caution above
        msg[11] = off & 0x7F
        msg[12] = (off >> 7) & 0x7F
        msg[13:13 + len(nibble)] = nibble
        msg[-1] = SYSEX_END
        chunks.append(bytes(msg))
    return chunks


def build_preset_change(slot: int) -> bytes:
    """CMD=0x12 sub=0x08, 30 bytes: switch the device to `slot`."""
    sh, sl = (slot >> 4) & 0x0F, slot & 0x0F
    return SYSEX_HEADER + bytes([
        0x12, 0x08,
        0x00, 0x00, 0x00, 0x00,
        0x08, 0x01,
        0x00, 0x00,
        0x04, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00,
        sh, sl,
        0x00, 0x00,
        SYSEX_END,
    ])


def build_identity_query() -> bytes:
    """CMD=0x11 sub=0x04: ask what device is on the other end."""
    return SYSEX_HEADER + bytes([
        0x11, 0x04,
        0x00, 0x00, 0x00, 0x00, 0x01, 0x02, 0x00, 0x00, 0x00, 0x00, 0x00,
        SYSEX_END,
    ])


def build_version_check() -> bytes:
    """CMD=0x11 sub=0x0A, 34 bytes: firmware compatibility check.

    The device replies with a sub=0x0A message. Only the envelope is
    validated upstream — the payload bytes are not a version number.
    """
    return SYSEX_HEADER + bytes([
        0x11, 0x0A,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x06, 0x00, 0x00,
        0x0D, 0x04, 0x0F, 0x07, 0x08, 0x0B, 0x00, 0x00, 0x0C, 0x0B, 0x04, 0x05,
        SYSEX_END,
    ])


def build_enter_editor_mode() -> bytes:
    """CMD=0x11 sub=0x12: put the device into editor mode. No reply.

    Part of the connect handshake gp200-studio performs. See
    build_state_dump_request for why the handshake is not optional.
    """
    return SYSEX_HEADER + bytes([
        0x11, 0x12,
        0x00, 0x00, 0x00,
        SYSEX_END,
    ])


def build_state_dump_request() -> bytes:
    """CMD=0x11 sub=0x04 with the 0x06 0x01 selector: request the state dump.

    The device answers with nibble-encoded sub=0x4E chunks whose decoded
    bytes [8:10] hold the current slot as LE16.

    This looks like a pure query and is not. Hardware testing on 2026-09-18
    showed the device answers identity and version checks immediately, but
    stays SILENT to every slot-read request until this dump has been
    requested — after it, reads work for every slot. Treat it as a required
    state transition in the handshake, not as optional information
    gathering. Dropping it is what made read_slot_name return None and the
    prompt display "(unknown)".
    """
    return SYSEX_HEADER + bytes([
        0x11, 0x04,
        0x00, 0x00, 0x00, 0x00, 0x06, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00,
        SYSEX_END,
    ])


def parse_state_dump(chunks: list[bytes]) -> int | None:
    """Current slot index from sub=0x4E chunks, or None if unreadable.

    Chunk payload offsets are 7-bit split across bytes [11] (low) and
    [12] (high) — offset = b12 * 128 + b11 — the same encoding the upload
    chunks use. Observed strides are 185 decoded bytes per chunk.
    """
    if not chunks:
        return None
    ordered = sorted(chunks, key=lambda c: (c[12] << 7) | c[11])
    decoded = nibble_decode(b"".join(c[13:len(c) - 1] for c in ordered))
    if len(decoded) < 10:
        return None
    slot = decoded[8] | (decoded[9] << 8)
    return slot if 0 <= slot < 256 else None


def build_read_request(slot: int) -> bytes:
    """CMD=0x11 sub=0x10, 46 bytes: full read of `slot` (7 chunks back).

    The slot travels nibble-encoded, high first, at three separate positions
    — [25-26], [37-38] and [41-42]. All three must agree.
    """
    sh, sl = (slot >> 4) & 0x0F, slot & 0x0F
    return SYSEX_HEADER + bytes([
        0x11, 0x10,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x04, 0x00, 0x00, 0x00,
        0x01, 0x00,
        0x00,
        sh, sl,
        0x00, 0x00, 0x00,
        0x01, 0x00,
        0x00, 0x00,
        0x04, 0x00, 0x00,
        sh, sl,
        0x00, 0x00,
        sh, sl,
        0x00, 0x00,
        SYSEX_END,
    ])


def build_name_read_request(slot: int) -> bytes:
    """Name-only read: build_read_request with sub=0x20 instead of 0x10.

    The device answers with a single sub=0x18 chunk instead of all 7, which
    is what makes checking a slot before overwriting it cheap. Documented for
    firmware 1.8.0; older firmware may not answer, so callers should fall back
    to build_read_request on timeout.
    """
    msg = bytearray(build_read_request(slot))
    msg[9] = 0x20
    return bytes(msg)


def parse_preset_name(msg: bytes) -> str:
    """Pull the 16-byte NUL-terminated patch name out of a sub=0x18 chunk.

    Layout: [F0 header(10B)][slot(1B)][off_lo(1B)][off_hi(1B)][nibble...][F7].
    The name lives at decoded bytes 28..43, so this only works on the
    offset-0 chunk.
    """
    decoded = nibble_decode(msg[13:len(msg) - 1])
    name = []
    for i in range(16):
        if 28 + i >= len(decoded):
            break
        b = decoded[28 + i]
        if b == 0:
            break
        name.append(chr(b))
    return "".join(name)


def build_save_commit(preset_name: str, slot: int) -> bytes:
    """CMD=0x12 sub=0x18, 62 bytes: persist the staged write to `slot`.

    Ported from gp200-studio's SysExCodec.buildSaveCommit, decoded from
    captures 100548, 101538 and 121732. Sent after live edits or after a
    flash-write chunk burst to make the change stick.

    Decoded 24-byte payload, nibble-encoded onto the wire:
        [0:3]   03 20 14  (fixed)
        [4]     target slot
        [8:24]  preset name, 16 bytes, NUL-padded

    Byte [4] is ambiguous in the reference: the code passes the absolute
    slot 0-255 while an adjacent comment describes it as a sub-slot index
    (A=0..D=3). The captures cannot separate the two because they only
    cover bank 1, where absolute and sub-slot coincide. We follow the code
    and send the absolute slot. Callers should issue a preset-change to the
    target first, which makes both readings land on the same slot.
    """
    decoded = bytearray(24)
    decoded[0], decoded[1], decoded[2] = 0x03, 0x20, 0x14
    decoded[4] = slot & 0xFF
    name = preset_name.encode("ascii", "replace")[:16]
    decoded[8:8 + len(name)] = name

    msg = bytearray(62)
    msg[0:13] = SYSEX_HEADER + bytes([0x12, 0x18, 0x00, 0x00, 0x00])
    msg[13:13 + 48] = nibble_encode(bytes(decoded))
    msg[61] = SYSEX_END
    return bytes(msg)


def preset_name_from_file(file_bytes: bytes) -> str:
    """The patch name stored in a .prst file (offset 0x44, 16 bytes, NUL-padded).

    This is the name the device will display, and the name the save-commit
    must carry so the committed slot is labelled correctly.
    """
    raw = file_bytes[0x44:0x44 + 16]
    return raw.split(b"\x00")[0].decode("ascii", "replace")

# ── Live-edit write path ────────────────────────────────────────────────
#
# The 0x12/0x20 flash-write chunk burst above does NOT work on this firmware.
# Measured on hardware 2026-09-18 against a scratch slot: the burst is
# ignored entirely, the slot keeps its previous effect payload, and the only
# thing that lands is the save-commit's 16-byte name — which is exactly why a
# name-only read-back reported success. Three framings were tried (the shipped
# 1184-byte/16-byte-preamble image; a 1176-byte image matching the device's own
# read format; chunks mirroring the read replies at sub=0x18/stride 185) and
# all three persisted nothing.
#
# gp200-studio's buildUploadChunks is the one builder in that file with no
# "Confirmed: capture NNNNNN" annotation, and its claim that the device
# "commits to flash directly, no save-commit follows" is contradicted by this
# repo's own hardware testing. Treat the chunk path as unverified guesswork.
#
# What the hardware does implement is live editing, which the official editor
# uses and whose messages ARE capture-confirmed: select the slot, then per
# block set the effect type, its parameters and its bypass state, then
# save-commit. Ported from gp200-studio's SysExCodec + writePresetToSlot.

def encode_display_value(value: float) -> bytes:
    """The 2-byte logarithmic display field at param-change decoded[14:16].

    u16 = round(16367 + 16 * log2(value)), non-positive -> 0. The device reads
    THIS field rather than the float32 for a block's FIRST parameter, so a
    constant here silently zeroes param 0 of every effect.
    """
    u16 = 0
    if value > 0:
        u16 = round(16367 + 16 * math.log2(value))
        u16 = max(0, min(0xFFFF, u16))
    return bytes([u16 & 0xFF, (u16 >> 8) & 0xFF])


def build_toggle_effect(block_index: int, enabled: bool) -> bytes:
    """CMD=0x12 sub=0x10, 46 bytes raw. Confirmed: captures 100548 + 101538."""
    return SYSEX_HEADER + bytes([
        0x12, 0x10,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x04, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00,
        0x00, 0x00,
        0x00, 0x00,
        0x01, 0x05,
        0x00, 0x00, 0x00,
        0x04, 0x00, 0x00, 0x00,
        block_index & 0x0F,
        0x00,
        0x01 if enabled else 0x00,
        0x09, 0x0C,
        0x00, 0x02,
        SYSEX_END,
    ])


def build_effect_change(block_index: int, effect_id: int) -> bytes:
    """CMD=0x12 sub=0x14, 54 bytes raw. Confirmed: captures 134828 + 143107.

    Only regular effects (sub-category 0x00) are confirmed; the User-IR
    sub-category encoding position is unknown.
    """
    module_type = (effect_id >> 24) & 0xFF
    variant = effect_id & 0xFF
    return SYSEX_HEADER + bytes([
        0x12, 0x14,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x04, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x01, 0x06,
        0x00, 0x00, 0x00,
        0x08,
        0x00, 0x00, 0x00,
        block_index & 0x0F,
        0x00, 0x00,
        0x07, 0x06, 0x00, 0x02,
        (variant >> 4) & 0x0F,
        variant & 0x0F,
        0x00, 0x00, 0x00, 0x00, 0x00,
        module_type & 0xFF,
        SYSEX_END,
    ])


def build_param_change(block_index: int, param_index: int,
                       effect_id: int, value: float) -> bytes:
    """CMD=0x12 sub=0x18, 62 bytes, nibble-encoded 24-byte payload.

    Confirmed: captures 102448 and 102857. `param_index` is the effect's own
    parameter order as the catalog lists it.
    """
    decoded = bytearray(24)
    decoded[2] = 0x04
    decoded[8] = 0x05
    decoded[10] = 0x0C
    decoded[12] = block_index
    decoded[13] = param_index
    decoded[14:16] = encode_display_value(value)
    struct.pack_into("<I", decoded, 16, effect_id & 0xFFFFFFFF)
    struct.pack_into("<f", decoded, 20, float(value))

    nibbles = nibble_encode(bytes(decoded))
    msg = bytearray(62)
    msg[0:13] = SYSEX_HEADER + bytes([0x12, 0x18, 0x00, 0x00, 0x00])
    msg[13:13 + len(nibbles)] = nibbles
    msg[61] = SYSEX_END
    return bytes(msg)


def build_patch_setting(target: int, value: int) -> bytes:
    """CMD=0x12 sub=0x10, 46 bytes. Confirmed: capture 140802.

    target: 0x00=VOL, 0x01=Tempo, 0x06=PAN. Distinguished from a block toggle
    by bytes [29:31] = 00 06 (the toggle uses 01 05).
    """
    msg = bytearray(SYSEX_HEADER + bytes([
        0x12, 0x10,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
        0x04, 0x00, 0x00, 0x00,
        0x00, 0x00, 0x00,
        0x00, 0x00,
        0x00, 0x00,
        0x00, 0x06,
        0x00, 0x00, 0x00,
        0x04, 0x00, 0x00, 0x00,
        target & 0x0F,
        0x00,
        0x00,
        (value >> 4) & 0x0F,
        value & 0x0F,
        0x00, 0x00,
        SYSEX_END,
    ]))
    if target == 0x06 and value > 127:
        msg[43] = 0x0F
        msg[44] = 0x0F
    if target == 0x01 and value > 255:
        msg[41] = (value >> 4) & 0x0F
        msg[42] = value & 0x0F
        msg[43] = (value >> 12) & 0x0F
        msg[44] = (value >> 8) & 0x0F
    return bytes(msg)


def build_reorder_effects(order: list[int], send: int, ret: int) -> bytes:
    """CMD=0x12 sub=0x20, 78 bytes. Confirmed: captures 101538 + 101714."""
    decoded = bytearray(32)
    decoded[2] = 0x04
    decoded[8] = 0x08
    decoded[10] = 0x10
    decoded[14] = send & 0xFF
    decoded[15] = ret & 0xFF
    for i, slot in enumerate(order[:11]):
        decoded[16 + i] = slot
    decoded[27] = 0x44
    nibbles = nibble_encode(bytes(decoded))
    msg = bytearray(78)
    msg[0:13] = SYSEX_HEADER + bytes([0x12, 0x20, 0x00, 0x00, 0x00])
    msg[13:13 + len(nibbles)] = nibbles
    msg[77] = SYSEX_END
    return bytes(msg)


def build_author_name(author: str) -> bytes:
    """CMD=0x12 sub=0x20, 78 bytes. Confirmed: capture 143029 pkt 71."""
    decoded = bytearray(32)
    decoded[2] = 0x04
    decoded[6] = 0x01
    decoded[8] = 0x09
    decoded[10] = 0x14
    decoded[12] = 0x01
    decoded[14] = 0x70
    decoded[15] = 0x0B
    raw = author.encode("ascii", "replace")[:16]
    decoded[16:16 + len(raw)] = raw
    nibbles = nibble_encode(bytes(decoded))
    msg = bytearray(78)
    msg[0:13] = SYSEX_HEADER + bytes([0x12, 0x20, 0x00, 0x00, 0x00])
    msg[13:13 + len(nibbles)] = nibbles
    msg[77] = SYSEX_END
    return bytes(msg)


def build_live_write_plan(blocks, name, author, tempo, volume, pan,
                          fx_send, fx_return, slot):
    """The ordered SysEx messages that write `blocks` into `slot`.

    `blocks` is 11 tuples of (effect_id, enabled, params), indexed by block
    (0=PRE .. 10=VOL). `params` is a list of (parameter index, value) pairs,
    NOT a positional list: parameter indices have gaps (every CAB model
    defines its knobs at 1/5/6), so a parameter's position in the catalog
    listing is not the index the device expects. Use prst_lib.defined_params
    to build these pairs.

    Order matters and is not cosmetic:
      - the effect TYPE precedes its parameters, because changing the type
        resets that block's parameters to the effect's defaults;
      - the bypass toggle follows the parameters;
      - blocks go in chain order, which puts CAB after AMP — the device's
        Auto Cab Match rewrites CAB whenever the amp model changes, so a CAB
        written first is silently replaced.

    Pure byte building; the caller owns the pacing, which matters as much as
    the order (see write_preset_live in upload_preset.py).
    """
    plan = [build_preset_change(slot)]
    for block_index, (effect_id, enabled, params) in enumerate(blocks):
        plan.append(build_effect_change(block_index, effect_id))
        for param_index, value in params:
            plan.append(build_param_change(block_index, param_index,
                                           effect_id, value))
        plan.append(build_toggle_effect(block_index, enabled))

    plan.append(build_reorder_effects(list(range(11)), fx_send, fx_return))
    plan.append(build_patch_setting(0x00, volume))
    plan.append(build_patch_setting(0x06, pan & 0xFF))
    plan.append(build_patch_setting(0x01, tempo))
    if author:
        plan.append(build_author_name(author))
    plan.append(build_save_commit(name, slot))
    return plan
