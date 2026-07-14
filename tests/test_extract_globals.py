import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.extract_globals import (  # noqa: E402
    build_container_map,
    canonicalize_global,
    discover_helpers,
    extract_file,
    extract_helper_calls,
    merge,
    parent_path,
    split_args,
)

SYNTH = """
uParam0->f_1 = DATAFILE::DATADICT_CREATE_DICT(uParam0->f_0, "mission");
uParam0->f_2 = DATAFILE::DATADICT_CREATE_DICT(uParam0->f_1, "gen");
DATAFILE::DATADICT_SET_INT(uParam0->f_2, "trntype", Global_100.f_5);
DATAFILE::DATADICT_SET_FLOAT(uParam0->f_2, "spd", func_9(uParam0));
TEXT_LABEL_ASSIGN_STRING(&key, "irbs", 16);
TEXT_LABEL_APPEND_INT(&key, i, 16);
uParam0->f_9[i] = DATAFILE::DATADICT_CREATE_ARRAY(uParam0->f_2, &key);
DATAFILE::DATAARRAY_ADD_INT(uParam0->f_9[i], Global_200.f_7[i]);
"""


class ExtractGlobalsTest(unittest.TestCase):
    def test_split_args_respects_nesting(self) -> None:
        self.assertEqual(
            split_args('a, "b,c", func(d, e), g[h, i]'),
            ["a", '"b,c"', "func(d, e)", "g[h, i]"],
        )

    def test_canonicalize_index_vars(self) -> None:
        # variable Indizes -> positionsbasiert i/j/k, Stride bleibt, Literale bleiben
        self.assertEqual(canonicalize_global("Global_1.f_2[iVar0]"), "Global_1.f_2[i]")
        self.assertEqual(
            canonicalize_global("Global_1.f_2[iVar182 /*26968*/]"),
            "Global_1.f_2[i /*26968*/]",
        )
        self.assertEqual(
            canonicalize_global("Global_1[iVar0 /*4*/].f_3[iVar1 /*36*/]"),
            "Global_1[i /*4*/].f_3[j /*36*/]",
        )
        self.assertEqual(canonicalize_global("Global_1.f_5[0 /*3*/]"), "Global_1.f_5[0 /*3*/]")

    def test_container_map_and_parent_chain(self) -> None:
        cont = build_container_map(SYNTH)
        self.assertIn("uParam0->f_2", cont)
        self.assertEqual(cont["uParam0->f_2"]["key"], "gen")
        self.assertEqual(parent_path("uParam0->f_2", cont), "mission.gen")

    def test_set_and_array_mappings(self) -> None:
        ms = extract_file(SYNTH)
        by_key: dict = {}
        for m in ms:  # erste (VOLLE) Variante je Key behalten
            by_key.setdefault(m["key"], m)

        self.assertIn("trntype", by_key)
        self.assertEqual(by_key["trntype"]["path"], "mission.gen.trntype")
        self.assertEqual(by_key["trntype"]["global"], "Global_100.f_5")
        self.assertEqual(by_key["trntype"]["type"], "int")

        # DATAARRAY_ADD leaf whose container was CREATE'd via a &label key
        self.assertIn("irbs", by_key)
        self.assertEqual(by_key["irbs"]["path"], "mission.gen.irbs")
        self.assertEqual(by_key["irbs"]["global"], "Global_200.f_7[i]")
        self.assertTrue(by_key["irbs"]["dynamic"])

    def test_container_stringcopy_key_two_pass(self) -> None:
        # Container-Name per StringCopy (statt TEXT_LABEL); ADD steht VOR dem
        # CREATE (Serialize- vor Setup-Funktion); der Wert traegt einen Element-
        # Iterations-Index, der zur Offset-Basis gestrippt werden muss.
        src = (
            "DATAFILE::DATAARRAY_ADD_INT(uParam0->f_50[bVar0], "
            "Global_300.f_3605[bVar0 /*26968*/].f_1699[bVar1]);\n"
            'StringCopy(&cVar2, "armr", 16);\n'
            "StringIntConCat(&cVar2, iVar0, 16);\n"
            "uParam0->f_50[iVar0] = DATAFILE::DATADICT_CREATE_ARRAY(uParam0->f_2, &cVar2);\n"
        )
        by_key: dict = {}
        for m in extract_file(src):  # erste (VOLLE) Variante je Key behalten
            by_key.setdefault(m["key"], m)
        self.assertIn("armr", by_key)
        # Element-Iterations-Index (zweite Ebene) gestrippt -> Offset-Basis:
        self.assertEqual(by_key["armr"]["global"], "Global_300.f_3605[i /*26968*/].f_1699")

    def test_non_global_values_are_skipped(self) -> None:
        # "spd" is set from a func_ call (no literal Global_) -> not a Stage-1 leaf
        ms = extract_file(SYNTH)
        self.assertNotIn("spd", {m["key"] for m in ms})

    def test_merge_dedupes_and_tracks_sources(self) -> None:
        a = extract_file(SYNTH)
        merged = merge({"file_a.c": a, "file_b.c": list(a)})
        trn = [m for m in merged if m["key"] == "trntype"]
        self.assertEqual(len(trn), 1)
        self.assertEqual(sorted(trn[0]["sources"]), ["file_a.c", "file_b.c"])


