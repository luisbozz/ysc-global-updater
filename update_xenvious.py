#!/usr/bin/env python3
"""Bring Xenvious up to date for a new GTA build, in one pass.

Runs the whole update chain and asks before every step that writes something:

  1. fetch the decompiled scripts and the decrypted dumps for the new build
  2. migrate offsets.ini from the old build to the new one
  3. check and repair the scrpatch patterns
  4. repair the injected customfuncs payloads
  5. deploy offsets.ini and scrpatches.json into Xenvious/OfflineData
  6. remind you to rebuild, because OfflineData is compiled into the .exe

GTA V ships as two separate games. ``--variant`` picks which one is updated;
their data lives side by side in OfflineData/legacy and OfflineData/enhanced and
is never mixed, because the two are compiled separately and share no addresses.

Enhanced has no decrypted bytecode dumps published anywhere, so steps 3 and 4
cannot run for it. The run says so and continues with the offsets, which are
derived from the decompiled source and work for both.

Every step first reports what it finds. A step whose output is already newer
than its inputs is reported as up to date and is not offered again. Nothing
outside reports/ is written without a yes, and the deploy step refuses to run
while any pattern is still broken.

    python3 update_xenvious.py --new 1.74-4012
    python3 update_xenvious.py --variant enhanced --new 1.74-1200
    python3 update_xenvious.py --variant both --new 1.74-4012 --dry-run

Rollback is git: the deploy step writes into a clean checkout of Xenvious, so
``git checkout -- Xenvious/OfflineData`` undoes it. It writes no .bak files.
"""
from __future__ import annotations

import argparse
import difflib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scrpatches"))

import versions  # noqa: E402
import doctor  # noqa: E402
from check_patches import DISASM, PATCHES, check, is_healthy  # noqa: E402
from doctor import ARROW, BAD_MARK, OK_MARK, c, status_colour  # noqa: E402
from tools.deploy_offsets import merge as merge_offsets  # noqa: E402
from versions import ENHANCED, LEGACY  # noqa: E402

SCRIPTS = ROOT / "scripts"
REPORTS = ROOT / "reports"
SOURCE_OFFSETS = ROOT / "offsets.ini"
REPAIRED = ROOT / "scrpatches" / "reports" / "scrpatches.repaired.json"

# What fetch_update.sh downloads: 8 decompiled scripts for the offset side,
# 6 decrypted dumps for the scrpatch side.
C_SCRIPTS = (
    "fm_capture_creator", "fm_deathmatch_creator", "fm_lts_creator",
    "fm_race_creator", "fm_survival_creator", "fmmc_launcher",
    "public_mission_creator", "tuneables_processing",
)
FULL_SCRIPTS = (
    "fm_capture_creator", "fm_deathmatch_creator", "fm_lts_creator",
    "fm_race_creator", "fm_survival_creator", "fmmc_launcher",
)

# The rollback this script documents is `git checkout -- Xenvious/OfflineData`,
# which touches nothing outside that folder. So the only uncommitted work the
# deploy can cost anyone is uncommitted work *in* the files it overwrites --
# everything else in the checkout is none of its business, and warning about it
# only trains people to ignore the warning.
ROLLBACK_SCOPE = "Xenvious/OfflineData/"


def in_rollback_scope(path: str) -> bool:
    return path.startswith(ROLLBACK_SCOPE)

README = "scrpatches/README.md"


def migrated_ini(variant: str) -> pathlib.Path:
    return REPORTS / ("offsets.migrated.ini" if variant == LEGACY
                      else f"offsets.migrated.{variant}.ini")


def migrate_report(variant: str) -> pathlib.Path:
    return REPORTS / ("migrate-report.json" if variant == LEGACY
                      else f"migrate-report.{variant}.json")


# ---------------------------------------------------------------- output ----

def rule(title: str, width: int = 74) -> str:
    text = f" {title} "
    return doctor.BAR * 3 + text + doctor.BAR * max(3, width - len(text) - 3)


def head(title: str) -> None:
    print("\n" + c(rule(title), "bold", "blue"))


def ok(text: str) -> None:
    print(f"  {c(OK_MARK, 'green')} {text}")


def bad(text: str) -> None:
    print(f"  {c(BAD_MARK, 'red')} {text}")


def note(text: str) -> None:
    print(f"    {c(text, 'grey')}")


def ask(question: str, auto_yes: bool) -> bool:
    """Ask a yes/no question. Non-interactive input counts as no."""
    if auto_yes:
        print(f"  {question} [j/n] j")
        return True
    if not sys.stdin.isatty():
        return False
    while True:
        answer = input(f"  {question} [j/n] ").strip().lower()
        if answer in ("j", "y", "ja", "yes"):
            return True
        if answer in ("n", "nein", "no", ""):
            return False


