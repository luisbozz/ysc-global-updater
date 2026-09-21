#!/usr/bin/env python3
"""Bring Xenvious up to date for a new GTA build, in one pass.

Runs the whole update chain and asks before every step that writes something:

  1. fetch the decompiled scripts and the decrypted dumps for the new build
  2. migrate offsets.ini from the old build to the new one
  3. check and repair the scrpatch patterns
  4. repair the injected customfuncs payloads
  5. deploy offsets.ini and scrpatches.json into Xenvious/OfflineData
  6. remind you to rebuild, because OfflineData is compiled into the .exe

Every step first reports what it finds. A step whose output is already newer
than its inputs is reported as up to date and is not offered again. Nothing
outside reports/ is written without a yes, and the deploy step refuses to run
while any pattern is still broken.

    python3 update_xenvious.py --new 1.74-4012
    python3 update_xenvious.py --new 1.74-4012 --dry-run
    python3 update_xenvious.py --new 1.74-4012 --yes

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

SCRIPTS = ROOT / "scripts"
MIGRATED = ROOT / "reports" / "offsets.migrated.ini"
MIGRATE_REPORT = ROOT / "reports" / "migrate-report.json"
REPAIRED = ROOT / "scrpatches" / "reports" / "scrpatches.repaired.json"
SOURCE_OFFSETS = ROOT / "offsets.ini"

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

# The two C# files that carry the `enabled` flag. They belong to this update and
# are expected to be modified when the deploy step runs.
EXPECTED_DIRTY = (
    "Xenvious/GTA.cs",
    "Xenvious/Creator Classes/ScrPatchesRunner.cs",
    "Xenvious/OfflineData/offsets.ini",
    "Xenvious/OfflineData/scrpatches.json",
)

README = "scrpatches/README.md"


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


def run(cmd: list[str], cwd: pathlib.Path = ROOT) -> int:
    """Run a child tool with its output going straight to the terminal."""
    print(f"    {c('$ ' + ' '.join(str(x) for x in cmd), 'grey')}\n")
    return subprocess.run([str(x) for x in cmd], cwd=str(cwd)).returncode


def newest_mtime(paths) -> float:
    times = [p.stat().st_mtime for p in paths if p.is_file()]
    return max(times) if times else 0.0


def diff_lines(old: str, new: str) -> tuple[int, int]:
    """(removed, added) line counts between two texts."""
    removed = added = 0
    for line in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0):
        if line.startswith("-") and not line.startswith("---"):
            removed += 1
        elif line.startswith("+") and not line.startswith("+++"):
            added += 1
    return removed, added


# ----------------------------------------------------------------- steps ----

def step_fetch(new_build: str, ref: str, dry_run: bool, auto_yes: bool) -> bool:
    """Download the scripts and dumps for the new build. False = cannot go on."""
    head("1. Dumps holen")
    missing_c = [s for s in C_SCRIPTS if not (SCRIPTS / new_build / f"{s}.c").is_file()]
    missing_full = [s for s in FULL_SCRIPTS
                    if not (DISASM / new_build / f"{s}.ysc.full").is_file()]
    if not missing_c and not missing_full:
        ok(f"{new_build}: {len(C_SCRIPTS)} Skripte, {len(FULL_SCRIPTS)} Dumps vorhanden")
        return True

    bad(f"{new_build}: {len(missing_c)} Skript(e), {len(missing_full)} Dump(s) fehlen")
    note(f"scripts/{new_build}/  und  scrpatches/disasm/{new_build}/")
    if dry_run:
        note("DRY RUN: nicht geholt")
        return False
    if not ask(f"Mit ./fetch_update.sh {new_build} {ref} holen?", auto_yes):
        bad("Ohne die Dumps kann kein weiterer Schritt laufen.")
        return False
    if run([ROOT / "fetch_update.sh", new_build, ref]) != 0:
        bad("fetch_update.sh fehlgeschlagen")
        return False
    return True


def _unresolved_count() -> int | None:
    if not MIGRATE_REPORT.is_file():
        return None
    return len(json.loads(MIGRATE_REPORT.read_text(encoding="utf-8")).get("unresolved", []))


def step_offsets(new_build: str, dry_run: bool, auto_yes: bool) -> bool:
    """Migrate offsets.ini to the new build. False = no usable result."""
    head("2. Offsets migrieren")
    inputs = list((SCRIPTS / new_build).glob("*.c")) + [SOURCE_OFFSETS]
    fresh = MIGRATED.is_file() and MIGRATED.stat().st_mtime > newest_mtime(inputs)

    if fresh:
        left = _unresolved_count()
        ok(f"reports/offsets.migrated.ini ist neuer als scripts/{new_build}/")
        if left:
            bad(f"{left} Offset(s) unaufgeloest -- siehe reports/migrate-report.json")
        elif left == 0:
            ok("0 Offsets unaufgeloest")
        return True

    if not MIGRATED.is_file():
        bad("reports/offsets.migrated.ini fehlt")
    else:
        bad(f"reports/offsets.migrated.ini ist aelter als scripts/{new_build}/")
    if dry_run:
        note("DRY RUN: nicht migriert")
        return MIGRATED.is_file()
    if not ask("Migration jetzt laufen lassen?", auto_yes):
        return MIGRATED.is_file()
    if run([sys.executable, ROOT / "tools" / "run_pipeline.py", "--new", new_build]) != 0:
        bad("run_pipeline.py fehlgeschlagen")
        return False

    left = _unresolved_count()
    if left:
        bad(f"{left} Offset(s) unaufgeloest -- siehe reports/migrate-report.json")
    else:
        ok("0 Offsets unaufgeloest")
    return True


def _health(patches_path: pathlib.Path, old_build: str, new_build: str) -> tuple[dict, int]:
    """(status counts, number of patches needing work) for one patches file."""
    patches = json.loads(patches_path.read_text(encoding="utf-8"))
    results = check(patches, old_build, new_build)
    counts: dict[str, int] = {}
    for r in results:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    return counts, sum(1 for r in results if not is_healthy(r["status"]))


def _print_health(counts: dict) -> None:
    for status in ("OK", "REDERIVED", "DISABLED", "BROKEN",
                   "AMBIG_NEW", "AMBIG_OLD", "NOT_IN_OLD"):
        if counts.get(status):
            print(f"    {c(f'{status:11}', *status_colour(status))}: {counts[status]:3d}")


def step_patterns(old_build: str, new_build: str, dry_run: bool, auto_yes: bool) -> int:
    """Check and repair the scrpatch patterns. Returns the count still broken."""
    head("3. scrpatches pruefen")
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


def step_payloads(new_build: str, dry_run: bool, auto_yes: bool) -> bool:
    """Rebuild the injected customfuncs payloads. False = no usable artifact."""
    head("4. Payloads reparieren")
    inputs = [PATCHES] + list((DISASM / new_build).glob("*.ysc.full"))
    fresh = REPAIRED.is_file() and REPAIRED.stat().st_mtime > newest_mtime(inputs)

    if fresh:
        ok("scrpatches/reports/scrpatches.repaired.json ist aktuell")
        return True

    if not REPAIRED.is_file():
        bad("scrpatches/reports/scrpatches.repaired.json fehlt")
    else:
        bad("scrpatches/reports/scrpatches.repaired.json ist aelter als seine Quellen")
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


def _dirty(xenvious: pathlib.Path) -> list[str]:
    # -z keeps paths raw: without it git quotes any name containing a space,
    # and "Xenvious/Creator Classes/ScrPatchesRunner.cs" would never match the
    # expected-dirty list.
    res = subprocess.run(["git", "status", "--porcelain", "-z"], cwd=str(xenvious),
                         capture_output=True, text=True)
    if res.returncode != 0:
        return []
    return [rec[3:] for rec in res.stdout.split("\0") if rec.strip()]


def step_deploy(xenvious: pathlib.Path, old_build: str, new_build: str,
                broken: int, dry_run: bool, auto_yes: bool) -> bool:
    """Write offsets.ini and scrpatches.json into OfflineData."""
    head("5. Deploy nach OfflineData")
    offline = xenvious / "Xenvious" / "OfflineData"
    target_ini = offline / "offsets.ini"
    target_json = offline / "scrpatches.json"

    if broken:
        bad(f"{broken} Patch(es) noch gebrochen -- Deploy blockiert")
        note("Erst Schritt 3 abschliessen, oder die Patches mit enabled=false parken.")
        return False
    for path in (MIGRATED, REPAIRED, target_ini, target_json):
        if not path.is_file():
            bad(f"fehlt: {path}")
            return False

    # The deploy artifact must be healthy too, not only the source file: it is a
    # separate copy and can lag behind data/scrpatches.json.
    counts, broken_deploy = _health(REPAIRED, old_build, new_build)
    if broken_deploy:
        bad(f"Deploy-Artefakt selbst hat {broken_deploy} gebrochene(n) Patch(es)")
        _print_health(counts)
        note("Schritt 4 neu laufen lassen.")
        return False

    merged_ini, stats, unmapped = merge_offsets(
        MIGRATED.read_text(encoding="utf-8"), target_ini.read_text(encoding="utf-8"))
    new_json = REPAIRED.read_text(encoding="utf-8")

    ini_removed, _ = diff_lines(target_ini.read_text(encoding="utf-8"), merged_ini)
    json_removed, _ = diff_lines(target_json.read_text(encoding="utf-8"), new_json)

    print(f"  {c('offsets.ini', 'bold')}      {stats['updated']} geaendert, "
          f"{stats['unchanged']} unveraendert, "
          f"{stats['target_only_static'] + stats['target_only_global']} nur im Ziel "
          f"({ini_removed} Zeilen Diff)")
    print(f"  {c('scrpatches.json', 'bold')}  {len(json.loads(new_json))} Patches, "
          f"{counts.get('DISABLED', 0)} davon enabled=false "
          f"({json_removed} Zeilen Diff)")
    if unmapped:
        bad(f"{len(unmapped)} migrierte(r) Offset(s) ohne Gegenstueck im Ziel:")
        for name in unmapped:
            note(name)

    if ini_removed == 0 and json_removed == 0:
        ok("Ziel ist bereits auf diesem Stand")
        return True

    unexpected = [f for f in _dirty(xenvious) if f not in EXPECTED_DIRTY]
    if unexpected:
        bad(f"{xenvious} hat {len(unexpected)} andere ungetrackte Aenderung(en):")
        for f in unexpected[:10]:
            note(f)
        note("Rollback per git checkout wuerde diese mitnehmen.")

    if dry_run:
        note("DRY RUN: nichts geschrieben")
        return True
    if not ask(f"Beide Dateien nach {offline} schreiben?", auto_yes and not unexpected):
        return False

    target_ini.write_text(merged_ini, encoding="utf-8")
    target_json.write_text(new_json, encoding="utf-8")
    ok(f"geschrieben: {target_ini.name}, {target_json.name}")
    return True


def step_rebuild(xenvious: pathlib.Path) -> None:
    head("6. Rebuild")
    print("  OfflineData wird als EmbeddedResource in die .exe kompiliert.")
    print(f"  Ohne Rebuild aendert sich im Programm {c('nichts', 'bold')}.\n")
    note("unter Windows:  msbuild Xenvious.sln /t:Rebuild /p:Configuration=Release")
    print()
    print(f"  {c('Die C#-Aenderung fuer enabled gehoert in denselben Commit:', 'yellow')}")
    note("ohne sie ignoriert Xenvious enabled=false und wendet geparkte Patches an.")
    print()
    print(f"    cd {xenvious}")
    print("    git add Xenvious/OfflineData/offsets.ini "
          "Xenvious/OfflineData/scrpatches.json \\")
    print("            Xenvious/GTA.cs 'Xenvious/Creator Classes/ScrPatchesRunner.cs'")
    print("    git diff --cached --stat")
    print()


# ------------------------------------------------------------------ main ----

def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--new", help="New build (e.g. 1.74-4012). "
                                  "Default: newest folder in scrpatches/disasm/.")
    ap.add_argument("--old", help="Old build the current data matches. "
                                  "Default: the build right before --new.")
    ap.add_argument("--ref", default="senpai",
                    help="git ref for fetch_update.sh (default: senpai = latest).")
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

    # The new build may not be fetched yet, so fall back to the literal label
    # instead of failing: step 1 is what downloads it.
    if args.new:
        try:
            new_build = versions.resolve(DISASM, args.new)
        except SystemExit:
            new_build = args.new
    else:
        new_build = versions.resolve(DISASM, None)
    old_build = (versions.resolve(DISASM, args.old) if args.old
                 else versions.previous(DISASM, new_build))
    if not old_build:
        raise SystemExit("no earlier build in scrpatches/disasm/ -- pass --old <build>")

    xenvious = pathlib.Path(args.xenvious).resolve()
    if not (xenvious / "Xenvious" / "OfflineData").is_dir():
        raise SystemExit(f"no Xenvious/OfflineData under {xenvious} -- pass --xenvious")

    print(f"\n  {c('Xenvious update', 'bold', 'blue')}   "
          f"{c(old_build, 'cyan')} {ARROW} {c(new_build, 'cyan')}")
    print(f"  {c(str(xenvious), 'grey')}")
    if args.dry_run:
        print(f"  {c('DRY RUN - es wird nichts geschrieben', 'yellow', 'bold')}")

    if not step_fetch(new_build, args.ref, args.dry_run, args.yes):
        return 1
    step_offsets(new_build, args.dry_run, args.yes)
    broken = step_patterns(old_build, new_build, args.dry_run, args.yes)
    step_payloads(new_build, args.dry_run, args.yes)
    deployed = step_deploy(xenvious, old_build, new_build, broken,
                           args.dry_run, args.yes)
    if deployed:
        step_rebuild(xenvious)
    print()
    return 0 if deployed else 1


if __name__ == "__main__":
    raise SystemExit(main())
