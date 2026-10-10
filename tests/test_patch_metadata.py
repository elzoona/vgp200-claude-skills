"""Patch-metadata offsets in the .prst file layout.

These mirror gp200-studio's PRSTEncoder.ts. Pan was previously written to
0x3C, which is the STYLE field — a preset with non-zero pan silently set
style instead and left pan at default.

Stdlib only: `python3 tests/test_patch_metadata.py -v`
"""
import pathlib
import struct
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent
                       / "skills" / "gp200-prst-export" / "scripts"))

import prst_lib  # noqa: E402
import build_preset  # noqa: E402


def _default_effect_slots():
    """Build the standard 11-slot default effect chain the way build_preset.py's
    build_effect_slots does when no module overrides are given."""
    return build_preset.build_effect_slots({})


class TestOffsetConstants(unittest.TestCase):
    """Guards against drifting from the upstream encoder again."""

    def test_offsets_match_upstream(self):
        self.assertEqual(prst_lib.OFFSET_PATCH_VOLUME, 0x38)
        self.assertEqual(prst_lib.OFFSET_PATCH_PAN, 0x3A)
        self.assertEqual(prst_lib.OFFSET_PATCH_STYLE, 0x3C)
        self.assertEqual(prst_lib.OFFSET_FX_MODE, 0x42)
        self.assertEqual(prst_lib.OFFSET_PATCH_NAME, 0x44)
        self.assertEqual(prst_lib.OFFSET_AUTHOR, 0x54)
        self.assertEqual(prst_lib.OFFSET_NOTE, 0x64)
        self.assertEqual(prst_lib.NOTE_MAX, 40)


class TestPanEncoding(unittest.TestCase):
    def _build(self, pan):
        preset = prst_lib.Preset(
            patch_name="Pan Test", author="Test",
            effects=_default_effect_slots(),
            patch_pan=pan,
        )
        return prst_lib.encode(preset)

    def test_pan_lands_at_0x3a_as_signed_le16(self):
        for pan in (0, 50, -50, 100, -100):
            with self.subTest(pan=pan):
                buf = self._build(pan)
                got, = struct.unpack_from("<h", buf, prst_lib.OFFSET_PATCH_PAN)
                self.assertEqual(got, pan)

    def test_pan_does_not_bleed_into_style(self):
        """The old bug: pan at 0x3C overwrote the style field."""
        buf = self._build(-75)
        style, = struct.unpack_from("<H", buf, prst_lib.OFFSET_PATCH_STYLE)
        self.assertEqual(style, 0)

    def test_volume_low_byte_at_0x38(self):
        preset = prst_lib.Preset(
            patch_name="Vol Test", author="Test",
            effects=_default_effect_slots(),
            patch_volume=87,
        )
        buf = prst_lib.encode(preset)
        self.assertEqual(buf[prst_lib.OFFSET_PATCH_VOLUME], 87)


if __name__ == "__main__":
    unittest.main()
