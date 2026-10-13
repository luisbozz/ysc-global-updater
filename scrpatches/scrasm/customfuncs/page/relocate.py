"""Moves a page payload source (.ysa) from the build it was written for to another build.

The custom funcs call into Rockstar's code and use its data, and all of that moves with an
update: function addresses, statics, string table offsets, enum values (menu ids), struct
fields. Hand-porting them was the step that needed a person who reads bytecode. This does it
from the two builds' bytecode, and says where it is unsure instead of guessing:

- CALL 0xADDR into the script: the function is found in the other build by its fingerprint
  (native hashes, globals, constants; scrasm.funcsig), so the call gets the new address.
- PUSH_CONST_* n ; STRING: the text at n is looked up in the other build's string table.
- A number with a "%key" comment (e.g. "PUSH_CONST_U24 127184 ; %clrovr"): moves by as much
  as that offsets.ini key moved between the two builds.
- Any other number Rockstar's code uses too (statics, globals, PUSH_CONST of an enum): every
  place in Rockstar's functions that uses it is aligned with the same function in the other
  build, and the operand found there is a vote. A clear majority wins; a split is reported.
- Numbers Rockstar's code never uses (the payload's own data page, small constants, struct
  fields) stay as they are.

    python3 relocate.py --script fm_lts_creator --from 1.73-3889 --to 1.74-4000 \
        --src src/legacy/fm_lts_creator.ysa [--ini-from old.ini --ini-to new.ini] [--out x.ysa]
"""
from __future__ import annotations

import argparse
import collections
import difflib
import pickle
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from scrasm.yscfull import YscFull  # noqa: E402
from scrasm.natives import NativeResolver  # noqa: E402
from scrasm.funcsig import FunctionIndex, build_address_map  # noqa: E402

DISASM = HERE.parents[2] / "disasm"
CACHE = HERE / ".cache"
# The payload's variables live in its own data page (string page 63), pushed as 0xFC000 + n.
DATA_PAGE = 0xFC000

CONST_OPS = {"PUSH_CONST_U8", "PUSH_CONST_S16", "PUSH_CONST_U24", "PUSH_CONST_U32"}
STATIC_OPS = {"STATIC_U8", "STATIC_U8_LOAD", "STATIC_U8_STORE",
              "STATIC_U16", "STATIC_U16_LOAD", "STATIC_U16_STORE",
              "STATIC_U24", "STATIC_U24_LOAD", "STATIC_U24_STORE"}
GLOBAL_OPS = {"GLOBAL_U16", "GLOBAL_U16_LOAD", "GLOBAL_U16_STORE",
              "GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE"}
CLASS = {**{n: "const" for n in CONST_OPS}, **{n: "static" for n in STATIC_OPS},
         **{n: "global" for n in GLOBAL_OPS}}
# Witness votes are only taken for constants big enough to mean something.
MIN_CONST = 100


def operand_value(ins) -> int | None:
    n = ins.name
    if n in ("PUSH_CONST_U8",) or n.startswith("STATIC_U8"):
        return ins.u8
    if n == "PUSH_CONST_S16":
        return ins.s16
    if n.endswith(("U16", "U16_LOAD", "U16_STORE")):
        return ins.u16
    if n.endswith(("U24", "U24_LOAD", "U24_STORE")):
        return ins.u24
    if n == "PUSH_CONST_U32":
        return int.from_bytes(ins.operands, "little")
    return None


@dataclass
class Script:
    build: str
    name: str
    full: YscFull
    index: FunctionIndex
    func_of: list  # instruction index -> Function index in index.functions
    uses: dict = field(default_factory=dict)  # (class, value) -> [instruction index]

    def strings(self) -> bytes:
        return self.full.strings()

    def string_at(self, off: int) -> str | None:
        s = self.full.strings()
        if not 0 <= off < len(s):
            return None
        end = s.find(b"\0", off)
        return s[off:end if end >= 0 else len(s)].decode("latin-1")


