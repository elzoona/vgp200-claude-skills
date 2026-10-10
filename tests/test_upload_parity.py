"""Byte-parity of the Python upload port against gp200-studio's TypeScript.

Reference vectors in fixtures/upload_reference.json are generated from
SysExCodec.ts — see regenerate_fixtures.md. A failure here means the port has
diverged from the implementation verified against real USB captures of the
official editor.

Stdlib only: `python3 tests/test_upload_parity.py -v`
"""
import json
import pathlib
import sys
import unittest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent
                       / "skills" / "gp200-prst-export" / "scripts"))

import prst_upload  # noqa: E402

REF = json.loads((FIXTURES / "upload_reference.json").read_text())
FILE_BYTES = (FIXTURES / "web-export.prst").read_bytes()


class TestUploadImage(unittest.TestCase):
    def test_matches_typescript(self):
        image = prst_upload.build_upload_image(FILE_BYTES)
        self.assertEqual(image.hex(), REF["image"])
        self.assertEqual(len(image), REF["imageLength"])

    def test_slot_mirror_byte_is_blanked(self):
        """image[20] is file[0x34], the in-file slot mirror. The device wants
        0xFF there; the real target slot rides in each chunk header instead."""
        self.assertEqual(prst_upload.build_upload_image(FILE_BYTES)[20], 0xFF)


class TestUploadChunks(unittest.TestCase):
    def test_match_typescript(self):
        # FIX 1: slot 255 was removed from this vector list. Its fixture
        # entry (REF["chunksBySlot"]["255"]) is byte-identical to slot 127's
        # because build_upload_chunks()'s chunk header only has a 7-bit slot
        # byte (`slot & 0x7F`) -- 255 & 0x7F == 127. Keeping 255 here meant
        # this test asserted that aliasing as *correct* parity with the
        # TypeScript port, when it is actually the bug fixed in
        # upload_preset.parse_slot(). There is no fixture entry for 127
        # itself (only 0, 9, 13, 255 were ever generated), so it is not
        # substituted in; if one is added later it belongs here.
        image = prst_upload.build_upload_image(FILE_BYTES)
        for slot in (0, 9, 13):
            with self.subTest(slot=slot):
                chunks = prst_upload.build_upload_chunks(image, slot)
                self.assertEqual([c.hex() for c in chunks],
                                 REF["chunksBySlot"][str(slot)])

    def test_all_data_bytes_in_range(self):
        """A data byte >= 0x80 terminates the message early on the wire."""
        image = prst_upload.build_upload_image(FILE_BYTES)
        for i, chunk in enumerate(prst_upload.build_upload_chunks(image, 9)):
            with self.subTest(chunk=i):
                self.assertEqual(chunk[0], 0xF0)
                self.assertEqual(chunk[-1], 0xF7)
                self.assertTrue(all(b <= 0x7F for b in chunk[1:-1]))


class TestReadRequestStructure(unittest.TestCase):
    """FIX 5: build_read_request() has no fixture in upload_reference.json
    (no "readRequest" key was ever generated), and build_name_read_request()
    overwrites byte[9], which masks any divergence at that position from
    TestMessageBuilders.test_name_read_request alone. That leaves the
    pre-1.8.0 firmware fallback path in read_slot_name() -- the full
    sub=0x10 read -- with no parity coverage at all: mutating
    `0x11, 0x10` to `0x11, 0x11` in build_read_request() passed the entire
    existing suite.

    There is no TypeScript fixture to regenerate this against without the
    gp200-studio vitest harness, so this pins the format structurally
    instead, against the documented layout in build_read_request()'s own
    docstring: 46 bytes, the standard 8-byte SysEx header, CMD=0x11
    sub=0x10, ending in F7, with the slot nibble-encoded high-then-low at
    three positions that must all agree.
    """

    def test_structure_and_length(self):
        for slot in (0, 9, 13, 127):
            with self.subTest(slot=slot):
                msg = prst_upload.build_read_request(slot)
                self.assertEqual(len(msg), 46)
                self.assertEqual(msg[:8], prst_upload.SYSEX_HEADER)
                self.assertEqual(msg[8], 0x11)
                self.assertEqual(msg[9], 0x10)
                self.assertEqual(msg[-1], prst_upload.SYSEX_END)

    def test_slot_nibbles_appear_high_then_low_at_all_three_positions(self):
        for slot in (0, 9, 13, 127):
            with self.subTest(slot=slot):
                msg = prst_upload.build_read_request(slot)
                sh, sl = (slot >> 4) & 0x0F, slot & 0x0F
                for hi_pos, lo_pos in ((25, 26), (37, 38), (41, 42)):
                    with self.subTest(hi_pos=hi_pos, lo_pos=lo_pos):
                        self.assertEqual(msg[hi_pos], sh)
                        self.assertEqual(msg[lo_pos], sl)

    def test_name_read_request_differs_only_at_byte_9(self):
        for slot in (0, 9, 13, 127):
            with self.subTest(slot=slot):
                read_req = prst_upload.build_read_request(slot)
                name_req = prst_upload.build_name_read_request(slot)
                self.assertEqual(len(read_req), len(name_req))
                diffs = [i for i in range(len(read_req)) if read_req[i] != name_req[i]]
                self.assertEqual(diffs, [9])
                self.assertEqual(name_req[9], 0x20)
                self.assertEqual(read_req[9], 0x10)


