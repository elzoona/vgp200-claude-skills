"""Decoding a .prst back into blocks, which the live-edit write path needs.

The device is written one parameter at a time, so a built file has to be read
back apart into (effect_id, enabled, params) per block. Round-tripping through
encode() is the strongest available check: anything the encoder writes, the
decoder must recover.

Stdlib only: `python3 tests/test_preset_decode.py -v`
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent
                       / "skills" / "gp200-prst-export" / "scripts"))

import prst_lib  # noqa: E402


def _preset():
    """A full 11-slot preset; encode() requires every slot to be present."""
    def slot(index, module, effect, enabled, overrides=None):
        eid = prst_lib.resolve_effect_id(module, effect)
        return prst_lib.EffectSlot(
            index, eid, enabled, prst_lib.resolve_params(eid, overrides))

    effects = [
        slot(i, module, prst_lib.EFFECT_MAP[prst_lib.MODULE_DEFAULTS[module]]["name"],
             False)
        for i, module in enumerate(prst_lib.SLOT_MODULES)
    ]
    effects[0] = slot(0, "PRE", "COMP", True, {"Sustain": 35, "Volume": 55})
    effects[3] = slot(3, "AMP", "Foxy 30TB", True, {"Gain": 32, "Volume": 62})
    effects[8] = slot(8, "DLY", "Tape Delay S", False, {"Time": 375, "Mix": 35})

    return prst_lib.Preset(
        patch_name="Clean Base",
        author="Test Author",
        effects=effects,
        slot_index=0,
    )


def _preset_with_cab(cab_id):
    preset = _preset()
    preset.effects[5] = prst_lib.EffectSlot(
        5, cab_id, False, prst_lib.default_params(cab_id))
    return preset


class TestDecode(unittest.TestCase):
    def setUp(self):
        self.decoded = prst_lib.decode(prst_lib.encode(_preset()))

    def test_recovers_patch_name(self):
        self.assertEqual(self.decoded.patch_name, "Clean Base")

    def test_recovers_author(self):
        self.assertEqual(self.decoded.author, "Test Author")

    def test_returns_all_eleven_blocks(self):
        self.assertEqual(len(self.decoded.effects), 11)

    def test_recovers_effect_ids(self):
        by_slot = {e.slot_index: e.effect_id for e in self.decoded.effects}
        self.assertEqual(by_slot[3], prst_lib.resolve_effect_id("AMP", "Foxy 30TB"))
        self.assertEqual(by_slot[8], prst_lib.resolve_effect_id("DLY", "Tape Delay S"))

    def test_recovers_enabled_flags(self):
        by_slot = {e.slot_index: e.enabled for e in self.decoded.effects}
        self.assertTrue(by_slot[0])
        self.assertTrue(by_slot[3])
        self.assertFalse(by_slot[8])

    def test_recovers_parameter_values(self):
        dly = next(e for e in self.decoded.effects if e.slot_index == 8)
        self.assertAlmostEqual(dly.params[0], 35.0, places=3)   # Mix
        self.assertAlmostEqual(dly.params[1], 375.0, places=3)  # Time

    def test_params_is_the_full_fifteen_slot_array(self):
        """Symmetry with encode(), which writes into the same 15-float array."""
        for effect in self.decoded.effects:
            self.assertEqual(len(effect.params), 15)

    def test_reads_params_at_their_catalog_index_not_their_position(self):
        """91 of 305 effects have gaps in their parameter indices.

        CAB AC Dream defines Volume/Low Cut/High Cut at idx 1/5/6, so reading
        the first three floats of the block returns a neighbouring field's
        value instead of Volume.
        """
        eid = prst_lib.resolve_effect_id("CAB", "AC Dream")
        indices = [d["idx"] for d in prst_lib.EFFECT_PARAMS[eid]]
        self.assertEqual(indices, [1, 5, 6], "catalog changed; update this test")

        encoded = prst_lib.encode(_preset_with_cab(eid))
        cab = next(e for e in prst_lib.decode(encoded).effects
                   if e.slot_index == 5)
        self.assertAlmostEqual(cab.params[1], 50.0, places=3)     # Volume
        self.assertAlmostEqual(cab.params[5], 19.0, places=3)     # Low Cut
        self.assertAlmostEqual(cab.params[6], 20001.0, places=3)  # High Cut

    def test_recovers_tempo_and_volume(self):
        self.assertEqual(self.decoded.patch_tempo, 120)
        self.assertEqual(self.decoded.patch_volume, 50)


class TestDefinedParams(unittest.TestCase):
    """The (index, value) pairs a live-edit write actually sends."""

    def test_yields_only_the_params_the_effect_defines(self):
        eid = prst_lib.resolve_effect_id("CAB", "AC Dream")
        pairs = prst_lib.defined_params(eid, prst_lib.default_params(eid))
        self.assertEqual([i for i, _ in pairs], [1, 5, 6])

    def test_pairs_carry_the_catalog_defaults(self):
        eid = prst_lib.resolve_effect_id("CAB", "AC Dream")
        pairs = prst_lib.defined_params(eid, prst_lib.default_params(eid))
        self.assertEqual([v for _, v in pairs], [50.0, 19.0, 20001.0])

    def test_sequential_effects_are_unaffected(self):
        eid = prst_lib.resolve_effect_id("AMP", "Foxy 30TB")
        pairs = prst_lib.defined_params(eid, prst_lib.default_params(eid))
        self.assertEqual([i for i, _ in pairs], [0, 1, 2, 3, 4, 5])



if __name__ == "__main__":
    unittest.main()
