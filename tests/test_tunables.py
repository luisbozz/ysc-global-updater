import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.tunables import (  # noqa: E402
    field_counts, key_map, resolve, run_shift,
)

G = "Global_4718592"


def corpus(tmp: pathlib.Path, name: str, text: str) -> pathlib.Path:
    d = tmp / name
    d.mkdir()
    (d / "x.c").write_text(text, encoding="utf-8")
    return d


def writes(*pairs) -> str:
    return "".join(
        f'\tDATAFILE::DATADICT_SET_INT(*uParam1, "{k}", {G}.f_{f});\n' for k, f in pairs)


class KeyMapTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = pathlib.Path(self.td.name)

    def test_a_key_with_stable_neighbours_maps(self):
        old = corpus(self.tmp, "old", writes(("type", 1), ("head", 2), ("lrgs", 3)))
        new = corpus(self.tmp, "new", writes(("type", 11), ("head", 12), ("lrgs", 13)))
        self.assertEqual(key_map(old, new)[f"{G}.f_2"], f"{G}.f_12")

    def test_a_key_repeated_with_different_neighbours_is_dropped(self):
        # Two writes of "head" in different places would make the mapping a
        # coin flip, so neither is used.
        old = corpus(self.tmp, "old",
                     writes(("type", 1), ("head", 2), ("lrgs", 3)) +
                     writes(("aaa", 7), ("head", 8), ("bbb", 9)))
        new = corpus(self.tmp, "new",
                     writes(("type", 11), ("head", 12), ("lrgs", 13)))
        m = key_map(old, new)
        self.assertNotIn(f"{G}.f_8", m)
        self.assertEqual(m.get(f"{G}.f_2"), f"{G}.f_12")

    def test_the_same_key_under_a_changed_neighbour_does_not_map(self):
        old = corpus(self.tmp, "old", writes(("type", 1), ("head", 2), ("lrgs", 3)))
        new = corpus(self.tmp, "new", writes(("type", 11), ("head", 12), ("xxxx", 13)))
        self.assertNotIn(f"{G}.f_2", key_map(old, new))


class RunShiftTest(unittest.TestCase):
    def test_a_run_that_lands_on_exactly_one_new_run(self):
        old = {10: 1, 11: 1, 12: 1}
        new = {20: 1, 21: 1, 22: 1}
        self.assertEqual(run_shift(11, old, new, [(5, 15), (30, 40)]), 10)

    def test_no_bracket_means_no_answer(self):
        old = {10: 1, 11: 1}
        new = {20: 1, 21: 1}
        self.assertIsNone(run_shift(11, old, new, []))
        self.assertIsNone(run_shift(11, old, new, [(5, 15)]))       # only below

    def test_two_candidate_runs_are_refused(self):
        old = {10: 1, 11: 1}
        new = {20: 1, 21: 1, 25: 1, 26: 1}
        self.assertIsNone(run_shift(11, old, new, [(5, 15), (40, 55)]))

    def test_a_run_of_the_wrong_length_is_refused(self):
        old = {10: 1, 11: 1, 12: 1}
        new = {20: 1, 21: 1}
        self.assertIsNone(run_shift(11, old, new, [(5, 15), (30, 40)]))

    def test_a_target_run_reusing_an_old_field_is_refused(self):
        # A field that already existed in the old corpus is a different thing
        # that happens to carry that number.
        old = {10: 1, 11: 1, 21: 1}
        new = {20: 1, 21: 1}
        self.assertIsNone(run_shift(11, old, new, [(5, 15), (30, 40)]))


class ResolveTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.addCleanup(self.td.cleanup)
        self.tmp = pathlib.Path(self.td.name)
        self.old = corpus(self.tmp, "old", writes(("type", 1), ("head", 2), ("lrgs", 3)))
        self.new = corpus(self.tmp, "new", writes(("type", 11), ("head", 12), ("lrgs", 13)))

    def test_a_stale_offset_is_fixed(self):
        ini = f'OFFSET_cps_head = "{G}.f_2"\n'
        fixes, notes = resolve(ini, ini, self.old, self.new)
        self.assertEqual(fixes, {"OFFSET_cps_head": f"{G}.f_12"})
        self.assertEqual(notes[0][3], "key")

    def test_an_already_migrated_offset_is_left_alone(self):
        ini = f'OFFSET_cps_head = "{G}.f_2"\n'
        mig = f'OFFSET_cps_head = "{G}.f_12"\n'
        self.assertEqual(resolve(ini, mig, self.old, self.new)[0], {})

    def test_a_field_that_still_exists_is_left_alone(self):
        # "Still there" is the best evidence available that it is still right,
        # and overwriting it on a guess is how a working offset gets broken.
        old = corpus(self.tmp, "old2", writes(("type", 1), ("head", 2)))
        new = corpus(self.tmp, "new2", writes(("type", 1), ("head", 2)))
        ini = f'OFFSET_cps_head = "{G}.f_2"\n'
        self.assertEqual(resolve(ini, ini, old, new)[0], {})

    def test_a_non_tuneables_value_is_ignored(self):
        ini = 'OFFSET_x = "Global_4980736.f_7044[i /*642*/].f_3"\n'
        self.assertEqual(resolve(ini, ini, self.old, self.new)[0], {})


SCRIPTS = ROOT / "scripts"
OLD, NEW = SCRIPTS / "1.71-3586", SCRIPTS / "1.73-3889"


@unittest.skipUnless(OLD.is_dir() and NEW.is_dir(), "1.71-3586 or 1.73-3889 missing")
class RealCorpusTest(unittest.TestCase):
    """Values cross-checked by hand against the decompiled source."""

    def test_known_fields(self):
        m = key_map(OLD, NEW)
        # "head" sits between "type" and "lrgs" in both builds.
        self.assertEqual(m[f"{G}.f_124916"], f"{G}.f_126191")
        self.assertEqual(m[f"{G}.f_124917"], f"{G}.f_126192")

    def test_every_tuneables_source_field_really_exists(self):
        # The map covers several roots now, so only tuneables entries can be
        # checked against the tuneables field index.
        old_c = field_counts(OLD)
        for old_value in key_map(OLD, NEW):
            if not old_value.startswith(G + ".f_"):
                continue
            if "." in old_value[len(G) + 1:].lstrip("f_"):
                continue                       # nested, not a bare field
            self.assertIn(int(old_value.rsplit("_", 1)[1]), old_c)

    def test_the_map_covers_the_three_call_shapes(self):
        m = key_map(OLD, NEW)
        # DATADICT_SET_ with a generic key, identified by its neighbours
        self.assertEqual(m[f"{G}.f_124916"], f"{G}.f_126191")     # "head"
        # helper call carrying the key as a literal
        self.assertEqual(m[f"{G}.f_3593"], f"{G}.f_3826")         # "camf"
        # key built from a literal prefix plus an index
        self.assertEqual(m[f"{G}.f_188047"], f"{G}.f_194563")     # "tmrph"

    def test_the_map_reaches_beyond_the_tuneables_root(self):
        m = key_map(OLD, NEW)
        self.assertEqual(m["Global_4980736.f_187603"], "Global_4980736.f_196762")
        self.assertEqual(m["Global_4980736.f_214055"], "Global_4980736.f_224044")


if __name__ == "__main__":
    unittest.main()