def run(cmd: list, cwd: pathlib.Path = ROOT) -> int:
    """Run a child tool with its output going straight to the terminal."""
    print(f"    {c('$ ' + ' '.join(str(x) for x in cmd), 'grey')}\n")
    return subprocess.run([str(x) for x in cmd], cwd=str(cwd)).returncode


def newest_mtime(paths) -> float:
    times = [p.stat().st_mtime for p in paths if p.is_file()]
    return max(times) if times else 0.0


def diff_lines(old: str, new: str) -> tuple:
    """(removed, added) line counts between two texts."""
    removed = added = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0):
        if line.startswith("-") and not line.startswith("---"):
            removed += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
    return removed, added


# ----------------------------------------------------------------- steps ----

def step_fetch(variant: str, new_build: str, ref: str,
               dry_run: bool, auto_yes: bool) -> bool:
    """Download the scripts (and dumps, for Legacy). False = cannot go on."""
    head("1. Dumps holen")
    missing_c = [s for s in C_SCRIPTS if not (SCRIPTS / new_build / f"{s}.c").is_file()]
    missing_full = ([] if variant == ENHANCED else
                    [s for s in FULL_SCRIPTS
                     if not (DISASM / new_build / f"{s}.ysc.full").is_file()])

    if not missing_c and not missing_full:
        dumps = "" if variant == ENHANCED else f", {len(FULL_SCRIPTS)} Dumps"
        ok(f"{new_build}: {len(C_SCRIPTS)} Skripte{dumps} vorhanden")
        return True

    bad(f"{new_build}: {len(missing_c)} Skript(e), {len(missing_full)} Dump(s) fehlen")
    note(f"scripts/{new_build}/")
    if dry_run:
        note("DRY RUN: nicht geholt")
        return False
    cmd = [ROOT / "fetch_update.sh", "--variant", variant,
           new_build.replace("enhanced-", "", 1), ref]
    if not ask(f"Mit {cmd[0].name} holen?", auto_yes):
        bad("Ohne die Skripte kann kein weiterer Schritt laufen.")
        return False
    if run(cmd) != 0:
        bad("fetch_update.sh fehlgeschlagen")
        return False
    return True


def _unresolved_count(variant: str):
    path = migrate_report(variant)
    if not path.is_file():
        return None
    return len(json.loads(path.read_text(encoding="utf-8")).get("unresolved", []))


def _offset_source(variant: str, new_build: str):
    """(source ini, old build) for the offset migration.

    For Legacy this is the repository's own offsets.ini against the previous
    Legacy build. Enhanced has no history of its own on the first run, so it is
    bootstrapped from the freshly migrated Legacy result against the Legacy
    build it was produced for: the two games share their script *content*, so
    that is the closest starting point that exists. Once an Enhanced build has
    been migrated before, the migration runs Enhanced-to-Enhanced like any
    other update."""
    if variant == LEGACY:
        return SOURCE_OFFSETS, versions.previous(SCRIPTS, new_build)
    earlier = versions.previous(SCRIPTS, new_build)
    if earlier:
        return migrated_ini(ENHANCED), earlier
    legacy_build = versions.resolve(SCRIPTS, "latest", variant=LEGACY)
    return migrated_ini(LEGACY), legacy_build


def step_offsets(variant: str, new_build: str, dry_run: bool, auto_yes: bool) -> bool:
    """Migrate offsets.ini to the new build. False = no usable result."""
    head("2. Offsets migrieren")
    out = migrated_ini(variant)
    source, old_build = _offset_source(variant, new_build)
    if not old_build:
        bad(f"kein Vergleichs-Build fuer {new_build} in scripts/")
        return False
    if not source.is_file():
        bad(f"Quelle fehlt: {source.relative_to(ROOT)}")
        if variant == ENHANCED:
            note("Enhanced wird aus dem Legacy-Ergebnis gebootstrappt; "
                 "erst --variant legacy laufen lassen.")
        return False

    inputs = list((SCRIPTS / new_build).glob("*.c")) + [source]
    fresh = out.is_file() and out.stat().st_mtime > newest_mtime(inputs)
    note(f"{source.relative_to(ROOT)}   {old_build} {ARROW} {new_build}")

    if fresh:
        left = _unresolved_count(variant)
        ok(f"{out.relative_to(ROOT)} ist neuer als seine Quellen")
        if left:
            bad(f"{left} Offset(s) unaufgeloest -- siehe {migrate_report(variant).relative_to(ROOT)}")
        elif left == 0:
            ok("0 Offsets unaufgeloest")
        return True

    bad(f"{out.relative_to(ROOT)} fehlt oder ist aelter als seine Quellen")
    if dry_run:
        note("DRY RUN: nicht migriert")
        return out.is_file()
    if not ask("Migration jetzt laufen lassen?", auto_yes):
        return out.is_file()
    if run([sys.executable, ROOT / "tools" / "run_pipeline.py",
            "--new", new_build, "--old", old_build,
            "--offsets", str(source), "--out", str(out),
            "--report-json", str(migrate_report(variant))]) != 0:
        bad("run_pipeline.py fehlgeschlagen")
        return False

    left = _unresolved_count(variant)
    if left:
        bad(f"{left} Offset(s) unaufgeloest -- siehe {migrate_report(variant).relative_to(ROOT)}")
    else:
        ok("0 Offsets unaufgeloest")
    return True


