import pathlib
import tempfile
import unittest

from tools import mp_tunables

ROOT = pathlib.Path(__file__).resolve().parent.parent
REAL_OLD = ROOT / "scripts" / "1.71-3586"
REAL_LEGACY = ROOT / "scripts" / "1.73-3889"
REAL_ENHANCED = ROOT / "scripts" / "enhanced-1.73-1158"


def corpus(text: str) -> pathlib.Path:
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "tuneables_processing.c").write_text(text, encoding="utf-8")
    return d


class ResolveTest(unittest.TestCase):
    INI = 'OFFSET_a = "Global_262145.f_10"\nOFFSET_b = "Global_262145.f_11"\nOFFSET_c = "Global_1.f_2"\n'

    def test_follows_the_name_and_ignores_case(self):
        old = corpus('\tGlobal_262145.f_10 = unk_0x1(joaat("some_flag"), 0);\n'
                     '\tGlobal_262145.f_11 = unk_0x2(12345, 3);\n')
        new = corpus('\tGlobal_262145.f_14 = NETWORK::_NETWORK_GET_TUNABLES_REGISTRATION_BOOL(joaat("SOME_FLAG"), false);\n'
                     '\tGlobal_262145.f_99 = NETWORK::_NETWORK_GET_TUNABLES_REGISTRATION_INT(12345, 3);\n')
        self.assertEqual(mp_tunables.resolve(self.INI, old, new),
                         {"OFFSET_a": "Global_262145.f_14", "OFFSET_b": "Global_262145.f_99"})

    def test_ambiguous_or_missing_names_are_left_alone(self):
        old = corpus('\tGlobal_262145.f_10 = f(joaat("x"), 0);\n\tGlobal_262145.f_11 = f(joaat("y"), 0);\n')
        new = corpus('\tGlobal_262145.f_1 = f(joaat("x"), 0);\n\tGlobal_262145.f_2 = f(joaat("x"), 0);\n')
        self.assertEqual(mp_tunables.resolve(self.INI, old, new), {})


@unittest.skipUnless(REAL_OLD.is_dir() and REAL_LEGACY.is_dir() and REAL_ENHANCED.is_dir(), "script dumps missing")
class RealDataTest(unittest.TestCase):
    INI = ('OFFSET_enable_murica = "Global_262145.f_15989"\n'
           'OFFSET_too_many_props = "Global_262145.f_15071"\n'
           'OFFSET_random_transform = "Global_262145.f_36024"\n')

    def test_legacy_1_73(self):
        self.assertEqual(mp_tunables.resolve(self.INI, REAL_OLD, REAL_LEGACY), {
            "OFFSET_enable_murica": "Global_262145.f_16078",   # ENABLE_CREATOR_AMERICAN_FLAG_STUNT_RACES
            "OFFSET_too_many_props": "Global_262145.f_15160",  # PROPPROXIMITYLIMIT
            "OFFSET_random_transform": "Global_262145.f_36114",
        })

    def test_enhanced(self):
        self.assertEqual(mp_tunables.resolve(self.INI, REAL_OLD, REAL_ENHANCED), {
            "OFFSET_enable_murica": "Global_262145.f_16082",
            "OFFSET_too_many_props": "Global_262145.f_15164",
            "OFFSET_random_transform": "Global_262145.f_36711",
        })


if __name__ == "__main__":
    unittest.main()
