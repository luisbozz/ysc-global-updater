#!/usr/bin/env python3
"""Generate readable ``.ysa`` source for the injected customfuncs payloads.

For each script this disassembles the (old-version) injected payload into
symbolic source with native NAMES, jump labels and internal-function labels,
verifies it re-assembles to the exact original bytes, and writes it to
``scrasm/customfuncs/src/<script>.ysa``.

This turns the hand-flattened hex blob in scrpatches.json into maintainable
source. (External R* CALL addresses are the old build's; use repair_scrpatches.py
to migrate a payload to a new game version.)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import argparse  # noqa: E402
import versions  # noqa: E402
from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.natives import NativeResolver  # noqa: E402
from scrasm.repair import find_anchor  # noqa: E402
from scrasm.disasm import parse_hex  # noqa: E402
from scrasm.importer import to_ysa  # noqa: E402
from scrasm.asm import assemble_text  # noqa: E402

DATA = ROOT / "data" / "scrpatches.json"
DISASM = ROOT / "disasm"
OUT = ROOT / "scrasm" / "customfuncs" / "src"


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Generate readable .ysa source from the customfuncs payloads.")
    ap.add_argument("--build", help="Build the payloads are authored for "
                                    "(default: oldest folder in disasm/).")
    args = ap.parse_args()
    builds = versions.list_versions(DISASM)
    if not builds:
        raise SystemExit("no build folders in disasm/ -- fetch one first")
    build = versions.resolve(DISASM, args.build) if args.build else builds[0]
    print(f"[versions] source build: {build}")

    patches = json.loads(DATA.read_text())
    OUT.mkdir(parents=True, exist_ok=True)
    done = []
    for p in patches:
        b = p.get("bytes_to_patch", "")
        if not (p.get("category") == "customfuncs" and "{" not in b
                and b.replace(" ", "").upper().startswith("2D")):
            continue
        script = p["script_name"]
        of = DISASM / build / f"{script}.ysc.full"
        if not of.exists():
            print(f"skip {script}: missing {of}")
            continue
        full = YscFull.parse(of)
        resolver = NativeResolver.from_full(full)
        anchors = find_anchor(full.code)
        if len(anchors) != 1:
            print(f"skip {script}: anchor not unique")
            continue
        base = anchors[0]
        raw = parse_hex(b)
        ysa = to_ysa(raw, base=base, resolver=resolver,
                     function_labels=True, comments=True)
        # verify the source re-assembles to the exact original bytes
        back = assemble_text(ysa, base=base, natives=resolver.index_of_name())
        if back != raw:
            print(f"!! {script}: generated source does NOT round-trip; not writing")
            continue
        header = (
            f"; customfuncs payload for {script}  (built for the OLD game version)\n"
            f"; injected at sacrificial function 0x{base:X} "
            f"(anchor 2D 04 3A 00 00 38 03)\n"
            f"; natives are symbolic (@NS::NAME); external R* CALLs are old-build\n"
            f"; addresses -- run repair_scrpatches.py to migrate to a new version.\n"
            f"; verified: re-assembles byte-identical to scrpatches.json.\n\n"
        )
        dst = OUT / f"{script}.ysa"
        dst.write_text(header + ysa)
        done.append(dst)
        print(f"wrote {dst}  ({len(raw)} bytes, round-trip OK)")
    print(f"\n{len(done)} source files generated in {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
