import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.verified_anchors import (  # noqa: E402
    build_anchor_map,
    rebase_value,
    resolve,
    resolve_actor_weapon_slot_family,
    resolve_moved_array_bases,
)

CHECK_CREATOR_OLD = """
bool func_12890(int iParam0, bool bParam1, int iParam2, bool bParam3)
{
	bool bVar0;
	int iVar1;
	int iVar2;
	struct<5> Var3;
	int iVar98;
	bool bVar99;
	int iVar100;
	
	Global_1921391 = 1;
	bVar0 = false;
	Var3.f_4 = 3;
	Var3.f_8 = 3;
	Var3.f_64 = 3;
	Var3.f_75 = 3;
	Var3.f_91 = 3;
	func_12892(&Var3, iParam0);
}
"""
CHECK_CREATOR_NEW = CHECK_CREATOR_OLD.replace("Global_1921391", "Global_1925981")

HIDE_MENU_OLD = """
int func_80(bool bParam0, bool bParam1)
{
	if ((((((((!CAM::IS_SCREEN_FADED_IN() || (func_82(8, -1) && func_81() != 65)) || (HUD::GET_PAUSE_MENU_STATE() != 0 && !bParam1)) || (STREAMING::IS_PLAYER_SWITCH_IN_PROGRESS() && !bParam0)) || NETWORK::IS_COMMERCE_STORE_OPEN()) || Global_80005) || Global_24131.f_9147) || HUD::IS_WARNING_MESSAGE_ACTIVE()) || Global_101945.f_1490)
	{
		return 0;
	}
	return 1;
}
"""
HIDE_MENU_NEW = HIDE_MENU_OLD.replace("Global_80005", "Global_80598").replace(
    "Global_24131.f_9147", "Global_24569.f_9243").replace("Global_101945.f_1490", "Global_102538.f_1514")

VSBSOUT_OLD = """
DATAFILE::DATADICT_SET_INT(*uParam1, "vsclout", Global_4718592.f_160654[0]);
DATAFILE::DATADICT_SET_INT(*uParam1, "vsthout", Global_4718592.f_160654[1]);
DATAFILE::DATADICT_SET_INT(*uParam1, "vsbsout", Global_4718592.f_160654[5]);
"""
VSBSOUT_NEW = VSBSOUT_OLD.replace("f_160654", "f_163240")

# Actor weapon-slot sub-array (f_161.f_9 in 1.71, stride 27) -> a new "agsf"
# field is inserted at old-position 15 in 1.73, shifting agvr (and the root
# f_161->f_166, stride 27->28) - mirrors the real fm_capture_creator.c shape.
ACTOR_WEAPON_SLOT_OLD = """
while (bVar1 <= 11)
{
	func_624(&bVar3, "actv", bVar1, -1);
	func_625(&bVar3, &(Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_2), &uParam0, &(Var7.f_34.f_9.f_25[bVar1]), bVar0);
	func_624(&bVar3, "achf", bVar1, -1);
	func_627(&bVar3, &(Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_5), &uParam0, &(Var7.f_34.f_9.f_38[bVar1]), bVar0, 0f);
	func_624(&bVar3, "awt", bVar1, -1);
	func_628(&bVar3, Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_6, &uParam0, &(Var7.f_34.f_9.f_51[bVar1]), bVar0, 0);
	func_624(&bVar3, "awr", bVar1, -1);
	func_628(&bVar3, Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_7, &uParam0, &(Var7.f_34.f_9.f_64[bVar1]), bVar0, 0);
	func_624(&bVar3, "awl", bVar1, -1);
	func_625(&bVar3, &(Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_8), &uParam0, &(Var7.f_34.f_9.f_77[bVar1]), bVar0);
	func_624(&bVar3, "awlr", bVar1, -1);
	func_628(&bVar3, Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_11, &uParam0, &(Var7.f_34.f_9.f_90[bVar1]), bVar0, 0);
	func_624(&bVar3, "agrd", bVar1, -1);
	func_627(&bVar3, &(Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_13), &uParam0, &(Var7.f_34.f_9.f_116[bVar1]), bVar0, 0f);
	func_624(&bVar3, "ags", bVar1, -1);
	func_628(&bVar3, Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_14, &uParam0, &(Var7.f_34.f_9.f_129[bVar1]), bVar0, -1);
	func_624(&bVar3, "agvr", bVar1, -1);
	func_627(&bVar3, &(Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_15), &uParam0, &(Var7.f_34.f_9.f_142[bVar1]), bVar0, 0f);
	bVar1++;
}
"""
ACTOR_WEAPON_SLOT_NEW = (
    ACTOR_WEAPON_SLOT_OLD
    .replace("func_624(&bVar3,", "func_636(&bVar4,")
    .replace('bVar1, -1);', 'bVar1, -1, -1);')
    .replace("f_89187", "f_93162")
    .replace("bVar0 /*1213*/", "bVar0 /*1277*/")
    .replace(".f_161.", ".f_166.")
    .replace("bVar1 /*27*/", "bVar1 /*28*/")
    # the new "agsf" field insertion at old-position 15 pushes agvr to .f_16
    .replace(".f_15), &uParam0,", ".f_16), &uParam0,")
)


