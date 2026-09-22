#!/usr/bin/env python3
"""Check that the app and the injected payloads mean the same scratch globals.

The custom functions keep their state in globals the game does not use. Two
places have to agree on which ones: the payload writes them as GLOBAL_U24
operands in its bytecode, and offsets.ini tells the app where to read them. They
are maintained by hand, in different files, and nothing connected them -- so
they drifted apart, and the app spent a while reading a global nothing wrote.

The base also has to stay addressable. A global index resolves to
``block[index >> 18][index & 0x3FFFF]``, and a block is allocated at the size
its owning script declares. An index past that size is not a free slot; it is
someone else's memory, readable and writable and already in use. The old base
sat 134760 slots past the end of its block.

    python3 tools/check_customfuncs.py

Static only. Whether the slots are actually unused in a running game is a
separate question -- tools/watch_globals.py answers that one.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scrpatches"))

from scrasm.disasm import disassemble  # noqa: E402

GLOBAL_OPS = {"GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE"}
INI_KEYS = ("OFFSET_custom_check", "OFFSET_custom_hovered_model",
            "OFFSET_custom_dimension_model", "OFFSET_custom_dimension_min",
            "OFFSET_custom_dimension_max")
BLOCK_SLOTS = 1 << 18


def payload_globals(patches: list) -> dict:
    """``{index: how often}`` over every global a customfuncs payload touches."""
    seen = collections.Counter()
    for patch in patches:
        if patch.get("category") != "customfuncs":
            continue
        text = patch.get("bytes_to_patch") or ""
        if not text or "{" in text or "?" in text:
            continue
        try:
            payload = bytes(int(b, 16) for b in text.split())
            for ins in disassemble(payload, base=0):
                if ins.name in GLOBAL_OPS and len(ins.operands) == 3:
                    seen[int.from_bytes(ins.operands, "little")] += 1
        except Exception:
            continue
    return dict(seen)


def ini_globals(text: str) -> dict:
    out = {}
    for key in INI_KEYS:
        m = re.search(rf'^{key}\s*=\s*"([^"]*)"', text, re.MULTILINE)
        if not m:
            continue
        value = m.group(1)
        if value.startswith("Global_"):
            out[key] = sum(int(x) for x in re.findall(r"\d+", value))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patches", default=str(ROOT / "scrpatches" / "data" / "scrpatches.json"))
    ap.add_argument("--ini", default=str(ROOT / "offsets.ini"))
    args = ap.parse_args()

    patches = json.loads(pathlib.Path(args.patches).read_text(encoding="utf-8"))
    ini_text = pathlib.Path(args.ini).read_text(encoding="utf-8", errors="replace")

    payload = payload_globals(patches)
    ini = ini_globals(ini_text)
    # The tuneables root is a game global the payload legitimately reads.
    scratch = {g: n for g, n in payload.items() if g not in (4718592,)}

    print(f"\n  {args.patches}")
    print(f"  {args.ini}\n")
    if not scratch:
        print("  no scratch globals in any payload -- nothing to check\n")
        return 0

    base = min(scratch)
    block, slot = base >> 18, base & (BLOCK_SLOTS - 1)
    print(f"  payload base  Global_{base}   block {block}, slot {slot}")
    print(f"  payload uses  {sorted(scratch)}")
    print(f"  ini says      {sorted(set(ini.values()))}\n")

    problems = []
    for key in INI_KEYS:
        if key not in ini:
            problems.append(f"{key} missing from the ini")
    unknown = sorted(set(ini.values()) - set(scratch))
    if unknown:
        problems.append(f"the ini names globals no payload writes: {unknown}")
    unread = sorted(set(scratch) - set(ini.values()))
    if unread:
        # Not every payload global is surfaced to the app; only flag the base.
        if base in unread:
            problems.append(f"the payload writes Global_{base} but no ini key reads it")
    spread = max(scratch) - base
    if spread >= BLOCK_SLOTS:
        problems.append(f"the payload's globals span {spread} slots, crossing a block")

    for p in problems:
        print(f"  [FAIL] {p}")
    if not problems:
        print("  ini and payload agree")
    print(f"\n  Not checked here: whether slot {slot} of block {block} is inside that")
    print("  block's allocation and unused. Read it from a running game:")
    print(f"    python3 tools/watch_globals.py --index {base} --span 40\n")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
