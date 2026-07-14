#!/usr/bin/env python3
"""Validierungs-Harness: prueft das Migrationsverfahren gegen eine Ground-Truth.

Nimmt eine Quell-offsets.ini (z. B. v1.71), die zugehoerigen ALTEN Scripts und
die NEUEN Ziel-Scripts, migriert per semantischem Pfad und meldet:
- Coverage: wie viele Global-Offsets ueberhaupt per Pfad aufloesbar sind,
- Migrations-Statistik,
- optional (--expect): Uebereinstimmung mit einer bekannten Ziel-offsets.ini
  (z. B. eine manuell gepflegte), kanonisch verglichen.

Reines Read-only-Werkzeug; schreibt nichts ausser optional einem JSON-Report.
Exit-Code 0 wenn (ohne --expect) Coverage >= --min-coverage bzw. (mit --expect)
Agreement >= --min-agreement, sonst 1.
"""
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.extract_globals import canonicalize_global  # noqa: E402
from tools.migrate_offsets import OFFSET_RE, build_maps, migrate_text  # noqa: E402


def load_offsets(text: str) -> dict:
    out = {}
    for line in text.splitlines():
        m = OFFSET_RE.match(line)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--offsets", required=True, help="Quell-offsets.ini (Ground-Truth-Start, z. B. v1.71).")
    p.add_argument("--old-dir", required=True, help="Scripts passend zur Quell-offsets.ini.")
    p.add_argument("--new-dir", required=True, help="Ziel-Scripts.")
    p.add_argument("--keep-list", default="reports/sources.keep.txt")
    p.add_argument("--expect", help="Bekannte Ziel-offsets.ini zum Abgleich (optional).")
    p.add_argument("--migrated", help="Bereits migrierte offsets.ini (z. B. kombinierte semantic+infer Ausgabe). Wenn gesetzt, wird NICHT intern neu migriert.")
    p.add_argument("--report-json")
    p.add_argument("--min-coverage", type=float, default=0.0)
    p.add_argument("--min-agreement", type=float, default=0.0)
    args = p.parse_args()

    root = pathlib.Path(__file__).resolve().parents[1]

    def rel(x: str) -> pathlib.Path:
        return pathlib.Path(x) if pathlib.Path(x).is_absolute() else root / x

    keep = [ln.strip() for ln in rel(args.keep_list).read_text(encoding="utf-8").splitlines() if ln.strip()]
    _, old_rev = build_maps(rel(args.old_dir), keep)
    new_fwd, _ = build_maps(rel(args.new_dir), keep)

    src_text = rel(args.offsets).read_text(encoding="utf-8")
    src = load_offsets(src_text)
    if args.migrated:
        migrated_text = rel(args.migrated).read_text(encoding="utf-8")
        migrated_map = load_offsets(migrated_text)
        # Statistik aus dem Vergleich Quelle->migriert rekonstruieren.
        stats = {"migrated": 0, "unchanged": 0}
        for n, v in src.items():
            if not v.startswith("Global_"):
                continue
            mv = migrated_map.get(n, v)
            if canonicalize_global(mv) != canonicalize_global(v):
                stats["migrated"] += 1
            else:
                stats["unchanged"] += 1
        changes = []
    else:
        migrated_text, stats, changes = migrate_text(src_text, old_rev, new_fwd)

    global_offsets = [n for n, v in src.items() if v.startswith("Global_")]
    resolvable = stats.get("migrated", 0) + stats.get("unchanged", 0)
    coverage = resolvable / max(len(global_offsets), 1)

    print("=== VALIDIERUNG ===")
    print(f"Global-Offsets in Quelle : {len(global_offsets)}")
    print(f"per Pfad aufloesbar      : {resolvable}  ({coverage:.0%} coverage)")
    print(f"  davon migriert         : {stats.get('migrated', 0)}")
    print(f"  davon unveraendert     : {stats.get('unchanged', 0)}")
    print(f"nicht aufloesbar         : {stats.get('not_found_in_old', 0)} (Extractor-Luecke)")
    print(f"mehrdeutig (geflaggt)    : {stats.get('ambiguous_path', 0)}")

    result = {"coverage": coverage, "stats": stats}
    ok = coverage >= args.min_coverage

    if args.expect:
        exp = load_offsets(rel(args.expect).read_text(encoding="utf-8"))
        migrated = load_offsets(migrated_text)
        src_changed = {n for n in exp if n in src and exp[n] != src[n]}
        mine_changed = {n for n in migrated if n in src and migrated[n] != src[n]}
        both = src_changed & mine_changed
        agree = sum(1 for n in both if canonicalize_global(migrated[n]) == canonicalize_global(exp[n]))
        disagree = sorted(n for n in both if canonicalize_global(migrated[n]) != canonicalize_global(exp[n]))
        agreement = agree / max(len(both), 1)
        print(f"--- Abgleich mit {args.expect} ---")
        print(f"beidseitig geaendert     : {len(both)}")
        print(f"kanonisch identisch      : {agree}  ({agreement:.0%} agreement)")
        print(f"echte Abweichungen       : {len(disagree)}")

        # Pro-Familie-Aufschluesselung (Praefix vor erstem '_' nach OFFSET_).
        from collections import defaultdict
        fam = defaultdict(lambda: [0, 0])  # [both, agree]
        for n in both:
            pre = n[len("OFFSET_"):].split("_")[0]
            fam[pre][0] += 1
            if canonicalize_global(migrated[n]) == canonicalize_global(exp[n]):
                fam[pre][1] += 1
        print("    Familie      beid  ok   quote")
        for pre in sorted(fam, key=lambda k: -fam[k][0]):
            b, a = fam[pre]
            if b >= 3:
                print(f"    {pre:12} {b:>4} {a:>4}  {100*a//max(b,1):>3}%")
        result["agreement"] = agreement
        result["disagreements"] = disagree
        result["per_family"] = {k: {"both": v[0], "agree": v[1]} for k, v in fam.items()}
        ok = ok and (agreement >= args.min_agreement)

    if args.report_json:
        rp = rel(args.report_json)
        rp.parent.mkdir(parents=True, exist_ok=True)
        rp.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        print(f"report: {rp}")

    print("RESULT:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
