#!/usr/bin/env python3
"""Where each creator keeps the handles of its placed entities ([ENTITYHANDLES] in offsets.ini).

Xenvious shows an edited position, rotation or heading at once by moving the entity that already
stands in the world (EntityMover.cs), instead of a rebuild. For that it needs the entity's handle,
which the creator keeps in an array in its locals, one per kind:

    (*uParam0)[bParam1] = OBJECT::CREATE_OBJECT(Global_5242880.f_1[bParam1 /*170*/].f_7, ...);

The function that creates one placed entity stores the handle at the same index as the job data it
reads. The array comes in as a parameter, so the tool follows the parameter through the callers
until it reaches a local (func_1145(&uLocal_8343, ...)), adding the fields it passes on the way
(&(uParam0->f_1[iVar0]) -> .f_1). The team start points are not entities but decals the creator
adds per team (ADD_DECAL at the start point); removing one makes the creator add it again with the
current position and heading, so their decal handles are found the same way; the same goes
for the spawn points.

The job data arrays are taken from offsets.ini (props_loc, dprops_loc, ...), so a moved global does
not break the search. When the create functions lead to more than one array, the one with the most create sites
wins; a tie leaves the kind out.

Output per creator script, element 0 as a local index and the array size the slot before it holds:
    props=7593:200, dprops=8344:32, obj=5775:32, veh=3922:32, actor=1966:80, team=2731:0+313
"team" is per team: base of team 0, plus the stride of the per-team struct. A size of 0 means the
script text does not give it (Xenvious then only checks the slot is not 0).
The Race Creator also gets "cps=<first>:<size>/<entry stride>", see checkpoint_cache().

    python3 tools/extract_entity_handles.py --scripts scripts/1.73-3889 --ini <offsets.ini>
"""
from __future__ import annotations

import argparse
import pathlib
import re
import sys
from collections import Counter, defaultdict

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from tools.dialect import read_source_lines  # noqa: E402

CREATORS = ("fm_lts_creator", "fm_capture_creator", "fm_deathmatch_creator", "fm_survival_creator",
            "fm_race_creator", "public_mission_creator")
# kind -> offsets.ini key of its job data array (the position field, element 0 of each entry).
KINDS = {"props": "props_loc", "dprops": "dprops_loc", "obj": "obj_loc", "veh": "veh_loc",
         "actor": "actor_loc", "weap": "weap_loc", "team": "player_loc", "spawn": "spawn_loc"}
# Team start points and spawn points are decals, every other kind an entity.
DECALS = ("team", "spawn")
CREATE = {
    # Weapons stand in the editor as weapon objects; pickups only exist in a test.
    "entity": r"(?:OBJECT::CREATE_OBJECT(?:_NO_OFFSET)?|VEHICLE::CREATE_VEHICLE|PED::CREATE_PED|WEAPON::CREATE_WEAPON_OBJECT)",
    "decal": r"GRAPHICS::ADD_DECAL",
}
# Enhanced dumps end a header with "// Position - 0x... (...)".
HDR = re.compile(r"^\S.*?\bfunc_(\d+)\((.*?)\)\s*(?://.*)?$")
_IDX = r"(\w+)(?: /\*\d+\*/)?"


def split_args(s: str) -> list:
    out, depth, cur = [], 0, ""
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur.strip())
    return out


def call_args(line: str, start: int) -> list:
    depth = 1
    for j, ch in enumerate(line[start:]):
        depth += ch == "("
        depth -= ch == ")"
        if depth == 0:
            return split_args(line[start:start + j])
    return []


def data_regex(ini_value: str) -> re.Pattern | None:
    """'Global_4980736.f_201288[i /*4261*/][j /*71*/]' -> regex whose group 1 is the entry index."""
    v = ini_value.strip().strip('"')
    parts = re.split(r"\[[^\]]*\]", v)
    if len(parts) < 2 or not v.startswith("Global_"):
        return None
    rx = ""
    n = len(parts) - 1
    for k, p in enumerate(parts[:-1]):
        rx += re.escape(p) + (r"\[" + _IDX + r"\]" if k == n - 1 else r"\[[^\]]*\]")
    return re.compile(rx + r"(?![\w\[])")


