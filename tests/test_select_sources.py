import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools.select_sources import (  # noqa: E402
    build_manifest,
    classify,
    load_tiers,
    measure,
)


TEST_CONFIG = {
    "stub_max_bytes": 128,
    "tiers": [
        {
            "name": "creator_serializer",
            "reason": "creator",
            "name_globs": ["*creator*"],
            "min_serializer_calls": 3,
        },
        {
            "name": "controller_root",
            "reason": "controller",
            "name_globs": ["*controller*", "*controler*"],
            "min_globals": 5,
        },
        {
            "name": "high_density_other",
            "reason": "dense",
            "min_globals": 10,
        },
    ],
}

STUB = "void __EntryFunction__()\n{\n}\n"
CREATOR = (
    "void func_1(var uParam0)\n{\n"
    'DATAFILE::DATADICT_SET_INT(uParam0, "a", Global_1);\n'
    'DATAFILE::DATADICT_SET_INT(uParam0, "b", Global_2);\n'
    "DATAFILE::DATAARRAY_ADD_INT(uParam0->f_1, Global_3);\n"
    "}\n"
)
CONTROLLER = "\n".join(f"x = Global_{i};" for i in range(6)) + "\n"
DENSE = "\n".join(f"y = Global_{i};" for i in range(12)) + "\n"
LOW = "z = Global_1;\n"


class SelectSourcesTest(unittest.TestCase):
    def _write_tree(self, tmp: Path) -> None:
        (tmp / "empty_stub.c").write_text(STUB, encoding="utf-8")
        (tmp / "foo_creator.c").write_text(CREATOR, encoding="utf-8")
        (tmp / "bar_controller.c").write_text(CONTROLLER, encoding="utf-8")
        (tmp / "misc_dense.c").write_text(DENSE, encoding="utf-8")
        (tmp / "low_signal.c").write_text(LOW, encoding="utf-8")

    def test_stub_is_dropped(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "empty_stub.c").write_text(STUB, encoding="utf-8")
            metrics = measure(tmp / "empty_stub.c", TEST_CONFIG["stub_max_bytes"])
            self.assertTrue(metrics.is_stub)
            verdict = classify("empty_stub.c", metrics, load_tiers(TEST_CONFIG))
            self.assertFalse(verdict.keep)

    def test_creator_serializer_wins_by_name_and_calls(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            (tmp / "foo_creator.c").write_text(CREATOR, encoding="utf-8")
            metrics = measure(tmp / "foo_creator.c", TEST_CONFIG["stub_max_bytes"])
            self.assertEqual(metrics.serializer_calls, 3)
            self.assertEqual(metrics.globals, 3)
            verdict = classify("foo_creator.c", metrics, load_tiers(TEST_CONFIG))
            self.assertTrue(verdict.keep)
            self.assertEqual(verdict.tier, "creator_serializer")

    def test_manifest_classification_and_counts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._write_tree(tmp)
            manifest = build_manifest(tmp, TEST_CONFIG)

            by_name = {entry["name"]: entry for entry in manifest["files"]}
            self.assertEqual(by_name["foo_creator.c"]["tier"], "creator_serializer")
            self.assertEqual(by_name["bar_controller.c"]["tier"], "controller_root")
            self.assertEqual(by_name["misc_dense.c"]["tier"], "high_density_other")
            self.assertFalse(by_name["low_signal.c"]["keep"])
            self.assertFalse(by_name["empty_stub.c"]["keep"])

            self.assertEqual(manifest["summary"]["scanned"], 5)
            self.assertEqual(manifest["summary"]["kept"], 3)
            self.assertEqual(manifest["summary"]["stubs"], 1)

    def test_manifest_is_json_serializable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            self._write_tree(tmp)
            manifest = build_manifest(tmp, TEST_CONFIG)
            json.dumps(manifest)


if __name__ == "__main__":
    unittest.main()
