"""xupdate: path notation, merging, patch answers. Needs no game dumps."""
import pathlib
import sys
import unittest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from xupdate import offsets, paths, space  # noqa: E402
from xupdate.common import Item  # noqa: E402


class Reshape(unittest.TestCase):
    def test_keeps_the_array_start_suffix(self):
        self.assertEqual(paths.reshape("Global_1925981[i]", "Global_1925601"), "Global_1925601[i]")
        self.assertEqual(paths.reshape("Global_4718592.f_3838[i /*26988*/].f_8432[i]",
                                       "Global_4718592.f_3605[i /*26968*/].f_8430"),
                         "Global_4718592.f_3605[i /*26968*/].f_8430[i]")

    def test_folded_literal_does_not_add_a_slot(self):
        # migrate_offsets writes ".f_102[0]" for a field; Xenvious would read one slot too far.
        self.assertEqual(paths.reshape("Global_5242880.f_1[i /*170*/].f_106",
                                       "Global_5242880.f_1[i /*165*/].f_102[0]"),
                         "Global_5242880.f_1[i /*165*/].f_102")

    def test_trailing_array_written_with_stride(self):
        self.assertEqual(paths.reshape("Global_4980736.f_51389[i]", "Global_4980736.f_49393[i /*258*/]"),
                         "Global_4980736.f_49393[i]")
        self.assertEqual(paths.reshape("Global_1837536.f_6[i /*104*/][i]", "Global_1837536.f_6[i /*100*/]"),
                         "Global_1837536.f_6[i /*100*/][i]")

    def test_other_structure_needs_a_person(self):
        self.assertIsNone(paths.reshape("Global_4980736.f_93162[i /*1277*/].f_166.f_9[j /*28*/].f_1",
                                        "Global_4980736.f_165.f_9"))


class Like(unittest.TestCase):
    def test_takes_the_old_index_names_and_new_strides(self):
        self.assertEqual(paths.like("Global_4980736.f_93162[i /*1277*/].f_166.f_9[j /*28*/].f_6",
                                    "Global_4980736.f_90320[25 /*1269*/].f_165.f_9[1 /*28*/].f_6.f_2"),
                         "Global_4980736.f_90320[i /*1269*/].f_165.f_9[j /*28*/].f_6")

    def test_index_expressions_and_locals(self):
        self.assertEqual(paths.like("Global_4718592.f_163249[i][i]", "Global_4718592.f_162033[iParam0->f_925 /*4*/]"),
                         "Global_4718592.f_162033[i][i]")
        self.assertEqual(paths.like("uLocal_8822", "iLocal_8815"), "uLocal_8815")

    def test_vector_components(self):
        self.assertEqual(paths.split_last("Global_1.f_3[i /*36*/].f_14", 1), "Global_1.f_3[i /*36*/].f_13.f_1")
        self.assertEqual(paths.join_last("Global_1.f_3[i /*36*/].f_13.f_1"), "Global_1.f_3[i /*36*/].f_14")


class Address(unittest.TestCase):
    def test_same_meaning_as_xenvious(self):
        self.assertEqual(paths.address("Global_4980736.f_71084[i /*631*/].f_14"), 4980736 + 71084 + 1 + 14)
        self.assertEqual(paths.address("Global_4980736.f_49494[i /*53*/].f_6[1 /*11*/].f_2"),
                         4980736 + 49494 + 1 + 6 + 1 + 11 + 2)
        self.assertEqual(paths.address("Global_4718592.f_112623[2]"), 4718592 + 112623 + 1 + 2)


INI = '''[BUILD]
label = "1.73-3889"
[OTHER]
x = "1"
[OFFSETS]
OFFSET_a = "Global_10.f_5[i]"
OFFSET_b = "Global_10.f_6"
OFFSET_c = "0x24"
'''


