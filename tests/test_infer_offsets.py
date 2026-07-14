import sys
import unittest
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.infer_offsets import (  # noqa: E402
    candidate_files_for_value_cached,
    current_value_present_in_new,
    find_present_entries_in_new,
    infer_candidates,
    is_safe_candidate,
    parse_offsets_ini,
    parse_quoted_offsets,
    path_shape_regex,
    preferred_creator_files,
    present_value_for_ini,
)


class InferOffsetsRegressionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.old_dir = ROOT / "old"
        cls.new_dir = ROOT / "new"
        _, entries = parse_offsets_ini(ROOT / "offsets.ini")
        cls.lookup = {entry.name: entry.value for entry in entries}

    def assert_inferred_value(self, offset_name: str, old_value: str, expected_value: str | None = None) -> None:
        _, candidates = infer_candidates(self.old_dir, self.new_dir, old_value, offset_name)
        self.assertTrue(candidates, msg=f"no candidates for {offset_name}")
        top = candidates[0]
        displayed = present_value_for_ini(old_value, top.value, offset_name)
        self.assertEqual(displayed, expected_value or self.lookup[offset_name], msg=offset_name)

    def assert_value_present_in_new(self, offset_name: str) -> None:
        value = self.lookup[offset_name]
        regex = path_shape_regex(value)
        files = list(preferred_creator_files(self.new_dir, offset_name, value)) or list(
            candidate_files_for_value_cached(self.new_dir, value)
        )
        self.assertTrue(files, msg=f"no candidate files for {offset_name}")
        found = False
        for path in files:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if regex.search(text):
                found = True
                break
        self.assertTrue(found, msg=f"{offset_name} current value not found in new files")

    def test_structured_creator_offsets(self) -> None:
        expected = {
            "OFFSET_props_vrot": "Global_5242880.f_1[i /*163*/].f_3",
            "OFFSET_dprops_vrot": "Global_4980736.f_48744[i /*255*/].f_3",
        }
        for offset_name, old_value in expected.items():
            with self.subTest(offset_name=offset_name):
                self.assert_inferred_value(offset_name, old_value)
        self.assert_value_present_in_new("OFFSET_doors_loc")

    def test_transition_state(self) -> None:
        self.assert_inferred_value("OFFSET_transitionState", "Global_2696496")

    def test_current_creator_roots_and_helpers(self) -> None:
        expected = {
            "OFFSET_current_creator_worker_survival": "fLocal_7208",
            "OFFSET_current_creator_pre_capture": "uLocal_9307",
            "OFFSET_current_creator_cam_heading_mission": "uLocal_7133",
            "OFFSET_current_creator_refresh_mission": "iLocal_41504",
        }
        for offset_name, value in expected.items():
            with self.subTest(offset_name=offset_name):
                self.assert_inferred_value(offset_name, self.lookup[offset_name], value)

    def test_current_creator_suspect_offsets(self) -> None:
        expected = {
            "OFFSET_current_creator_cam_heading_survival": "uLocal_7003",
            "OFFSET_current_creator_test_dm": "iLocal_45661",
            "OFFSET_current_creator_test_race": "iLocal_53837",
            "OFFSET_current_creator_refresh_race": "iLocal_8761",
            "OFFSET_current_creator_refresh_dm": "iLocal_4683",
        }
        for offset_name, value in expected.items():
            with self.subTest(offset_name=offset_name):
                self.assert_inferred_value(offset_name, self.lookup[offset_name], value)

    def test_verified_special_offsets(self) -> None:
        expected = {
            "OFFSET_launch_creator_local_3": "Global_33776",
            "OFFSET_check_creator": "Global_1925601",
            "OFFSET_hide_creator_menu": "Global_24529.f_9243",
        }
        for offset_name, value in expected.items():
            with self.subTest(offset_name=offset_name):
                self.assert_inferred_value(offset_name, self.lookup[offset_name], value)

    def test_stable_launch_creator_offsets_present(self) -> None:
        for offset_name in (
            "OFFSET_launch_creator_local_1",
            "OFFSET_launch_creator_local_2",
            "OFFSET_launch_creator_local_4",
            "OFFSET_launch_creator_local_5",
        ):
            with self.subTest(offset_name=offset_name):
                self.assert_value_present_in_new(offset_name)

    def test_current_creator_relative_field_override(self) -> None:
        offset_name = "OFFSET_current_creator_worker_offset_menu"
        current_value = self.lookup[offset_name]
        _, candidates = infer_candidates(self.old_dir, self.new_dir, current_value, offset_name)
        self.assertTrue(candidates)
        top = candidates[0]
        displayed = present_value_for_ini(current_value, top.value, offset_name)
        self.assertEqual(displayed, "f_533")
        self.assertTrue(is_safe_candidate(current_value, top))

    def test_current_creator_relative_field_stable(self) -> None:
        for offset_name in (
            "OFFSET_current_creator_worker_offset_refresh",
            "OFFSET_current_creator_worker_heading",
            "OFFSET_current_creator_worker_pos",
            "OFFSET_current_creator_cam_heading_offset",
            "OFFSET_current_creator_pre_current_menu",
            "OFFSET_current_creator_pre_idk",
        ):
            with self.subTest(offset_name=offset_name):
                self.assertTopRelativeFieldIsStable(offset_name)

    def assertTopRelativeFieldIsStable(self, offset_name: str) -> None:
        current_value = self.lookup[offset_name]
        _, candidates = infer_candidates(self.old_dir, self.new_dir, current_value, offset_name)
        self.assertTrue(candidates, msg=f"no candidates for {offset_name}")
        top = candidates[0]
        displayed = present_value_for_ini(current_value, top.value, offset_name)
        self.assertEqual(displayed, current_value, msg=offset_name)
        self.assertTrue(is_safe_candidate(current_value, top), msg=offset_name)

    def test_team_block_offsets(self) -> None:
        expected = {
            "OFFSET_irbs": "Global_4718592.f_3605[i /*26949*/].f_8429",
            "OFFSET_minv": "Global_4718592.f_3605[iVar0 /*26949*/].f_648",
            "OFFSET_tms": "Global_4718592.f_3605[i /*26949*/].f_6200",
            "OFFSET_spar": "Global_4718592.f_3605[iVar0 /*26949*/].f_5937",
            "OFFSET_gbnum": "Global_4718592.f_3605[bVar0 /*26949*/].f_6717",
        }
        for offset_name, old_value in expected.items():
            with self.subTest(offset_name=offset_name):
                self.assert_inferred_value(offset_name, old_value)

    def test_vehicle_object_weapon_offsets(self) -> None:
        for offset_name in (
            "OFFSET_veh_model",
            "OFFSET_obj_model",
            "OFFSET_weap_model",
            "OFFSET_scene",
        ):
            with self.subTest(offset_name=offset_name):
                self.assert_value_present_in_new(offset_name)

    def test_current_value_presence_blindspots(self) -> None:
        # These values are valid in new/, but the fast batch scan can miss them
        # because the source often accesses a deeper child path on the same root.
        for offset_name in (
            "OFFSET_props_prpsdp",
            "OFFSET_dhprop_locx",
            "OFFSET_irbs",
        ):
            with self.subTest(offset_name=offset_name):
                self.assertTrue(
                    current_value_present_in_new(self.new_dir, self.lookup[offset_name], offset_name),
                    msg=offset_name,
                )

    def test_batch_presence_blindspots(self) -> None:
        _, quoted_entries = parse_quoted_offsets(ROOT / "offsets.ini")
        selected = {
            "OFFSET_props_prpsdp",
            "OFFSET_dhprop_locx",
            "OFFSET_irbs",
            "OFFSET_aveh",
            "OFFSET_adlc",
        }
        entries = [entry for entry in quoted_entries if entry.name in selected]
        found = find_present_entries_in_new(self.new_dir, entries)
        self.assertEqual(found, selected)

    def test_recent_root_shifts_are_present(self) -> None:
        for offset_name in (
            "OFFSET_SMS_team",
            "OFFSET_ptemp_pto",
            "OFFSET_tp_WAz",
            "OFFSET_PwrUp_pwrBS",
            "OFFSET_emp_pnEMPd",
            "OFFSET_aveh",
            "OFFSET_todhr",
            "OFFSET_xpr",
            "OFFSET_dlcrel",
            "OFFSET_rcvs",
            "OFFSET_nm",
            "OFFSET_mrd",
            "OFFSET_racetype",
            "OFFSET_clrovr",
            "OFFSET_vsbsout",
            "OFFSET_teamrvc",
            "OFFSET_pol",
            "OFFSET_traf",
            "OFFSET_geard",
            "OFFSET_cposr",
            "OFFSET_ptint",
            "OFFSET_dtmp",
            "OFFSET_nrcid",
            "OFFSET_dtspk",
            "OFFSET_entCont_Num",
            "OFFSET_gbtpi",
            "OFFSET_rsgmx",
            "OFFSET_eoir",
            "OFFSET_trsrl",
            "OFFSET_mcpbs1",
            "OFFSET_otzone_otvt",
            "OFFSET_cspnm",
            "OFFSET_ddblip_frul",
        ):
            with self.subTest(offset_name=offset_name):
                self.assert_value_present_in_new(offset_name)

    def test_no_legacy_root_markers_in_offsets_ini(self) -> None:
        text = (ROOT / "offsets.ini").read_text(encoding="utf-8")
        legacy_patterns = (
            r"Global_4980736\.f_67545",
            r"Global_4980736\.f_7044",
            r"Global_4980736\.f_57169",
            r"Global_5242880\.f_1\[i /\*163\*/\]",
            r"Global_4980736\.f_48744",
            r"/\*26949\*/",
            r"Global_4718592\.f_112261",
            r"Global_4718592\.f_188240",
            r"Global_4718592\.f_195976",
            r"Global_4718592\.f_201687",
            r"Global_4718592\.f_200398",
            r"Global_4718592\.f_121564",
            r"Global_4718592\.f_121593",
            r"Global_4718592\.f_124924",
            r"Global_4718592\.f_124977",
            r"Global_4718592\.f_124998",
            r"Global_4718592\.f_131623",
            r"Global_4718592\.f_131645",
            r"Global_4718592\.f_131774",
            r"Global_4718592\.f_132077",
            r"Global_4718592\.f_132078",
            r"Global_4718592\.f_132079",
            r"Global_4718592\.f_132083",
            r"Global_4718592\.f_160502",
            r"Global_4718592\.f_160505",
            r"Global_4718592\.f_160508",
            r"Global_4718592\.f_186692",
            r"Global_4718592\.f_186693",
            r"Global_4718592\.f_190775",
            r"Global_4718592\.f_190776",
            r"Global_4718592\.f_192207",
            r"Global_4718592\.f_192211",
            r"Global_4718592\.f_192212",
            r"Global_4980736\.f_208936",
            r"Global_4980736\.f_214055",
            r"Global_4980736\.f_187441",
            r"Global_4980736\.f_187522",
            r"Global_4980736\.f_187603",
            r"Global_4718592\.f_129045",
            r"Global_4718592\.f_183637",
            r"Global_4718592\.f_183568",
            r"Global_4718592\.f_183569",
            r"Global_4718592\.f_183570",
            r"Global_4718592\.f_183571",
            r"Global_4718592\.f_183572",
            r"Global_4718592\.f_191435",
            r"Global_4718592\.f_160654",
            r"Global_4718592\.f_160662",
            r"Global_4718592\.f_188199",
            r"Global_4718592\.f_191016",
            r"Global_4718592\.f_183574",
            r"Global_4718592\.f_183642",
            r"Global_4718592\.f_160720",
            r"Global_4718592\.f_188047",
            r"Global_4718592\.f_188112",
            r"Global_4718592\.f_160160",
            r"Global_4718592\.f_160449",
            r"Global_4718592\.f_121552",
            r"Global_4718592\.f_125909",
            r"Global_4718592\.f_160663",
            r"Global_4718592\.f_124972",
            r"Global_4718592\.f_124982",
            r"Global_4718592\.f_124987",
            r"Global_4718592\.f_124967",
            r"Global_4718592\.f_190977",
            r"Global_4718592\.f_132029",
            r"Global_4718592\.f_132072",
            r"Global_4718592\.f_125003",
            r"Global_4718592\.f_121554",
            r"Global_4718592\.f_132071",
            r"Global_4718592\.f_132067",
            r"Global_4718592\.f_132068",
            r"Global_4718592\.f_124923",
            r"Global_4718592\.f_132075",
            r"Global_4718592\.f_160680",
            r"Global_4718592\.f_188200",
            r"Global_4718592\.f_188201",
            r"Global_4718592\.f_188202",
            r"Global_4718592\.f_188203",
            r"Global_4718592\.f_111418",
            r"Global_4718592\.f_124957",
            r"Global_4718592\.f_124962",
            r"Global_4718592\.f_160492",
            r"Global_4718592\.f_113912",
            r"Global_4718592\.f_113917",
            r"Global_4718592\.f_160486",
            r"Global_4718592\.f_131931",
            r"Global_4718592\.f_131968",
            r"Global_4718592\.f_191957",
            r"Global_4718592\.f_125051",
            r"Global_4718592\.f_125052",
            r"Global_4718592\.f_125053",
            r"Global_4718592\.f_125559",
            r"Global_4718592\.f_125945",
            r"Global_4718592\.f_125914",
            r"Global_4718592\.f_125976",
            r"Global_4718592\.f_127957",
            r"Global_4718592\.f_188117",
            r"Global_4718592\.f_130577",
            r"Global_4718592\.f_180870",
            r"Global_4718592\.f_130432",
            r"Global_4718592\.f_130063",
            r"Global_4718592\.f_112251",
            r"Global_4718592\.f_112256",
            r"Global_4718592\.f_112234",
            r"Global_4718592\.f_112200",
            r"Global_4718592\.f_112217",
        )
        for pattern in legacy_patterns:
            with self.subTest(pattern=pattern):
                self.assertNotRegex(text, pattern)

    def test_static_unsupported_quoted_offsets_are_explicit(self) -> None:
        expected = {
            "OFFSET_creator_index": "20,58",
            "OFFSET_tp_player_loc": "8,30,50",
            "OFFSET_tp_playerveh_loc": "8,DD0,30,50",
            "OFFSET_tp_playervehcam_loc": "8,DD0,90",
            "OFFSET_tp_cam_loc": "8,90",
            "OFFSET_isplayerinveh": "8,14A2",
            "OFFSET_creator_cam_loc": "0x60",
        }
        _, quoted_entries = parse_quoted_offsets(ROOT / "offsets.ini")
        quoted_lookup = {entry.name: entry.value for entry in quoted_entries}
        actual = {
            name: value
            for name, value in quoted_lookup.items()
            if not value.startswith(("Global_", "uLocal_", "iLocal_", "fLocal_", "f_"))
            and not re.fullmatch(r"\d+(?:\.f_\d+)?", value)
        }
        self.assertEqual(actual, expected)


if __name__ == "__main__":
    unittest.main()
