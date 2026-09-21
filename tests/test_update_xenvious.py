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
        # match EXPECTED_DIRTY and would block the deploy on a clean tree.
        payload = " M Xenvious/GTA.cs\0 M Xenvious/Creator Classes/ScrPatchesRunner.cs\0"
        with mock.patch("subprocess.run",
                        return_value=mock.Mock(returncode=0, stdout=payload)):
            found = ux._dirty(pathlib.Path("/nowhere"))
        self.assertEqual(found, ["Xenvious/GTA.cs",
                                 "Xenvious/Creator Classes/ScrPatchesRunner.cs"])
        for path in found:
            self.assertIn(path, ux.EXPECTED_DIRTY)

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
        written = ux.step_deploy(self.xen, "1.71-3586", "1.73-3889",
                                 broken=3, dry_run=True, auto_yes=True)
        self.assertFalse(written)

    def test_missing_target_blocks_the_deploy(self):
        # OfflineData exists but is empty: nothing to merge into.
        written = ux.step_deploy(self.xen, "1.71-3586", "1.73-3889",
                                 broken=0, dry_run=True, auto_yes=True)
        self.assertFalse(written)


class TestExpectedDirty(unittest.TestCase):
    def test_lists_both_data_files_and_both_csharp_files(self):
        # The enabled flag only works if the C# change ships with the data, so
        # both C# files count as part of this update, not as foreign edits.
        self.assertIn("Xenvious/OfflineData/offsets.ini", ux.EXPECTED_DIRTY)
        self.assertIn("Xenvious/OfflineData/scrpatches.json", ux.EXPECTED_DIRTY)
        self.assertIn("Xenvious/GTA.cs", ux.EXPECTED_DIRTY)
        self.assertIn("Xenvious/Creator Classes/ScrPatchesRunner.cs", ux.EXPECTED_DIRTY)


class TestScriptLists(unittest.TestCase):
    def test_match_what_fetch_update_downloads(self):
        text = (ROOT / "fetch_update.sh").read_text(encoding="utf-8")
        for name in ux.C_SCRIPTS:
            self.assertIn(name, text)
        self.assertEqual(len(ux.C_SCRIPTS), 8)
        self.assertEqual(len(ux.FULL_SCRIPTS), 6)


if __name__ == "__main__":
    unittest.main()