class VerifiedAnchorsTest(unittest.TestCase):
    def _dirs(self, old_src: str, new_src: str):
        old_tmp = tempfile.TemporaryDirectory()
        new_tmp = tempfile.TemporaryDirectory()
        (pathlib.Path(old_tmp.name) / "fm_race_creator.c").write_text(old_src, encoding="utf-8")
        (pathlib.Path(new_tmp.name) / "fm_race_creator.c").write_text(new_src, encoding="utf-8")
        self.addCleanup(old_tmp.cleanup)
        self.addCleanup(new_tmp.cleanup)
        return pathlib.Path(old_tmp.name), pathlib.Path(new_tmp.name)

    def test_check_creator_anchor_resolves(self) -> None:
        old_dir, new_dir = self._dirs(CHECK_CREATOR_OLD, CHECK_CREATOR_NEW)
        self.assertEqual(resolve("OFFSET_check_creator", old_dir, new_dir), "Global_1925981")

    def test_hide_creator_menu_anchor_resolves(self) -> None:
        old_dir, new_dir = self._dirs(HIDE_MENU_OLD, HIDE_MENU_NEW)
        self.assertEqual(resolve("OFFSET_hide_creator_menu", old_dir, new_dir), "Global_24569.f_9243")

    def test_unknown_offset_returns_none(self) -> None:
        old_dir, new_dir = self._dirs(CHECK_CREATOR_OLD, CHECK_CREATOR_NEW)
        self.assertIsNone(resolve("OFFSET_not_an_anchor", old_dir, new_dir))

    def test_anchor_missing_in_new_returns_none(self) -> None:
        old_dir, new_dir = self._dirs(CHECK_CREATOR_OLD, "// anchor removed in this version\n")
        self.assertIsNone(resolve("OFFSET_check_creator", old_dir, new_dir))

    def test_anchor_ambiguous_in_old_returns_none(self) -> None:
        # Anchor duplicated with a DIFFERENT captured value in old -> not trustworthy.
        dup_old = CHECK_CREATOR_OLD + CHECK_CREATOR_OLD.replace("Global_1921391", "Global_9999999")
        old_dir, new_dir = self._dirs(dup_old, CHECK_CREATOR_NEW)
        self.assertIsNone(resolve("OFFSET_check_creator", old_dir, new_dir))

    def test_build_anchor_map_covers_both(self) -> None:
        old_dir, new_dir = self._dirs(CHECK_CREATOR_OLD + HIDE_MENU_OLD, CHECK_CREATOR_NEW + HIDE_MENU_NEW)
        amap = build_anchor_map(old_dir, new_dir)
        self.assertEqual(amap.get("OFFSET_check_creator"), "Global_1925981")
        self.assertEqual(amap.get("OFFSET_hide_creator_menu"), "Global_24569.f_9243")

    def test_vsbsout_anchor_resolves(self) -> None:
        old_dir, new_dir = self._dirs(VSBSOUT_OLD, VSBSOUT_NEW)
        self.assertEqual(resolve("OFFSET_vsbsout", old_dir, new_dir), "Global_4718592.f_163240")

    def test_actor_weapon_slot_family_resolves(self) -> None:
        old_dir, new_dir = self._dirs(ACTOR_WEAPON_SLOT_OLD, ACTOR_WEAPON_SLOT_NEW)
        fam = resolve_actor_weapon_slot_family(old_dir, new_dir)
        self.assertEqual(fam["OFFSET_actor_actv_bs"], "Global_4980736.f_166.f_9")
        self.assertEqual(fam["OFFSET_actor_actv"], "Global_4980736.f_166.f_9.f_2")
        self.assertEqual(fam["OFFSET_actor_achf"], "Global_4980736.f_166.f_9.f_5")
        self.assertEqual(fam["OFFSET_actor_awt"], "Global_4980736.f_166.f_9.f_6")
        self.assertEqual(fam["OFFSET_actor_awr"], "Global_4980736.f_166.f_9.f_7")
        self.assertEqual(fam["OFFSET_actor_awl"], "Global_4980736.f_166.f_9.f_8")
        self.assertEqual(fam["OFFSET_actor_awlr"], "Global_4980736.f_166.f_9.f_11")
        self.assertEqual(fam["OFFSET_actor_agrd"], "Global_4980736.f_166.f_9.f_13")
        self.assertEqual(fam["OFFSET_actor_ags"], "Global_4980736.f_166.f_9.f_14")
        # the inserted "agsf" field shifts agvr from .f_15 (old) to .f_16 (new)
        self.assertEqual(fam["OFFSET_actor_agvr"], "Global_4980736.f_166.f_9.f_16")
        self.assertEqual(fam["OFFSET_actor_actv_NEXT"], "28")

    def test_actor_weapon_slot_family_partial_returns_empty(self) -> None:
        # Drop one key's writer line -> the family is no longer fully resolvable
        # in old -> all-or-nothing guard must return {} instead of a partial guess.
        partial_old = ACTOR_WEAPON_SLOT_OLD.replace(
            'func_627(&bVar3, &(Global_4980736.f_89187[bVar0 /*1213*/].f_161.f_9[bVar1 /*27*/].f_15), '
            '&uParam0, &(Var7.f_34.f_9.f_142[bVar1]), bVar0, 0f);',
            "// agvr writer removed\n")
        old_dir, new_dir = self._dirs(partial_old, ACTOR_WEAPON_SLOT_NEW)
        self.assertEqual(resolve_actor_weapon_slot_family(old_dir, new_dir), {})


