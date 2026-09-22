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
import re
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


# Each build keeps its own patch set. The patterns start out identical -- most
# Legacy patterns match Enhanced unchanged -- but they drift apart as soon as
# one build needs a pattern re-derived, and the injected payloads are compiled
# per build and never match across one.
def patches_file(variant: str) -> pathlib.Path:
    return (PATCHES if variant == LEGACY
            else PATCHES.with_name(f"scrpatches.{variant}.json"))


def repaired_file(variant: str) -> pathlib.Path:
    return (REPAIRED if variant == LEGACY
            else REPAIRED.with_name(f"scrpatches.{variant}.repaired.json"))


def _dump_source(variant: str, new_build: str):
    """The build to compare dumps against, and whether this is a bootstrap.

    A variant's first build has no predecessor of its own. The two games share
    their script content, so the other game's newest build is the closest
    comparison that exists -- good enough to tell a pattern that still matches
    from one that does not."""
    earlier = versions.previous(DISASM, new_build)
    if earlier:
        return earlier, False
    other = LEGACY if variant == ENHANCED else ENHANCED
    candidates = versions.list_versions(DISASM, other)
    return (candidates[-1] if candidates else None), True


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
    if missing_full:
        note(f"Dumps kommen aus der lokalen Installation, nicht vom Upstream:")
        note(f"  python3 tools/extract_ysc.py --variant {variant} "
             f"--build {new_build.replace('enhanced-', '', 1)} \\")
        note("      --game <GTA-Ordner> --codewalker <CodeWalker.Core.dll>")
    if dry_run:
        note("DRY RUN: nicht geholt")
        return False
    if missing_c:
        cmd = [ROOT / "fetch_update.sh", "--variant", variant,
               new_build.replace("enhanced-", "", 1), ref]
        if not ask(f"Skripte mit {cmd[0].name} holen?", auto_yes):
            bad("Ohne die Skripte kann kein weiterer Schritt laufen.")
            return False
        if run(cmd) != 0:
            bad("fetch_update.sh fehlgeschlagen")
            return False
    return not missing_full


def _unresolved_count(variant: str):
    path = migrate_report(variant)
    if not path.is_file():
        return None
    return len(json.loads(path.read_text(encoding="utf-8")).get("unresolved", []))


def authored_for(ini: pathlib.Path):
    """The build an offsets.ini was written against, if it says so.

    Guessing this from the folder listing is wrong whenever a build was fetched
    that the file never passed through: offsets.ini is authored for 1.71-3586,
    scripts/ also holds 1.72-3788, and "the build before 1.73" picked the 1.72
    one. The migration then compared a 1.71 file against 1.72 code and rewrote
    almost every offset into nonsense -- 913 of 988 values fitting the new
    corpus fell to 86."""
    if not ini.is_file():
        return None
    for line in ini.read_text(encoding="utf-8", errors="replace").splitlines()[:20]:
        m = re.match(r"^\s*;\s*authored-for:\s*(\S+)", line)
        if m:
            return m.group(1)
    return None


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
        return SOURCE_OFFSETS, authored_for(SOURCE_OFFSETS) or versions.previous(
            SCRIPTS, new_build)
    earlier = versions.previous(SCRIPTS, new_build)
    if earlier:
        return migrated_ini(ENHANCED), earlier
    legacy_build = versions.resolve(SCRIPTS, "latest", variant=LEGACY)
    return migrated_ini(LEGACY), legacy_build


def _review_hint(variant: str) -> None:
    """What an unresolved count does and does not mean.

    Most entries on that list are offsets that offsets.ini stores in a
    simplified form which never appears literally in any corpus -- they are
    correct and were already on the list before this update. The count alone
    therefore says nothing about whether the migration went well; comparing the
    result against both corpora does."""
    note("Die Liste enthaelt auch Offsets, die offsets.ini vereinfacht speichert")
    note("und die deshalb in keinem Korpus woertlich vorkommen. Ob die Migration")
    note("gut lief, zeigt der Vergleich gegen beide Korpora:")
    # Its own corpus first, the other game's second: the result must fit the
    # first better than the second, whatever the absolute numbers are.
    other = LEGACY if variant == ENHANCED else ENHANCED
    builds = [b for b in (versions.list_versions(SCRIPTS, variant)[-1:]
                          + versions.list_versions(SCRIPTS, other)[-1:])]
    corpora = " ".join(f"--corpus scripts/{b}" for b in builds)
    note(f"  python3 tools/score_corpus.py --ini {migrated_ini(variant).relative_to(ROOT)} "
         f"{corpora}")


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
            _review_hint(variant)
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
        _review_hint(variant)
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


def step_patterns(variant: str, old_build, new_build: str, bootstrap: bool,
                  dry_run: bool, auto_yes: bool) -> int:
    """Check and repair the scrpatch patterns. Returns the count still broken."""
    head("3. scrpatches pruefen")
    patches = patches_file(variant)
    if not patches.is_file():
        bad(f"fehlt: {patches.relative_to(ROOT)}")
        note("Ein neuer Build startet mit einer Kopie des zuletzt gepflegten Satzes.")
        return 0
    if not old_build:
        bad(f"kein Vergleichs-Build fuer {new_build} in scrpatches/disasm/")
        return 0
    if bootstrap:
        note(f"Erstlauf: verglichen gegen {old_build} aus der anderen Variante.")

    counts, broken = _health(patches, old_build, new_build)
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

    cmd = [sys.executable, ROOT / "scrpatches" / "update_patches.py",
           "--new", new_build, "--old", old_build, "--patches", str(patches)]
    if auto_yes:
        cmd.append("--yes")
    run(cmd)

    counts, broken = _health(patches, old_build, new_build)
    print()
    _print_health(counts)
    if broken:
        bad(f"{broken} Patch(es) weiterhin offen -- Handarbeit, siehe {README}")
    else:
        ok("alle Patterns gesund")
    return broken