def load(build: str, script: str) -> Script:
    path = DISASM / build / f"{script}.ysc.full"
    st = path.stat()
    key = CACHE / f"reloc-{build}-{script}-{st.st_size}-{int(st.st_mtime)}.pickle"
    full = YscFull.parse(path)
    if key.is_file():
        index, func_of, uses = pickle.loads(key.read_bytes())
        index.resolver = NativeResolver.from_full(full)
        return Script(build, script, full, index, func_of, uses)
    index = FunctionIndex.build(full.code, NativeResolver.from_full(full))
    func_of = [0] * len(index.instructions)
    for k, f in enumerate(index.functions):
        for i in range(f.start_index, f.end_index + 1):
            func_of[i] = k
    uses = collections.defaultdict(list)
    for i, ins in enumerate(index.instructions):
        cls = CLASS.get(ins.name)
        if cls:
            v = operand_value(ins)
            if v is not None:
                uses[(cls, v)].append(i)
    uses = dict(uses)
    CACHE.mkdir(exist_ok=True)
    resolver, index.resolver = index.resolver, None
    key.write_bytes(pickle.dumps((index, func_of, uses)))
    index.resolver = resolver
    return Script(build, script, full, index, func_of, uses)


class Mover:
    def __init__(self, a: Script, b: Script, ini_from=None, ini_to=None):
        self.a, self.b = a, b
        self.amap, self.tier = build_address_map(a.index, b.index)
        self.ini_from, self.ini_to = ini_from or {}, ini_to or {}
        self._align = {}

    # -- functions ------------------------------------------------------------------
    def call(self, addr: int):
        if addr in self.amap:
            return self.amap[addr], self.tier[addr]
        return None, "no match"

    # -- strings --------------------------------------------------------------------
    def string(self, off: int):
        text = self.a.string_at(off)
        if text is None or not text:
            return None, "no string"
        s = self.b.strings()
        needle = b"\0" + text.encode("latin-1") + b"\0"
        hits = []
        if s.startswith(needle[1:]):
            hits.append(0)
        start = 0
        while True:
            i = s.find(needle, start)
            if i < 0:
                break
            hits.append(i + 1)
            start = i + 1
        if len(hits) == 1:
            return hits[0], f'"{text}"'
        return None, f'"{text}" found {len(hits)} times'

    # -- votes from Rockstar's code ---------------------------------------------------
    def _aligned(self, fa: int, fb: int) -> dict:
        key = (fa, fb)
        if key not in self._align:
            ia, ib = self.a.index, self.b.index
            A, B = ia.functions[fa], ib.functions[fb]
            na = [ia.instructions[i].name for i in range(A.start_index, A.end_index + 1)]
            nb = [ib.instructions[i].name for i in range(B.start_index, B.end_index + 1)]
            m = {}
            for blk in difflib.SequenceMatcher(None, na, nb, autojunk=False).get_matching_blocks():
                for k in range(blk.size):
                    m[A.start_index + blk.a + k] = B.start_index + blk.b + k
            self._align[key] = m
        return self._align[key]

    def vote(self, cls: str, value: int, limit: int = 40):
        sites = self.a.uses.get((cls, value), [])
        if not sites:
            return None, "not used by Rockstar's code"
        ia, ib = self.a.index, self.b.index
        start_to_k = {f.start_offset: k for k, f in enumerate(ib.functions)}
        votes = collections.Counter()
        tried = 0
        for i in sites:
            if tried >= limit:
                break
            fa = self.a.func_of[i]
            new_start = self.amap.get(ia.functions[fa].start_offset)
            if new_start is None:
                continue
            tried += 1
            j = self._aligned(fa, start_to_k[new_start]).get(i)
            if j is None:
                continue
            ins = ib.instructions[j]
            if CLASS.get(ins.name) == cls:
                votes[operand_value(ins)] += 1
        if not votes:
            return None, f"{len(sites)} uses, none in a function found in the other build"
        (best, n), = votes.most_common(1)
        total = sum(votes.values())
        if n >= 2 and n >= 0.6 * total or total == 1:
            how = f"{n}/{total} votes" + (" (single)" if total == 1 else "")
            return best, how
        return None, "votes split: " + ", ".join(f"{v}x{c}" for v, c in votes.most_common(4))

    # -- ini deltas -----------------------------------------------------------------
    def delta(self, key: str):
        from_v, to_v = self.ini_from.get(key.lower()), self.ini_to.get(key.lower())
        if from_v is None or to_v is None:
            return None
        return to_v - from_v