REAL_OLD = ROOT / "scripts" / "1.71-3586"
REAL_NEW = ROOT / "scripts" / "1.73-3889"


@unittest.skipUnless(REAL_OLD.is_dir() and REAL_NEW.is_dir(), "scripts/1.71-3586 or scripts/1.73-3889 dump missing")
class VerifiedAnchorsRealDataTest(unittest.TestCase):
    """Locks in the manually-verified 1.71 -> 1.73 migration for these two
    offsets (cross-checked by hand against fm_race_creator.c / fm_capture_creator.c)."""

    def test_check_creator_1_71_to_1_73(self) -> None:
        self.assertEqual(resolve("OFFSET_check_creator", REAL_OLD, REAL_NEW), "Global_1925981")

    def test_hide_creator_menu_1_71_to_1_73(self) -> None:
        self.assertEqual(resolve("OFFSET_hide_creator_menu", REAL_OLD, REAL_NEW), "Global_24569.f_9243")

    def test_vsbsout_1_71_to_1_73(self) -> None:
        self.assertEqual(resolve("OFFSET_vsbsout", REAL_OLD, REAL_NEW), "Global_4718592.f_163240")

    def test_actor_weapon_slot_family_1_71_to_1_73(self) -> None:
        fam = resolve_actor_weapon_slot_family(REAL_OLD, REAL_NEW)
        self.assertEqual(fam, {
            "OFFSET_actor_actv": "Global_4980736.f_166.f_9.f_2",
            "OFFSET_actor_achf": "Global_4980736.f_166.f_9.f_5",
            "OFFSET_actor_awt": "Global_4980736.f_166.f_9.f_6",
            "OFFSET_actor_awr": "Global_4980736.f_166.f_9.f_7",
            "OFFSET_actor_awl": "Global_4980736.f_166.f_9.f_8",
            "OFFSET_actor_awlr": "Global_4980736.f_166.f_9.f_11",
            "OFFSET_actor_agrd": "Global_4980736.f_166.f_9.f_13",
            "OFFSET_actor_ags": "Global_4980736.f_166.f_9.f_14",
            "OFFSET_actor_agvr": "Global_4980736.f_166.f_9.f_16",
            "OFFSET_actor_actv_bs": "Global_4980736.f_166.f_9",
            "OFFSET_actor_actv_NEXT": "28",
        })

    def test_moved_array_bases_1_71_to_1_73(self) -> None:
        rules = resolve_moved_array_bases(REAL_OLD, REAL_NEW)
        cases = {
            # published_* — base moved, field layout identical
            "Global_993502.f_4": "Global_995355.f_4",
            "Global_993502.f_4.f_90": "Global_995355.f_4.f_90",
            "Global_993502.f_4.f_64": "Global_995355.f_4.f_64",
            # saved_* — same, different base
            "Global_1011388.f_33": "Global_1015489.f_33",
            "Global_1011388.f_33.f_74": "Global_1015489.f_33.f_74",
            # gbtpi/gbtpp — outer field, stride and inner field all moved;
            # the inner [13] index and the leaf field are carried over
            "Global_4718592.f_3605[bVar0 /*26949*/].f_6494[bVar1 /*13*/].f_1":
                "Global_4718592.f_3838[i /*26988*/].f_6497[j /*13*/].f_1",
            "Global_4718592.f_3605[bVar0 /*26949*/].f_6494[bVar1 /*13*/].f_2":
                "Global_4718592.f_3838[i /*26988*/].f_6497[j /*13*/].f_2",
            # gbtpm is the same base with no leaf field; its value must come out
            # exactly as the helper resolver already produced it
            "Global_4718592.f_3605[bVar0 /*26949*/].f_6494[bVar1 /*13*/]":
                "Global_4718592.f_3838[i /*26988*/].f_6497[j /*13*/]",
        }
        for old_value, expected in cases.items():
            with self.subTest(value=old_value):
                self.assertEqual(rebase_value(old_value, rules), expected)

    def test_moved_array_bases_leave_other_values_alone(self) -> None:
        rules = resolve_moved_array_bases(REAL_OLD, REAL_NEW)
        for value in (
            "Global_4980736.f_89187[j /*1213*/].f_161",
            "Global_4718592.f_163240",
            "Global_1925981",
            "uLocal_9223.f_808",
            # a different array under the same root as gbtp*
            "Global_4718592.f_3605[i /*26949*/]",
        ):
            with self.subTest(value=value):
                self.assertIsNone(rebase_value(value, rules))


