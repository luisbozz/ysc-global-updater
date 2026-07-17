import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.migrate_offsets import build_maps, migrate_text, postprocess_strides  # noqa: E402

OLD_C = """
uParam0->f_1 = DATAFILE::DATADICT_CREATE_DICT(uParam0->f_0, "mission");
DATAFILE::DATADICT_SET_INT(uParam0->f_1, "trntype", Global_100.f_5);
DATAFILE::DATADICT_SET_INT(uParam0->f_1, "spd", Global_100.f_8);
"""
# Zielversion: trntype-Offset ist verschoben (f_5 -> f_9), spd unveraendert.
NEW_C = OLD_C.replace("Global_100.f_5", "Global_100.f_9")


class MigrateOffsetsTest(unittest.TestCase):
    def _maps(self, old_src: str, new_src: str):
        with tempfile.TemporaryDirectory() as old, tempfile.TemporaryDirectory() as new:
            (pathlib.Path(old) / "c.c").write_text(old_src, encoding="utf-8")
            (pathlib.Path(new) / "c.c").write_text(new_src, encoding="utf-8")
            _, old_rev = build_maps(pathlib.Path(old), ["c.c"])
            new_fwd, _ = build_maps(pathlib.Path(new), ["c.c"])
        return old_rev, new_fwd

    def test_migrates_shifted_offset_by_path(self) -> None:
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)
        ini = 'OFFSET_trntype = "Global_100.f_5"\n'
        out, stats, changes = migrate_text(ini, old_rev, new_fwd)
        self.assertIn('OFFSET_trntype = "Global_100.f_9"', out)
        self.assertEqual(stats.get("migrated"), 1)
        self.assertEqual(changes[0], ("OFFSET_trntype", "Global_100.f_5", "Global_100.f_9"))

    def test_unchanged_offset_stays(self) -> None:
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)
        ini = 'OFFSET_spd = "Global_100.f_8"\n'
        out, stats, _ = migrate_text(ini, old_rev, new_fwd)
        self.assertIn('OFFSET_spd = "Global_100.f_8"', out)
        self.assertEqual(stats.get("unchanged"), 1)

    def test_unknown_and_nonglobal_are_left_untouched(self) -> None:
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)
        ini = 'OFFSET_x = "Global_999.f_0"\nOFFSET_static = "0x60"\n'
        out, stats, _ = migrate_text(ini, old_rev, new_fwd)
        self.assertIn('OFFSET_x = "Global_999.f_0"', out)
        self.assertIn('OFFSET_static = "0x60"', out)
        self.assertEqual(stats.get("not_found_in_old"), 1)
        self.assertEqual(stats.get("skipped_non_global"), 1)

    def test_non_offset_lines_preserved(self) -> None:
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)
        ini = "; comment\nOFFSET_trntype = \"Global_100.f_5\"\n; tail\n"
        out, _, _ = migrate_text(ini, old_rev, new_fwd)
        self.assertTrue(out.startswith("; comment"))
        self.assertIn("; tail", out)

    def test_semantic_first_even_for_struct_families(self) -> None:
        # NEU: Der semantische Pfad (Container-Name) ist die Grundwahrheit und
        # wird ZUERST versucht — auch fuer code-accessed Familien wie veh_. Ein
        # Fallback darf NICHT laufen, wenn ein eindeutiger Pfad existiert.
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)
        called = {}

        def fake_fallback(name, val):
            called["name"] = name
            return "Global_4980736.f_68415[i].f_19"

        ini = 'OFFSET_veh_pri = "Global_100.f_5"\n'
        out, stats, _ = migrate_text(
            ini, old_rev, new_fwd, fallback=fake_fallback,
            infer_families=("veh_",), struct_families=("veh_",))
        self.assertIsNone(called.get("name"))  # Fallback NICHT aufgerufen
        self.assertIn('OFFSET_veh_pri = "Global_100.f_9"', out)  # semantisch migriert
        self.assertEqual(stats.get("migrated"), 1)

    def test_fallback_used_when_no_semantic_path(self) -> None:
        # Nur wenn KEIN eindeutiger semantischer Pfad existiert, greift der
        # strukturelle/infer-Fallback.
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)
        called = {}

        def fake_fallback(name, val):
            called["name"] = name
            return "Global_4980736.f_68415[i].f_19"

        ini = 'OFFSET_veh_zzz = "Global_4980736.f_0"\n'  # kein Pfad in old_rev, gleicher Basis-Global
        out, stats, _ = migrate_text(
            ini, old_rev, new_fwd, fallback=fake_fallback, struct_families=("veh_",))
        self.assertEqual(called.get("name"), "OFFSET_veh_zzz")
        self.assertIn('OFFSET_veh_zzz = "Global_4980736.f_68415[i].f_19"', out)
        self.assertEqual(stats.get("migrated_fallback"), 1)

    def test_local_value_is_routed_to_fallback(self) -> None:
        # Locals sind nicht serialisiert -> nur ueber Kontext-Matching migrierbar.
        # Reale Formen: fLocal_/uLocal_/iLocal_/Local_.
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)

        def fake_fallback(name, val):
            return "iLocal_1234"

        for src in ("Local_999", "iLocal_999", "fLocal_7208", "uLocal_8126"):
            ini = f'OFFSET_current_creator_x = "{src}"\n'
            out, stats, _ = migrate_text(ini, old_rev, new_fwd, fallback=fake_fallback)
            self.assertIn('OFFSET_current_creator_x = "iLocal_1234"', out)
            self.assertEqual(stats.get("migrated_fallback"), 1)

    def test_static_constant_is_left_untouched_even_with_fallback(self) -> None:
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)

        def fake_fallback(name, val):  # sollte fuer statische Konstanten NICHT greifen
            raise AssertionError("fallback darf fuer statische Konstanten nicht laufen")

        ini = 'OFFSET_tp = "8,30,50"\nOFFSET_hex = "0x60"\n'
        out, stats, _ = migrate_text(ini, old_rev, new_fwd, fallback=fake_fallback)
        self.assertIn('OFFSET_tp = "8,30,50"', out)
        self.assertIn('OFFSET_hex = "0x60"', out)
        self.assertEqual(stats.get("skipped_non_global"), 2)

    def test_anchor_map_overrides_bare_root_skip(self) -> None:
        # anchor_map (tools/verified_anchors.py) muss VOR dem bare-root-skip greifen,
        # z. B. fuer OFFSET_check_creator (bare Global_N, sonst versions-stabil
        # angenommen, hier aber ueber einen echten Quelltext-Anker migriert).
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)
        ini = 'OFFSET_check_creator = "Global_1921391"\n'
        out, stats, changes = migrate_text(
            ini, old_rev, new_fwd, anchor_map={"OFFSET_check_creator": "Global_1925981"})
        self.assertIn('OFFSET_check_creator = "Global_1925981"', out)
        self.assertEqual(stats.get("migrated_anchor"), 1)
        self.assertEqual(stats.get("skipped_root", 0), 0)
        self.assertEqual(changes[0], ("OFFSET_check_creator", "Global_1921391", "Global_1925981"))

    def test_anchor_map_unchanged_value_counts_as_unchanged(self) -> None:
        old_rev, new_fwd = self._maps(OLD_C, NEW_C)
        ini = 'OFFSET_check_creator = "Global_1921391"\n'
        out, stats, changes = migrate_text(
            ini, old_rev, new_fwd, anchor_map={"OFFSET_check_creator": "Global_1921391"})
        self.assertIn('OFFSET_check_creator = "Global_1921391"', out)
        self.assertEqual(stats.get("unchanged"), 1)
        self.assertEqual(changes, [])


