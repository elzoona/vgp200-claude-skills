"""Effect names that appear more than once must be addressable individually.

Three (module, name) pairs are duplicated in Valeton's catalog: CAB/User IR
(x20), AMP/SnapTone (x5) and DST/SnapTone (x5). _BY_MODULE_NAME is a dict
keyed on (module, lowercased name), so every duplicate collapsed onto the
last id inserted: resolve_effect_id('CAB', 'User IR') silently returned IR
slot 20. A preset asking for the first IR slot got the twentieth, with no
error -- the kind of wrong answer you only discover by ear.

Stdlib only: `python3 tests/test_effect_name_resolution.py -v`
"""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent
                       / "skills" / "gp200-prst-export" / "scripts"))

import prst_lib  # noqa: E402
from prst_lib import EFFECT_MAP, PresetBuildError, resolve_effect_id  # noqa: E402


def ids_for(module, name):
    return sorted(i for i, v in EFFECT_MAP.items()
                  if v["module"] == module and v["name"] == name)


class TestDuplicateNamesAreNumbered(unittest.TestCase):
    def test_user_ir_1_is_the_lowest_id(self):
        self.assertEqual(resolve_effect_id("CAB", "User IR 1"), ids_for("CAB", "User IR")[0])

    def test_user_ir_1_is_the_york_audio_slot(self):
        # 0x0A100000 -- an example User IR slot.
        self.assertEqual(resolve_effect_id("CAB", "User IR 1"), 0x0A100000)

    def test_user_ir_20_is_the_highest_id(self):
        self.assertEqual(resolve_effect_id("CAB", "User IR 20"), ids_for("CAB", "User IR")[-1])

    def test_numbering_is_contiguous_and_ordered(self):
        got = [resolve_effect_id("CAB", f"User IR {n}") for n in range(1, 21)]
        self.assertEqual(got, ids_for("CAB", "User IR"))

    def test_snaptone_is_numbered_per_module(self):
        self.assertEqual(resolve_effect_id("AMP", "SnapTone 1"), ids_for("AMP", "SnapTone")[0])
        self.assertEqual(resolve_effect_id("DST", "SnapTone 1"), ids_for("DST", "SnapTone")[0])
        self.assertNotEqual(resolve_effect_id("AMP", "SnapTone 1"),
                            resolve_effect_id("DST", "SnapTone 1"))


class TestAmbiguousBareNameIsRejected(unittest.TestCase):
    def test_bare_duplicate_name_raises_instead_of_guessing(self):
        with self.assertRaises(PresetBuildError) as cm:
            resolve_effect_id("CAB", "User IR")
        self.assertIn("User IR 1", str(cm.exception))

    def test_out_of_range_index_raises(self):
        with self.assertRaises(PresetBuildError):
            resolve_effect_id("CAB", "User IR 21")
        with self.assertRaises(PresetBuildError):
            resolve_effect_id("CAB", "User IR 0")


class TestUniqueNamesUnaffected(unittest.TestCase):
    def test_ordinary_names_still_resolve(self):
        for module, name in (("CAB", "AC Dream"), ("DST", "Green OD"),
                             ("AMP", "Foxy 30TB"), ("EQ", "Hyper EQ"),
                             ("DLY", "Tape Delay S"), ("RVB", "Plate")):
            with self.subTest(effect=name):
                eid = resolve_effect_id(module, name)
                self.assertEqual(EFFECT_MAP[eid]["name"], name)
                self.assertEqual(EFFECT_MAP[eid]["module"], module)

    def test_unknown_name_still_raises(self):
        with self.assertRaises(PresetBuildError):
            resolve_effect_id("CAB", "Not A Real Cab")


if __name__ == "__main__":
    unittest.main(verbosity=2)
