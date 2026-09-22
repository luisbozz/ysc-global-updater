#!/usr/bin/env python3
"""Move the injected customfuncs payloads onto a different scratch global.

The custom functions keep their state in globals that the game does not use.
Picking those is a judgement call that goes stale: a global index resolves to
``globalTable[index >> 18][index & 0x3FFFF]``, and both halves can move. The
block a script owns is allocated at its declared size, so an index past that
size lands in whatever allocation follows -- readable, writable, and owned by
something else. That is what happened to the old base on 1.73: it sat at slot
254056 of a block that is only 119296 slots long, and the payloads had been
writing into a neighbouring object's memory.

This rewrites the operand of every GLOBAL_U24 / GLOBAL_U24_LOAD /
GLOBAL_U24_STORE that points into the old scratch range, keeping each one's
distance from the base. Operands are found by decoding the instruction stream,
not by searching for bytes: the three bytes of an address occur inside other
instructions' operands too, and rewriting one of those would corrupt the
payload silently.

    python3 scrpatches/rebase_customfuncs.py --old 1826920 --new 2884084
    python3 scrpatches/rebase_customfuncs.py --old 1826920 --new 2884084 --apply

Nothing is written without --apply. Verify a candidate first -- it has to be
inside its block's allocation, clear of what the block already uses, and clear
of anything the scripts reference.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

from scrasm.disasm import disassemble  # noqa: E402

DATA = ROOT / "data" / "scrpatches.json"

# The instructions that carry a full 24-bit global index.
GLOBAL_OPS = {"GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE"}

# How far above the base the payload's own globals reach. Anything further is a
# different global that happens to sit nearby and is left alone.
SPAN = 16


def parse_bytes(text: str) -> bytes:
    return bytes(int(b, 16) for b in text.split())


def format_bytes(data: bytes) -> str:
    return " ".join(f"{b:02X}" for b in data)


def rebase(payload: bytes, old_base: int, new_base: int) -> tuple:
    """(new payload, [(offset, old index, new index)]) -- length never changes."""
    out = bytearray(payload)
    moved = []
    for ins in disassemble(payload, base=0):
        if ins.name not in GLOBAL_OPS or len(ins.operands) != 3:
            continue
        index = int.from_bytes(ins.operands, "little")
        if not (old_base <= index < old_base + SPAN):
            continue
        new_index = new_base + (index - old_base)
        # The operand sits right after the one-byte opcode.
        start = ins.offset + 1
        out[start:start + 3] = new_index.to_bytes(3, "little")
        moved.append((ins.offset, index, new_index))
    return bytes(out), moved


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", type=int, required=True, help="Current scratch base.")
    ap.add_argument("--new", type=int, required=True, help="Where it should go.")
    ap.add_argument("--patches", default=str(DATA))
    ap.add_argument("--apply", action="store_true", help="Write the change.")
    args = ap.parse_args()

    if not 0 <= args.new < (1 << 24):
        raise SystemExit("a GLOBAL_U24 operand only holds 24 bits")

    path = pathlib.Path(args.patches)
    patches = json.loads(path.read_text(encoding="utf-8"))

    total, touched = 0, 0
    for patch in patches:
        text = patch.get("bytes_to_patch") or ""
        if not text or "{" in text or "?" in text:
            continue                      # placeholder payloads are not bytecode
        try:
            payload = parse_bytes(text)
        except ValueError:
            continue
        try:
            new_payload, moved = rebase(payload, args.old, args.new)
        except Exception as exc:
            print(f"  [SKIP] {patch.get('patch_name')} [{patch.get('script_name')}]: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr)
            continue
        if not moved:
            continue
        assert len(new_payload) == len(payload), "payload length changed"
        touched += 1
        total += len(moved)
        print(f"  {patch.get('patch_name')} [{patch.get('script_name')}]: "
              f"{len(moved)} operand(s)")
        for off, a, b in moved:
            print(f"      +0x{off:04X}  {a} -> {b}")
        if args.apply:
            patch["bytes_to_patch"] = format_bytes(new_payload)

    print(f"\n  {total} operand(s) in {touched} payload(s)")
    if args.apply:
        path.write_text(json.dumps(patches, indent=4) + "\n", encoding="utf-8")
        print(f"  written: {path}")
    else:
        print("  dry run -- pass --apply to write")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