class PostprocessStridesTest(unittest.TestCase):
    def test_family_stride_from_array_offset(self) -> None:
        # props_next (blanke Zahl) folgt dem /*N*/ des props-Array-Offsets.
        ini = ('OFFSET_props_loc = "Global_5.f_1[i /*163*/]"\n'
               'OFFSET_props_next = 163\n')
        migrated = ('OFFSET_props_loc = "Global_5.f_1[i /*165*/]"\n'
                    'OFFSET_props_next = 163\n')
        out, notes = postprocess_strides(migrated, ini)
        self.assertIn("OFFSET_props_next = 165", out)
        self.assertEqual(len(notes), 1)

    def test_2d_array_dims_map_to_settings_strides(self) -> None:
        # team_NEXT_settings/next_settings = aeussere/innere player-Array-Dimension.
        ini = ('OFFSET_player_loc = "Global_5.f_100[i /*4141*/][j /*69*/]"\n'
               'OFFSET_team_NEXT_settings = 4141\n'
               'OFFSET_next_settings = 69\n')
        migrated = ('OFFSET_player_loc = "Global_5.f_200[i /*4201*/][j /*70*/]"\n'
                    'OFFSET_team_NEXT_settings = 4141\n'
                    'OFFSET_next_settings = 69\n')
        out, _ = postprocess_strides(migrated, ini)
        self.assertIn("OFFSET_team_NEXT_settings = 4201", out)
        self.assertIn("OFFSET_next_settings = 70", out)

    def test_unmatched_integer_is_left_untouched(self) -> None:
        # Ein Stride-Wert ohne passenden Array-Offset bleibt unveraendert.
        ini = 'OFFSET_foo_next = 999\n'
        out, notes = postprocess_strides(ini, ini)
        self.assertIn("OFFSET_foo_next = 999", out)
        self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
