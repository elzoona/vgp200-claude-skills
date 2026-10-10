"""Byte-parity of the live-edit write path against gp200-studio's TypeScript.

The device ignores the 0x12/0x20 flash-write chunk burst entirely (hardware,
2026-09-18): it persists nothing and only the save-commit's name lands. The
write path that the hardware does implement is live editing — set each block's
effect type, its parameters and its bypass state on the edit buffer, then
save-commit.

Reference vectors in fixtures/liveedit_reference.json are generated from
SysExCodec.ts's capture-confirmed builders — see regenerate_fixtures.md.

Stdlib only: `python3 tests/test_liveedit_parity.py -v`
"""
import json
import pathlib
import sys
import unittest

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
sys.path.insert(0, str(pathlib.Path(__file__).parent.parent
                       / "skills" / "gp200-prst-export" / "scripts"))

import prst_upload  # noqa: E402

REF = json.loads((FIXTURES / "liveedit_reference.json").read_text())


class TestToggleEffect(unittest.TestCase):
    def test_matches_typescript(self):
        for key, block, enabled in (("dly_on", 8, True),
                                    ("wah_off", 1, False),
                                    ("pre_on", 0, True)):
            with self.subTest(key):
                got = prst_upload.build_toggle_effect(block, enabled)
                self.assertEqual(got.hex(), REF["toggleEffect"][key])

    def test_is_46_bytes(self):
        self.assertEqual(len(prst_upload.build_toggle_effect(0, True)), 46)


class TestEffectChange(unittest.TestCase):
    def test_matches_typescript(self):
        for key, block, eid in (("amp_foxy30tb", 3, 117440551),
                                ("pre_comp", 0, 0),
                                ("dly_tapes", 8, 184549409)):
            with self.subTest(key):
                got = prst_upload.build_effect_change(block, eid)
                self.assertEqual(got.hex(), REF["effectChange"][key])

    def test_is_54_bytes(self):
        self.assertEqual(len(prst_upload.build_effect_change(3, 117440551)), 54)


class TestDisplayValue(unittest.TestCase):
    def test_matches_typescript(self):
        for key, value in (("0", 0), ("1", 1), ("35", 35),
                           ("375", 375), ("4000", 4000), ("neg", -25)):
            with self.subTest(key):
                got = prst_upload.encode_display_value(value)
                self.assertEqual(got.hex(), REF["displayValue"][key])

    def test_non_positive_encodes_zero(self):
        self.assertEqual(prst_upload.encode_display_value(0), b"\x00\x00")
        self.assertEqual(prst_upload.encode_display_value(-25), b"\x00\x00")


class TestParamChange(unittest.TestCase):
    def test_matches_typescript(self):
        for key, block, idx, eid, val in (
                ("dly_time_375", 8, 1, 184549409, 375),
                ("amp_gain_32", 3, 0, 117440551, 32),
                ("eq_800_neg2", 6, 2, 16777269, -2)):
            with self.subTest(key):
                got = prst_upload.build_param_change(block, idx, eid, val)
                self.assertEqual(got.hex(), REF["paramChange"][key])

    def test_is_62_bytes(self):
        self.assertEqual(
            len(prst_upload.build_param_change(8, 1, 184549409, 375)), 62)


class TestPatchSetting(unittest.TestCase):
    def test_matches_typescript(self):
        for key, target, value in (("vol_50", 0x00, 50),
                                   ("tempo_120", 0x01, 120),
                                   ("tempo_300", 0x01, 300),
                                   ("pan_left", 0x06, 200)):
            with self.subTest(key):
                got = prst_upload.build_patch_setting(target, value)
                self.assertEqual(got.hex(), REF["patchSetting"][key])


class TestReorderAndAuthor(unittest.TestCase):
    def test_reorder_matches_typescript(self):
        got = prst_upload.build_reorder_effects(list(range(11)), 4, 4)
        self.assertEqual(got.hex(), REF["reorderEffects"]["identity_4_4"])

    def test_author_matches_typescript(self):
        got = prst_upload.build_author_name("Test Author")
        self.assertEqual(got.hex(), REF["authorName"]["test_author"])




