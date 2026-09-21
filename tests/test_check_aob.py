import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.check_aob import AMBIGUOUS_OK, UNUSED, aob_section, to_regex  # noqa: E402

INI = '''[AOB]
worldptr = "48 8B 05 ? ? ? ? 48 8B 48 08"
empty_one = ""
[OFFSETS]
OFFSET_a = "Global_1.f_2"
notanaob = "48 8B"
'''


class SectionTest(unittest.TestCase):
    def test_only_the_aob_section_is_read(self):
        got = aob_section(INI)
        self.assertEqual(set(got), {"worldptr", "empty_one"})

    def test_an_empty_pattern_is_kept_not_dropped(self):
        # Empty means "not derived for this build", which the report has to
        # show; dropping it would make a missing pattern look like a passing one.
        self.assertEqual(aob_section(INI)["empty_one"], "")

    def test_no_aob_section_yields_nothing(self):
        self.assertEqual(aob_section("[OFFSETS]\nOFFSET_a = \"x\"\n"), {})


class RegexTest(unittest.TestCase):
    def test_wildcards_match_any_byte(self):
        rx = to_regex("48 8B 05 ? ? ? ? 45")
        self.assertTrue(rx.search(bytes.fromhex("488b0500112233" + "45")))
        self.assertTrue(rx.search(bytes.fromhex("488b05ffffffff" + "45")))

    def test_literal_bytes_must_match(self):
        rx = to_regex("48 8B 05")
        self.assertIsNone(rx.search(bytes.fromhex("488b06")))

    def test_a_pattern_byte_is_never_treated_as_a_regex_metacharacter(self):
        # 0x2E is '.' and 0x5C is '\\' in ASCII; unescaped they would match
        # anything, and a broken pattern would silently look fine.
        rx = to_regex("2E 5C 2A")
        self.assertIsNotNone(rx.search(bytes.fromhex("2e5c2a")))
        self.assertIsNone(rx.search(bytes.fromhex("415c2a")))

    def test_a_zero_byte_matches_across_newlines(self):
        # DOTALL matters: machine code is full of 0x0A.
        rx = to_regex("41 0A 42")
        self.assertIsNotNone(rx.search(bytes.fromhex("410a42")))
        self.assertIsNotNone(to_regex("41 ? 42").search(bytes.fromhex("410a42")))


class KnownStateTest(unittest.TestCase):
    def test_unused_patterns_are_documented_with_a_reason(self):
        # The point of the list is that nobody re-derives a pattern nothing uses.
        for name, why in UNUSED.items():
            self.assertTrue(why.strip(), f"{name} has no reason given")

    def test_the_ambiguous_exception_is_narrow(self):
        # Every other multi-hit pattern must still be reported as work.
        self.assertEqual(set(AMBIGUOUS_OK), {"versionptr"})


SHIPPED = pathlib.Path("/opt/Xenvious/Xenvious/OfflineData")


@unittest.skipUnless(SHIPPED.is_dir(), "no Xenvious checkout")
class ShippedIniTest(unittest.TestCase):
    def test_every_scanned_legacy_pattern_is_populated(self):
        aob = aob_section((SHIPPED / "legacy" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace"))
        for name, pattern in aob.items():
            if name in UNUSED:
                continue
            self.assertTrue(pattern.strip(), f"legacy {name} is empty")

    def test_the_variants_share_the_same_pattern_names(self):
        names = [set(aob_section((SHIPPED / v / "offsets.ini").read_text(
            encoding="utf-8", errors="replace"))) for v in ("legacy", "enhanced")]
        self.assertEqual(names[0], names[1])

    def test_no_enhanced_pattern_was_copied_from_legacy(self):
        # A Legacy pattern in the Enhanced file would scan, find nothing, and
        # produce a pointer computed from address zero.
        legacy = aob_section((SHIPPED / "legacy" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace"))
        enhanced = aob_section((SHIPPED / "enhanced" / "offsets.ini").read_text(
            encoding="utf-8", errors="replace"))
        for name, pattern in enhanced.items():
            if not pattern.strip():
                continue
            self.assertNotEqual(pattern, legacy.get(name),
                                f"enhanced {name} is the Legacy pattern verbatim")


if __name__ == "__main__":
    unittest.main()
