import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.helper_names import build_key_map, migrate_value  # noqa: E402

# Serialisierungs-Helper mit &label-Key (StringCopy) und Global als 2. Argument,
# plus ein mehrdeutiger Key ("start") der verworfen werden muss.
OLD = """
StringCopy(&Var3, "irbs15", 16);
StringIntConCat(&Var3, bVar0, 16);
func_673(&Var3, Global_100.f_3605[bVar0 /*111*/].f_8681[bVar1], &d, &c, bVar1, 0);
func_641("start", Global_100.f_10, &d, &c, 0, 0);
func_641("start", Global_100.f_20, &d, &c, 0, 0);
"""
# neue Version: irbs15 shiftet f_8681 -> f_8682, Stride 111 -> 120.
NEW = OLD.replace("f_8681", "f_8682").replace("/*111*/", "/*120*/")


class HelperNamesTest(unittest.TestCase):
    def _dirs(self, old_src, new_src):
        tmp = tempfile.mkdtemp()
        old = pathlib.Path(tmp) / "old"
        new = pathlib.Path(tmp) / "new"
        old.mkdir()
        new.mkdir()
        (old / "s.c").write_text(old_src, encoding="utf-8")
        (new / "s.c").write_text(new_src, encoding="utf-8")
        migrate_value.__defaults__[-1].clear()  # Resolver-Cache leeren
        return str(old), str(new)

    def test_unambiguous_key_map_drops_duplicates(self):
        m = build_key_map(str(self._dirs(OLD, NEW)[0]))
        # "irbs15" ist eindeutig -> vorhanden; Element-Iterationsindex gestrippt.
        self.assertEqual(m.get("irbs15"), "Global_100.f_3605[i /*111*/].f_8681")
        # "start" ist mehrdeutig (zwei Globals) -> verworfen.
        self.assertNotIn("start", m)

    def test_migrates_helper_named_field(self):
        old, new = self._dirs(OLD, NEW)
        self.assertEqual(
            migrate_value("Global_100.f_3605[i /*111*/].f_8681", old, new),
            "Global_100.f_3605[i /*120*/].f_8682",
        )

    def test_unknown_value_returns_none(self):
        old, new = self._dirs(OLD, NEW)
        self.assertIsNone(migrate_value("Global_999.f_1", old, new))

    def test_ambiguous_value_not_migrated(self):
        # "start" wurde verworfen -> der zugehoerige alte Wert bleibt unaufloesbar.
        old, new = self._dirs(OLD, NEW)
        self.assertIsNone(migrate_value("Global_100.f_10", old, new))


if __name__ == "__main__":
    unittest.main()
