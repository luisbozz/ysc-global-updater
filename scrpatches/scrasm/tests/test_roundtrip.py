"""Round-trip tests: disassemble(bytes) -> assemble() must be byte-identical.

Uses the real ``bytes_to_patch`` blobs shipped in
``scrpatches/data/scrpatches.json`` (the ``customfuncs`` payloads are whole
GTA5 functions and thus exercise a wide slice of the opcode table).
"""

from __future__ import annotations

import json
import re
import sys
import unittest
from pathlib import Path

# Make ``scrasm`` importable as a top-level package regardless of CWD.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scrasm.disasm import disassemble, assemble, parse_hex  # noqa: E402
from scrasm.opcodes import DecodeError  # noqa: E402

DATA = Path(__file__).resolve().parents[2] / "data" / "scrpatches.json"

_PURE_HEX = re.compile(r"^([0-9A-Fa-f]{2}\s+)*[0-9A-Fa-f]{2}\s*$")


def _pure_hex_blobs():
    patches = json.loads(DATA.read_text())
    for p in patches:
        b = p.get("bytes_to_patch", "")
        if b and "{" not in b and "?" not in b and _PURE_HEX.match(b):
            yield p


class RoundTripTest(unittest.TestCase):
    def test_data_present(self):
        self.assertTrue(DATA.exists(), f"missing {DATA}")
        self.assertGreater(len(list(_pure_hex_blobs())), 0, "no pure-hex blobs found")

    def test_customfuncs_roundtrip(self):
        """Every pure-hex customfuncs payload round-trips; injected functions
        (those starting with ENTER=0x2D) decode as whole ENTER..LEAVE streams."""
        enter_blobs = 0
        for p in _pure_hex_blobs():
            if p.get("category") != "customfuncs":
                continue
            raw = parse_hex(p["bytes_to_patch"])
            with self.subTest(patch=p.get("patch_name"), script=p.get("script_name"),
                              nbytes=len(raw)):
                ins = disassemble(raw)
                self.assertEqual(assemble(ins), raw, "round-trip mismatch")
                if raw and raw[0] == 0x2D:  # ENTER -> full injected function
                    enter_blobs += 1
                    self.assertEqual(ins[0].name, "ENTER", "should start with ENTER")
                    self.assertTrue(any(i.name == "LEAVE" for i in ins),
                                    "injected function must contain a LEAVE")
        self.assertGreaterEqual(enter_blobs, 3, "expected >=3 injected-function blobs")

    def test_all_decodable_blobs_roundtrip(self):
        """Any decodable blob must round-trip; undecodable ones are partial patches."""
        decoded = skipped = 0
        for p in _pure_hex_blobs():
            raw = parse_hex(p["bytes_to_patch"])
            try:
                ins = disassemble(raw)
            except DecodeError:
                skipped += 1
                continue
            decoded += 1
            with self.subTest(patch=p.get("patch_name"), script=p.get("script_name")):
                self.assertEqual(assemble(ins), raw)
        self.assertGreater(decoded, skipped, "most blobs should be valid code")


if __name__ == "__main__":
    unittest.main()