def _health(patches_path: pathlib.Path, old_build: str, new_build: str):
    """(status counts, number of patches needing work) for one patches file."""
    patches = json.loads(patches_path.read_text(encoding="utf-8"))
    results = check(patches, old_build, new_build)
    counts = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts, sum(1 for r in results if not is_healthy(r["status"]))


def _print_health(counts: dict) -> None:
    for status in ("OK", "REDERIVED", "DISABLED", "BROKEN",
                   "AMBIG_NEW", "AMBIG_OLD", "NOT_IN_OLD"):
        if counts.get(status):
            print(f"    {c(f'{status:11}', *status_colour(status))}: {counts[status]:3d}")


def _no_dumps(variant: str) -> None:
    note(f"Fuer {variant} gibt es keine entschluesselten .ysc.full-Dumps.")
    note("Patterns lassen sich damit weder pruefen noch ableiten; die Patches")
    note(f"werden mit enabled=false ausgeliefert. Details: {README}")


def step_patterns(variant: str, old_build, new_build: str,
                  dry_run: bool, auto_yes: bool) -> int:
    """Check and repair the scrpatch patterns. Returns the count still broken."""
    head("3. scrpatches pruefen")
    if variant == ENHANCED:
        ok("uebersprungen")
        _no_dumps(variant)
        return 0
    if not old_build:
        bad(f"kein Vergleichs-Build fuer {new_build} in scrpatches/disasm/")
        return 0

    counts, broken = _health(PATCHES, old_build, new_build)
    _print_health(counts)
    if not broken:
        ok("alle Patterns gesund")
        return 0

    bad(f"{broken} Patch(es) brauchen Arbeit")
    if dry_run:
        note("DRY RUN: update_patches.py nicht gestartet")
        return broken
    if not ask("update_patches.py starten? (fragt selbst nach jeder Aenderung)", auto_yes):
        return broken

    cmd = [sys.executable, ROOT / "scrpatches" / "update_patches.py", "--new", new_build]
    if auto_yes:
        cmd.append("--yes")
    run(cmd)

    counts, broken = _health(PATCHES, old_build, new_build)
    print()
    _print_health(counts)
    if broken:
        bad(f"{broken} Patch(es) weiterhin offen -- Handarbeit, siehe {README}")
    else:
        ok("alle Patterns gesund")
    return broken


def step_payloads(variant: str, new_build: str, dry_run: bool, auto_yes: bool) -> bool:
    """Rebuild the injected customfuncs payloads. False = no usable artifact."""
    head("4. Payloads reparieren")
    if variant == ENHANCED:
        ok("uebersprungen")
        _no_dumps(variant)
        return True

    inputs = [PATCHES] + list((DISASM / new_build).glob("*.ysc.full"))
    fresh = REPAIRED.is_file() and REPAIRED.stat().st_mtime > newest_mtime(inputs)

    if fresh:
        ok(f"{REPAIRED.relative_to(ROOT)} ist aktuell")
        return True

    bad(f"{REPAIRED.relative_to(ROOT)} fehlt oder ist aelter als seine Quellen")
    note("Diese Datei ist das Deploy-Artefakt, nicht data/scrpatches.json:")
    note("nur hier sind die injizierten Payloads auf den neuen Build umgeschrieben.")
    if dry_run:
        note("DRY RUN: nicht repariert")
        return REPAIRED.is_file()
    if not ask("repair_scrpatches.py jetzt laufen lassen?", auto_yes):
        return REPAIRED.is_file()
    if run([sys.executable, ROOT / "scrpatches" / "repair_scrpatches.py",
            "--new", new_build]) != 0:
        bad("repair_scrpatches.py fehlgeschlagen")
        return False
    return True


