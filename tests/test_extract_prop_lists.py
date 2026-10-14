import json
import pathlib
import sys
import tempfile
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import extract_prop_lists as E  # noqa: E402

A, B, C, D = (E.joaat(n) for n in ("prop_a", "prop_b", "prop_c", "prop_d"))


def script(*lines: str) -> str:
    return "\n".join(lines) + "\n"


class RefreshListsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = pathlib.Path(self.tmp.name)
        self.scripts = root / "scripts"
        self.scripts.mkdir()
        (self.scripts / "fm_race_creator.c").write_text(script(
            'if (m == joaat("prop_a") || m == joaat("prop_b") || m == joaat("prop_c"))',
            # shares only one hash with the list: must not be pulled in
            'if (m == joaat("prop_c") || m == joaat("prop_d") || m == joaat("prop_e"))'))
        self.seed = root / "seed.json"
        seed = {k: [] for k in E.LISTS}
        seed["prop_model_booster"] = [f"{A:X}", f"{B:X}"]
        self.seed.write_text(json.dumps(seed))

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, lists):
        return E.refresh_lists(lists, self.scripts, self.seed)

    def test_adds_the_rest_of_a_matching_condition_only(self):
        lists, rows = self.run_once({"prop_model_booster": ["0"]})
        got = E.parse_value(",".join(lists["prop_model_booster"]), "hex")
        self.assertEqual(got, {A, B, C})
        self.assertEqual(rows[0][3], 3)

    def test_second_run_changes_nothing(self):
        once, _ = self.run_once({"prop_model_booster": []})
        twice, rows = self.run_once(once)
        self.assertEqual(E.dump(once), E.dump(twice))
        self.assertEqual(rows[0][3], 0)

    def test_missing_list_is_left_out(self):
        lists, rows = self.run_once({"prop_model_booster": []})
        self.assertEqual(set(lists), {"prop_model_booster"})
        self.assertIsNone(rows[1][1])

    def test_all_digit_hex_is_read_as_hex(self):
        # "12345678" in a hex list is 0x12345678, not decimal
        self.assertEqual(E.parse_value("12345678", "hex"), {0x12345678})
        self.assertEqual(E.parse_value("-1", "dec"), {0xFFFFFFFF})

    def test_hex_is_written_without_leading_zeros(self):
        self.assertEqual(E.fmt({0x0516376C}, "hex"), "516376C")
        self.assertEqual(E.fmt({0xFFFFFFFF}, "dec"), "-1")

    def test_missing_scripts_raise_instead_of_emptying(self):
        with self.assertRaises(FileNotFoundError):
            E.refresh_lists({"prop_model_booster": ["A"]}, self.scripts / "nope", self.seed)


if __name__ == "__main__":
    unittest.main()
