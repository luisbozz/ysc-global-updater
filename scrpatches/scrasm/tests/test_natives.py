"""Native index<->name resolution, verified against ground-truth annotations.

The ``scrcustomfuncs_*.txt`` legend gives known (index -> native) pairs for the
*old* capture script; all six must resolve exactly. The same natives sit at
*different* indices in the new version -- that shift is exactly what breaks the
injected customfuncs payloads after a game update.
"""

from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.natives import NativeResolver, load_hash_names, load_crossmap  # noqa: E402

DISASM = Path(__file__).resolve().parents[2] / "disasm"
OLD_BUILD = "1.71-3586"   # build the ground-truth annotations were taken from

# ground truth from scrcustomfuncs_capture.txt (OLD capture version)
CAPTURE_OLD_TRUTH = {
    665: "IS_MODEL_VALID",
    1098: "IS_ENTITY_AN_OBJECT",
    180: "DOES_ENTITY_EXIST",
    70: "GET_ENTITY_MODEL",
    14: "GET_MODEL_DIMENSIONS",
    308: "GET_HUD_COLOUR",
}


def _builds() -> list[str]:
    if not DISASM.is_dir():
        return []
    return sorted((d.name for d in DISASM.iterdir() if d.is_dir()),
                  key=lambda n: [int(x) for x in re.findall(r"\d+", n)] or [0])


def _cap(build: str) -> Path:
    return DISASM / build / "fm_capture_creator.ysc.full"


def _newest():
    b = _builds()
    return b[-1] if b else None


def _have_old() -> bool:
    return _cap(OLD_BUILD).is_file()


def _have_new() -> bool:
    n = _newest()
    return bool(n and n != OLD_BUILD and _cap(n).is_file())


class NativeResolverTest(unittest.TestCase):
    def test_json_and_crossmap_load(self):
        names = load_hash_names()
        self.assertIn(0x4EDE34FBADD967A6, names)  # WAIT
        self.assertTrue(names[0x4EDE34FBADD967A6].endswith("WAIT"))
        self.assertGreater(len(load_crossmap()), 1000)

    @unittest.skipUnless(_have_old(), "capture 1.71-3586 dump missing")
    def test_capture_old_ground_truth(self):
        nr = NativeResolver.from_full(YscFull.parse(_cap(OLD_BUILD)))
        for idx, want in CAPTURE_OLD_TRUTH.items():
            self.assertTrue(nr.name_at(idx).endswith(want),
                            f"index {idx} -> {nr.name_at(idx)!r}, expected …{want}")

    @unittest.skipUnless(_have_old(), "capture 1.71-3586 dump missing")
    def test_index_of_name_invertible(self):
        nr = NativeResolver.from_full(YscFull.parse(_cap(OLD_BUILD)))
        ion = nr.index_of_name()
        for idx, want in CAPTURE_OLD_TRUTH.items():
            full = nr.name_at(idx)
            self.assertEqual(ion[full], idx)

    @unittest.skipUnless(_have_old() and _have_new(), "need old+new dumps")
    def test_indices_shift_between_versions(self):
        old = NativeResolver.from_full(YscFull.parse(_cap(OLD_BUILD)))
        new = NativeResolver.from_full(YscFull.parse(_cap(_newest())))
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