class Ini(unittest.TestCase):
    def test_build_label(self):
        self.assertEqual(offsets.build_label(INI), "1.73-3889")
        self.assertIn('label = "1.74-4000"', offsets.set_build_label(INI, "1.74-4000"))

    def test_answers_replace_only_offsets(self):
        out = offsets.apply_answers(INI, {"OFFSET_b": "Global_10.f_7"})
        self.assertIn('OFFSET_b = "Global_10.f_7"', out)
        self.assertIn('OFFSET_a = "Global_10.f_5[i]"', out)
        self.assertIn('OFFSET_c = "0x24"', out)

    def test_sibling_guess(self):
        v, how = offsets.sibling_guess("Global_10.f_3[i /*9*/].f_7", {
            "Global_10.f_3[i /*9*/].f_6": "Global_10.f_2[i /*8*/].f_5",
            "Global_10.f_3[i /*9*/].f_20": "Global_10.f_2[i /*8*/].f_20"})
        self.assertEqual(v, "Global_10.f_2[i /*8*/].f_6")


class PatchAnswers(unittest.TestCase):
    def test_accept_disable_own(self):
        from xupdate import patches
        ps = [{"pattern": "A", "offset": 0, "bytes_to_patch": "00"} for _ in range(3)]
        items = [Item(id="patch:f.json#0", kind="patch", title="", question="", guess="B | 2 | 11", answer="übernehmen"),
                 Item(id="patch:f.json#1", kind="patch", title="", question="", answer="abschalten"),
                 Item(id="patch:f.json#2", kind="patch", title="", question="", answer="C | 1 | 22 33")]
        patches.apply_answers(ps, items, "f.json")
        self.assertEqual((ps[0]["pattern"], ps[0]["offset"], ps[0]["bytes_to_patch"]), ("B", 2, "11"))
        self.assertIs(ps[1]["enabled"], False)
        self.assertEqual(ps[2]["bytes_to_patch"], "22 33")


class Space(unittest.TestCase):
    # Shipped flags of Enhanced 1.73-1158 and the flags all six creators loaded with in game.
    LOADED = {0x00C40012: 0x00006884, 0x00F60012: 0x00008084, 0x01BC0023: 0x0000C084,
              0x03C60012: 0x00006884, 0x00E60012: 0x00007884}

    def test_layout_is_the_dump_size(self):
        self.assertEqual(sum(space.layout(0x00C40012)), 0x390000)   # fm_lts_creator.ysc.full
        self.assertEqual(sum(space.layout(0x01BC0023)), 0x668000)   # fm_race_creator.ysc.full

    def test_target_is_what_loaded_in_game(self):
        for shipped, loaded in self.LOADED.items():
            self.assertEqual(space.target(shipped), loaded, f"0x{shipped:08X}")
            self.assertIsNone(space.check(shipped, loaded))

    def test_check_rejects_what_failed_in_game(self):
        # One more 0x8000 chunk: the page map overflows (DM crashed).
        self.assertIn("page map", space.check(0x00F60012, 0x00F80012))
        # Two chunks wholly behind the data: the load never finishes.
        self.assertIn("wholly behind", space.check(0x00F60012, 0x00F40032))

    def test_section_keeps_comments(self):
        ini = ('[SCRIPTSPACE]\n; Shipped flags from enhanced-1.73-1158.\nfm_lts_creator = "00C40012, 00006884"\n'
               '\n[SCRIPTVICTIM]\nfm_lts_creator = "1"\n')
        out = space.set_section(ini, {"fm_lts_creator": "00C60012, 00006884"}, "enhanced-1.74-1200")
        self.assertIn("; Shipped flags from enhanced-1.74-1200.", out)
        self.assertIn('fm_lts_creator = "00C60012, 00006884"', out)
        self.assertIn('[SCRIPTVICTIM]\nfm_lts_creator = "1"', out)
        self.assertEqual(space.scripts_in(ini), ["fm_lts_creator"])


if __name__ == "__main__":
    unittest.main()
