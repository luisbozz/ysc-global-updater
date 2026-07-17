import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.verified_anchors import build_anchor_map, resolve  # noqa: E402

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


if __name__ == "__main__":
    unittest.main()
