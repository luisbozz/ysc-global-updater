import os
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.score_corpus import globals_in, present, read_corpus, score  # noqa: E402

INI = '''[OFFSETS]
OFFSET_a = "Global_262145.f_36114"
OFFSET_b = "Global_4980736.f_7044[i /*642*/].f_88"
OFFSET_c = "Local_42"
worldptr = "48 8B 05 ? ? ? ?"
'''


class ParseTest(unittest.TestCase):
    def test_only_global_values_are_scored(self):
        names = [n for n, _ in globals_in(INI)]
        self.assertEqual(names, ["OFFSET_a", "OFFSET_b"])


class PresenceTest(unittest.TestCase):
    def test_plain_path_must_match_verbatim(self):
        self.assertTrue(present("Global_262145.f_36114", "x = Global_262145.f_36114;"))
        self.assertFalse(present("Global_262145.f_36114", "x = Global_262145.f_3611;"))

    def test_array_index_is_wildcarded_but_its_comment_is_not(self):
        # The ini names the index positionally; the corpus uses whatever the
        # decompiler called that local. The /*N*/ comment is the stable part.
        value = "Global_4980736.f_7044[i /*642*/].f_88"
        self.assertTrue(present(value, "Global_4980736.f_7044[iVar3 /*642*/].f_88"))
        self.assertTrue(present(value, "Global_4980736.f_7044[num7 /*642*/].f_88"))
        self.assertFalse(present(value, "Global_4980736.f_7044[iVar3 /*643*/].f_88"))

    def test_empty_value_is_never_present(self):
        self.assertFalse(present("", "anything"))

    def test_score_counts_hits_out_of_all_globals(self):
        corpus = "Global_262145.f_36114 and Global_4980736.f_7044[k /*642*/].f_88"
        self.assertEqual(score(INI, corpus), (2, 2))
        self.assertEqual(score(INI, "nothing here"), (0, 2))


SCRIPTS = ROOT / "scripts"
LEGACY = SCRIPTS / "1.73-3889"
ENHANCED = SCRIPTS / "enhanced-1.73-1158"
REPORTS = ROOT / "reports"
MIGRATED_LEGACY = REPORTS / "offsets.migrated.ini"
MIGRATED_ENHANCED = REPORTS / "offsets.migrated.enhanced.ini"

HAVE_BOTH = all(p.exists() for p in (LEGACY, ENHANCED, MIGRATED_LEGACY, MIGRATED_ENHANCED))


@unittest.skipUnless(HAVE_BOTH, "needs both corpora and both migration results")
class VariantMigrationTest(unittest.TestCase):
    """Each migration result must fit its own game better than the other one.

    This is the end-to-end check that the Legacy -> Enhanced migration did real
    work: without it, the honest alternative would be to ship the Legacy file
    for Enhanced too."""

    @classmethod
    def setUpClass(cls):
        cls.legacy_corpus = read_corpus(LEGACY)
        cls.enhanced_corpus = read_corpus(ENHANCED)
        cls.legacy_ini = MIGRATED_LEGACY.read_text(encoding="utf-8")
        cls.enhanced_ini = MIGRATED_ENHANCED.read_text(encoding="utf-8")

    def test_the_enhanced_result_fits_enhanced_better_than_legacy(self):
        own, _ = score(self.enhanced_ini, self.enhanced_corpus)
        other, _ = score(self.enhanced_ini, self.legacy_corpus)
        self.assertGreater(own, other)

    def test_the_legacy_result_fits_legacy_better_than_enhanced(self):
        own, _ = score(self.legacy_ini, self.legacy_corpus)
        other, _ = score(self.legacy_ini, self.enhanced_corpus)
        self.assertGreater(own, other)

    def test_migrating_beats_shipping_the_legacy_file_unchanged(self):
        migrated, _ = score(self.enhanced_ini, self.enhanced_corpus)
        copied, _ = score(self.legacy_ini, self.enhanced_corpus)
        self.assertGreater(migrated, copied,
                           "migration gained nothing over copying the Legacy ini")

    def test_both_results_reach_a_comparable_fit_on_their_own_corpus(self):
        # Neither variant may be systematically worse served than the other.
        enh, total = score(self.enhanced_ini, self.enhanced_corpus)
        leg, _ = score(self.legacy_ini, self.legacy_corpus)
        self.assertLess(abs(enh - leg) / total, 0.05)


if __name__ == "__main__":
    unittest.main()