class TestMessageBuilders(unittest.TestCase):
    def test_preset_change(self):
        for slot in (0, 9, 255):
            with self.subTest(slot=slot):
                self.assertEqual(prst_upload.build_preset_change(slot).hex(),
                                 REF["presetChange"][str(slot)])

    def test_name_read_request(self):
        for slot in (0, 9, 255):
            with self.subTest(slot=slot):
                self.assertEqual(prst_upload.build_name_read_request(slot).hex(),
                                 REF["nameRead"][str(slot)])

    def test_identity_query(self):
        self.assertEqual(prst_upload.build_identity_query().hex(),
                         REF["identityQuery"])


class TestNibbleCoding(unittest.TestCase):
    def test_roundtrip(self):
        data = bytes(range(256))
        self.assertEqual(
            prst_upload.nibble_decode(prst_upload.nibble_encode(data)), data)

    def test_parse_preset_name_reads_nul_terminated_16_bytes(self):
        decoded = bytearray(200)
        decoded[28:28 + 11] = b"Verse Clean"
        msg = (bytes([0xF0]) + bytes(12)
               + prst_upload.nibble_encode(bytes(decoded)) + bytes([0xF7]))
        self.assertEqual(prst_upload.parse_preset_name(msg), "Verse Clean")


class TestHandshakeMessages(unittest.TestCase):
    """The connect handshake, pinned against gp200-studio's SysExCodec.ts.

    Hardware testing on 2026-09-18 proved the state dump is not optional:
    the device ignores every slot-read request until it has been sent.
    These vectors are transcribed from the TypeScript, and the identity,
    state-dump and version-check messages were confirmed on a real GP-200
    (it answered sub=0x08, sub=0x4E and sub=0x0A respectively).
    """

    def test_enter_editor_mode(self):
        self.assertEqual(
            prst_upload.build_enter_editor_mode(),
            bytes([0xF0, 0x21, 0x25, 0x7E, 0x47, 0x50, 0x2D, 0x32,
                   0x11, 0x12, 0x00, 0x00, 0x00, 0xF7]),
        )

    def test_state_dump_request(self):
        m = prst_upload.build_state_dump_request()
        self.assertEqual(m, bytes([0xF0, 0x21, 0x25, 0x7E, 0x47, 0x50, 0x2D, 0x32,
                                   0x11, 0x04, 0x00, 0x00, 0x00, 0x00, 0x06, 0x01,
                                   0x00, 0x00, 0x00, 0x00, 0x00, 0xF7]))
        self.assertEqual(len(m), 22)
        # Distinct from the identity query, which shares CMD/sub 0x11/0x04
        # and differs only in the 0x06 0x01 selector at [14:16].
        self.assertNotEqual(m, prst_upload.build_identity_query())
        self.assertEqual(m[8:10], prst_upload.build_identity_query()[8:10])

    def test_version_check(self):
        m = prst_upload.build_version_check()
        self.assertEqual(len(m), 34)
        self.assertEqual(m[:10], prst_upload.SYSEX_HEADER + bytes([0x11, 0x0A]))
        self.assertEqual(m[-1], 0xF7)

    def test_all_handshake_messages_are_valid_sysex(self):
        for name, m in [
            ("identity", prst_upload.build_identity_query()),
            ("editor", prst_upload.build_enter_editor_mode()),
            ("statedump", prst_upload.build_state_dump_request()),
            ("version", prst_upload.build_version_check()),
        ]:
            with self.subTest(msg=name):
                self.assertEqual(m[:8], prst_upload.SYSEX_HEADER)
                self.assertEqual(m[-1], 0xF7)
                self.assertTrue(all(b <= 0x7F for b in m[1:-1]))


class TestStateDumpParsing(unittest.TestCase):
    def test_parses_current_slot_from_offset_zero_chunk(self):
        """Offsets are 7-bit split: offset = b12 * 128 + b11."""
        decoded = bytearray(200)
        decoded[8], decoded[9] = 127, 0        # slot 127 as LE16
        nib = prst_upload.nibble_encode(bytes(decoded))
        chunk = (prst_upload.SYSEX_HEADER + bytes([0x12, 0x4E, 0x00, 0x00, 0x00])
                 + nib + bytes([0xF7]))
        self.assertEqual(prst_upload.parse_state_dump([chunk]), 127)

    def test_returns_none_on_no_chunks(self):
        self.assertIsNone(prst_upload.parse_state_dump([]))

    def test_rejects_out_of_range_slot(self):
        decoded = bytearray(200)
        decoded[8], decoded[9] = 0x00, 0x09    # 2304, impossible
        nib = prst_upload.nibble_encode(bytes(decoded))
        chunk = (prst_upload.SYSEX_HEADER + bytes([0x12, 0x4E, 0x00, 0x00, 0x00])
                 + nib + bytes([0xF7]))
        self.assertIsNone(prst_upload.parse_state_dump([chunk]))

if __name__ == "__main__":
    unittest.main()
