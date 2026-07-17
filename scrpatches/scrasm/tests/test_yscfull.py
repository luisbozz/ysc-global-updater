"""Full-script round-trip over real decrypted dumps (old + new game versions).

For every ``scrpatches/disasm/*.ysc.full`` this decodes the *entire* code
section into instructions and re-encodes it; the result must be byte-identical.
A single wrong operand length anywhere would desync the linear walk and either
raise, fail round-trip, or stop short of ``code_length`` -- so this is a very
strong correctness proof of the opcode table.

The dumps are large and git-ignored; if they are absent the test is skipped.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrasm.disasm import disassemble, assemble  # noqa: E402
from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.functions import iter_functions  # noqa: E402

DISASM = Path(__file__).resolve().parents[2] / "disasm"


def _full_files():
    if not DISASM.is_dir():
        return []
    return sorted(DISASM.glob("*/*.ysc.full"))


@unittest.skipUnless(_full_files(), "no .ysc.full dumps present in scrpatches/disasm/")
class YscFullRoundTripTest(unittest.TestCase):
    def test_roundtrip_all_versions(self):
        files = _full_files()
        # expect old+new for the six patched scripts
        self.assertGreaterEqual(len(files), 2, "need at least old+new dumps")
        for f in files:
            with self.subTest(dump=f.name):
                y = YscFull.parse(f)
                self.assertEqual(len(y.code), y.code_length,
                                 "assembled code length != header CodeLength")
                ins = disassemble(y.code)
                self.assertEqual(assemble(ins), y.code, "full round-trip mismatch")
                self.assertEqual(ins[0].name, "ENTER", "code must start with ENTER")

    def test_function_segmentation(self):
        """Functions tile the code back-to-back; each ENTER region contains a LEAVE."""
        for f in _full_files():
            with self.subTest(dump=f.name):
                y = YscFull.parse(f)
                ins = disassemble(y.code)
                funcs = list(iter_functions(ins))
                self.assertGreater(len(funcs), 100, "expected many functions")
                self.assertEqual(funcs[0].start_index, 0)
                self.assertEqual(ins[0].name, "ENTER")
                for fn in funcs:
                    self.assertEqual(ins[fn.start_index].name, "ENTER")
                    body = ins[fn.start_index:fn.end_index + 1]
                    self.assertTrue(any(g.name == "LEAVE" for g in body),
                                    f"function {fn.name} has no LEAVE")
                for a, b in zip(funcs, funcs[1:]):
                    self.assertEqual(a.end_index + 1, b.start_index, "not contiguous")
                self.assertEqual(funcs[-1].end_offset, len(y.code))


if __name__ == "__main__":
    unittest.main()
