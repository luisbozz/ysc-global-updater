"""Step "pages": the custom funcs payloads for the new build.

- Legacy: the victim function (vehicle preset table) is found again in the new build
  (victims/layout.py); that gives [SCRIPTVICTIM], the payload's page_base and the stub
  patch. Enhanced: the payload goes to the first extra page behind the code.
- The sources (page/src/<edition>/*.ysa, written for the build in src/<edition>/BUILD) are
  moved to the new build (relocate.py). What relocate cannot decide becomes a review item
  with the source line and Rockstar's code that votes on it.
- build_page.build assembles them and points the hooks at the new labels.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

from .common import ROOT, Item, snippet

PAGE_DIR = ROOT / "scrpatches" / "scrasm" / "customfuncs" / "page"
sys.path.insert(0, str(PAGE_DIR))
sys.path.insert(0, str(PAGE_DIR / "victims"))
sys.path.insert(0, str(ROOT / "scrpatches"))

PAGE = 0x4000


def work_src(work: Path) -> Path:
    return work / "page-src"


def relocate_sources(edition: str, new_build: str, ini_old: dict, ini_new: dict, work: Path,
                     log=print) -> list[Item]:
    """Writes the sources for new_build into work_src(work); returns review items."""
    import relocate
    from build_page import authored_build
    src = PAGE_DIR / "src" / edition
    old_build = authored_build(edition)
    out = work_src(work)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "BUILD").write_text(new_build + "\n")
    items = []
    for f in sorted(src.glob("*.ysa")):
        if old_build == new_build:
            shutil.copy(f, out / f.name)
            continue
        mv = relocate.Mover(relocate.load(old_build, f.stem), relocate.load(new_build, f.stem),
                            ini_old, ini_new)
        text, report, problems = relocate.relocate(f.read_text(), mv)
        (out / f.name).write_text(text)
        moved = sum(1 for r in report if not r.lstrip().startswith("!!"))
        log(f"    {f.stem}: {moved} Werte verschoben, {problems} offen")
        for r in report:
            m = re.match(r"\s*!! line (\d+): (\S+) (\S+): (.*)", r)
            if not m:
                continue
            line, op, arg, why = int(m.group(1)), m.group(2), m.group(3), m.group(4)
            item = Item(id=f"page:{edition}:{f.stem}:{line}", kind="page",
                        title=f"{f.stem}.ysa Zeile {line}: {op} {arg}",
                        question="Welchen Wert hat das im neuen Build?", old_value=arg,
                        old=snippet(f, line, arg), new=snippet(out / f.name, line, arg), note=why)
            if op == "CALL":
                # Show the called function in both builds: the old one and the most alike.
                from xupdate.patches import code_snippet
                target = int(arg, 0)
                start, ratio = mv.likeliest(target)
                item.question = "Die aufgerufene Funktion hat sich stark geändert. Welche ist es im neuen Build?"
                item.old = code_snippet(old_build, f.stem, target, 1, 24)
                if start is not None:
                    item.new = code_snippet(new_build, f.stem, start, 1, 24)
                    item.guess = f"0x{start:06X}"
                    item.note = f"ähnlichste Funktion mit gleichen Parametern: {ratio:.0%} gleiche Instruktionen"
            else:
                votes = re.findall(r"(\d+)x(\d+)", why)
                item.guess = votes[0][0] if votes else None
                if votes:
                    item.note = why + " — ein Kommentar \"; ~IOFFSET n\" in der Quelle lässt nur " \
                                      "Stellen mit diesem Nachbarn abstimmen."
            items.append(item)
    return items


def apply_source_answers(edition: str, items: list[Item], work: Path) -> None:
    for it in items:
        if not it.id.startswith(f"page:{edition}:") or not it.answer:
            continue
        _, _, script, line = it.id.split(":")
        path = work_src(work) / f"{script}.ysa"
        lines = path.read_text().split("\n")
        k = int(line) - 1
        value = it.guess if it.answer == "übernehmen" else it.answer
        lines[k] = re.sub(r"(\s)" + re.escape(it.old_value) + r"\b", r"\g<1>" + value, lines[k], count=1)
        path.write_text("\n".join(lines))


def victims(new_build: str) -> dict:
    from layout import CREATORS, layout
    return {s: layout(new_build, s) for s in CREATORS}


def set_victim_section(ini_text: str, values: dict, new_build: str) -> str:
    """[SCRIPTVICTIM] with the new values; its comment says which build they are from."""
    from layout import ini_value
    m = re.search(r"^\[SCRIPTVICTIM\][^\n]*\n(.*?)(?=^\[|\Z)", ini_text, re.S | re.M)
    if not m:
        return ini_text
    body = m.group(1)
    for s, v in values.items():
        body = re.sub(rf'^({re.escape(s)}\s*=\s*)"[^"]*"', lambda x: f'{x.group(1)}"{ini_value(v)}"',
                      body, flags=re.M)
    body = re.sub(r"^; From the decompiled \S+ scripts\.", f"; From the {new_build} scripts.", body, flags=re.M)
    body = re.sub(r"^; From the \S+ scripts\.", f"; From the {new_build} scripts.", body, flags=re.M)
    return ini_text[:m.start(1)] + body + ini_text[m.end(1):]


def enhanced_base(new_build: str, script: str) -> int:
    from scrasm.yscfull import YscFull
    full = YscFull.parse(ROOT / "scrpatches" / "disasm" / new_build / f"{script}.ysc.full")
    return (full.code_length + PAGE - 1) // PAGE * PAGE


def update(edition: str, patches: list, old_build: str, new_build: str, ini_text: str,
           ini_old: dict, ini_new: dict, work: Path, log=print):
    """(patches, ini text, items). Writes the moved sources to work_src(work)."""
    items = relocate_sources(edition, new_build, ini_old, ini_new, work, log)
    room = {}
    if edition == "legacy":
        try:
            vals = victims(new_build)
        except Exception as e:  # noqa: BLE001
            items.append(Item(id="page:victims", kind="info", title="Victim-Funktion",
                              question=f"Nicht gefunden: {e}", note="Custom-Funcs bleiben wie sie sind."))
            vals = {}
        ini_text = set_victim_section(ini_text, vals, new_build)
        for p in patches:
            v = vals.get(p.get("script_name"))
            if not v:
                continue
            if p.get("page"):
                p["page_base"] = v["code"]
            elif p.get("patch_name", "").strip() == "give up the vehicle preset table":
                p["pattern"], p["offset"], p["bytes_to_patch"] = v["pattern"], 0, v["stub"]
        room = {s: (v["code"], v["strings"]) for s, v in vals.items()}
    else:
        from . import space
        vals, problems = space.values(new_build, space.scripts_in(ini_text))
        ini_text = space.set_section(ini_text, vals, new_build)
        for s, why in problems.items():
            items.append(Item(id=f"space:{s}", kind="info", title=f"[SCRIPTSPACE] {s}",
                              question=f"Nicht berechnet: {why}.",
                              note="Der alte Wert bleibt; ScriptSpace.CheckLayout lehnt ihn im Spiel ab, "
                                   "wenn er nicht passt, dann fehlen die Extra-Seiten."))
        for p in patches:
            if p.get("page"):
                p["page_base"] = enhanced_base(new_build, p["script_name"])
    return patches, ini_text, items


def assemble(edition: str, new_build: str, patches: list, work: Path,
             room: dict | None = None) -> tuple[list[Item], list[str]]:
    """Assembles the page payloads from the moved sources into ``patches`` (in place)."""
    from build_page import build
    new, msgs, problems = build(edition, new_build, patches, src_dir=work_src(work), room=room)
    for i, b in new.items():
        patches[i]["bytes_to_patch"] = b
    items = []
    for n, pr in enumerate(problems):
        script = pr.split(":")[0]
        items.append(Item(
            id=f"build:{edition}:{n}", kind="info", title=f"Payload {script}",
            question=pr, note="Der Payload bleibt wie im alten Build. Mit \"abschalten\" werden "
                              "der Payload und alle Patches, die ihn aufrufen, abgeschaltet.",
            choices=["abschalten"], guess=None, old_value=script))
    return items, msgs


def disable_script_funcs(patches: list, script: str) -> None:
    """The payload off, and everything that calls into it (else GTA crashes)."""
    from build_page import HOOKS
    for p in patches:
        if p.get("script_name") != script:
            continue
        if p.get("page") or (script, p.get("patch_name", "").strip()) in HOOKS:
            p["enabled"] = False


def victim_room(ini_text: str) -> dict:
    m = re.search(r"^\[SCRIPTVICTIM\](.*?)(?=^\[|\Z)", ini_text, re.S | re.M)
    out = {}
    for line in (m.group(1) if m else "").splitlines():
        v = re.match(r'\s*(\w+)\s*=\s*"([^"]+)"', line)
        if v:
            stub, code, data, strings = (int(x.strip(), 16) for x in v.group(2).split(","))
            out[v.group(1)] = (code, strings)
    return out
