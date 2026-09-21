import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.deploy_offsets import (  # noqa: E402
    DEFAULT_TARGETS, main, merge, split_vector_xyz,
)

MIGRATED = """
OFFSET_check_creator = "Global_1925981"
OFFSET_actor_loc = "Global_4980736.f_93162[i /*1277*/]"
OFFSET_actor_actv = "Global_4980736.f_166.f_9.f_2"
OFFSET_new_thing = "Global_100.f_5"
"""

TARGET = """[AOB]
worldptr = "48 8B 05 ? ? ? ? 45 0F C6 C0"
[OFFSETS]
OFFSET_check_creator = "Global_1921391"
OFFSET_actor_locx = "Global_4980736.f_89187[j /*1213*/].f_0"
OFFSET_actor_locy = "Global_4980736.f_89187[j /*1213*/].f_1"
OFFSET_actor_locz = "Global_4980736.f_89187[j /*1213*/].f_2"
OFFSET_actor_actvx = "Global_4980736.f_89187[j /*1213*/].f_161.f_9.f_2"
OFFSET_actor_actvy = "Global_4980736.f_89187[j /*1213*/].f_161.f_9.f_3"
OFFSET_actor_actvz = "Global_4980736.f_89187[j /*1213*/].f_161.f_9.f_4"
OFFSET_untouched = "Global_999.f_1"
OFFSET_image = 0x1C
"""


class SplitVectorTest(unittest.TestCase):
    def test_base_shape_appends_f0_f1_f2(self) -> None:
        x, y, z = split_vector_xyz("Global_4980736.f_93162[i /*1277*/]")
        self.assertEqual(x, "Global_4980736.f_93162[i /*1277*/].f_0")
        self.assertEqual(y, "Global_4980736.f_93162[i /*1277*/].f_1")
        self.assertEqual(z, "Global_4980736.f_93162[i /*1277*/].f_2")

    def test_trailing_field_shape_bumps_by_1_and_2(self) -> None:
        x, y, z = split_vector_xyz("Global_4980736.f_166.f_9.f_2")
        self.assertEqual(x, "Global_4980736.f_166.f_9.f_2")
        self.assertEqual(y, "Global_4980736.f_166.f_9.f_3")
        self.assertEqual(z, "Global_4980736.f_166.f_9.f_4")


class MergeTest(unittest.TestCase):
    def test_direct_match_updates_value(self) -> None:
        merged, stats, _ = merge(MIGRATED, TARGET)
        self.assertIn('OFFSET_check_creator = "Global_1925981"', merged)
        self.assertEqual(stats["updated"], 1 + 6)  # check_creator + 6 split values

    def test_vector_split_updates_all_three(self) -> None:
        merged, _, _ = merge(MIGRATED, TARGET)
        self.assertIn('OFFSET_actor_locx = "Global_4980736.f_93162[i /*1277*/].f_0"', merged)
        self.assertIn('OFFSET_actor_locy = "Global_4980736.f_93162[i /*1277*/].f_1"', merged)
        self.assertIn('OFFSET_actor_locz = "Global_4980736.f_93162[i /*1277*/].f_2"', merged)
        self.assertIn('OFFSET_actor_actvx = "Global_4980736.f_166.f_9.f_2"', merged)
        self.assertIn('OFFSET_actor_actvy = "Global_4980736.f_166.f_9.f_3"', merged)
        self.assertIn('OFFSET_actor_actvz = "Global_4980736.f_166.f_9.f_4"', merged)

    def test_non_offset_and_target_only_lines_preserved(self) -> None:
        merged, stats, _ = merge(MIGRATED, TARGET)
        self.assertIn("[AOB]", merged)
        self.assertIn('worldptr = "48 8B 05 ? ? ? ? 45 0F C6 C0"', merged)
        self.assertIn('OFFSET_untouched = "Global_999.f_1"', merged)
        self.assertIn("OFFSET_image = 0x1C", merged)  # unquoted static value untouched
        self.assertEqual(stats["target_only_global"], 1)
        self.assertEqual(stats["target_only_static"], 0)  # OFFSET_image doesn't match the quoted regex at all

    def test_migrated_only_offset_is_reported_unmapped(self) -> None:
        _, _, unmapped = merge(MIGRATED, TARGET)
        self.assertEqual(unmapped, ["OFFSET_new_thing"])

    def test_identical_value_counts_as_unchanged(self) -> None:
        target_same = TARGET.replace("Global_1921391", "Global_1925981")
        merged, stats, _ = merge(MIGRATED, target_same)
        self.assertEqual(stats["unchanged"], 1)
        self.assertIn('OFFSET_check_creator = "Global_1925981"', merged)


class PreviewNamingTest(unittest.TestCase):
    """Preview filenames must depend only on the targets of the current run."""

    def _run(self, tmp: pathlib.Path) -> pathlib.Path:
        migrated = tmp / "offsets.migrated.ini"
        migrated.write_text(MIGRATED, encoding="utf-8")

        out_dir = tmp / "deploy"
        targets = []
        for parent in ("OfflineData", "resources"):
            d = tmp / parent
            d.mkdir()
            t = d / "offsets.ini"
            t.write_text(TARGET, encoding="utf-8")
            targets.append(str(t))

        argv = ["deploy_offsets.py", "--migrated", str(migrated), "--out-dir", str(out_dir)]
        for t in targets:
            argv += ["--target", t]
        with mock.patch.object(sys, "argv", argv):
            self.assertEqual(main(), 0)
        return out_dir

    def test_same_filename_targets_get_distinct_previews(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            out_dir = self._run(pathlib.Path(td))
            self.assertEqual(
                sorted(p.name for p in out_dir.iterdir()),
                ["offsets.ini", "resources__offsets.ini"],
            )

    def test_stale_preview_is_overwritten_not_renamed_around(self) -> None:
        """A leftover preview from an earlier run must not shift the naming."""
        with tempfile.TemporaryDirectory() as td:
            tmp = pathlib.Path(td)
            stale = tmp / "deploy" / "offsets.ini"
            stale.parent.mkdir(parents=True)
            stale.write_text("STALE\n", encoding="utf-8")

            out_dir = self._run(tmp)
            self.assertEqual(
                sorted(p.name for p in out_dir.iterdir()),
                ["offsets.ini", "resources__offsets.ini"],
            )
            self.assertNotIn("STALE", (out_dir / "offsets.ini").read_text(encoding="utf-8"))


class TestDefaultTargets(unittest.TestCase):
    def test_only_the_app_is_a_default_target(self) -> None:
        # The old backend is not the source of truth any more. A bare run must
        # never write into it by accident; it needs an explicit --target.
        self.assertEqual(DEFAULT_TARGETS,
                         ["/opt/Xenvious/Xenvious/OfflineData/offsets.ini"])


if __name__ == "__main__":
    unittest.main()
