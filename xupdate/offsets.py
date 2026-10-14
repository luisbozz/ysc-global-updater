"""Step "offsets": Xenvious' offsets.ini moved from the build it names to the new build.

The source is Xenvious' own offsets.ini (its [BUILD] label says which build it is for), so
every key Xenvious has takes part. tools/run_pipeline.py does the migration through the
decompiled scripts; this module puts the result back into the source file in its own
notation (paths.reshape) and turns everything it is not sure about into review items.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

from . import paths
from .common import ROOT, SCRIPTS, SURE_CLOSE as SURE, Item, snippet_for_path, guess_by_context

_KEY = re.compile(r'^(\s*)(OFFSET_\w+|Offset_\w+)(\s*=\s*)"([^"]*)"(.*)$')
_LABEL = re.compile(r'^(\s*label\s*=\s*)"([^"]*)"', re.M)


def build_label(ini_text: str) -> str | None:
    m = re.search(r"^\[BUILD\][^\[]*?^\s*label\s*=\s*\"([^\"]+)\"", ini_text, re.M | re.S)
    return m.group(1) if m else None


def set_build_label(ini_text: str, label: str) -> str:
    return _LABEL.sub(lambda m: f'{m.group(1)}"{label}"', ini_text, count=1)


def section_of_lines(text: str):
    sec = None
    for line in text.splitlines(keepends=True):
        s = line.strip()
        if s.startswith("[") and s.endswith("]"):
            sec = s[1:-1]
        yield sec, line


def offsets_values(text: str) -> dict:
    """[OFFSETS] key -> value, first key wins (as Xenvious reads it)."""
    out = {}
    for sec, line in section_of_lines(text):
        if sec != "OFFSETS":
            continue
        m = _KEY.match(line.rstrip("\r\n"))
        if m and m.group(2).lower() not in out:
            out[m.group(2).lower()] = (m.group(2), m.group(4))
    return out


def migrate(source: Path, old_build: str, new_build: str, work: Path) -> Path:
    """Run the migration; returns the path of migrate_offsets' result."""
    out, report = work / "offsets.migrated.ini", work / "migrate-report.json"
    cmd = [sys.executable, str(ROOT / "tools" / "run_pipeline.py"),
           "--new", new_build, "--old", old_build, "--offsets", str(source),
           "--out", str(out), "--report-json", str(report),
           "--keep-list", str(work / "sources.keep.txt")]
    log = work / "migrate.log"
    with log.open("w") as f:
        res = subprocess.run(cmd, cwd=str(ROOT), stdout=f, stderr=subprocess.STDOUT)
    if res.returncode != 0:
        raise RuntimeError(f"run_pipeline.py failed, see {log}")
    return out


def merge(source_text: str, migrated_text: str, report: dict, old_build: str,
          new_build: str) -> tuple[str, list[Item]]:
    """The source ini with the migrated values, and the review items."""
    migrated = offsets_values(migrated_text)
    unresolved = {e["offset"].lower() for e in report.get("unresolved", [])}
    unresolved |= {e["offset"].lower() for e in report.get("kept_on_dead_path", [])}
    items, out, seen, moved = [], [], set(), {}
    for sec, line in section_of_lines(source_text):
        m = _KEY.match(line.rstrip("\r\n")) if sec == "OFFSETS" else None
        if not m or m.group(2).lower() in seen:
            out.append(line)
            continue
        key, old = m.group(2), m.group(4)
        seen.add(key.lower())
        if paths.parse(old) is None:      # numbers, strides, empty: not a path
            out.append(line)
            continue
        new_raw = migrated.get(key.lower(), (key, old))[1]
        mig = None if key.lower() in unresolved else paths.reshape(old, new_raw)
        guess = guess_by_context(old, old_build, new_build, key=key)
        ctx = guess.value if guess and paths.parse(guess.value) else None
        sure = guess is not None and guess.score >= SURE
        why, choices = None, []
        if mig is not None and (not sure or paths.address(ctx) == paths.address(mig)):
            new = mig                                   # one answer, or two that agree
        elif mig is None and ctx == old and sure:
            new = old                                   # did not move; the code says so too
        elif mig is None:
            why = ("Die Migration hat keinen neuen Pfad gefunden." if key.lower() in unresolved
                   else f"Der migrierte Pfad hat eine andere Form: {new_raw}")
        else:
            why = "Migration und Kontext nennen verschiedene Werte."
            choices = [mig, ctx]
        if why:
            items.append(Item(
                id=f"offset:{key}", kind="offset", title=key, question=why, old_value=old,
                guess=ctx if (sure or mig is None) else mig,
                old=snippet_for_path(old, old_build, key), new=guess.snippet if guess else None,
                note=(guess.how if guess else "Kein Vorschlag aus dem Code-Vergleich."),
                choices=choices, sure=bool(sure and ctx)))
            new = old  # stays until answered
        else:
            moved[old] = new
        eol = "\r\n" if line.endswith("\r\n") else "\n"
        out.append(f'{m.group(1)}{key}{m.group(3)}"{new}"{m.group(5)}{eol}')
    known = dict(moved)
    known.update({it.old_value: it.guess for it in items if it.guess})
    for it in items:
        if it.guess is None:
            sib = sibling_guess(it.old_value, known)
            if sib:
                it.guess, it.note = sib
                it.note = "Vorschlag " + it.note
    return "".join(out), items