REAL_ENHANCED = ROOT / "scripts" / "enhanced-1.73-1158"


@unittest.skipUnless(REAL_NEW.is_dir() and REAL_ENHANCED.is_dir(),
                     "scripts/1.73-3889 or scripts/enhanced-1.73-1158 dump missing")
class CrossVariantAnchorTest(unittest.TestCase):
    """Legacy 1.73 -> Enhanced 1.73, verified by hand.

    Each expected value was confirmed in the Enhanced corpus by its context,
    not by a numeric delta: f_128458 carries the same run of type constants
    (6, 7, 18..22, 24, 25, 30, 31) that f_121958 carries in Legacy, and
    f_128499 sits in the same DATAARRAY_ADD_INT call with the same /*5*/
    dimension. Those constants are game content and do not move with a build."""

    def test_mission_type_field_moved(self):
        for name in ("OFFSET_cps_type", "OFFSET_racetype"):
            self.assertEqual(resolve(name, REAL_NEW, REAL_ENHANCED),
                             "Global_4718592.f_128458")

    def test_adlc_array_moved(self):
        self.assertEqual(resolve("OFFSET_adlc", REAL_NEW, REAL_ENHANCED),
                         "Global_4718592.f_128499")

    def test_hide_creator_menu_survives_the_paren_dialect(self):
        # The newer decompiler drops the redundant grouping parentheses. An
        # anchor that required them would return None here, the offset would
        # keep its Legacy value, and the report would show no change at all.
        self.assertEqual(resolve("OFFSET_hide_creator_menu", REAL_NEW, REAL_ENHANCED),
                         "Global_24586.f_9243")

    def test_check_creator_resolves_on_both_dialects(self):
        # This anchor used to spell out the local declarations around the write
        # -- "bool bVar0; int iVar1; struct<5> Var3;" -- and the Enhanced corpus
        # is decompiled with names instead: "BOOL flag; int num; Vector3 vector".
        # So it matched nothing there, the offset kept Legacy's value, and the
        # report showed no change at all. That is worse than a failure, because
        # check_creator gates whether the editor loads anything: with a stale
        # value every field in the app stays empty and nothing says why.
        #
        # It now anchors on the five struct-field constants, which belong to the
        # game's data rather than the decompiler's vocabulary.
        self.assertEqual(resolve("OFFSET_check_creator", REAL_NEW, REAL_ENHANCED),
                         "Global_1926457")

    def test_check_creator_gives_each_variant_its_own_answer(self):
        # The whole point: the two builds hold this flag in different globals,
        # and an anchor that cannot tell them apart is how the stale value got
        # shipped in the first place.
        enhanced = resolve("OFFSET_check_creator", REAL_NEW, REAL_ENHANCED)
        legacy = resolve("OFFSET_check_creator", REAL_OLD, REAL_NEW)
        self.assertNotEqual(enhanced, legacy)

    def test_array_bases_did_not_move_between_the_variants(self):
        # published_*, saved_* and gbtpi/gbtpp resolve to the same base in both
        # games. The rule list is therefore empty, and every leaf correctly
        # stays as it is -- this guards against a resolver that "finds" a move
        # where there is none.
        self.assertEqual(resolve_moved_array_bases(REAL_NEW, REAL_ENHANCED), [])

    def test_array_bases_still_resolve_on_each_side(self):
        from tools.verified_anchors import _gbtp_base, _published_base, _saved_base
        for corpus in (REAL_NEW, REAL_ENHANCED):
            self.assertEqual(_saved_base(corpus), ("1015489", "33"))
            self.assertEqual(_published_base(corpus), ("995355", "4"))
            self.assertEqual(_gbtp_base(corpus), ("3838", "26988", "6497"))


