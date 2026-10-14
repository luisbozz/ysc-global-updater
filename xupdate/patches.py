"""Step "patches": Xenvious' scrpatches.json carried to the new build.

Every patch is carried (scrpatches/carry.py), not only the broken ones: a pattern that
still matches can sit in front of bytes whose operands (field offsets, CALL targets,
string positions) moved. A patch whose pattern and bytes come out unchanged needs
nothing; one that changed is a review item, marked sure when the function was found by
its fingerprint and nothing was left unexplained. Patches carry.py cannot place fall
back to doctor.propose (operand wildcards, string anchors) under a time limit.

The page payloads ("custom funcs in extra page") and the victim stub belong to the
pages step.
"""
from __future__ import annotations

import copy
import signal
import sys
from pathlib import Path

from .common import DISASM, ROOT, WORK, Item, Snippet

sys.path.insert(0, str(ROOT / "scrpatches"))
sys.path.insert(0, str(ROOT))

PAGE_PATCHES = ("give up the vehicle preset table",)
# Patches whose CALL goes into the page payload; build_page.py points those CALLs.
from scrasm.customfuncs.page.build_page import HOOKS  # noqa: E402

TIME_LIMIT = 20  # seconds per patch for the doctor fallback


def is_page(p: dict) -> bool:
    return bool(p.get("page")) or p.get("patch_name", "").strip() in PAGE_PATCHES


def is_hook(p: dict) -> bool:
    return (p.get("script_name"), p.get("patch_name", "").strip()) in HOOKS


class _Timeout(Exception):
    pass


def _limited(fn, *args):
    def stop(*_):
        raise _Timeout()
    old = signal.signal(signal.SIGALRM, stop)
    signal.alarm(TIME_LIMIT)
    try:
        return fn(*args)
    except _Timeout:
        return None
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)


def listing(build: str, script: str) -> Path:
    """The whole script disassembled, one instruction per line (for the review and VS Code)."""
    out = WORK / "disasm" / build / f"{script}.txt"
    if out.is_file():
        return out
    from scrasm.crossbuild import load
    from scrasm.disasm import format_listing
    s = load(build, script)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(format_listing(s.index.instructions) + "\n", encoding="utf-8")
    return out


def code_snippet(build: str, script: str, offset: int, before: int = 6, after: int = 6) -> Snippet | None:
    from scrasm.crossbuild import load
    from .common import snippet
    s = load(build, script)
    i = s.instruction_at(offset)
    if i is None:
        return None
    return snippet(listing(build, script), i + 1, f"0x{s.index.instructions[i].offset:06X}",
                   before, after)


def label(p: dict) -> str:
    return f"{p['script_name']} / {p.get('patch_name', '?').strip()}"


