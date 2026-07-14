"""Symbolic round-trip: bytes -> to_ysa() -> assemble_text() must be identical.

This proves the assembler encodes every operand kind correctly. It runs on:
  * the real customfuncs payloads from scrpatches.json, and
  * a large sample of real functions from a .ysc.full dump (which exercises
    SWITCH, NATIVE, CALL, named ENTER, and every jump variant).
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrasm.asm import assemble_text  # noqa: E402
from scrasm.importer import to_ysa, import_hex  # noqa: E402
from scrasm.disasm import disassemble, parse_hex  # noqa: E402
from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.functions import iter_functions  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data" / "scrpatches.json"
DISASM = ROOT / "disasm"
_PURE_HEX = re.compile(r"^([0-9A-Fa-f]{2}\s+)*[0-9A-Fa-f]{2}\s*$")


def _one_full():
    files = sorted(DISASM.glob("*.ysc.full")) if DISASM.is_dir() else []
    return files[0] if files else None


class AsmRoundTripTest(unittest.TestCase):
    def test_customfuncs_symbolic_roundtrip(self):
        patches = json.loads(DATA.read_text())
        n = 0
        for p in patches:
            b = p.get("bytes_to_patch", "")
            if not b or "{" in b or "?" in b or not _PURE_HEX.match(b):
                continue
            raw = parse_hex(b)
            try:
                ysa = import_hex(b)
            except Exception:  # partial (non-code) overwrite; skip
                continue
            with self.subTest(patch=p.get("patch_name"), script=p.get("script_name")):
                self.assertEqual(assemble_text(ysa), raw, "symbolic round-trip failed")
                n += 1
        self.assertGreater(n, 5)

    def test_handwritten_ysa(self):
        src = """
        ; a tiny hand-written function
        ENTER 0, 2
          GLOBAL_U24_LOAD 1826920
          PUSH_CONST_U8 0
          IS_BIT_SET
          INOT
          JZ done
          NATIVE 1, 1, 665
          DROP
        done:
          LEAVE 0, 0
        """
        code = assemble_text(src)
        ins = disassemble(code)
        self.assertEqual(ins[0].name, "ENTER")
        self.assertEqual(ins[-1].name, "LEAVE")
        # the JZ must resolve to the LEAVE offset
        jz = next(i for i in ins if i.name == "JZ")
        self.assertEqual(jz.jump_target, ins[-1].offset)
        # and re-importing yields the same bytes
        self.assertEqual(assemble_text(to_ysa(code)), code)

    @unittest.skipUnless(_one_full(), "no .ysc.full dump present")
    def test_sampled_functions_symbolic_roundtrip(self):
        y = YscFull.parse(_one_full())
        ins = disassemble(y.code)
        funcs = list(iter_functions(ins))
        # sample every Nth function to keep runtime reasonable
        sample = funcs[::37]
        # guarantee coverage of SWITCH and named ENTER
        for f in funcs:
            body = ins[f.start_index:f.end_index + 1]
            if any(i.name == "SWITCH" for i in body) and f not in sample:
                sample.append(f)
                break
        for f in funcs:
            if ins[f.start_index].enter_namelen > 0 and f not in sample:
                sample.append(f)
                break
        checked = switches = named = 0
        for f in sample:
            fb = y.code[f.start_offset:f.end_offset]
            ysa = to_ysa(fb)  # base 0: internal jumps relative, calls absolute
            with self.subTest(func=f.name, off=f.start_offset, nbytes=len(fb)):
                self.assertEqual(assemble_text(ysa), fb, "function round-trip failed")
            checked += 1
            body = ins[f.start_index:f.end_index + 1]
            switches += any(i.name == "SWITCH" for i in body)
            named += ins[f.start_index].enter_namelen > 0
        self.assertGreater(checked, 100, "expected a large function sample")
        # informational: how much variety we covered
        print(f"\n  [asm] sampled {checked} funcs, {switches} with SWITCH, {named} named")


if __name__ == "__main__":
    unittest.main()