def _dirty(xenvious: pathlib.Path) -> list:
    # -z keeps paths raw: without it git quotes any name containing a space,
    # and "Xenvious/Creator Classes/ScrPatchesRunner.cs" would never match the
    # expected-dirty list.
    #
    # -z also changes the record format for a rename: "R  <new>" is followed by
    # a second record holding the old path, with no status prefix of its own.
    # Stripping three characters from every record would eat the first three
    # characters of that path and report a file that does not exist.
    res = subprocess.run(["git", "status", "--porcelain", "-z"], cwd=str(xenvious),
                         capture_output=True, text=True)
    if res.returncode != 0:
        return []
    records = [r for r in res.stdout.split("\0") if r]
    out, i = [], 0
    while i < len(records):
        status, path = records[i][:2], records[i][3:]
        out.append(path)
        i += 1
        if status[0] in ("R", "C") and i < len(records):
            out.append(records[i])   # the source path of the rename
            i += 1
    return out


def _park_all(patches_json: str) -> str:
    """Every patch disabled, for a variant whose patterns cannot be verified.

    The definitions are kept rather than dropped: each carries the payload and
    the intent, which is the expensive part to reconstruct. They ship parked, so
    the runner skips them until someone verifies a pattern for this build."""
    patches = json.loads(patches_json)
    for p in patches:
        p["enabled"] = False
        p["note"] = "unverified for Enhanced: pattern was derived from Legacy bytecode"
        p.pop("derived_for", None)
    return json.dumps(patches, indent=4) + "\n"


def step_deploy(variant: str, xenvious: pathlib.Path, old_build, new_build: str,
                broken: int, dry_run: bool, auto_yes: bool) -> bool:
    """Write offsets.ini and scrpatches.json into OfflineData/<variant>."""
    head("5. Deploy nach OfflineData")
    offline = xenvious / "Xenvious" / "OfflineData" / variant
    target_ini = offline / "offsets.ini"
    target_json = offline / "scrpatches.json"
    source_ini = migrated_ini(variant)

    if broken:
        bad(f"{broken} Patch(es) noch gebrochen -- Deploy blockiert")
        note("Erst Schritt 3 abschliessen, oder die Patches mit enabled=false parken.")
        return False
    for path in (source_ini, REPAIRED, target_ini, target_json):
        if not path.is_file():
            bad(f"fehlt: {path}")
            return False

    counts = {}
    if variant == ENHANCED:
        new_json = _park_all(REPAIRED.read_text(encoding="utf-8"))
        counts["DISABLED"] = len(json.loads(new_json))
    else:
        # The deploy artifact must be healthy too, not only the source file: it
        # is a separate copy and can lag behind data/scrpatches.json.
        counts, broken_deploy = _health(REPAIRED, old_build, new_build)
        if broken_deploy:
            bad(f"Deploy-Artefakt selbst hat {broken_deploy} gebrochene(n) Patch(es)")
            _print_health(counts)
            note("Schritt 4 neu laufen lassen.")
            return False
        new_json = REPAIRED.read_text(encoding="utf-8")

    merged_ini, stats, unmapped = merge_offsets(
        source_ini.read_text(encoding="utf-8"),
        target_ini.read_text(encoding="utf-8", errors="surrogateescape"))

    ini_removed, _ = diff_lines(
        target_ini.read_text(encoding="utf-8", errors="surrogateescape"), merged_ini)
    json_removed, _ = diff_lines(target_json.read_text(encoding="utf-8"), new_json)

    print(f"  {c(f'{variant}/offsets.ini', 'bold')}      {stats['updated']} geaendert, "
          f"{stats['unchanged']} unveraendert, "
          f"{stats['target_only_static'] + stats['target_only_global']} nur im Ziel "
          f"({ini_removed} Zeilen Diff)")
    print(f"  {c(f'{variant}/scrpatches.json', 'bold')}  {len(json.loads(new_json))} Patches, "
          f"{counts.get('DISABLED', 0)} davon enabled=false "
          f"({json_removed} Zeilen Diff)")
    if unmapped:
        bad(f"{len(unmapped)} migrierte(r) Offset(s) ohne Gegenstueck im Ziel:")
        for name in unmapped:
            note(name)

    if ini_removed == 0 and json_removed == 0:
        ok("Ziel ist bereits auf diesem Stand")
        return True

    # Uncommitted changes in the files about to be overwritten would be lost
    # without a trace, because the deploy writes and does not merge.
    targets = {str(t.relative_to(xenvious)) for t in (target_ini, target_json)}
    at_risk = [f for f in _dirty(xenvious) if f in targets]
    if at_risk:
        bad(f"{len(at_risk)} Zieldatei(en) haben ungetrackte Aenderungen:")
        for f in at_risk:
            note(f)
        note("Der Deploy ueberschreibt sie ohne zu mergen.")

    if dry_run:
        note("DRY RUN: nichts geschrieben")
        return True
    if not ask(f"Beide Dateien nach {offline} schreiben?", auto_yes and not at_risk):
        return False

    target_ini.write_text(merged_ini, encoding="utf-8", errors="surrogateescape")
    target_json.write_text(new_json, encoding="utf-8")
    ok(f"geschrieben: {variant}/{target_ini.name}, {variant}/{target_json.name}")
    return True


