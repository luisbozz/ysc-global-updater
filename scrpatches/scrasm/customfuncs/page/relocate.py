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
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[2]))
from scrasm.crossbuild import (  # noqa: E402
    CLASS, CONST_OPS, Pair, Script, load, operand_value)

# The payload's variables live in its own data page (string page 63), pushed as 0xFC000 + n.
DATA_PAGE = 0xFC000
# Witness votes are only taken for constants big enough to mean something.
MIN_CONST = 100


class Mover(Pair):
    def __init__(self, a: Script, b: Script, ini_from=None, ini_to=None):
        super().__init__(a, b)
        self.ini_from, self.ini_to = ini_from or {}, ini_to or {}

    # -- votes from Rockstar's code ---------------------------------------------------
    def vote(self, cls: str, value: int, context: tuple = (), near: tuple = (), limit: int = 40):
        """The other build's operand at the places Rockstar's code uses this one.

        A small number like a menu id is used for many unrelated things. Only the places
        followed by the same instructions as in the payload (``context``) mean the same;
        when some places match, only they vote. ``near`` comes from a "~IOFFSET 272"
        comment in the source: only places with these instructions in the next few vote
        (e.g. a menu id is compared with the current-menu field)."""
        sites = self.a.uses.get((cls, value), [])
        if not sites:
            return None, "not used by Rockstar's code"
        if near:
            sites = [i for i in sites
                     if set(near) <= set(self._context(self.a.index, i, CONTEXT))]
            if not sites:
                return None, "no use next to " + ", ".join(near)
        elif context:
            same = [i for i in sites if self._context(self.a.index, i, len(context)) == context]
            if same:
                sites = same
        ib = self.b.index
        votes = collections.Counter()
        tried = 0
        for i in sites:
            if tried >= limit:
                break
            j, how = self.counterpart(i)
            if j is None and how.startswith("Funktion"):
                continue
            tried += 1
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
        return None, "votes split: " + ", ".join(f"{v}x{c}" for v, c in votes.most_common(4)) \
            + "; " + self._split_hint(sites, votes)

    def _split_hint(self, sites, votes) -> str:
        """Which neighbour goes with which answer, so a person (or a "~" comment) can pick."""
        ia = self.a.index
        by = collections.defaultdict(collections.Counter)
        for i in sites:
            for tok in self._context(ia, i, CONTEXT):
                if tok.startswith(("IOFFSET ", "ARRAY ")):
                    by[tok][self._answer(i)] += 1
        clean = [(tok, c) for tok, c in by.items() if len(c) == 1 and None not in c and sum(c.values()) >= 3]
        clean.sort(key=lambda x: -sum(x[1].values()))
        return " ".join(f"next to {tok}: {next(iter(c))}" for tok, c in clean[:3]) or "no clear pattern"

    def _answer(self, i: int):
        j, _ = self.counterpart(i)
        return None if j is None else operand_value(self.b.index.instructions[j])

    @staticmethod
    def _context(index, i: int, n: int) -> tuple:
        return tuple(context_token(ins.name, ins.operands)
                     for ins in index.instructions[i + 1:i + 1 + n])

    # -- ini deltas -----------------------------------------------------------------
    def delta(self, key: str):
        from_v, to_v = self.ini_from.get(key.lower()), self.ini_to.get(key.lower())
        if from_v is None or to_v is None:
            return None
        return to_v - from_v


# How many following instructions must match for a vote to count as the same use.
CONTEXT = 4
_SHORT_PUSH = re.compile(r"^PUSH_CONST_(U8|S16|U24|U32|M?\d|F)")


def context_token(name: str, operands: bytes | str = b"") -> str:
    """An instruction as context: pushes of any size are alike (the compiler picks the
    size by the value), field and array offsets keep their operand."""
    if _SHORT_PUSH.match(name):
        return "PUSH"
    if name.startswith(("IOFFSET", "ARRAY")):
        if isinstance(operands, (bytes, bytearray)):
            operands = str(int.from_bytes(operands, "little"))
        return f"{name.split('_')[0]} {operands}"
    return name


# "~IOFFSET 272" or "~IOFFSET 272,ARRAY 3" in a comment: see Mover.vote.
_NEAR = re.compile(r"~([A-Z_]+(?: -?\d+)?(?:,\s*[A-Z_]+(?: -?\d+)?)*)")
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
        following = [m2 for m2 in (_INS.match(l) or _OP.match(l) for l in lines[n + 1:n + 40]) if m2]
        nxt = following[0].group(1 if following[0].re is _OP else 2) if following else None
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
            ctx = tuple(context_token(f.group(2), f.group(4).strip().split(",")[0].strip())
                        if f.re is _INS else context_token(f.group(1))
                        for f in following[:CONTEXT])
            hint = _NEAR.search(comment)
            near = tuple(t.strip() for t in hint.group(1).split(",")) if hint else ()
            new, why = mv.vote(cls, value, ctx if len(ctx) == CONTEXT else (), near)
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
    """offsets.ini [OFFSETS] key (lower case, without OFFSET_) -> the address Xenvious makes
    of it, for %key deltas."""
    sys.path.insert(0, str(HERE.parents[4]))
    from xupdate.offsets import offsets_values
    from xupdate.paths import address
    out = {}
    for low, (name, value) in offsets_values(Path(path).read_text(encoding="utf-8-sig")).items():
        a = address(value)
        if a is None:
            try:
                a = int(value, 0)
            except ValueError:
                continue
        out[re.sub(r"^offset_", "", low)] = a
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
