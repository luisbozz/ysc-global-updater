import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import structural  # noqa: E402
from tools.structural import _align_runs, migrate_value  # noqa: E402


class AlignRunsTest(unittest.TestCase):
    def test_step_function_via_delta_overlap(self):
        # zwei Regionen mit unterschiedlicher Verschiebung (+76, dann +844).
        old = {1000, 1005, 1010, 5000, 5005}
        new = {1076, 1081, 1086, 5844, 5849}
        mp = _align_runs(old, new)
        self.assertEqual(mp.get(1005), 1081)   # +76
        self.assertEqual(mp.get(5000), 5844)   # +844

    def test_coincidental_field_does_not_fool_shift(self):
        # ein zufaelliges Zwischenfeld bei +8 darf den dominanten +76-Shift nicht
        # kippen (Delta-Overlap statt nearest-forward).
        old = {2000, 2005, 2010}
        new = {2008, 2076, 2081, 2086}  # 2008 = koinzident (+8)
        mp = _align_runs(old, new)
        self.assertEqual(mp.get(2000), 2076)
        self.assertEqual(mp.get(2005), 2081)

    def test_dominant_shift_wins_over_noise(self):
        # In einem dichten Lauf setzt sich die Mehrheits-Verschiebung (+50) gegen
        # einzelne abweichende new-Felder durch.
        old = {100, 105, 110, 115}
        new = {150, 155, 160, 165, 300}  # 300 ist Rauschen
        mp = _align_runs(old, new)
        self.assertEqual(mp.get(100), 150)
        self.assertEqual(mp.get(115), 165)


class StructuralMigrateTest(unittest.TestCase):
    def _dirs(self, old_src, new_src):
        tmp = tempfile.mkdtemp()
        old = pathlib.Path(tmp) / "old"
        new = pathlib.Path(tmp) / "new"
        old.mkdir()
        new.mkdir()
        (old / "s.c").write_text(old_src, encoding="utf-8")
        (new / "s.c").write_text(new_src, encoding="utf-8")
        # Resolver-Cache je Test leeren (Modul-global default arg).
        migrate_value.__defaults__[-1].clear()
        return str(old), str(new)

    def test_scalar_step_function_migration(self):
        old_src = "\n".join(f"x = Global_100.f_{n};" for n in (1000, 1005, 1010))
        new_src = "\n".join(f"x = Global_100.f_{n};" for n in (1076, 1081, 1086))
        old, new = self._dirs(old_src, new_src)
        self.assertEqual(migrate_value("Global_100.f_1005", old, new),
                         "Global_100.f_1081")

    def test_scalar_only_rejects_arrays(self):
        old_src = ("a = Global_100.f_67545[iVar0 /*500*/].f_10;\n"
                   "b = Global_100.f_67545[iVar0 /*500*/].f_11;\n")
        new_src = ("a = Global_100.f_67600[iVar0 /*505*/].f_10;\n"
                   "b = Global_100.f_67600[iVar0 /*505*/].f_11;\n")
        old, new = self._dirs(old_src, new_src)
        # scalar_only: ein Array-Wert wird NICHT geraten -> None.
        self.assertIsNone(
            migrate_value("Global_100.f_67545[i /*500*/].f_10", old, new, scalar_only=True))

    def test_unresolvable_value_returns_none(self):
        old, new = self._dirs("noise = Global_1.f_1;", "noise = Global_1.f_1;")
        self.assertIsNone(migrate_value("Global_999.f_5", old, new))

    def test_rg_prefilter_reads_only_matching_files(self):
        # _files_with darf nur Dateien liefern, die den Glob erwaehnen (rg-Filter).
        tmp = tempfile.mkdtemp()
        d = pathlib.Path(tmp)
        (d / "hit.c").write_text("v = Global_4718592.f_5;", encoding="utf-8")
        (d / "miss.c").write_text("v = Global_1.f_2;", encoding="utf-8")
        structural._files_with.cache_clear()
        files = structural._files_with(str(d), "Global_4718592")
        names = {pathlib.Path(f).name for f in files}
        self.assertIn("hit.c", names)
        self.assertNotIn("miss.c", names)


if __name__ == "__main__":
    unittest.main()
