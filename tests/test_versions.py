import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import versions  # noqa: E402

BUILDS = ["1.71-3586", "1.73-3889", "enhanced-1.72-1094", "enhanced-1.73-1158"]


class VariantTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        for b in BUILDS:
            (self.root / b).mkdir()
        self.addCleanup(self.tmp.cleanup)

    def test_variant_of(self):
        self.assertEqual(versions.variant_of("1.73-3889"), "legacy")
        self.assertEqual(versions.variant_of("enhanced-1.73-1158"), "enhanced")

    def test_label_is_idempotent(self):
        self.assertEqual(versions.label("enhanced", "1.73-1158"), "enhanced-1.73-1158")
        self.assertEqual(versions.label("enhanced", "enhanced-1.73-1158"),
                         "enhanced-1.73-1158")
        self.assertEqual(versions.label("legacy", "1.73-3889"), "1.73-3889")

    def test_label_rejects_an_unknown_variant(self):
        with self.assertRaises(ValueError):
            versions.label("deluxe", "1.73")

    def test_listing_is_filtered_by_variant(self):
        self.assertEqual(versions.list_versions(self.root, "legacy"),
                         ["1.71-3586", "1.73-3889"])
        self.assertEqual(versions.list_versions(self.root, "enhanced"),
                         ["enhanced-1.72-1094", "enhanced-1.73-1158"])
        self.assertEqual(len(versions.list_versions(self.root)), 4)

    def test_previous_never_crosses_the_variant_split(self):
        # The two games are numbered independently: Enhanced 1158 is a newer
        # game than Legacy 3889 despite the smaller number. Comparing across
        # the split would migrate one game's offsets against the other's code.
        self.assertEqual(versions.previous(self.root, "enhanced-1.73-1158"),
                         "enhanced-1.72-1094")
        self.assertEqual(versions.previous(self.root, "1.73-3889"), "1.71-3586")

    def test_previous_returns_none_for_the_first_build_of_a_variant(self):
        self.assertIsNone(versions.previous(self.root, "enhanced-1.70-1000"))

    def test_resolve_infers_the_variant_from_the_spec(self):
        self.assertEqual(versions.resolve(self.root, "enhanced-1.73"),
                         "enhanced-1.73-1158")
        self.assertEqual(versions.resolve(self.root, "1.73"), "1.73-3889")

    def test_resolve_accepts_a_bare_spec_once_the_variant_is_known(self):
        self.assertEqual(versions.resolve(self.root, "1.73", variant="enhanced"),
                         "enhanced-1.73-1158")

    def test_resolve_latest_stays_inside_the_variant(self):
        self.assertEqual(versions.resolve(self.root, "latest", variant="enhanced"),
                         "enhanced-1.73-1158")
        self.assertEqual(versions.resolve(self.root, "latest", variant="legacy"),
                         "1.73-3889")

    def test_resolve_reports_the_variant_when_nothing_is_fetched(self):
        with tempfile.TemporaryDirectory() as empty:
            with self.assertRaises(SystemExit) as cm:
                versions.resolve(pathlib.Path(empty), "1.73", variant="enhanced")
            self.assertIn("enhanced", str(cm.exception))

    def test_unprefixed_labels_keep_their_old_meaning(self):
        # Every label written before the split is a Legacy label.
        for name in ("1.71-3586", "1.73-3889", "latest-ish", "2245"):
            self.assertEqual(versions.variant_of(name), "legacy")


if __name__ == "__main__":
    unittest.main()
