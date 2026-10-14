#!/usr/bin/env python3
"""What moves with a rule when two rules of a team swap places (Xenvious' rule_fields.json).

Only the Public Mission Creator can move a rule ("PMCI_BTN_RLMV"). It swaps the rule with
its neighbour in four parts, all on the job data every creator shares:

  data   per-rule arrays in the team block and a few global [rule] arrays: text, time limit,
         points, play area, ... (the function after the move button that copies the
         [rule] and [rule +- 1] entries; fm 1.73 func_1443)
  bits   per-team bitsets with one bit per rule (func_1451)
  refs   fields elsewhere that hold a rule number: jumps, spawn and cleanup rules, ... They
         are swapped when they belong to the moved team (func_1395(16, ...) and the
         helpers it calls, case 16 / iParam0 == 16 (13 in 1.72); func_1400 for a number, the
         six-argument helpers for a bitset of rules)
  prio   the entities' own rule (priority) per team; the Mission Creator rebuilds those
         from its rule list, the other creators keep them in the entity (func_1438 loops
         over peds, vehicles, objects and locations and func_1439 names the field)

The code is shared with other scripts: func_18() (is public_mission_creator running) is true
here, so its parts count; the "!func_18()" parts are left out, and so is what loops over the
Mission Creator's own rule list (the other creators have none).

Paths are in decompiler notation with named indices: t the team, r the rule, other names are
loops whose counts are in "loops" (a number, a path whose value is the count, or "rules:<team>",
the team's rule count).

Usage
-----
    python3 tools/extract_rule_fields.py --scripts scripts/<build> [--out rule_fields.json]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

SCRIPT = "public_mission_creator.c"
_PATH = r"Global_\d+(?:\.f_\d+|\[(?:[^\[\]]|\[[^\[\]]*\])*\])*"
# Some decompiles print small numbers as the names of an unrelated enum (PV_COMP_BERD for 1).
_ENUMS = {n: str(i) for i, n in enumerate(
    ["PV_COMP_HEAD", "PV_COMP_BERD", "PV_COMP_HAIR", "PV_COMP_UPPR", "PV_COMP_LOWR", "PV_COMP_HAND",
     "PV_COMP_FEET", "PV_COMP_TEEF", "PV_COMP_ACCS", "PV_COMP_TASK", "PV_COMP_DECL", "PV_COMP_JBIB"])}
_ENUMS.update({"false": "0", "true": "1"})

# func_1395(16, rule, 0, list, team): the reference update after a move.
_REFS_CALL = r"func_\d+\(\d+, \w+, (?:0|false), \w+, "
_HEADER = re.compile(r"^(?:else\s+)?(if|while|for|switch)\s*\((.*)\)\s*$|^(case\s+[^:]+|default):$|^else$")


class Script:
    def __init__(self, path: pathlib.Path):
        self.lines = path.read_text(encoding="utf-8", errors="replace").split("\n")
        self.starts = {}
        for k, line in enumerate(self.lines):
            m = re.match(r"^\w[\w *]*\b(func_\d+)\(", line)
            if m and not line.rstrip().endswith(";"):
                self.starts.setdefault(m.group(1), k)

    def body(self, fn: str) -> list[str]:
        s = self.starts[fn]
        e = next(k for k in range(s + 1, len(self.lines)) if self.lines[k].startswith("}"))
        return self.lines[s + 1:e]

    def params(self, fn: str) -> list[str]:
        head = self.lines[self.starts[fn]]
        inner = head[head.index("(") + 1:head.index(")")]
        return [p.split()[-1].lstrip("*") for p in inner.split(",") if p.strip()]


def statements(body: list[str]):
    """(statement, enclosing headers) by indentation: a header (if/while/for/switch/case/else)
    covers the lines indented deeper than it, with or without braces."""
    stack: list[tuple[int, str, str]] = []
    for line in body:
        text = line.strip()
        if not text or text in ("{", "}") or text.startswith("//"):
            continue
        indent = len(line) - len(line.lstrip("\t"))
        while stack and stack[-1][0] >= indent:
            stack.pop()
        m = _HEADER.match(text)
        if m:
            kind = m.group(1) or ("case" if m.group(3) else "else")
            stack.append((indent, kind, m.group(2) or m.group(3) or ""))
            continue
        yield text, [(k, c) for _, k, c in stack]


def args(call: str) -> list[str]:
    """Top-level arguments of the first call in the text."""
    i = call.index("(") + 1
    out, depth, cur = [], 0, ""
    for ch in call[i:]:
        if ch in "([":
            depth += 1
        elif ch in ")]":
            if depth == 0:
                out.append(cur.strip())
                return out
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur.strip())
            cur = ""
            continue
        cur += ch
    raise ValueError(f"unbalanced call: {call}")


def strip_ref(arg: str) -> str:
    a = arg.strip()
    if a.startswith("&"):
        a = a[1:].strip()
    if a.startswith("(") and a.endswith(")"):
        a = a[1:-1].strip()
    return a.lstrip("*")


def loop_of(kind: str, cond: str):
    """(variable, count) of a while/for header counting from 0, else None."""
    if kind == "for":
        parts = [p.strip() for p in cond.split(";")]
        if len(parts) != 3:
            return None
        cond = parts[1]
    m = re.match(r"^(\w+) <= \(?(.+?)\)?$", cond.strip())
    if not m:
        return None
    var, limit = m.group(1), m.group(2).strip()
    n = re.match(r"^(.+?) - 1$", limit)
    if n:
        return var, n.group(1).strip().lstrip("*").strip("()")
    if re.fullmatch(r"\d+", limit):
        return var, str(int(limit) + 1)
    return None


class Extractor:
    def __init__(self, scripts: pathlib.Path):
        self.s = Script(scripts / SCRIPT)
        self.notes: list[str] = []
        self.pmc_check = self._pmc_check()
        self.move = self._move_calls()

    def _pmc_check(self) -> str | None:
        for fn, k in self.s.starts.items():
            body = " ".join(self.s.body(fn)[:4])
            if "GET_NUMBER_OF_THREADS_RUNNING_THE_SCRIPT_WITH_THIS_HASH" in body and (
                    "-2136380241" in body or 'joaat("public_mission_creator")' in body):
                return fn
        return None

    def _move_calls(self) -> dict:
        """The calls the move button makes (moving up): bits, data, select, refs."""
        lines = self.s.lines
        k = next(i for i, l in enumerate(lines) if '"PMCI_BTN_RLMV"' in l)
        calls = []
        for l in lines[k:k + 40]:
            m = re.match(r"^\s*(func_\d+)\((.*)\);\s*$", l)
            if m:
                calls.append((m.group(1), l.strip()))
            if len(calls) >= 4 and re.match(_REFS_CALL, calls[-1][1]):
                break
        calls = [c for c in calls if not c[1].endswith("();")]
        refs = next(c for c in calls if re.match(_REFS_CALL, c[1]))
        i = calls.index(refs)
        bits, data, _select = calls[i - 3], calls[i - 2], calls[i - 1]
        team_list = args(bits[1])[0]           # the Mission Creator's rule list global
        # The kind of thing the references point at: a rule (16 in 1.73, 13 in 1.72).
        kind = int(re.match(r"func_\d+\((\d+),", refs[1]).group(1))
        return {"bits": bits[0], "data": data[0], "refs": refs[0], "list": team_list, "kind": kind}

    def team(self, path: str) -> str:
        return re.sub(rf"\[\*?{re.escape(self.move['list'])}\.f_4( /\*\d+\*/)?\]", r"[t\1]", path)

    def skip(self, headers) -> bool:
        """Parts for the other creators only (!func_18()); func_18() itself is true here."""
        return any(self.pmc_check and f"!{self.pmc_check}()" in c for _, c in headers)

    def loops(self, headers) -> dict:
        out = {}
        for kind, cond in headers:
            if kind in ("while", "for"):
                lp = loop_of(kind, cond)
                if lp:
                    out[lp[0]] = self.team(lp[1])
        return out

    def swap_size(self, fn: str, path: str) -> int:
        """Slots a two-pointer swap helper exchanges: its struct<N>, 1 for a plain value, and for
        a struct copy without a size (Enhanced: "unk = { *p }") the element the path ends at."""
        body = " ".join(self.s.body(fn)[:8])
        m = re.search(r"struct<(\d+)>", body)
        if m:
            return int(m.group(1))
        if "{" not in body.split("=", 1)[-1]:
            return 1
        last = re.search(r"\[[^\[\]]*?(?:/\*(\d+)\*/)?\]$", path)
        return int(last.group(1)) if last and last.group(1) else 1

    # -- parts -----------------------------------------------------------------------------

    def bits(self) -> list[str]:
        out = []
        for text, _ in statements(self.s.body(self.move["bits"])):
            for a in re.findall(r"&\((" + _PATH + r")\)|&(" + _PATH + r")", text):
                p = self.team(a[0] or a[1])
                if p not in out:
                    out.append(p)
        return out

    def data(self) -> list[dict]:
        # The data function is the first of the checks the move wrapper ANDs together that
        # copies rule entries.
        wrapper = self.s.body(self.move["data"])
        first_if = next(l for l in wrapper if l.strip().startswith("if ("))
        fns = re.findall(r"(func_\d+)\(", first_if)
        fn = next(f for f in fns if any("COPY_SCRIPT_STRUCT" in l for l in self.s.body(f)))
        body = self.s.body(fn)
        p0 = self.s.params(fn)[0]
        other = next(m.group(1) for l in body for m in [re.match(rf"^\s*(\w+) = {p0} \+ 1;", l)] if m)
        out, seen = [], set()
        for text, headers in statements(body):
            if self.skip(headers):
                continue
            if "COPY_SCRIPT_STRUCT" in text:
                a = args(text[text.index("COPY_SCRIPT_STRUCT"):])
                path, size = strip_ref(a[1]), int(a[2])
            elif re.match(r"^func_\d+\(&", text):
                a = args(text)
                if len(a) != 2:
                    continue
                path = strip_ref(a[0])
                size = self.swap_size(re.match(r"^(func_\d+)", text).group(1), path)
            else:
                continue
            if not re.search(rf"\[{other}\b", path) or path.startswith(self.move["list"]):
                continue
            path = self.team(re.sub(rf"\[{other}\b", "[r", path))
            if path in seen:
                continue
            seen.add(path)
            entry = {"path": path, "size": size}
            lp = {v: c for v, c in self.loops(headers).items() if re.search(rf"\[{v}\b", path)}
            if lp:
                entry["loops"] = lp
            out.append(entry)
        return out

    def prio(self) -> list[dict]:
        """func_1438-like: loops over entity counts with a call naming the priority field."""
        sel = None
        lines = self.s.lines
        k = next(i for i, l in enumerate(lines) if '"PMCI_BTN_RLMV"' in l)
        for l in lines[k:k + 40]:
            m = re.match(r"^\s*(func_\d+)\(&", l.strip()) or re.match(r"^(func_\d+)\(&", l.strip())
            if m:
                sel = m.group(1)
                break
        out = []
        for text, headers in statements(self.s.body(sel)):
            m = re.match(r"^(func_\d+)\((\d+), (\w+), (\w+)\);$", text)
            if not m:
                continue
            fn, typ, evar, tvar = m.groups()
            lp = self.loops(headers)
            if evar not in lp:
                continue
            for t2, h2 in statements(self.s.body(fn)):
                if not any(kind == "case" and cond == f"case {typ}" for kind, cond in h2):
                    continue
                f = re.match(r"^(" + _PATH + r") = 99999;$", t2)
                if f:
                    ps = self.s.params(fn)
                    path = f.group(1)
                    path = re.sub(rf"\[{ps[1]}\b", "[e", path)
                    path = re.sub(rf"\[{ps[2]}\]", "[t]", path)
                    out.append({"path": path, "loops": {"e": lp[evar]}})
                    break
        return out

    def refs(self) -> list[dict]:
        out = []
        disp = self.s.body(self.move["refs"])
        first = self.s.params(self.move["refs"])[0]
        helpers = []
        for l in disp:
            m = re.match(rf"^\s*(func_\d+)\({first},", l)
            if m:
                helpers.append(m.group(1))
        for h in helpers:
            ps = self.s.params(h)
            p_kind = ps[0]
            for text, headers in statements(self.s.body(h)):
                rk = self.move["kind"]
                in_rule = any(kind == "case" and cond == f"case {rk}" for kind, cond in headers) and any(
                    kind == "switch" and cond.strip() == p_kind for kind, cond in headers)
                in_rule = in_rule or any(kind == "if" and re.search(rf"\b{p_kind} == {rk}\b", cond) for kind, cond in headers)
                if not in_rule or self.skip(headers):
                    continue
                m = re.match(r"^(func_\d+)\(", text)
                if not m:
                    continue
                a = args(text)
                if len(a) not in (6, 7) or not a[2].lstrip().startswith("&"):
                    continue
                moved = a[3]        # the helper's parameter that holds the moved team
                entry = {"kind": "val" if len(a) == 7 else "bits",
                         "path": re.sub(rf"\[{moved}\]", "[t]", strip_ref(a[2])),
                         "team": "t" if a[4] == moved else strip_ref(a[4])}
                lp = self.loops(headers)
                for v, c in lp.items():
                    if self.move["list"] in c:
                        # Counted by the Mission Creator's own rule list, which the others lack:
                        # "rules:<team>" is the team's rule count.
                        tv = re.search(r"\[\*?(\w+)", c)
                        lp[v] = "rules:" + ("t" if tv and tv.group(1) == moved else tv.group(1) if tv else "t")
                if lp:
                    entry["loops"] = lp
                conds = []
                for kind, cond in headers:
                    if kind != "if" or re.search(rf"\b{p_kind} == {rk}\b", cond):
                        continue
                    if self.pmc_check and cond.strip() == f"{self.pmc_check}()":
                        continue
                    # "... || other != -1": always true when two rules swap (other is the second rule).
                    if len(a) == 7 and f"{a[5]} != -1" in [t.strip(" ()") for t in cond.split("||")]:
                        continue
                    c = re.fullmatch(r"(" + _PATH + r") (==|!=) (-?\d+|\w+)", cond.strip())
                    value = c and (c.group(3) if re.fullmatch(r"-?\d+", c.group(3)) else _ENUMS.get(c.group(3)))
                    if value is not None:
                        conds.append(f"{c.group(1)} {c.group(2)} {value}")
                    else:
                        conds.append(None)
                if None in conds:
                    self.notes.append(f"{h}: skipped (condition not understood): {text[:90]}")
                    continue
                if conds:
                    entry["if"] = conds
                out.append(entry)
        return out

    def run(self) -> dict:
        if not self.pmc_check:
            self.notes.append("no 'is the Mission Creator running' check found; nothing skipped for it")
        return {"bits": self.bits(), "data": self.data(), "prio": self.prio(), "refs": self.refs()}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--scripts", required=True, type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path)
    a = ap.parse_args()
    ex = Extractor(a.scripts)
    res = ex.run()
    print(f"bits {len(res['bits'])}, data {len(res['data'])}, prio {len(res['prio'])}, refs {len(res['refs'])}",
          file=sys.stderr)
    for n in ex.notes:
        print("  " + n, file=sys.stderr)
    text = json.dumps(res, indent=1) + "\n"
    if a.out:
        a.out.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