def sibling_guess(old: str, moved: dict):
    """(value, how) from the resolved key in the same struct with the nearest field: the
    fields of one struct usually move together."""
    p = paths.parse(old)
    if not p or not p[2] or p[2][-1].kind != "f":
        return None
    head, base, toks = p
    prefix, last = paths.text(head, base, toks[:-1]), toks[-1].n
    best = None
    for o, n in moved.items():
        q, r = paths.parse(o), paths.parse(n)
        if not q or not r or not q[2] or q[2][-1].kind != "f" or not r[2] or r[2][-1].kind != "f":
            continue
        if paths.text(q[0], q[1], q[2][:-1]) != prefix:
            continue
        dist = abs(q[2][-1].n - last)
        if best is None or dist < best[0]:
            best = (dist, o, n, r)
    if not best:
        return None
    dist, o, n, r = best
    delta = r[2][-1].n - paths.parse(o)[2][-1].n
    value = paths.text(r[0], r[1], r[2][:-1] + [paths.Tok("f", last + delta)])
    return value, f"wie der Nachbar {o} -> {n} (Feld {delta:+d})"


def apply_answers(ini_text: str, answers: dict) -> str:
    """answers: key -> value for the offset items a person answered."""
    lower = {k.lower(): v for k, v in answers.items()}
    out = []
    for sec, line in section_of_lines(ini_text):
        m = _KEY.match(line.rstrip("\r\n")) if sec == "OFFSETS" else None
        if m and m.group(2).lower() in lower:
            eol = "\r\n" if line.endswith("\r\n") else "\n"
            line = f'{m.group(1)}{m.group(2)}{m.group(3)}"{lower[m.group(2).lower()]}"{m.group(5)}{eol}'
            lower.pop(m.group(2).lower())
        out.append(line)
    return "".join(out)


_NEXT = re.compile(r'^(\s*)(OFFSET_\w*next\w*)(\s*=\s*)"?(\d+)"?(\s*(?:;.*)?)$', re.IGNORECASE | re.MULTILINE)


def sync_strides(old_text: str, new_text: str) -> tuple[str, list[str]]:
    """Stride keys (``spawn_NEXT = 71``, ``team_NEXT_settings``) are plain numbers, which the
    migration leaves alone. When the arrays they step through changed their stride
    (``[i /*71*/]`` -> ``[i /*70*/]``), the key follows. Which arrays: the paths of its own
    family (``spawn_*``) first, else every path with that stride. Only paths the migration
    changed count (one it left as it was may not have moved yet); when they disagree, or one
    keeps the old stride, the key stays and a note says so. Returns the text and the notes."""
    from . import paths
    old_vals = {k: v for k, (_, v) in offsets_values(old_text).items()}
    new_vals = {k: v for k, (_, v) in offsets_values(new_text).items()}
    old_next = {m.group(2).lower(): int(m.group(4)) for m in _NEXT.finditer(old_text)}

    def strides(old: int, family: str) -> set:
        found = set()
        for k, ov in old_vals.items():
            nv = new_vals.get(k)
            if nv is None or nv == ov or not k.startswith(family):
                continue
            po, pn = paths.parse(ov), paths.parse(nv)
            if not po or not pn or len(po[2]) != len(pn[2]):
                continue
            for to, tn in zip(po[2], pn[2]):
                if to.kind != "f" and to.stride == old and tn.stride is not None:
                    found.add(tn.stride)
        return found

    out, notes = [], []
    for sec, line in section_of_lines(new_text):
        m = _NEXT.match(line.rstrip("\r\n")) if sec == "OFFSETS" else None
        old = old_next.get(m.group(2).lower()) if m else None
        if m and old is not None and int(m.group(4)) == old:
            key = m.group(2)
            low = key.lower()
            family = low[:low.rindex("next")].rstrip("_") if low.endswith("next") else None
            found = strides(old, family) if family and family != "offset" else set()
            how = f"Stride der {family[7:]}*-Pfade" if found else "Stride aller Pfade mit /*" + str(old) + "*/"
            if not found:
                found = strides(old, "offset_")
            if len(found) == 1 and old not in found:
                new = found.pop()
                eol = "\r\n" if line.endswith("\r\n") else "\n"
                line = f"{m.group(1)}{key}{m.group(3)}{new}{m.group(5)}{eol}"
                notes.append(f"{key}: {old} -> {new} ({how})")
            elif len(found) > 1:
                notes.append(f"{key}: Pfade nennen verschiedene Strides {sorted(found)}, bleibt {old}")
        out.append(line)
    return "".join(out), notes


def load_report(work: Path) -> dict:
    return json.loads((work / "migrate-report.json").read_text(encoding="utf-8"))