HELPER_SYNTH = """
void func_627(var cont, char* key, int value)
{
    DATAFILE::DATADICT_SET_INT(cont, key, value);
}

void serialize(var uParam0)
{
    uParam0->f_1 = DATAFILE::DATADICT_CREATE_DICT(uParam0->f_0, "mission");
    func_627(uParam0->f_1, "trntype", Global_100.f_5);
    func_627(uParam0->f_1, "skipme", localOnly);
}
"""


class HelperResolutionTest(unittest.TestCase):
    def test_discover_forwarding_helper(self) -> None:
        helpers = discover_helpers(HELPER_SYNTH, {"func_627"})
        self.assertIn("func_627", helpers)
        h = helpers["func_627"]
        self.assertEqual((h["container_idx"], h["key_idx"], h["val_idx"]), (0, 1, 2))
        self.assertEqual(h["type"], "int")

    def test_helper_call_site_mapping(self) -> None:
        ms = extract_helper_calls(HELPER_SYNTH, build_container_map(HELPER_SYNTH))
        by_key = {m["key"]: m for m in ms}
        self.assertIn("trntype", by_key)
        self.assertEqual(by_key["trntype"]["path"], "mission.trntype")
        self.assertEqual(by_key["trntype"]["global"], "Global_100.f_5")
        self.assertEqual(by_key["trntype"]["type"], "int")
        self.assertEqual(by_key["trntype"]["helper"], "func_627")
        self.assertNotIn("skipme", by_key)

    def test_helpers_included_in_extract_file(self) -> None:
        keys = {m["key"] for m in extract_file(HELPER_SYNTH)}
        self.assertIn("trntype", keys)


class ExtractGlobalsSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.keep = ROOT / "reports" / "sources.keep.txt"
        cls.new = ROOT / "new"

    def test_real_extraction_yields_semantic_paths(self) -> None:
        if not self.keep.is_file() or not self.new.is_dir():
            self.skipTest("new/ oder Keep-Liste nicht vorhanden")
        out = ROOT / "reports" / "globals.json"
        if not out.is_file():
            self.skipTest("globals.json noch nicht erzeugt")
        data = json.loads(out.read_text(encoding="utf-8"))
        self.assertGreater(data["summary"]["unique_mappings"], 1000)
        self.assertGreater(data["summary"]["with_semantic_path"], 500)
        keys = {m["key"] for m in data["mappings"]}
        self.assertIn("debugOnlyVersion", keys)


if __name__ == "__main__":
    unittest.main()
