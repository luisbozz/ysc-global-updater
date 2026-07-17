#!/usr/bin/env python3
"""Repair the injected ``customfuncs`` payloads in scrpatches.json for the new
game version and write the results to ``reports/`` (never touching ``data/``).

For each injected payload it updates native indices, relocates internal calls
to the new injection base, and re-resolves external R* call addresses via
function fingerprinting. It emits:

  * reports/scrpatches.repaired.json  -- a full copy of scrpatches.json with the
    customfuncs ``bytes_to_patch`` updated for the new version
  * reports/customfuncs_repair.txt    -- a per-payload report (what changed and
    what still needs manual review)

Usage:  python3 repair_scrpatches.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import versions  # noqa: E402  (repo-root helper: disasm/<build> resolution)
from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.repair import ScriptContext, find_anchor  # noqa: E402
from scrasm.disasm import parse_hex  # noqa: E402

DATA = ROOT / "data" / "scrpatches.json"
DISASM = ROOT / "disasm"
REPORTS = ROOT / "reports"


def _hex(b: bytes) -> str:
    return " ".join(f"{x:02X}" for x in b)


def _is_injected(p: dict) -> bool:
    b = p.get("bytes_to_patch", "")
    return (p.get("category") == "customfuncs" and "{" not in b
            and "?" not in b and b.replace(" ", "").upper().startswith("2D"))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Repair injected customfuncs payloads for a new game build.")
    ap.add_argument("--new", help="New build (e.g. 1.73-3889, 1.73, or 'latest'). "
                                  "Default: newest folder in disasm/.")
    ap.add_argument("--old", help="Old build the payloads were built for. "
                                  "Default: the build right before --new.")
    args = ap.parse_args()

    new_build = versions.resolve(DISASM, args.new)
    old_build = (versions.resolve(DISASM, args.old) if args.old
                 else versions.previous(DISASM, new_build))
    if not old_build:
        raise SystemExit("no earlier build in disasm/ -- pass --old <build>")
    print(f"[versions] repairing {old_build} -> {new_build}")

    patches = json.loads(DATA.read_text())
    scripts = sorted({p["script_name"] for p in patches if _is_injected(p)})

    contexts: dict[str, ScriptContext] = {}
    notes: dict[str, str] = {}
    for s in scripts:
        of = DISASM / old_build / f"{s}.ysc.full"
        nf = DISASM / new_build / f"{s}.ysc.full"
        if not (of.exists() and nf.exists()):
            notes[s] = f"missing dumps ({old_build}/ or {new_build}/)"
            continue
        ctx = ScriptContext.build(YscFull.parse(of), YscFull.parse(nf))
        if ctx.old_base is None or ctx.new_base is None:
            notes[s] = "sacrificial anchor not unique; cannot locate base"
            continue
        contexts[s] = ctx

    out_patches: list[dict] = []
    report: list[str] = []
    n_repaired = n_review = 0

    for p in patches:
        q = dict(p)
        if _is_injected(p) and p["script_name"] in contexts:
            ctx = contexts[p["script_name"]]
            raw = parse_hex(p["bytes_to_patch"])
            new_bytes, rep = ctx.repair(raw, p["script_name"])
            q["bytes_to_patch"] = _hex(new_bytes)
            n_repaired += 1
            if not rep.ok:
                n_review += 1

            report.append(f"### {p['patch_name']}  [{p['script_name']}]")
            report.append(f"    inject base 0x{rep.old_base:X} -> 0x{rep.new_base:X}   "
                          f"({len(raw)} bytes, {'OK' if rep.ok else 'NEEDS REVIEW'})")
            if rep.native_updated:
                report.append(f"    natives updated ({len(rep.native_updated)}):")
                for name, o, n in rep.native_updated:
                    report.append(f"        {name}: {o} -> {n}")
            report.append(f"    natives unchanged: {rep.native_unchanged}")
            report.append(f"    internal calls relocated: {rep.internal_calls}")
            ext = sorted(set(rep.external_resolved))
            if ext:
                report.append(f"    external calls resolved ({len(ext)}):")
                for o, n, c in ext:
                    report.append(f"        0x{o:06X} -> 0x{n:06X}  [{c}]")
            if rep.needs_review:
                report.append(f"    !! external calls UNRESOLVED (manual review): "
                              + ", ".join(f"0x{a:06X}" for a in rep.needs_review))
            if rep.native_missing:
                report.append(f"    !! natives missing from new table: "
                              + ", ".join(rep.native_missing))
            if rep.stride_updated:
                report.append(f"    embedded strides updated ({len(rep.stride_updated)}):")
                for g, io, o, n in sorted(set(rep.stride_updated)):
                    report.append(f"        global 0x{g:X} +{io}: stride {o} -> {n}")
            if rep.stride_review:
                report.append("    !! embedded strides needing review: "
                              + ", ".join(f"0x{g:X}+{io}={v}"
                                          for g, io, v in sorted(set(rep.stride_review))))
            if rep.globals_seen:
                report.append("    globals referenced: "
                              + ", ".join(f"0x{g:X}" for g in sorted(rep.globals_seen)))
            report.append("")
        out_patches.append(q)

    REPORTS.mkdir(exist_ok=True)
    (REPORTS / "scrpatches.repaired.json").write_text(json.dumps(out_patches, indent=4))

    header = [
        "customfuncs payload repair report",
        "=" * 60,
        f"build: {old_build} -> {new_build}",
        f"injected payloads repaired: {n_repaired}   need review: {n_review}",
        "",
    ]
    for s, why in notes.items():
        header.append(f"skipped script {s}: {why}")
    if notes:
        header.append("")
    (REPORTS / "customfuncs_repair.txt").write_text("\n".join(header + report))

    print("\n".join(header + report))
    print(f"wrote {REPORTS/'scrpatches.repaired.json'}")
    print(f"wrote {REPORTS/'customfuncs_repair.txt'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
