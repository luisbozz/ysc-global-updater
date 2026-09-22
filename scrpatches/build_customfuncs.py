#!/usr/bin/env python3
"""Assemble the customfuncs ``.ysa`` sources back into ``scrpatches.json``.

This is the reverse of ``gen_customfuncs_src.py`` and makes the sources under
``scrasm/customfuncs/src/`` the thing you edit: write assembly, build, deploy.

For each source it resolves the injection base from the target build's script
(the sacrificial-function anchor), assembles with that build's native table and
replaces the ``bytes_to_patch`` of the matching injected customfuncs entry.

Without ``--write`` nothing is modified; the run only reports what would change,
which doubles as a drift check between the sources and the shipped payloads.

``--target`` points this at any scrpatches.json, not just the one under data/.
Use it on a deployed copy (Xenvious/OfflineData/<variant>/scrpatches.json) to
catch the case a build_customfuncs.py run against data/ cannot see: someone
edited the deployed file directly -- by hand, or with a narrower one-off script
-- and it now says something different from what the source describes, even
though data/scrpatches.json itself still matches. That happened once already:
a same-build global rebase was applied straight to the deployed file, fixed
the GLOBAL_U24 operands, and left its internal CALL targets pointing at
whatever the payload used to jump to -- tens of KB into unrelated code that a
check against data/ alone had no way to see, because data/ was never touched.

Usage:
  python3 build_customfuncs.py                           # report only
  python3 build_customfuncs.py --check                   # exit 1 on any drift
  python3 build_customfuncs.py --write                   # update data/scrpatches.json
  python3 build_customfuncs.py --check \
      --target ../Xenvious/Xenvious/OfflineData/legacy/scrpatches.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import versions  # noqa: E402
from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.natives import NativeResolver  # noqa: E402
from scrasm.repair import find_anchor  # noqa: E402
from scrasm.asm import assemble_text, AsmError  # noqa: E402

DATA = ROOT / "data" / "scrpatches.json"
DISASM = ROOT / "disasm"
SRC = ROOT / "scrasm" / "customfuncs" / "src"


def _hex(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b) + " "


def is_injection(patch: dict) -> bool:
    """An injected function body, as opposed to a call-site rewrite."""
    b = patch.get("bytes_to_patch", "")
    return (patch.get("category") == "customfuncs" and "{" not in b
            and b.replace(" ", "").upper().startswith("2D"))


def first_diff(a: bytes, b: bytes) -> str:
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return f"byte {i} (0x{i:X}): {a[i]:02X} -> {b[i]:02X}"
    if len(a) != len(b):
        return f"length {len(a)} -> {len(b)}"
    return ""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Assemble the customfuncs sources into scrpatches.json.")
    ap.add_argument("--build", help="Build to assemble against "
                                    "(default: oldest folder in disasm/).")
    ap.add_argument("--write", action="store_true",
                    help="Write the assembled payloads back to --target.")
    ap.add_argument("--check", action="store_true",
                    help="Exit non-zero if a source and its payload differ.")
    ap.add_argument("--target", type=Path, default=DATA,
                    help="scrpatches.json to check/update (default: data/scrpatches.json). "
                         "Point this at a deployed OfflineData copy to verify what actually "
                         "ships, not just the source-of-truth file.")
    args = ap.parse_args()
    target = args.target

    builds = versions.list_versions(DISASM)
    if not builds:
        raise SystemExit("no build folders in disasm/ -- fetch one first")
    build = versions.resolve(DISASM, args.build) if args.build else builds[0]
    print(f"[versions] target build: {build}")
    if target != DATA:
        print(f"[target] {target}")

    patches = json.loads(target.read_text())
    by_script: dict[str, list[int]] = {}
    for i, p in enumerate(patches):
        if is_injection(p):
            by_script.setdefault(p["script_name"], []).append(i)

    sources = sorted(SRC.glob("*.ysa"))
    if not sources:
        raise SystemExit(f"no sources in {SRC} -- run gen_customfuncs_src.py first")

    changed, drift, failed = [], [], []
    for src in sources:
        script = src.stem
        idxs = by_script.get(script, [])
        if len(idxs) != 1:
            print(f"!! {script}: expected 1 injected payload, found {len(idxs)}")
            failed.append(script)
            continue
        of = DISASM / build / f"{script}.ysc.full"
        if not of.exists():
            print(f"skip {script}: missing {of}")
            failed.append(script)
            continue
        full = YscFull.parse(of)
        anchors = find_anchor(full.code)
        if len(anchors) != 1:
            print(f"!! {script}: anchor not unique ({len(anchors)} hits)")
            failed.append(script)
            continue
        base = anchors[0]
        resolver = NativeResolver.from_full(full)
        try:
            out = assemble_text(src.read_text(), base=base,
                                natives=resolver.index_of_name())
        except AsmError as exc:
            print(f"!! {script}: {exc}")
            failed.append(script)
            continue

        p = patches[idxs[0]]
        old = bytes.fromhex(p["bytes_to_patch"].replace(" ", ""))
        if out == old:
            print(f"ok   {script}: {len(out)} bytes, base 0x{base:X}, unchanged")
            continue
        drift.append(script)
        print(f"DIFF {script}: {len(old)} -> {len(out)} bytes, base 0x{base:X}"
              f"  [{first_diff(old, out)}]")
        if args.write:
            p["bytes_to_patch"] = _hex(out)
            changed.append(script)

    if args.write and changed:
        target.write_text(json.dumps(patches, indent=4) + "\n")
        print(f"\nwrote {target}  ({len(changed)} payload(s) updated)")
    elif drift and not args.write:
        print(f"\n{len(drift)} payload(s) differ from their source "
              f"-- re-run with --write to apply")

    if failed:
        return 2
    if args.check and drift:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