_INS = re.compile(r"^(\s*)([A-Z][A-Z0-9_]*)(\s+)([^;]*?)(\s*)(;.*)?$")
_OP = re.compile(r"^\s*([A-Z][A-Z0-9_]*)\b(?!:)")


def relocate(text: str, mv: Mover):
    """(new text, report lines, number of problems)."""
    lines = text.split("\n")
    out, report, problems = [], [], 0
    parsed = [_INS.match(l) for l in lines]
    for n, (line, m) in enumerate(zip(lines, parsed)):
        if not m or not m.group(4).strip():
            out.append(line)
            continue
        op, arg, comment = m.group(2), m.group(4).strip(), m.group(6) or ""
        nxt = next((o.group(1) for o in map(_OP.match, lines[n + 1:]) if o), None)
        new, why = None, None
        try:
            value = int(arg, 0)
        except ValueError:
            out.append(line)
            continue
        sym = re.search(r"%(\w+)", comment)
        if sym:
            d = mv.delta(sym.group(1))
            if d is None:
                why, problems = f"%{sym.group(1)} not in both offsets.ini", problems + 1
            else:
                new, why = value + d, f"%{sym.group(1)} moved {d:+d}"
        elif op == "CALL":
            new, why = mv.call(value)
            if new is None:
                problems += 1
        elif op in CONST_OPS and nxt == "STRING" and value < DATA_PAGE:
            new, why = mv.string(value)
            if new is None:
                problems += 1
        elif CLASS.get(op) in ("static", "global") or (op in CONST_OPS and value >= MIN_CONST and value < DATA_PAGE):
            cls = CLASS[op]
            new, why = mv.vote(cls, value)
            if new is None and not why.startswith("not used"):
                problems += 1
            elif new is None:
                why = None  # the payload's own number
        if new is None or new == value:
            if why and new is None:
                report.append(f"  !! line {n + 1}: {op} {arg}: {why}")
            out.append(line)
            continue
        fmt = f"0x{new:06X}" if arg.lower().startswith("0x") else str(new)
        report.append(f"  line {n + 1}: {op} {arg} -> {fmt}  ({why})")
        out.append(f"{m.group(1)}{op}{m.group(3)}{fmt}{m.group(5)}{comment}")
    return "\n".join(out), report, problems


def ini_numbers(path) -> dict:
    """offsets.ini [OFFSETS] key (lower case, without OFFSET_) -> number, for %key deltas."""
    sys.path.insert(0, str(Path("/opt/xenvious/tools/builddata")))
    from builddata import is_number, number, parse_ini, member_name
    ini = parse_ini(Path(path).read_text(encoding="utf-8-sig"))
    out = {}
    for (sec, low), v in ini.items():
        if sec == "OFFSETS" and v.strip() and is_number(v):
            out[member_name(ini.names[(sec, low)]).lower()] = number(v)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--script", required=True)
    ap.add_argument("--from", dest="src_build", required=True)
    ap.add_argument("--to", dest="dst_build", required=True)
    ap.add_argument("--src", type=Path, required=True)
    ap.add_argument("--ini-from", type=Path)
    ap.add_argument("--ini-to", type=Path)
    ap.add_argument("--out", type=Path)
    a = ap.parse_args()
    mv = Mover(load(a.src_build, a.script), load(a.dst_build, a.script),
               ini_numbers(a.ini_from) if a.ini_from else None,
               ini_numbers(a.ini_to) if a.ini_to else None)
    text, report, problems = relocate(a.src.read_text(), mv)
    print("\n".join(report))
    moved = sum(1 for r in report if not r.lstrip().startswith("!!"))
    print(f"{a.script}: {moved} moved, {problems} unresolved")
    if a.out:
        a.out.write_text(text)
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
