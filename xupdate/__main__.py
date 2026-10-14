"""xupdate: brings Xenvious' build data to a new GTA build, step by step.

    python3 -m xupdate 1.74-4012                 # Legacy
    python3 -m xupdate enhanced-1.74-1200        # Enhanced
    python3 -m xupdate 1.74-4012 --terminal      # review in the terminal instead of the browser

Steps (each one resumes from reports/xupdate/<build>/ when run again):

  1. scripts    the new build's decompiled .c and decrypted .ysc.full are there
  2. offsets    Xenvious' offsets.ini from its [BUILD] label to the new build
  3. patches    scrpatches.json / scrpatchesdev.json carried to the new build
  4. payloads   victim function or extra page, custom funcs sources moved
  5. lists      prop lists, race vehicles (ADLC), prop-menu originals
  6. review     what the tool is not sure about: old code left, new code right
  7. build      answers applied, payloads assembled
  8. deploy     written into Xenvious (OfflineData + Generated/*.g.cs), sources into page/src

Nothing outside reports/xupdate/ is written before step 8 asks.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import extract, offsets, pages, patches as patch_step
from .common import DISASM, ROOT, SCRIPTS, WORK, Item

C_SCRIPTS = ("fm_capture_creator", "fm_deathmatch_creator", "fm_lts_creator", "fm_race_creator",
             "fm_survival_creator", "fmmc_launcher", "public_mission_creator", "tuneables_processing")
# creator: the creator hub (menu unlock patch).
FULL_SCRIPTS = ("creator", "fm_capture_creator", "fm_deathmatch_creator", "fm_lts_creator", "fm_race_creator",
                "fm_survival_creator", "public_mission_creator")
PATCH_FILES = ("scrpatches.json", "scrpatchesdev.json")
LIST_FILES = ("race_vehicles.json", "mprops.json")

_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text, code):
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def head(n, title):
    print("\n" + c(f"━━ {n}. {title} " + "━" * max(3, 60 - len(title)), "1;34"))


def ok(text):
    print(f"  {c('✓', '32')} {text}")


def bad(text):
    print(f"  {c('✗', '31')} {text}")


def note(text):
    print(f"    {c(text, '90')}")


def ask(question, yes=False) -> bool:
    if yes:
        print(f"  {question} [j/n] j")
        return True
    if not sys.stdin.isatty():
        return False
    return input(f"  {question} [j/n] ").strip().lower() in ("j", "ja", "y", "yes")


class State:
    """reports/xupdate/<build>/state.json: finished steps and every review item with its answer."""

    def __init__(self, path: Path):
        self.path = path
        self.data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {"steps": {}, "items": []}
        self.items = [Item.from_json(d) for d in self.data["items"]]

    def done(self, step) -> bool:
        return step in self.data["steps"]

    def finish(self, step, info=None):
        self.data["steps"][step] = info or True
        self.save()

    def redo(self, step, prefixes):
        self.data["steps"].pop(step, None)
        self.items = [it for it in self.items if not it.id.startswith(prefixes)]
        self.save()

    def add(self, new_items):
        """New items; an item seen before keeps its answer when its guess did not change."""
        old = {it.id: it for it in self.items}
        for it in new_items:
            prev = old.get(it.id)
            if prev and prev.answer and prev.guess == it.guess:
                it.answer = prev.answer
            old[it.id] = it
        self.items = list(old.values())
        self.save()

    def save(self):
        self.data["items"] = [it.to_json() for it in self.items]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.path)

    def open_items(self):
        return [it for it in self.items if it.answer is None]


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_patches(path: Path, data: list):
    path.write_text(json.dumps(data, indent=4, ensure_ascii=False) + "\n", encoding="utf-8")


# -- steps ------------------------------------------------------------------------------

def step_scripts(edition, new_build) -> bool:
    head(1, "Skripte")
    miss_c = [s for s in C_SCRIPTS if not (SCRIPTS / new_build / f"{s}.c").is_file()]
    miss_f = [s for s in FULL_SCRIPTS if not (DISASM / new_build / f"{s}.ysc.full").is_file()]
    if not miss_c and not miss_f:
        ok(f"{new_build}: {len(C_SCRIPTS)} .c und {len(FULL_SCRIPTS)} .ysc.full vorhanden")
        return True
    if not miss_c and miss_f == ["creator"]:
        # Only the hub's menu patch needs it; that patch goes to the review.
        bad(f"scrpatches/disasm/{new_build}/ fehlt: creator.ysc.full (nur der Menü-Patch, kommt in die Prüfung)")
        return True
    if miss_c:
        bad(f"scripts/{new_build}/ fehlt: " + ", ".join(f"{s}.c" for s in miss_c))
    if miss_f:
        bad(f"scrpatches/disasm/{new_build}/ fehlt: " + ", ".join(f"{s}.ysc.full" for s in miss_f))
    plain = new_build.replace("enhanced-", "", 1)
    print()
    if edition == "legacy":
        note("Legacy: die Dumps gibt es fertig im Netz:")
        note(f"  ./fetch_update.sh {plain} <git-ref>")
    else:
        note("Enhanced: aus dem eigenen Spiel holen (CodeWalker, siehe tools/extract_ysc.py):")
    note(f"  python3 tools/extract_ysc.py --variant {edition} --build {plain} \\")
    note("      --game '<GTA-Ordner>' --codewalker '<CodeWalker.Core.dll>'")
    note(f"  python3 tools/decompile_ysc.py ...   (die .c aus den .ysc, siehe README)")
    note("Danach diesen Befehl noch einmal starten.")
    return False


def ini_numbers(text: str) -> dict:
    from .paths import address
    out = {}
    for low, (_, v) in offsets.offsets_values(text).items():
        a = address(v)
        if a is not None:
            out[low.removeprefix("offset_")] = a
    return out


def step_offsets(st, work, src_text, old_build, new_build):
    head(2, "Offsets")
    out = work / "offsets.ini"
    if st.done("offsets") and out.is_file():
        ok(f"schon gemacht ({sum(1 for i in st.items if i.kind == 'offset')} Punkte zur Prüfung)")
        return
    note(f"Quelle: Xenvious offsets.ini, gebaut für {old_build}")
    note("Migration läuft (1-3 Minuten) ...")
    mig = offsets.migrate(_tmp_ini(work, src_text), old_build, new_build, work)
    note("Gegenprobe über den Code um jede Stelle ...")
    text, items = offsets.merge(src_text, mig.read_text(encoding="utf-8-sig"),
                                offsets.load_report(work), old_build, new_build)
    out.write_text(text, encoding="utf-8")
    st.add(items)
    sure = sum(1 for i in items if i.sure)
    ok(f"{len(offsets.offsets_values(src_text))} Offsets; {len(items)} zur Prüfung ({sure} davon sicher)")
    st.finish("offsets")


def _tmp_ini(work, text) -> Path:
    p = work / "offsets.source.ini"
    p.write_text(text, encoding="utf-8")
    return p


def step_patches(st, work, offline, old_build, new_build):
    head(3, "Script-Patches")
    if st.done("patches") and all((work / f).is_file() for f in PATCH_FILES if (offline / f).is_file()):
        ok(f"schon gemacht ({sum(1 for i in st.items if i.kind == 'patch')} Punkte zur Prüfung)")
        return
    note("jeder Patch wird über seine Funktion im neuen Build gesucht (einige Minuten) ...")
    for name in PATCH_FILES:
        if not (offline / name).is_file():
            continue
        data = read_json(offline / name)
        new, items = patch_step.update(data, old_build, new_build, name)
        write_patches(work / name, new)
        st.add(items)
        ok(f"{name}: {len(data)} Patches, {len(items)} geändert oder kaputt")
    st.finish("patches")


def step_payloads(st, work, edition, src_text, old_build, new_build):
    head(4, "Custom-Funcs-Payloads")
    if st.done("payloads"):
        ok("schon gemacht")
        return
    data = read_json(work / "scrpatches.json")
    text = (work / "offsets.ini").read_text(encoding="utf-8")
    data, text, items = pages.update(edition, data, old_build, new_build, text,
                                     ini_numbers(src_text), ini_numbers(text), work, log=note)
    write_patches(work / "scrpatches.json", data)
    (work / "offsets.ini").write_text(text, encoding="utf-8")
    st.add(items)
    ok(f"Quellen verschoben, {len(items)} Punkte zur Prüfung")
    st.finish("payloads")


def step_lists(st, work, edition, new_build, offline):
    head(5, "Listen")
    if st.done("lists"):
        ok("schon gemacht")
        return
    files = {}
    for fn in extract.EXTRACTORS:
        got, items, lines = fn(new_build, edition, work, offline)
        files.update({k: str(v) for k, v in got.items()})
        st.add(items)
        for line in lines:
            ok(line)
    items, lines = extract.entity_handles(new_build, work)
    st.add(items)
    for line in lines:
        ok(line)
    st.finish("lists", {"files": files})


def step_review(st, args, title) -> None:
    head(6, "Prüfen")
    from . import review
    todo = st.open_items()
    if not todo:
        ok("nichts offen")
        return
    sure = sum(1 for i in todo if i.sure)
    print(f"  {len(todo)} Punkte offen ({sure} sicher, die gehen mit einem Klick).")
    if args.terminal:
        if not sys.stdin.isatty():
            bad("--terminal braucht ein Terminal")
            return
        review.terminal(st.items, st.save)
    else:
        review.serve(st.items, st.save, title, port=args.port, browser=not args.no_browser)
    left = len(st.open_items())
    (ok if not left else bad)(f"{len(st.items) - left} beantwortet, {left} offen")


def step_build(st, work, edition, new_build, offline):
    head(7, "Antworten anwenden, Payloads bauen")
    text = (work / "offsets.ini").read_text(encoding="utf-8")
    answers = {}
    for it in st.items:
        if it.kind == "offset" and it.answer:
            answers[it.title] = it.guess if it.answer == "übernehmen" else it.answer
    text = offsets.apply_answers(text, answers)
    (work / "offsets.final.ini").write_text(text, encoding="utf-8")
    ok(f"{len(answers)} Offsets aus der Prüfung übernommen")

    for name in PATCH_FILES:
        if not (work / name).is_file():
            continue
        data = patch_step.apply_answers(read_json(work / name), st.items, name)
        write_patches(work / f"{name}.final", data)
    pages.apply_source_answers(edition, st.items, work)
    data = read_json(work / "scrpatches.json.final")
    room = pages.victim_room(text) if edition == "legacy" else None
    items, msgs = pages.assemble(edition, new_build, data, work, room)
    for m in msgs:
        if not m.startswith("  "):
            ok(m)
    for it in st.items:
        if it.id.startswith("build:") and it.answer == "abschalten":
            pages.disable_script_funcs(data, it.old_value)
    write_patches(work / "scrpatches.json.final", data)
    # Problems that are gone drop out; the ones still there keep their answer.
    now = {i.id for i in items}
    st.items = [it for it in st.items if not it.id.startswith("build:") or it.id in now]
    st.add(items)
    if any(i.id.startswith("build:") and not i.answer for i in st.items):
        bad("Payload-Probleme, siehe Prüfung")
    if any(it.kind == "vehicle" and it.answer for it in st.items):
        extract.name_vehicles(st.items)
        extract.race_vehicles(new_build, edition, work, offline)
        ok("Fahrzeugnamen eingetragen, race_vehicles.json neu gelesen")


def step_deploy(st, args, work, xen, offline, edition, old_build, new_build) -> bool:
    head(8, "In Xenvious schreiben")
    left = st.open_items()
    if left:
        bad(f"{len(left)} Punkte unbeantwortet: dort bleiben die alten Werte.")
        for it in left[:8]:
            note(it.title)
    text = offsets.set_build_label((work / "offsets.final.ini").read_text(encoding="utf-8"), new_build)
    targets = {offline / "offsets.ini": text}
    for name in PATCH_FILES:
        if (work / f"{name}.final").is_file():
            targets[offline / name] = (work / f"{name}.final").read_text(encoding="utf-8")
    for name, src in (st.data["steps"].get("lists") or {}).get("files", {}).items():
        targets[offline / name] = Path(src).read_text(encoding="utf-8")
    def same(p, t):
        if not p.is_file():
            return False
        old = p.read_text(encoding="utf-8-sig")
        if p.suffix == ".json":
            return json.loads(old) == json.loads(t)  # same content in another layout
        return old == t
    changed = [p for p, t in targets.items() if not same(p, t)]
    for p in changed:
        note(str(p.relative_to(xen)))
    note(f"Payload-Quellen -> scrpatches/scrasm/customfuncs/page/src/{edition}/ (BUILD = {new_build})")
    if not ask(f"{len(changed)} Dateien in {xen} schreiben?", args.yes):
        bad("nichts geschrieben; mit demselben Befehl geht es hier weiter")
        return False
    for p in changed:
        p.write_text(targets[p], encoding="utf-8")
    dst = pages.PAGE_DIR / "src" / edition
    for f in pages.work_src(work).iterdir():
        shutil.copy(f, dst / f.name)
    gen = xen / "tools" / "builddata" / "gen.py"
    res = subprocess.run([sys.executable, str(gen)], cwd=str(xen), capture_output=True, text=True)
    if res.returncode == 0:
        ok("Generated/*.g.cs neu erzeugt")
    else:
        bad("gen.py fehlgeschlagen: " + (res.stderr.strip().splitlines() or ["?"])[-1])
    st.finish("deploy")
    print()
    print("  Fertig. Jetzt:")
    print(f"    git -C {xen} diff --stat")
    print(f"    git -C {ROOT} status --short")
    print("    Xenvious bauen (Windows: msbuild Xenvious.sln /p:Configuration=Release /p:Platform=x64)")
    print("    im Spiel testen, dann beide Repos committen")
    return True


def main() -> int:
    ap = argparse.ArgumentParser(prog="xupdate", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("build", help="new build, as the folder under scripts/ (1.74-4012, enhanced-1.74-1200)")
    ap.add_argument("--xenvious", default="/opt/xenvious", help="Xenvious checkout (default /opt/xenvious)")
    ap.add_argument("--terminal", action="store_true", help="review in the terminal")
    ap.add_argument("--no-browser", action="store_true", help="print the review URL, do not open it")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--redo", action="append", default=[],
                    choices=("offsets", "patches", "payloads", "lists"), help="run a step again")
    ap.add_argument("--yes", action="store_true", help="write without asking (step 8)")
    args = ap.parse_args()

    new_build = args.build
    edition = "enhanced" if new_build.startswith("enhanced-") else "legacy"
    xen = Path(args.xenvious).resolve()
    offline = xen / "Xenvious" / "OfflineData" / edition
    if not (offline / "offsets.ini").is_file():
        raise SystemExit(f"no {offline / 'offsets.ini'} -- pass --xenvious")
    src_text = (offline / "offsets.ini").read_text(encoding="utf-8-sig")
    old_build = offsets.build_label(src_text)
    if not old_build:
        raise SystemExit("offsets.ini has no [BUILD] label")
    work = WORK / new_build
    work.mkdir(parents=True, exist_ok=True)
    st = State(work / "state.json")
    prefixes = {"offsets": ("offset:",), "patches": ("patch:",),
                "payloads": ("page:", "space:", "build:"), "lists": ("list:", "vehicle:")}
    for step in args.redo:
        st.redo(step, prefixes[step])

    print(f"\n  {c('Xenvious-Update', '1;34')}  {edition}  {old_build} → {c(new_build, '1;36')}")
    print(f"  {c(str(work), '90')}")
    if old_build == new_build:
        bad("Xenvious ist schon auf diesem Build")
        return 0
    if not step_scripts(edition, new_build):
        return 1
    for b in (old_build,):
        if not (SCRIPTS / b).is_dir() or not (DISASM / b).is_dir():
            bad(f"die Skripte des alten Builds {b} fehlen auch (scripts/{b}, scrpatches/disasm/{b})")
            return 1
    step_offsets(st, work, src_text, old_build, new_build)
    step_patches(st, work, offline, old_build, new_build)
    step_payloads(st, work, edition, src_text, old_build, new_build)
    step_lists(st, work, edition, new_build, offline)
    while True:
        step_review(st, args, f"Xenvious {edition}: {old_build} → {new_build}")
        step_build(st, work, edition, new_build, offline)
        if not any(i.id.startswith("build:") and not i.answer for i in st.items):
            break
        if not ask("Payload-Probleme prüfen?"):
            break
    return 0 if step_deploy(st, args, work, xen, offline, edition, old_build, new_build) else 1


if __name__ == "__main__":
    raise SystemExit(main())
