import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.dialect import normalize_source, read_source, read_source_lines  # noqa: E402


class NormalizeTest(unittest.TestCase):
    def test_dereference_star_is_removed(self):
        self.assertEqual(normalize_source("if (*Global_4718592.f_128458 == 6)"),
                         "if (Global_4718592.f_128458 == 6)")
        self.assertEqual(normalize_source("return *Global_262145.f_38199;"),
                         "return Global_262145.f_38199;")

    def test_a_cast_before_the_star_is_kept(self):
        self.assertEqual(normalize_source("(float)*Global_262145.f_37874"),
                         "(float)Global_262145.f_37874")

    def test_spaced_multiplication_is_untouched(self):
        # The decompiler prints a product with spaces on both sides, so it can
        # never look like the tight dereference form.
        for text in ("iVar0 * Global_5", "f_1 * Global_262145.f_3",
                     "(a + b) * Global_9"):
            self.assertEqual(normalize_source(text), text)

    def test_the_legacy_dialect_is_already_normal(self):
        text = "Global_4718592.f_121958 == 6 || Global_4718592.f_121958 == 7"
        self.assertEqual(normalize_source(text), text)

    def test_a_star_before_anything_else_is_kept(self):
        for text in ("*iVar0", "*uParam0->f_8", "a */* comment */b", "*pLocal_3"):
            self.assertEqual(normalize_source(text), text)

    def test_normalizing_twice_changes_nothing_more(self):
        once = normalize_source("*Global_1.f_2 + *Global_3")
        self.assertEqual(normalize_source(once), once)


class ReadTest(unittest.TestCase):
    def test_reading_applies_the_normalisation(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "x.c"
            p.write_text("a = *Global_7.f_1;\nb = Global_7.f_2;\n", encoding="utf-8")
            self.assertEqual(read_source(p), "a = Global_7.f_1;\nb = Global_7.f_2;\n")
            self.assertEqual(read_source_lines(p),
                             ["a = Global_7.f_1;", "b = Global_7.f_2;"])

    def test_undecodable_bytes_do_not_raise(self):
        with tempfile.TemporaryDirectory() as td:
            p = pathlib.Path(td) / "x.c"
            p.write_bytes(b"a = *Global_7;\n\xff\xfe\n")
            self.assertIn("Global_7", read_source(p))


if __name__ == "__main__":
    unittest.main()
