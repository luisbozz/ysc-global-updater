"""End-to-end repair of a real customfuncs payload (old game version -> new).

Verifies that operand rewriting updates the volatile references while keeping
the payload byte-length and semantics intact:
  * native indices update so each NATIVE still means the same hash,
  * internal cross-function calls relocate to the new injection base,
  * external R* calls resolve via fingerprint matching (with an honest list of
    the few that cannot be matched).
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.repair import ScriptContext  # noqa: E402
from scrasm.disasm import parse_hex, disassemble  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DISASM = ROOT / "disasm"
DATA = ROOT / "data" / "scrpatches.json"


def _capture_blob():
    for p in json.loads(DATA.read_text()):
        if (p.get("category") == "customfuncs"
                and p.get("script_name") == "fm_capture_creator"
                and p.get("bytes_to_patch", "").startswith("2D")):
            return p["bytes_to_patch"]
    return None


def _builds():
    if not DISASM.is_dir():
        return []
    return sorted((d.name for d in DISASM.iterdir() if d.is_dir()),
                  key=lambda n: [int(x) for x in re.findall(r"\d+", n)] or [0])


def _old_new_capture():
    b = _builds()
    if len(b) < 2:
        return None
    of = DISASM / b[0] / "fm_capture_creator.ysc.full"
    nf = DISASM / b[-1] / "fm_capture_creator.ysc.full"
    return (of, nf) if of.is_file() and nf.is_file() else None


@unittest.skipUnless(_old_new_capture() and _capture_blob(), "capture dumps/blob missing")
class RepairTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = parse_hex(_capture_blob())
        of, nf = _old_new_capture()
        cls.ctx = ScriptContext.build(YscFull.parse(of), YscFull.parse(nf))
        cls.new_bytes, cls.rep = cls.ctx.repair(cls.raw, "fm_capture_creator")

    def test_length_preserved(self):
        self.assertEqual(len(self.new_bytes), len(self.raw))

    def test_actually_changed(self):
        self.assertNotEqual(self.new_bytes, self.raw, "repair should change something")

    def test_natives_updated_and_semantically_stable(self):
        self.assertGreater(len(self.rep.native_updated), 0)
        self.assertEqual(self.rep.native_missing, [])
        old_ins = disassemble(self.raw)
        new_ins = disassemble(self.new_bytes)
        for a, b in zip(old_ins, new_ins):
            if a.name == "NATIVE":
                self.assertEqual(
                    self.ctx.old_resolver.name_at(a.native_index),
                    self.ctx.new_resolver.name_at(b.native_index),
                    "a repaired native points at a different function",
                )

    def test_internal_calls_relocated(self):
        self.assertGreater(self.rep.internal_calls, 0)
        lo, hi = self.rep.new_base, self.rep.new_base + len(self.new_bytes)
        old_ins = disassemble(self.raw, base=self.rep.old_base)
        new_ins = disassemble(self.new_bytes, base=self.rep.new_base)
        for a, b in zip(old_ins, new_ins):
            if a.name == "CALL" and self.rep.old_base <= a.call_target < self.rep.old_base + len(self.raw):
                self.assertTrue(lo <= b.call_target < hi,
                                "internal call not relocated into new block")
                self.assertEqual(b.call_target - self.rep.new_base,
                                 a.call_target - self.rep.old_base,
                                 "internal call offset drifted")

    def test_external_calls_mostly_resolved(self):
        total = len(self.rep.external_resolved) + len(self.rep.external_unresolved)
        self.assertGreater(total, 0)
        # the vast majority resolve; unresolved ones are honestly reported
        self.assertGreaterEqual(len(self.rep.external_resolved), len(self.rep.external_unresolved))

    def test_embedded_strides_repaired(self):
        # any stride that was updated must have really changed and none may be
        # left in review (whether strides change at all is version-dependent).
        self.assertEqual(self.rep.stride_review, [], "no stride should be left unresolved")
        for g, io, old, new in self.rep.stride_updated:
            self.assertNotEqual(old, new)
        # the repaired payload's creator-global array accesses must use the new stride
        new_ins = disassemble(self.new_bytes)
        for i in range(2, len(new_ins)):
            a, io_i, g_i = new_ins[i], new_ins[i - 1], new_ins[i - 2]
            if (a.name == "ARRAY_U16" and io_i.name == "IOFFSET_S16"
                    and g_i.name.startswith("GLOBAL_U24")):
                key = (g_i.u24, io_i.s16)
                if key in self.ctx.new_strides:
                    self.assertEqual(a.u16, self.ctx.new_strides[key],
                                     "repaired stride does not match new script")

    def test_fully_repaired(self):
        self.assertTrue(self.rep.ok, f"payload still needs review: {self.rep.needs_review}")


if __name__ == "__main__":
    unittest.main()