def update(patches: list, old_build: str, new_build: str, file_tag: str,
           log=print) -> tuple[list, list[Item]]:
    """(patches for the new build, review items). Unanswered items keep the old entry."""
    import carry
    from check_patches import _load, check, is_healthy
    import doctor

    health = {}
    for r, p in zip(_check_each(patches, old_build, new_build, check), patches):
        health[id(p)] = r
    out, items = [copy.deepcopy(p) for p in patches], []
    # By script, so carry and the snippets need one script pair in memory at a time.
    order = sorted(range(len(patches)), key=lambda k: patches[k].get("script_name") or "")
    for n in order:
        p = patches[n]
        if not p.get("pattern") or is_page(p) or p.get("enabled") is False:
            continue
        r = health.get(id(p))
        healthy = r is not None and is_healthy(r["status"])
        c = carry.carry(p, old_build, new_build)
        notes = [x for x in c.notes if not (is_hook(p) and x.startswith("CALL"))]
        tag = f"{file_tag}#{n}"
        if c.ok:
            same = (c.pattern == p["pattern"].strip() and c.offset == int(p.get("offset") or 0)
                    and c.bytes_to_patch.split() == p.get("bytes_to_patch", "").split())
            if same or (healthy and c.pattern != p["pattern"].strip()
                        and c.bytes_to_patch.split() == p.get("bytes_to_patch", "").split()
                        and c.offset == int(p.get("offset") or 0)):
                continue  # nothing to change (a healthy pattern stays as written)
            warn = [x for x in notes if not x.startswith(("Funktion per", "Instruktionen", "Patch-Bytes"))
                    and "angepasst" not in x]
            sure = not warn and "neighbours" not in " ".join(notes)
            changes = []
            if c.pattern != p["pattern"].strip():
                changes.append("Pattern")
            if c.offset != int(p.get("offset") or 0):
                changes.append(f"Offset {p.get('offset', 0)} -> {c.offset}")
            if c.bytes_to_patch.split() != p.get("bytes_to_patch", "").split():
                changes.append("Patch-Bytes")
            items.append(Item(
                id=f"patch:{tag}", kind="patch", title=label(p),
                question=("Pattern trifft nicht mehr. " if not healthy else "")
                + "Vorschlag ändert: " + ", ".join(changes),
                old_value=_fmt(p["pattern"], p.get("offset", 0), p.get("bytes_to_patch", "")),
                guess=_fmt(c.pattern, c.offset, c.bytes_to_patch),
                old=code_snippet(old_build, p["script_name"], c.old_site + int(p.get("offset") or 0)),
                new=code_snippet(new_build, p["script_name"], c.new_site + c.offset),
                note="; ".join(notes), sure=sure,
                choices=["übernehmen", "abschalten"]))
            continue
        if healthy:
            continue
        # carry.py found nothing: the doctor's ways, under a time limit
        prop = _limited(doctor.propose, p, old_build, new_build)
        if prop and prop.get("pattern"):
            items.append(Item(
                id=f"patch:{tag}", kind="patch", title=label(p),
                question="Pattern trifft nicht mehr; Vorschlag aus dem Byte-Vergleich (ohne Funktionsabgleich).",
                old_value=_fmt(p["pattern"], p.get("offset", 0), p.get("bytes_to_patch", "")),
                guess=_fmt(prop["pattern"], p.get("offset", 0), p.get("bytes_to_patch", "")),
                note=prop.get("reason", "") + "; Patch-Bytes nicht geprüft", sure=False,
                choices=["übernehmen", "abschalten"]))
        else:
            items.append(Item(
                id=f"patch:{tag}", kind="patch", title=label(p),
                question="Pattern trifft nicht mehr und es gibt keinen Vorschlag.",
                old_value=_fmt(p["pattern"], p.get("offset", 0), p.get("bytes_to_patch", "")),
                note="; ".join(c.notes) + ("; Doctor: Zeitlimit" if prop is None else
                                          "; Doctor: " + prop.get("reason", "")),
                choices=["abschalten"]))
    carry._PAIRS.clear()  # the next step loads its own pairs
    return out, items


def _check_each(patches, old_build, new_build, check):
    """check() per entry, in order; entries without a pattern give None."""
    res = iter(check(patches, old_build, new_build))
    for p in patches:
        yield next(res) if p.get("script_name") and p.get("pattern") else None


def _fmt(pattern, offset, data) -> str:
    return f"{pattern.strip()} | {offset} | {data.strip()}"


def parse_answer(text: str) -> tuple[str, int, str] | None:
    parts = [x.strip() for x in text.split("|")]
    if len(parts) != 3:
        return None
    return parts[0], int(parts[1] or 0), parts[2]


def apply_answers(patches: list, items: list[Item], file_tag: str) -> list:
    """Writes answered items into the patch list. "abschalten" parks a patch."""
    for it in items:
        if not it.id.startswith(f"patch:{file_tag}#") or it.answer in (None, ""):
            continue
        n = int(it.id.rsplit("#", 1)[1])
        q = patches[n]
        if it.answer == "abschalten":
            q["enabled"] = False
            continue
        value = it.guess if it.answer == "übernehmen" else it.answer
        parsed = parse_answer(value or "")
        if parsed:
            q["pattern"], q["offset"], q["bytes_to_patch"] = parsed
            q.pop("derived_for", None)
    return patches