if __name__ == "__main__":
    unittest.main()


CONTEXT_OLD = ROOT / "scripts" / "1.71-3586" / "context" / "maintransition.c"
CONTEXT_NEW = ROOT / "scripts" / "1.73-3889" / "context" / "maintransition.c"


class ContextOffsetsUnitTest(unittest.TestCase):
    """The context resolver, on text rather than the real corpus."""

    def _write(self, old_text: str, new_text: str):
        from tools.verified_anchors import resolve_context_offsets
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        root = pathlib.Path(td.name)
        for name, text in (("old", old_text), ("new", new_text)):
            (root / name / "context").mkdir(parents=True)
            (root / name / "context" / "maintransition.c").write_text(
                text, encoding="utf-8")
        return resolve_context_offsets(root / "old", root / "new")

    def test_a_missing_context_script_resolves_nothing(self):
        from tools.verified_anchors import resolve_context_offsets
        with tempfile.TemporaryDirectory() as td:
            root = pathlib.Path(td)
            (root / "old").mkdir()
            (root / "new").mkdir()
            self.assertEqual(resolve_context_offsets(root / "old", root / "new"), {})

    def test_both_sides_are_reported_not_just_the_new_one(self):
        old = "if (Global_100 == 0 || Global_99 == 0)\n"
        new = "if (Global_200 == 0 || Global_199 == 0)\n"
        got = self._write(old, new)
        self.assertEqual(got["OFFSET_launch_creator_local_3"],
                         ("Global_100", "Global_200"))

    def test_an_ambiguous_anchor_is_dropped(self):
        # Two matches means the anchor no longer identifies one global, and a
        # guess here would write a wrong address into production.
        old = "if (Global_100 == 0 || Global_99 == 0)\n"
        new = ("if (Global_200 == 0 || Global_199 == 0)\n"
               "if (Global_300 == 0 || Global_299 == 0)\n")
        self.assertNotIn("OFFSET_launch_creator_local_3", self._write(old, new))

    def test_a_vanished_anchor_is_dropped(self):
        old = "if (Global_100 == 0 || Global_99 == 0)\n"
        self.assertNotIn("OFFSET_launch_creator_local_3", self._write(old, "nothing\n"))

    def test_the_state_field_anchor_needs_all_three_constants(self):
        old = "if ((Global_10 != 4 && Global_10 != 5) && Global_10 != 7)\n"
        new = "if ((Global_20 != 4 && Global_20 != 5) && Global_20 != 7)\n"
        self.assertEqual(self._write(old, new)["OFFSET_launch_creator_local_5"],
                         ("Global_10", "Global_20"))
        partial = "if ((Global_20 != 4 && Global_20 != 5) && Global_20 != 9)\n"
        self.assertNotIn("OFFSET_launch_creator_local_5", self._write(old, partial))

    def test_the_getter_run_needs_the_same_count_on_both_sides(self):
        # A restructured script with a different number of runs must not be
        # aligned by position; the last run would then be a different one.
        def runs(*groups):
            out = []
            for base in groups:
                for g in (base + 1, base, base - 1):
                    out.append(f"var f()\n{{\n\treturn Global_{g};\n}}\n")
            return "".join(out)
        got = self._write(runs(100, 500), runs(200, 600))
        self.assertEqual(got["OFFSET_transitionState"], ("Global_500", "Global_600"))
        self.assertNotIn("OFFSET_transitionState",
                         self._write(runs(100, 500), runs(200, 600, 900)))


