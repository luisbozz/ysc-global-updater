#!/usr/bin/env python3
"""Bring scrpatches.json up to date for a new game build, in one pass.

Runs the whole scrpatch side of an update:

  1. check every pattern against the old and the new bytecode
  2. offer a verified fix for each pattern that can be repaired automatically,
     and write the ones you accept into data/scrpatches.json
  3. drop ``derived_for`` markers that have served their purpose
  4. repair the injected customfuncs payloads
  5. list what is left for manual work, and offer to park it with
     enabled=false until someone derives a new pattern

Nothing is written without asking, and data/scrpatches.json is backed up to
data/scrpatches.json.bak before the first change.

    python3 update_patches.py --new 1.73-3889
    python3 update_patches.py --new 1.73-3889 --dry-run
    python3 update_patches.py --new 1.73-3889 --yes
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import doctor  # noqa: E402
import versions  # noqa: E402
from check_patches import DISASM, PATCHES, _load, check, is_healthy  # noqa: E402
from doctor import (  # noqa: E402
    ARROW, BAD_MARK, OK_MARK, PROPOSAL_AUTO, PROPOSAL_MANUAL, PROPOSAL_OK,
    PROPOSAL_REDERIVED, c, hexdump_pattern, propose, status_colour,
)

README = "scrpatches/README.md"


def rule(title: str, width: int = 74) -> str:
    text = f" {title} "
    return doctor.BAR * 3 + text + doctor.BAR * max(3, width - len(text) - 3)


def ask(question: str, auto_yes: bool) -> str:
    """-> 'y', 'n' or 'a' (yes to all). Non-interactive input counts as no."""
    if auto_yes:
        return "a"
    if not sys.stdin.isatty():
        return "n"
    while True:
        answer = input(f"      {question} [j/n/a] ").strip().lower()
        if answer in ("j", "y", "ja", "yes"):
            return "y"
        if answer in ("n", "nein", "no", ""):
            return "n"
        if answer in ("a", "alle", "all"):
            return "a"


def obsolete_markers(patches: list, old_build: str, new_build: str) -> list:
    """``derived_for`` entries that have served their purpose.

    The marker exists to stop a pattern written against a new instruction shape
    from being judged against the old build it can never match. Once that build
    has itself become the *old* one, the pattern matches it normally and the
    ordinary rules classify it correctly - the marker is then a stale note that
    only invites confusion on the update after this one.

    Removing it is safe exactly when the pattern does hit the old build once,
    which is the same condition under which the normal rules apply. So require
    that, rather than trusting the build label alone."""
    out = []
    for p in patches:
        owners = [(None, p)] + [(v.get("id"), v) for v in (p.get("values") or [])]
        for vid, entry in owners:
            marker = entry.get("derived_for")
            if not marker or marker == new_build:
                continue
            pattern = entry.get("pattern")
            if not pattern:
                continue
            old = _load(p["script_name"], old_build)
            if len(doctor.hits(old, pattern)) != 1:
                continue        # still cannot be judged normally; keep the marker
            label = p.get("patch_name", "?")
            if vid is not None:
                label += f" values#{vid}"
            out.append((entry, label, p.get("script_name", "?"), marker))
    return out


def apply_proposal(entry: dict, prop: dict) -> None:
    """Write a proposal into one patch entry, in place."""
    entry["pattern"] = prop["pattern"]
    if prop["derived_for"]:
        entry["derived_for"] = prop["derived_for"]
    if prop["nbytes"]:
        entry["bytes_to_patch"] = " ".join(["00"] * prop["nbytes"])


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", help="New build. Default: newest folder in disasm/.")
    ap.add_argument("--old", help="Old build. Default: the build right before --new.")
    ap.add_argument("--patches", default=str(PATCHES))
    ap.add_argument("--dry-run", action="store_true",
                    help="Show everything, write nothing.")
    ap.add_argument("--yes", action="store_true",
                    help="Accept every verified proposal without asking.")
    ap.add_argument("--no-payloads", action="store_true",
                    help="Skip the customfuncs payload repair (step 3).")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    args = ap.parse_args()

    doctor._USE_COLOR = (args.color == "always" or
                         (args.color == "auto" and sys.stdout.isatty()
                          and not os.environ.get("NO_COLOR")))

    new_build = versions.resolve(DISASM, args.new)
    old_build = (versions.resolve(DISASM, args.old) if args.old
                 else versions.previous(DISASM, new_build))
    if not old_build:
        raise SystemExit("no earlier build in disasm/ -- pass --old <build>")

    path = pathlib.Path(args.patches)
    patches = json.loads(path.read_text(encoding="utf-8"))

    print(f"\n  {c('scrpatches update', 'bold', 'blue')}   "
          f"{c(old_build, 'cyan')} {ARROW} {c(new_build, 'cyan')}")
    if args.dry_run:
        print(f"  {c('DRY RUN - es wird nichts geschrieben', 'yellow', 'bold')}")

    # -- 1. check ----------------------------------------------------------
    print("\n" + c(rule("1. Pruefen"), "bold", "blue"))
    results = check(patches, old_build, new_build)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    for status in ("OK", "REDERIVED", "BROKEN", "AMBIG_NEW", "AMBIG_OLD", "NOT_IN_OLD"):
        if counts.get(status):
            print(f"  {c(f'{status:11}', *status_colour(status))}: {counts[status]:3d}")
    print(f"  {'TOTAL':11}: {len(results):3d}")

    proposals = [(p, propose(p, old_build, new_build))
                 for p in patches if p.get("pattern")]
    fixable = [(p, pr) for p, pr in proposals
               if pr["kind"] in (PROPOSAL_AUTO, PROPOSAL_REDERIVED)]
    manual = [(p, pr) for p, pr in proposals if pr["kind"] == PROPOSAL_MANUAL]

    # -- 2. automatic fixes ------------------------------------------------
    print("\n" + c(rule("2. Automatisch reparierbar"), "bold", "blue"))
    written = cleaned = parked = 0
    backed_up = False
    if not fixable:
        print(f"  {c('nichts', 'grey')}")
    else:
        print(f"  {len(fixable)} Patch(es). Jeder Vorschlag ist gegen beide Builds"
              f" geprueft.\n")
        answer = "a" if args.yes else None
        for entry, prop in fixable:
            kind = ("Operand gewandert" if prop["kind"] == PROPOSAL_AUTO
                    else "neu abgeleitet")
            print(f"  {c(prop['patch'], 'bold')} {c('·', 'grey')} {prop['script']}")
            print(f"    {c(prop['reason'], 'grey')}")
            print(f"    alt  {hexdump_pattern(entry['pattern'])}")
            print(f"    neu  {hexdump_pattern(prop['pattern'])}")
            if prop["nbytes"]:
                old_n = len(entry.get("bytes_to_patch", "").split())
                new_n = prop["nbytes"]
                if new_n != old_n:
                    note = f"bytes_to_patch {old_n} {ARROW} {new_n}"
                    print(f"    {c(note, 'yellow')}")
            if prop["derived_for"]:
                note = "derived_for: " + prop["derived_for"]
                print(f"    {c(note, 'yellow')}")

            if args.dry_run:
                print(f"    {c('(dry run)', 'grey')}\n")
                continue
            if answer != "a":
                answer = ask("uebernehmen?", args.yes)
            if answer in ("y", "a"):
                if not backed_up:
                    shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
                    print(f"    {c(f'Backup: {path.name}.bak', 'grey')}")
                    backed_up = True
                apply_proposal(entry, prop)
                written += 1
                print(f"    {c(OK_MARK + ' uebernommen', 'green', 'bold')}\n")
            else:
                print(f"    {c('uebersprungen', 'grey')}\n")

        if written:
            path.write_text(json.dumps(patches, indent=4), encoding="utf-8")
            print(f"  {c(f'{written} Patch(es) in {path} geschrieben.', 'green')}")

    # -- 3. drop markers that have done their job -------------------------
    print("\n" + c(rule("3. Aufraeumen"), "bold", "blue"))
    obsolete = obsolete_markers(patches, old_build, new_build)
    if not obsolete:
        print(f"  {c('nichts', 'grey')}")
    else:
        print(f"  {len(obsolete)} veraltete(r) derived_for-Marker. Das Pattern trifft")
        print(f"  {c(old_build, 'cyan')} inzwischen normal, der Marker wird nicht mehr"
              f" gebraucht.\n")
        for _entry, label, script, marker in obsolete:
            print(f"    {c(label, 'bold')} {c('·', 'grey')} {script}"
                  f"   {c('derived_for: ' + marker, 'grey')}")
        if args.dry_run:
            print(f"\n  {c('(dry run)', 'grey')}")
        elif ask("entfernen?", args.yes) in ("y", "a"):
            if not backed_up:
                shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
                print(f"    {c(f'Backup: {path.name}.bak', 'grey')}")
                backed_up = True
            for entry, _label, _script, _marker in obsolete:
                entry.pop("derived_for", None)
            path.write_text(json.dumps(patches, indent=4), encoding="utf-8")
            cleaned = len(obsolete)
            print(f"\n  {c(f'{cleaned} Marker entfernt.', 'green')}")
        else:
            print(f"\n  {c('uebersprungen', 'grey')}")

    # -- 4. injected payloads ---------------------------------------------
    print("\n" + c(rule("4. Injizierte Payloads"), "bold", "blue"))
    if args.no_payloads:
        print(f"  {c('uebersprungen (--no-payloads)', 'grey')}")
    elif args.dry_run:
        print(f"  {c('uebersprungen (dry run)', 'grey')}")
    else:
        proc = subprocess.run(
            [sys.executable, str(ROOT / "repair_scrpatches.py"),
             "--new", new_build, "--old", old_build],
            capture_output=True, text=True)
        tail = [ln for ln in proc.stdout.splitlines()
                if ln.startswith(("injected payloads", "wrote", "skipped"))
                or "!!" in ln]
        for ln in tail:
            colour = ("red",) if "!!" in ln else ("grey",)
            print(f"  {c(ln.strip(), *colour)}")
        if proc.returncode:
            print(f"  {c('repair_scrpatches.py fehlgeschlagen', 'red', 'bold')}")
            print(f"  {proc.stderr.strip()[:300]}")

    # -- 4. manual work ----------------------------------------------------
    print("\n" + c(rule("5. Handarbeit noetig"), "bold", "blue"))
    if not manual:
        print(f"  {c('nichts', 'green')}")
    else:
        for _entry, prop in manual:
            print(f"  {c(BAD_MARK, 'red', 'bold')} {c(prop['patch'], 'bold')} "
                  f"{c('·', 'grey')} {prop['script']}")
            print(f"      {c(prop['reason'], 'yellow')}")
        cmd = f'python3 scrpatches/doctor.py --new {new_build} --patch "<name>"'
        print("\n  Diagnose pro Patch:")
        print(f"    {c(cmd, 'cyan')}")
        print("  Vorgehen mit dem GTA5 Script Decompiler:")
        print(f"    {c(README, 'cyan')} {c('-> Repairing a BROKEN pattern', 'grey')}")

        # A broken patch is applied blind: the pattern no longer matches, so
        # Xenvious writes nothing - or, worse, writes somewhere else if the
        # pattern became ambiguous. Parking it with enabled=false is the safe
        # state until someone derives a new pattern, and it keeps the old one
        # around to derive from.
        print(f"\n  Bis dahin abschalten? {c('enabled=false', 'cyan')} - Xenvious"
              f" ueberspringt den Patch,")
        print(f"  das Pattern bleibt fuer die spaetere Ableitung erhalten.")
        if args.dry_run:
            print(f"  {c('(dry run)', 'grey')}")
        else:
            answer = "a" if args.yes else None
            for entry, prop in manual:
                if entry.get("enabled") is False:
                    continue
                if answer != "a":
                    print(f"\n    {c(prop['patch'], 'bold')} {c('·', 'grey')}"
                          f" {prop['script']}")
                    answer = ask("abschalten?", args.yes)
                if answer in ("y", "a"):
                    if not backed_up:
                        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))
                        print(f"      {c(f'Backup: {path.name}.bak', 'grey')}")
                        backed_up = True
                    entry["enabled"] = False
                    parked += 1
            if parked:
                path.write_text(json.dumps(patches, indent=4), encoding="utf-8")
                print(f"\n  {c(f'{parked} Patch(es) abgeschaltet.', 'yellow')}")

    # -- summary -----------------------------------------------------------
    print("\n" + c(rule("Ergebnis"), "bold", "blue"))
    healthy = counts.get("OK", 0) + counts.get("REDERIVED", 0)
    parts = [c(f"{healthy} in Ordnung", "green")]
    if counts.get("DISABLED"):
        parts.append(c(f"{counts['DISABLED']} abgeschaltet", "grey"))
    parts.append(c(f"{len(fixable)} automatisch reparierbar", "yellow"))
    parts.append(c(f"{len(manual)} Handarbeit", "red" if manual else "green"))
    print(f"  {len(results)} Patches: " + ", ".join(parts))
    if written or cleaned or parked:
        done = []
        if written:
            done.append(f"{written} uebernommen")
        if cleaned:
            done.append(f"{cleaned} Marker entfernt")
        if parked:
            done.append(f"{parked} abgeschaltet")
        print(f"  {c(', '.join(done), 'green', 'bold')} "
              f"{c(f'(Backup: {path.name}.bak)', 'grey')}")
    print(f"\n  Danach pruefen:")
    print(f"    {c('python3 scrpatches/check_patches.py --new ' + new_build, 'cyan')}")
    print(f"  Ausliefern, wenn alles sauber ist:")
    print(f"    {c('scrpatches/reports/scrpatches.repaired.json', 'cyan')}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