def step_rebuild(xenvious: pathlib.Path) -> None:
    head("6. Rebuild")
    print("  OfflineData wird als EmbeddedResource in die .exe kompiliert.")
    print(f"  Ohne Rebuild aendert sich im Programm {c('nichts', 'bold')}.\n")
    note("unter Windows:  msbuild Xenvious.sln /t:Rebuild /p:Configuration=Release")
    print()
    print(f"    cd {xenvious}")
    print("    git status --short")
    print("    git diff --stat")
    print()


# ------------------------------------------------------------------ main ----

def run_variant(variant: str, args, xenvious: pathlib.Path) -> bool:
    # The new build may not be fetched yet, so fall back to the literal label
    # instead of failing: step 1 is what downloads it.
    spec = args.new
    if spec:
        try:
            new_build = versions.resolve(SCRIPTS, spec, variant=variant)
        except SystemExit:
            new_build = versions.label(variant, spec)
    else:
        new_build = versions.resolve(SCRIPTS, None, variant=variant)

    dump_old = (versions.resolve(DISASM, args.old, variant=variant) if args.old
                else versions.previous(DISASM, new_build))

    print(f"\n  {c('Xenvious update', 'bold', 'blue')}   "
          f"{c(variant, 'bold', 'cyan')} {ARROW} {c(new_build, 'cyan')}")
    print(f"  {c(str(xenvious), 'grey')}")
    if args.dry_run:
        print(f"  {c('DRY RUN - es wird nichts geschrieben', 'yellow', 'bold')}")

    if not step_fetch(variant, new_build, args.ref, args.dry_run, args.yes):
        return False
    step_offsets(variant, new_build, args.dry_run, args.yes)
    broken = step_patterns(variant, dump_old, new_build, args.dry_run, args.yes)
    step_payloads(variant, new_build, args.dry_run, args.yes)
    return step_deploy(variant, xenvious, dump_old, new_build, broken,
                       args.dry_run, args.yes)


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", choices=(LEGACY, ENHANCED, "both"), default=LEGACY,
                    help="Which game to update (default: legacy).")
    ap.add_argument("--new", help="New build (e.g. 1.74-4012). "
                                  "Default: newest folder for this variant.")
    ap.add_argument("--old", help="Old build the current data matches. "
                                  "Default: the build right before --new.")
    ap.add_argument("--ref", default=None,
                    help="git ref for fetch_update.sh (default: the upstream's).")
    ap.add_argument("--xenvious", default="/opt/Xenvious",
                    help="Xenvious checkout (default: /opt/Xenvious).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report everything, write nothing.")
    ap.add_argument("--yes", action="store_true",
                    help="Answer every question with yes.")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto")
    args = ap.parse_args()

    doctor._USE_COLOR = (args.color == "always" or
                         (args.color == "auto" and sys.stdout.isatty()
                          and not os.environ.get("NO_COLOR")))

    xenvious = pathlib.Path(args.xenvious).resolve()
    if not (xenvious / "Xenvious" / "OfflineData").is_dir():
        raise SystemExit(f"no Xenvious/OfflineData under {xenvious} -- pass --xenvious")

    variants = (LEGACY, ENHANCED) if args.variant == "both" else (args.variant,)
    if args.new and len(variants) > 1:
        raise SystemExit("--new names one build, so it cannot be combined with "
                         "--variant both; run the variants separately")

    deployed = []
    for variant in variants:
        if args.ref is None:
            args.ref = "main" if variant == ENHANCED else "senpai"
        deployed.append(run_variant(variant, args, xenvious))
        args.ref = None

    if any(deployed):
        step_rebuild(xenvious)
    print()
    return 0 if all(deployed) else 1


if __name__ == "__main__":
    raise SystemExit(main())
