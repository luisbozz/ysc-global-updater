import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.locals import (  # noqa: E402
    creator_file_for,
    migrate_field,
    migrate_local,
    migrate_local_field,
)

# Synthetische Creator-Scripts (Aufbau wie beim Decompiler: Funktionen, dann der
# Block der Script-globalen Locals, dann __EntryFunction__). Deckt alle vier
# Match-Strategien ab: Struct-Init, Zeilen-Anker, Switch-Fingerprint, Decl-Ordinal.
OLD_SRC = """\
void func_1()
{
    struct<47> Local_145 = { 0, 0, 0, 0, 0, -1, 2, 4 } ;
    func_10(&Local_700, "SC_RESET_W", "", 0);
    if (Local_700.f_562 == 94)
    {
        Local_700.f_562 = 94;
    }
    if (iLocal_300 == 3)
    {
        return;
    }
    switch (iLocal_300)
    {
        case 0:
            MISC::SET_OVERRIDE_WEATHER("CLEAR");
            PED::CLEAR_PED_TASKS_IMMEDIATELY(x);
            break;
        case 1:
            NETWORK::NETWORK_SPECIAL_THING(y);
            break;
    }
}
    int iLocal_900 = 0;
    var uLocal_901 = 0;
    var uLocal_902 = 0;
#endregion

void __EntryFunction__()
{
    func_1();
}
"""

# NEW: worker/test +2..+20, refresh-switch +20, decl-block +50 verschoben.
NEW_SRC = """\
void func_1()
{
    struct<47> Local_147 = { 0, 0, 0, 0, 0, -1, 2, 4 } ;
    func_11(&Local_720, "SC_RESET_W", "", 0);
    if (Local_720.f_565 == 94)
    {
        Local_720.f_565 = 94;
    }
    if (iLocal_320 == 3)
    {
        return;
    }
    switch (iLocal_320)
    {
        case 0:
            MISC::SET_OVERRIDE_WEATHER("CLEAR");
            PED::CLEAR_PED_TASKS_IMMEDIATELY(x);
            break;
        case 1:
            NETWORK::NETWORK_SPECIAL_THING(y);
            break;
    }
}
    int iLocal_950 = 0;
    var uLocal_951 = 0;
    var uLocal_952 = 0;
#endregion

void __EntryFunction__()
{
    func_1();
}
"""


class LocalsMatcherTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.mkdtemp()
        self.old = pathlib.Path(tmp) / "old"
        self.new = pathlib.Path(tmp) / "new"
        self.old.mkdir()
        self.new.mkdir()
        (self.old / "fm_survival_creator.c").write_text(OLD_SRC, encoding="utf-8")
        (self.new / "fm_survival_creator.c").write_text(NEW_SRC, encoding="utf-8")

    def _migrate(self, value, offset):
        return migrate_local(value, offset, str(self.old), str(self.new), _cache={})

    def test_mode_to_file_mapping(self):
        self.assertEqual(creator_file_for("current_creator_worker_survival"),
                         "fm_survival_creator.c")
        # mission teilt sich das Script mit lts.
        self.assertEqual(creator_file_for("current_creator_worker_mission"),
                         creator_file_for("current_creator_worker_lts"))

    def test_struct_init_strategy(self):
        # struct<47> mit identischer Init-Liste -> neuer Index (test-Local).
        self.assertEqual(self._migrate("iLocal_145",
                         "current_creator_test_survival"), "iLocal_147")

    def test_line_anchor_strategy(self):
        # String-Konstante ("SC_RESET_W") auf derselben Zeile (worker-Local).
        self.assertEqual(self._migrate("fLocal_700",
                         "current_creator_worker_survival"), "fLocal_720")

    def test_switch_fingerprint_strategy(self):
        # Control-Flow-Local ohne eigene Anker -> Switch-Rumpf-Fingerprint.
        self.assertEqual(self._migrate("iLocal_300",
                         "current_creator_refresh_survival"), "iLocal_320")

    def test_decl_ordinal_strategy(self):
        # Toter Local (nur deklariert) -> Position vom Ende des Decl-Blocks.
        self.assertEqual(self._migrate("uLocal_902",
                         "current_creator_cam_heading_survival"), "uLocal_952")

    def test_prefix_is_preserved(self):
        # Der Typ-Praefix (f/u/i) der offsets.ini bleibt erhalten.
        self.assertTrue(self._migrate("uLocal_902",
                        "current_creator_cam_heading_survival").startswith("uLocal_"))

    def test_struct_field_by_constant(self):
        # worker.f_562 -> f_565: Feldnummer im aufgeloesten Container-Local
        # (700->720) per Zuweisungs-/Vergleichs-Konstante (== 94 / = 94).
        self.assertEqual(
            migrate_field("f_562", [("700", "720")], str(self.old), str(self.new),
                          "fm_survival_creator.c", _cache={}), "f_565")

    def test_struct_field_unknown_returns_none(self):
        self.assertIsNone(
            migrate_field("f_9999", [("700", "720")], str(self.old), str(self.new),
                          "fm_survival_creator.c", _cache={}))

    def test_struct_field_container_autodetect(self):
        # Kandidat 999 greift f_562 NICHT zu -> Autodetektion waehlt 700/720.
        self.assertEqual(
            migrate_field("f_562", [("999", "999"), ("700", "720")],
                          str(self.old), str(self.new),
                          "fm_survival_creator.c", _cache={}), "f_565")

    def test_local_field_combined(self):
        # Kombiniertes <local>.f_<feld> (neu eingetragene Adresse): Local UND Feld
        # zusammen aufgeloest -> Local_700.f_562 -> Local_720.f_565.
        self.assertEqual(
            migrate_local_field("Local_700.f_562", str(self.old), str(self.new),
                                _cache={}), "Local_720.f_565")

    def test_unknown_local_returns_none(self):
        self.assertIsNone(self._migrate("iLocal_99999",
                          "current_creator_test_survival"))

    def test_non_local_returns_none(self):
        self.assertIsNone(self._migrate("Global_100.f_1",
                          "current_creator_test_survival"))


if __name__ == "__main__":
    unittest.main()
