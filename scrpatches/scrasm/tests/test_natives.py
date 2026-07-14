"""Native index<->name resolution, verified against ground-truth annotations.

The ``scrcustomfuncs_*.txt`` legend gives known (index -> native) pairs for the
*old* capture script; all six must resolve exactly. The same natives sit at
*different* indices in the new version -- that shift is exactly what breaks the
injected customfuncs payloads after a game update.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.natives import NativeResolver, load_hash_names, load_crossmap  # noqa: E402

DISASM = Path(__file__).resolve().parents[2] / "disasm"

# ground truth from scrcustomfuncs_capture.txt (OLD capture version)
CAPTURE_OLD_TRUTH = {
    665: "IS_MODEL_VALID",
    1098: "IS_ENTITY_AN_OBJECT",
    180: "DOES_ENTITY_EXIST",
    70: "GET_ENTITY_MODEL",
    14: "GET_MODEL_DIMENSIONS",
    308: "GET_HUD_COLOUR",
}


def _have(tag: str) -> bool:
    return (DISASM / f"fm_capture_creator.{tag}.ysc.full").exists()


class NativeResolverTest(unittest.TestCase):
    def test_json_and_crossmap_load(self):
        names = load_hash_names()
        self.assertIn(0x4EDE34FBADD967A6, names)  # WAIT
        self.assertTrue(names[0x4EDE34FBADD967A6].endswith("WAIT"))
        self.assertGreater(len(load_crossmap()), 1000)

    @unittest.skipUnless(_have("old"), "capture.old dump missing")
    def test_capture_old_ground_truth(self):
        y = YscFull.parse(DISASM / "fm_capture_creator.old.ysc.full")
        nr = NativeResolver.from_full(y)
        for idx, want in CAPTURE_OLD_TRUTH.items():
            self.assertTrue(nr.name_at(idx).endswith(want),
                            f"index {idx} -> {nr.name_at(idx)!r}, expected …{want}")

    @unittest.skipUnless(_have("old"), "capture.old dump missing")
    def test_index_of_name_invertible(self):
        y = YscFull.parse(DISASM / "fm_capture_creator.old.ysc.full")
        nr = NativeResolver.from_full(y)
        ion = nr.index_of_name()
        for idx, want in CAPTURE_OLD_TRUTH.items():
            full = nr.name_at(idx)
            self.assertEqual(ion[full], idx)

    @unittest.skipUnless(_have("old") and _have("new"), "need old+new dumps")
    def test_indices_shift_between_versions(self):
        old = NativeResolver.from_full(YscFull.parse(DISASM / "fm_capture_creator.old.ysc.full"))
        new = NativeResolver.from_full(YscFull.parse(DISASM / "fm_capture_creator.new.ysc.full"))
        old_by_name = old.index_of_name()
        new_by_name = new.index_of_name()
        shifted = 0
        for idx, want in CAPTURE_OLD_TRUTH.items():
            full = old.name_at(idx)
            self.assertIn(full, new_by_name, f"{full} vanished in new table")
            if new_by_name[full] != idx:
                shifted += 1
        # the whole point: at least some volatile natives moved
        self.assertGreater(shifted, 0, "expected native indices to shift across versions")


if __name__ == "__main__":
    unittest.main()