class TestLiveWritePlan(unittest.TestCase):
    """The ordered message sequence a live-edit write sends, without any I/O.

    Order is load-bearing and was established on hardware: the effect type
    must precede its parameters (a type change resets them to defaults), and
    CAB must follow AMP because Auto Cab Match rewrites CAB when AMP changes.
    """

    def _blocks(self):
        """(effect_id, enabled, [(param_index, value), ...]) for 11 blocks.

        Parameters are (index, value) pairs rather than a positional list
        because parameter indices have gaps: every CAB model defines its three
        knobs at indices 1/5/6, so sending them as 0/1/2 writes the wrong
        fields.
        """
        blocks = [(0, False, []) for _ in range(11)]
        blocks[3] = (117440551, True, [(0, 32.0), (1, 42.0)])    # AMP Foxy 30TB
        blocks[5] = (167772221, False, [(1, 50.0), (5, 19.0)])   # CAB AC Dream
        blocks[8] = (184549409, False, [(0, 35.0), (1, 375.0)])  # DLY Tape Delay S
        return blocks

    def test_sends_gapped_parameter_indices_as_their_catalog_index(self):
        plan = self._plan()
        self.assertIn(prst_upload.build_param_change(5, 5, 167772221, 19.0), plan)
        self.assertNotIn(prst_upload.build_param_change(5, 1, 167772221, 19.0), plan)

    def _plan(self):
        return prst_upload.build_live_write_plan(
            self._blocks(), name="Clean Base", author="Test Author",
            tempo=120, volume=50, pan=0, fx_send=4, fx_return=4, slot=127)

    def test_starts_by_selecting_the_target_slot(self):
        self.assertEqual(self._plan()[0], prst_upload.build_preset_change(127))

    def test_ends_with_the_save_commit(self):
        self.assertEqual(self._plan()[-1],
                         prst_upload.build_save_commit("Clean Base", 127))

    def test_effect_type_precedes_its_parameters(self):
        plan = self._plan()
        type_msg = prst_upload.build_effect_change(3, 117440551)
        param_msg = prst_upload.build_param_change(3, 0, 117440551, 32.0)
        self.assertLess(plan.index(type_msg), plan.index(param_msg))

    def test_bypass_state_follows_its_parameters(self):
        plan = self._plan()
        param_msg = prst_upload.build_param_change(3, 1, 117440551, 42.0)
        toggle_msg = prst_upload.build_toggle_effect(3, True)
        self.assertLess(plan.index(param_msg), plan.index(toggle_msg))

    def test_cab_is_written_after_amp_for_auto_cab_match(self):
        plan = self._plan()
        self.assertLess(plan.index(prst_upload.build_effect_change(3, 117440551)),
                        plan.index(prst_upload.build_effect_change(5, 167772221)))

    def test_sends_every_block_type_and_toggle(self):
        plan = self._plan()
        for i, (eid, enabled, _) in enumerate(self._blocks()):
            self.assertIn(prst_upload.build_effect_change(i, eid), plan)
            self.assertIn(prst_upload.build_toggle_effect(i, enabled), plan)

    def test_includes_patch_settings_and_author(self):
        plan = self._plan()
        self.assertIn(prst_upload.build_patch_setting(0x00, 50), plan)
        self.assertIn(prst_upload.build_patch_setting(0x01, 120), plan)
        self.assertIn(prst_upload.build_author_name("Test Author"), plan)

    def test_omits_author_message_when_author_is_empty(self):
        plan = prst_upload.build_live_write_plan(
            self._blocks(), name="X", author="", tempo=120, volume=50,
            pan=0, fx_send=4, fx_return=4, slot=0)
        self.assertNotIn(prst_upload.build_author_name(""), plan)


if __name__ == "__main__":
    unittest.main()
