#!/usr/bin/env python3
"""One-command pipeline for the ysc-global-updater.

Chain: pick the relevant scripts from the fresh dump (select_sources) -> migrate
offsets.ini from the old to the new script version (migrate_offsets) -> optional
validation against a reference (validate) -> a formatted summary of what was
migrated and what still needs a manual look.

Safe: only writes into reports/ and never touches the original offsets.ini.
"""
import argparse
import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
OFFSET_RE = re.compile(r'^(OFFSET_[A-Za-z0-9_]+)\s*=\s*"([^"]+)"')


def run(step: str, args: list[str]) -> None:
    print(f"\n=== {step} ===", flush=True)
    res = subprocess.run([sys.executable, str(ROOT / "tools" / args[0])] + args[1:], cwd=ROOT)
    if res.returncode != 0:
        print(f"[ABORT] {step} exited with {res.returncode}", file=sys.stderr)
        raise SystemExit(res.returncode)


def _skeleton(value: str) -> str:
    # strip array indices AND stride comments -> version-independent field path
    return re.sub(r"\[[^\]]*\]", "[]", value).replace(" ", "")


def _new_skeletons(new_dir: pathlib.Path) -> set:
    """All Global_ field-paths present in the new scripts (index/stride stripped)."""
    tok = re.compile(r"Global_\d+(?:\.f_\d+|\[[^\]]+\])+")
    found = set()
    for f in new_dir.glob("*.c"):
        for m in tok.finditer(f.read_text(encoding="utf-8", errors="ignore")):
            found.add(_skeleton(m.group(0)))
    return found


def print_summary(report_path: pathlib.Path, out_path: pathlib.Path, new_dir: pathlib.Path) -> None:
    data = json.loads(report_path.read_text(encoding="utf-8"))
    stats = data.get("stats", {})
    unresolved = data.get("unresolved", [])
    migrated = sum(v for k, v in stats.items() if k.startswith("migrated"))
    unchanged = stats.get("unchanged", 0)
    static = stats.get("skipped_non_global", 0)

    # Split the unresolved offsets: if the (old) value still exists as a field-path
    # in the new scripts, it most likely did not move -> "stable". Otherwise it
    # changed and we could not resolve it -> real REVIEW.
    skel = _new_skeletons(new_dir)
    stable, review = [], []
    for e in unresolved:
        (stable if _skeleton(e["value"]) in skel else review).append(e)

    bar = "=" * 72
    print("\n" + bar)
    print("  MIGRATION SUMMARY")
    print(bar)
    print(f"  Result          : {out_path}   (your original offsets.ini is untouched)")
    print("-" * 72)
    print(f"  [ OK ]     migrated automatically  : {migrated}")
    print(f"  [ OK ]     unchanged / still valid : {unchanged + len(stable)}")
    print(f"  [ -- ]     static constants        : {static}   (hex / coords, not script offsets)")
    print(f"  [REVIEW]   need a manual look      : {len(review)}")
    if review:
        print("-" * 72)
        print("  These offsets changed but could NOT be resolved automatically.")
        print("  Open the result file and set them by hand:")
        for e in sorted(review, key=lambda x: x["offset"]):
            print(f"      {e['offset']:<34} old = {e['value']}")
    print(bar)
    if not review:
        print("  Everything resolved -- nothing left to do by hand.")
    else:
        print(f"  {migrated} migrated automatically, {len(review)} need your attention.")
    print(bar + "\n")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--old-dir", required=True, help="Decompiled scripts that match the CURRENT offsets.ini.")
    p.add_argument("--new-dir", required=True, help="Freshly decompiled scripts of the NEW game version.")
    p.add_argument("--offsets", default="offsets.ini", help="Source offsets.ini (never modified).")
    p.add_argument("--expect", help="A known-good target offsets.ini to score against (optional).")
    p.add_argument("--keep-list", default="reports/sources.keep.txt")
    p.add_argument("--out", default="reports/offsets.migrated.ini")
    p.add_argument("--infer", action="store_true",
                   help="Also run the slow infer context-matcher for the last gaps (optional, minutes).")
    args = p.parse_args()

    def rel(x: str) -> pathlib.Path:
        return pathlib.Path(x) if pathlib.Path(x).is_absolute() else ROOT / x

    run("STEP 1/3  select the relevant creator scripts", [
        "select_sources.py", "--source-dir", args.new_dir,
        "--keep-list", args.keep_list, "--summary-only",
    ])

    migrate_args = [
        "migrate_offsets.py", "--ini", args.offsets,
        "--old-dir", args.old_dir, "--new-dir", args.new_dir,
        "--keep-list", args.keep_list, "--out", args.out,
        "--report-json", "reports/migrate-report.json", "--structural",
    ]
    if args.infer:
        migrate_args.append("--fallback")
    run("STEP 2/3  migrate offsets.ini (old -> new)", migrate_args)

    if args.expect:
        run("STEP 3/3  validate against the reference", [
            "validate.py", "--offsets", args.offsets,
            "--old-dir", args.old_dir, "--new-dir", args.new_dir,
            "--keep-list", args.keep_list, "--expect", args.expect,
            "--migrated", args.out, "--report-json", "reports/validate.json",
        ])
    else:
        print("\n(STEP 3/3 validation skipped -- no --expect given)")

    print_summary(rel("reports/migrate-report.json"), rel(args.out), rel(args.new_dir))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