class Script:
    def __init__(self, path: pathlib.Path):
        self.lines = read_source_lines(path)
        self.funcs, self.owner = {}, []
        self.calls = defaultdict(list)
        cur = None
        for i, l in enumerate(self.lines):
            m = HDR.match(l)
            if m:
                cur = m.group(1)
                args = split_args(m.group(2)) if m.group(2).strip() else []
                self.funcs[cur] = [a.split()[-1].lstrip("*") for a in args]
            self.owner.append(cur)
            if not m:
                for c in re.finditer(r"\bfunc_(\d+)\(", l):
                    self.calls[c.group(1)].append((i, c.end()))

    def trace(self, base: str, path: list, fid, depth=0):
        """Follow a parameter to the locals passed for it. Yields (local number, path); path is a
        list of ("f", n) fields and ("a", stride) per-element steps, outermost first."""
        m = re.fullmatch(r"[a-z]*Local_(\d+)", base)
        if m:
            yield int(m.group(1)), path
            return
        if depth > 10 or fid not in self.funcs or base not in self.funcs[fid]:
            return
        k = self.funcs[fid].index(base)
        for li, start in self.calls.get(fid, []):
            args = call_args(self.lines[li], start)
            if k >= len(args):
                continue
            got = parse_arg(args[k], path)
            if got:
                yield from self.trace(got[0], got[1], self.owner[li], depth + 1)


def parse_arg(a: str, path: list):
    """An argument passed for a parameter -> (its base variable, path in front of `path`)."""
    elem = path[:1] == [("elem", 0)]
    m = re.fullmatch(r"&\(?\(?(\w+)(?:\[[\w.]+ /\*(\d+)\*/\]\)?)?((?:(?:->|\.)f_\d+)*)(\[\w+(?: /\*\d+\*/)?\])?\)?", a.strip())
    if not m:
        m2 = re.fullmatch(r"(\w+)", a.strip())
        return (m2.group(1), path) if m2 and not elem else None
    base, stride, fields, index = m.groups()
    if elem != bool(index):
        return None
    pre = [("a", int(stride))] if stride else []
    pre += [("f", int(f)) for f in re.findall(r"f_(\d+)", fields or "")]
    return base, pre + (path[1:] if elem else path)


def lhs_path(lhs: str, idx: str):
    """Handle store -> (base parameter or local, path). None when it is not indexed by idx."""
    lhs = lhs.strip()
    # (*uParam0)[i], in the Enhanced dumps uParam0->[i]
    m = re.fullmatch(r"\(\*(\w+)\)\[" + _IDX + r"\]", lhs) or re.fullmatch(r"(\w+)->\[" + _IDX + r"\]", lhs)
    if m:
        return (m.group(1), []) if m.group(2) == idx else None
    m = re.fullmatch(r"(\w+)((?:(?:->|\.)f_\d+)*)\[" + _IDX + r"\]", lhs)
    if m and m.group(3) == idx:
        return m.group(1), [("f", int(f)) for f in re.findall(r"f_(\d+)", m.group(2))]
    m = re.fullmatch(r"\*(\w+)", lhs)
    if m:
        return m.group(1), [("elem", 0)]
    return None


def declared_sizes(lines: list) -> dict:
    """local number -> its initial values (struct init list, or the one value of a var)."""
    out = {}
    for l in lines[:60000]:
        if l.startswith("#endregion"):
            break
        m = re.match(r"\s*struct<\d+> [a-z]*Local_(\d+) = \{ (.*) \}\s*;", l)
        if m:
            vals = []
            for v in m.group(2).split(","):
                try:
                    vals.append(int(v.strip()))
                except ValueError:
                    vals.append(None)
            out[int(m.group(1))] = vals
            continue
        m = re.match(r"\s*\w+ [a-z]*Local_(\d+) = (-?\d+)\s*;", l)
        if m:
            out[int(m.group(1))] = [int(m.group(2))]
    return out


def resolve(local: int, path: list, inits: dict):
    """(element 0 index, size or 0, per-team stride or 0) of the handle array."""
    at, stride = local, 0
    for kind, n in path:
        if kind == "a":
            stride = n
            at += 1
        elif kind == "f":
            at += n
    size = 0
    if not stride:
        for start, vals in inits.items():
            if start <= at < start + len(vals) and vals[at - start]:
                size = vals[at - start]
                break
    return at + 1, size, stride


def survey(path: pathlib.Path, ini: dict) -> dict:
    s = Script(path)
    inits = declared_sizes(s.lines)
    found = defaultdict(Counter)
    for kind, key in KINDS.items():
        rx = data_regex(ini.get(key, ""))
        if rx is None:
            continue
        create = re.compile(r"^\s*(.+?) = " + CREATE["decal" if kind in DECALS else "entity"] + r"\((.*)\);")
        for i, l in enumerate(s.lines):
            m = create.match(l)
            if not m:
                continue
            g = rx.search(m.group(2))
            if not g:
                continue
            lp = lhs_path(m.group(1), g.group(1))
            if not lp:
                continue
            for local, p in s.trace(lp[0], lp[1], s.owner[i]):
                if all(k != "elem" for k, _ in p):
                    found[kind][resolve(local, p, inits)] += 1
    out = {}
    for k, v in found.items():
        ranked = v.most_common()
        # Some creators keep a second array for a special case (the Mission Creator's
        # alternative object models); the placed entities' array has the most create sites.
        if len(ranked) == 1 or ranked[0][1] > ranked[1][1]:
            out[k] = ranked[0][0]
    return out


