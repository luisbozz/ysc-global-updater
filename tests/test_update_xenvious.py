import json
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scrpatches"))

import update_xenvious as ux  # noqa: E402


class TestDiffLines(unittest.TestCase):
    def test_counts_changed_lines_in_both_directions(self):
        self.assertEqual(ux.diff_lines("a\nb\nc", "a\nB\nc"), (1, 1))
        self.assertEqual(ux.diff_lines("a\nb", "a\nb"), (0, 0))
        self.assertEqual(ux.diff_lines("a", "a\nb\nc"), (0, 2))

    def test_ignores_the_unified_diff_file_headers(self):
        # A naive startswith("-")/("+") count would add 2 phantom lines for the
        # '---' and '+++' headers of every non-empty diff.
        removed, added = ux.diff_lines("x", "y")
        self.assertEqual((removed, added), (1, 1))


class TestDirty(unittest.TestCase):
    def test_paths_with_spaces_survive(self):
        # git quotes such names unless -z is used; the quoted form would never
        # be recognised and would block the deploy on a clean tree.
        payload = " M Xenvious/GTA.cs\0 M Xenvious/Creator Classes/ScrPatchesRunner.cs\0"
        with mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout=payload)):
            found = ux._dirty(pathlib.Path("/nowhere"))
        self.assertEqual(found, ["Xenvious/GTA.cs",
                                 "Xenvious/Creator Classes/ScrPatchesRunner.cs"])

    def test_a_rename_yields_both_paths_intact(self):
        # With -z a rename is two records: "R  <new>" then the old path alone,
        # with no status prefix. Stripping three characters from that second
        # record would report a path that does not exist.
        payload = ("R  Xenvious/OfflineData/legacy/offsets.ini\0"
                   "Xenvious/OfflineData/offsets.ini\0"
                   " M Xenvious/GTA.cs\0")
        with mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout=payload)):
            found = ux._dirty(pathlib.Path("/nowhere"))
        self.assertEqual(found, ["Xenvious/OfflineData/legacy/offsets.ini",
                                 "Xenvious/OfflineData/offsets.ini",
                                 "Xenvious/GTA.cs"])

    def test_no_git_repo_reports_nothing_dirty(self):
        with mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=128, stdout="")):
            self.assertEqual(ux._dirty(pathlib.Path("/nowhere")), [])


class TestDeployGate(unittest.TestCase):
    """The deploy step must refuse to ship a broken set of patterns."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.xen = pathlib.Path(self.tmp.name)
        (self.xen / "Xenvious" / "OfflineData").mkdir(parents=True)
        self.addCleanup(self.tmp.cleanup)

    def test_broken_patterns_block_the_deploy(self):
        written = ux.step_deploy("legacy", self.xen, "1.71-3586", "1.73-3889",
                                 broken=3, dry_run=True, auto_yes=True)
        self.assertFalse(written)

    def test_missing_target_blocks_the_deploy(self):
        # OfflineData exists but is empty: nothing to merge into.
        written = ux.step_deploy("legacy", self.xen, "1.71-3586", "1.73-3889",
                                 broken=0, dry_run=True, auto_yes=True)
        self.assertFalse(written)


class TestRollbackScope(unittest.TestCase):
    def test_the_scope_is_the_folder_the_rollback_command_touches(self):
        # `git checkout -- Xenvious/OfflineData` restores exactly this subtree,
        # so nothing outside it can be lost by a deploy.
        for variant in ("legacy", "enhanced"):
            for name in ("offsets.ini", "scrpatches.json"):
                self.assertTrue(
                    ux.in_rollback_scope(f"Xenvious/OfflineData/{variant}/{name}"))

    def test_source_files_are_outside_the_scope(self):
        for path in ("Xenvious/GTA.cs", "Xenvious/MainWindow.xaml.cs",
                     "Xenvious/Xenvious.csproj"):
            self.assertFalse(ux.in_rollback_scope(path),
                             "warning about a file the rollback never touches "
                             "only teaches people to ignore the warning")


class TestVariantPaths(unittest.TestCase):
    def test_each_variant_has_its_own_migration_artifacts(self):
        # One shared output path would let a Legacy run overwrite the Enhanced
        # result, and the next deploy would ship Legacy offsets as Enhanced.
        self.assertNotEqual(ux.migrated_ini("legacy"), ux.migrated_ini("enhanced"))
        self.assertNotEqual(ux.migrate_report("legacy"), ux.migrate_report("enhanced"))

    def test_legacy_keeps_the_established_file_names(self):
        self.assertEqual(ux.migrated_ini("legacy").name, "offsets.migrated.ini")
        self.assertEqual(ux.migrate_report("legacy").name, "migrate-report.json")

    def test_each_variant_has_its_own_patch_set(self):
        # The patterns start out identical, but they drift as soon as one build
        # needs a pattern re-derived -- and the injected payloads are compiled
        # per build and never match across one.
        self.assertNotEqual(ux.patches_file("legacy"), ux.patches_file("enhanced"))
        self.assertNotEqual(ux.repaired_file("legacy"), ux.repaired_file("enhanced"))

    def test_legacy_keeps_the_established_patch_file_names(self):
        self.assertEqual(ux.patches_file("legacy").name, "scrpatches.json")
        self.assertEqual(ux.repaired_file("legacy").name, "scrpatches.repaired.json")


class TestScriptLists(unittest.TestCase):
    def test_match_what_fetch_update_downloads(self):
        text = (ROOT / "fetch_update.sh").read_text(encoding="utf-8")
        for name in ux.C_SCRIPTS:
            self.assertIn(name, text)
        self.assertEqual(len(ux.C_SCRIPTS), 8)
        self.assertEqual(len(ux.FULL_SCRIPTS), 6)


if __name__ == "__main__":
    unittest.main()