@unittest.skipUnless(CONTEXT_OLD.is_file() and CONTEXT_NEW.is_file(),
                     "context/maintransition.c missing for 1.71-3586 or 1.73-3889")
class ContextOffsetsRealDataTest(unittest.TestCase):
    """The creator launch sequence, 1.71 -> 1.73.

    Each value was derived by hand first and cross-checked: 33282 -> 33816 by
    its 3-to-0 / 0-to-3 usage swap, 1574943 -> 1574944 by an identical call
    expression next to IS_PLAYER_SWITCH_IN_PROGRESS, 1575013 -> 1575021 by the
    4/5/7 comparison, 2696496 -> 2697030 by a getter run that shifts wholesale
    by +534. The 1574943 case is the reason the old value is checked too: that
    number still exists in 1.73, but as a different variable with 34 call sites."""

    EXPECTED = {
        "OFFSET_launch_creator_local_3": ("Global_33282", "Global_33816"),
        "OFFSET_launch_creator_local_4": ("Global_1574943", "Global_1574944"),
        "OFFSET_launch_creator_local_5": ("Global_1575013", "Global_1575021"),
        "OFFSET_transitionState": ("Global_2696496", "Global_2697030"),
    }

    def test_resolves_the_launch_sequence(self):
        from tools.verified_anchors import resolve_context_offsets
        got = resolve_context_offsets(CONTEXT_OLD.parents[1], CONTEXT_NEW.parents[1])
        self.assertEqual(got, self.EXPECTED)

    def test_the_old_values_are_the_ones_offsets_ini_still_holds(self):
        import re
        ini = (ROOT / "offsets.ini").read_text(encoding="utf-8", errors="replace")
        for name, (before, _) in self.EXPECTED.items():
            m = re.search(rf'^{name}\s*=\s*"([^"]*)"', ini, re.M)
            self.assertIsNotNone(m, f"{name} missing from offsets.ini")
            self.assertEqual(m.group(1), before)