def checkpoint_cache(path: pathlib.Path, ini: dict, inits: dict | None = None):
    """The Race Creator's list of checkpoints it shows (fm_race_creator 1.73 func_13649): per
    entry the checkpoint index, secondary flag, marker handle (f_3) and the position it was made
    at (f_4). Every frame a marker whose f_4 no longer matches the job data is deleted and made
    again, so moving f_4 away makes the creator rebuild that one marker with all its settings.
    -> (element 0, size, entry stride) or None."""
    m = re.match(r"(Global_\d+(?:\.f_\d+)*)\[", ini.get("cps_locx", ""))
    if not m:
        return None
    data = re.escape(m.group(1)) + r"\["
    rx = re.compile(r"^\s*(?:\((\w+)\[\w+ /\*(\d+)\*/\]\)->|(\w+)->\[\w+ /\*(\d+)\*/\]\.)f_4 = \{ " + data)
    s = Script(path)
    inits = declared_sizes(s.lines)
    found = set()
    for i, l in enumerate(s.lines):
        g = rx.match(l)
        if not g:
            continue
        base, stride = (g.group(1), g.group(2)) if g.group(1) else (g.group(3), g.group(4))
        for local, p in s.trace(base, [], s.owner[i]):
            first, size, _ = resolve(local, p, inits)
            found.add((first, size, int(stride)))
    return found.pop() if len(found) == 1 else None


def ini_offsets(text: str) -> dict:
    out = {}
    for m in re.finditer(r'^OFFSET_(\w+)\s*=\s*"([^"]*)"', text, re.M):
        out[m.group(1)] = m.group(2)
    return out


def value(found: dict) -> str:
    parts = []
    for kind in KINDS:
        if kind in found:
            base, size, stride = found[kind]
            parts.append(f"{kind}={base}:{size}" + (f"+{stride}" if stride else ""))
    if "cps" in found:
        base, size, stride = found["cps"]
        parts.append(f"cps={base}:{size}/{stride}")
    return ", ".join(parts)


def values(scripts: pathlib.Path, ini_text: str) -> dict:
    ini = ini_offsets(ini_text)
    out = {}
    for c in CREATORS:
        p = scripts / f"{c}.c"
        if p.is_file():
            out[c] = survey(p, ini)
            if c == "fm_race_creator":
                cps = checkpoint_cache(p, ini)
                if cps:
                    out[c]["cps"] = cps
    return out


HEAD = """; Where each creator keeps the handles of its placed entities, so Xenvious can move one in place
; (EntityMover.cs). "<kind>=<local index of element 0>:<array size in the slot before it, 0 unknown>",
; team (start point decals): "+<stride>" per team; cps (Race: the checkpoints the creator shows):
; "/<stride>" per entry, f_4 is the position it made the marker at. Written by ysc-global-updater
; (tools/extract_entity_handles.py)."""


def set_section(ini_text: str, vals: dict, build: str) -> str:
    body = HEAD + f"\n; From the {build} scripts.\n" + "".join(
        f'{c} = "{value(v)}"\n' for c, v in vals.items()) + "\n"
    m = re.search(r"^\[ENTITYHANDLES\][^\n]*\n(.*?)(?=^\[|\Z)", ini_text, re.S | re.M)
    if m:
        return ini_text[:m.start(1)] + body + ini_text[m.end(1):]
    o = re.search(r"^\[OFFSETS\]", ini_text, re.M)
    at = o.start() if o else len(ini_text)
    return ini_text[:at] + "[ENTITYHANDLES]\n" + body + ini_text[at:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scripts", required=True, type=pathlib.Path)
    ap.add_argument("--ini", required=True, type=pathlib.Path, help="offsets.ini of the same build")
    ap.add_argument("--write", action="store_true", help="write [ENTITYHANDLES] into --ini")
    a = ap.parse_args()
    text = a.ini.read_text(encoding="utf-8-sig")
    vals = values(a.scripts, text)
    for c, v in vals.items():
        missing = [k for k in KINDS if k not in v]
        print(f"{c:24} {value(v)}" + (f"   (none: {', '.join(missing)})" if missing else ""))
    if a.write:
        a.ini.write_text(set_section(text, vals, a.scripts.name), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