def check_customfuncs_source(variant: str) -> None:
    """Warn when the .ysa sources no longer describe the payloads we ship.

    The payloads are what the game gets; the sources are what a human reads and
    edits. If they drift, the readable version silently stops being true -- the
    same class of mistake that left the scratch globals pointing outside their
    block. Only legacy carries sources today, so this is a warning, not a gate.
    """
    builder = ROOT / "scrpatches" / "build_customfuncs.py"
    if variant != LEGACY or not builder.is_file():
        return
    res = subprocess.run([sys.executable, str(builder), "--check"],
                         cwd=str(ROOT / "scrpatches"), capture_output=True, text=True)
    if res.returncode == 0:
        ok("customfuncs-Quellen stimmen mit den Payloads ueberein")
        return
    bad("customfuncs-Quellen weichen von den Payloads ab")
    for line in res.stdout.splitlines():
        if line.startswith("DIFF") or line.startswith("!!"):
            note(line)
    note("scrasm/customfuncs/src/*.ysa beschreibt nicht mehr, was ausgeliefert wird.")
    note("Entweder build_customfuncs.py --write, oder gen_customfuncs_src.py.")


def step_payloads(variant: str, old_build, new_build: str,
                  dry_run: bool, auto_yes: bool) -> bool:
    """Rebuild the injected customfuncs payloads. False = no usable artifact."""
    head("4. Payloads reparieren")
    patches = patches_file(variant)
    repaired = repaired_file(variant)
    if not patches.is_file() or not old_build:
        bad("Schritt 3 hat kein Ergebnis geliefert")
        return False
    check_customfuncs_source(variant)

    inputs = [patches] + list((DISASM / new_build).glob("*.ysc.full"))
    fresh = repaired.is_file() and repaired.stat().st_mtime > newest_mtime(inputs)

    if fresh:
        ok(f"{repaired.relative_to(ROOT)} ist aktuell")
        return True

    bad(f"{repaired.relative_to(ROOT)} fehlt oder ist aelter als seine Quellen")
    note(f"Diese Datei ist das Deploy-Artefakt, nicht {patches.relative_to(ROOT)}:")
    note("nur hier sind die injizierten Payloads auf den neuen Build umgeschrieben.")
    if dry_run:
        note("DRY RUN: nicht repariert")
        return repaired.is_file()
    if not ask("repair_scrpatches.py jetzt laufen lassen?", auto_yes):
        return repaired.is_file()
    if run([sys.executable, ROOT / "scrpatches" / "repair_scrpatches.py",
            "--new", new_build, "--old", old_build,
            "--patches", str(patches)]) != 0:
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


def step_deploy(variant: str, xenvious: pathlib.Path, old_build, new_build: str,
                broken: int, dry_run: bool, auto_yes: bool) -> bool:
    """Write offsets.ini and scrpatches.json into OfflineData/<variant>."""
    head("5. Deploy nach OfflineData")
    offline = xenvious / "Xenvious" / "OfflineData" / variant
    target_ini = offline / "offsets.ini"
    target_json = offline / "scrpatches.json"
    source_ini = migrated_ini(variant)
    repaired = repaired_file(variant)

    if broken:
        bad(f"{broken} Patch(es) noch gebrochen -- Deploy blockiert")
        note("Erst Schritt 3 abschliessen, oder die Patches mit enabled=false parken.")
        return False
    for path in (source_ini, repaired, target_ini, target_json):
        if not path.is_file():
            bad(f"fehlt: {path}")
            return False

    # The deploy artifact must be healthy too, not only the source file: it is
    # a separate copy and can lag behind the patch set it was built from.
    counts, broken_deploy = _health(repaired, old_build, new_build)
    if broken_deploy:
        bad(f"Deploy-Artefakt selbst hat {broken_deploy} gebrochene(n) Patch(es)")
        _print_health(counts)
        note("Schritt 4 neu laufen lassen.")
        return False
    new_json = repaired.read_text(encoding="utf-8")

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

    if args.old:
        dump_old, bootstrap = versions.resolve(DISASM, args.old, variant=variant), False
    else:
        dump_old, bootstrap = _dump_source(variant, new_build)

    print(f"\n  {c('Xenvious update', 'bold', 'blue')}   "
          f"{c(variant, 'bold', 'cyan')} {ARROW} {c(new_build, 'cyan')}")
    print(f"  {c(str(xenvious), 'grey')}")
    if args.dry_run:
        print(f"  {c('DRY RUN - es wird nichts geschrieben', 'yellow', 'bold')}")

    if not step_fetch(variant, new_build, args.ref, args.dry_run, args.yes):
        return False
    step_offsets(variant, new_build, args.dry_run, args.yes)
    broken = step_patterns(variant, dump_old, new_build, bootstrap,
                           args.dry_run, args.yes)
    step_payloads(variant, dump_old, new_build, args.dry_run, args.yes)
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
