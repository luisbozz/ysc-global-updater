"""offsets.ini paths as tokens, and moving the new numbers into the old notation.

Xenvious reads a path with the decompiler's meaning: every ``[..]`` counts the slot before
element 0 (the array size), ``[n /*s*/]`` adds ``1 + n * s``. migrate_offsets.py compares
paths in a looser form (no size slots, ``[n /*s*/]`` folded into a field, a trailing bare
``[i]`` dropped) and writes its result in that form. Taken as is, that result loses the
``[i]`` suffixes Xenvious needs and can turn ``.f_106`` into ``.f_102[0]`` (one slot too
far). ``reshape`` keeps the old path's notation and only moves its numbers.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HEAD = re.compile(r"^\s*(Global|[a-z]?Local)_(\d+)")
_TOKEN = re.compile(r"\.f_(\d+)|\[\s*([^\[\]/]+?)\s*(?:/\*\s*(\d+)\s*\*/)?\s*\]")


@dataclass(frozen=True)
class Tok:
    kind: str          # "f" field, "lit" [n /*s*/], "var" [i /*s*/] or bare [i]
    n: int = 0         # field offset, or the literal index
    stride: int | None = None
    name: str = ""

    def text(self) -> str:
        if self.kind == "f":
            return f".f_{self.n}"
        inner = self.name if self.kind == "var" else str(self.n)
        return f"[{inner} /*{self.stride}*/]" if self.stride is not None else f"[{inner}]"


def parse(path: str):
    """(head, base number, tokens), or None when the text is not a path."""
    m = _HEAD.match(path)
    if not m:
        return None
    head, base, rest, toks, pos = m.group(1), int(m.group(2)), path[m.end():], [], 0
    for t in _TOKEN.finditer(rest):
        if t.start() != pos:
            return None
        pos = t.end()
        if t.group(1) is not None:
            toks.append(Tok("f", int(t.group(1))))
        elif t.group(2).isdigit():
            toks.append(Tok("lit", int(t.group(2)), int(t.group(3)) if t.group(3) else None))
        else:
            toks.append(Tok("var", 0, int(t.group(3)) if t.group(3) else None, t.group(2)))
    if pos != len(rest.rstrip()):
        return None
    return head, base, toks


def address(path: str) -> int | None:
    """What Xenvious makes of the path (tools/builddata in Xenvious): every [..] counts the
    size slot, [n /*s*/] adds 1 + n*s, a named index only the slot."""
    p = parse(path)
    if not p:
        return None
    total = p[1]
    for t in p[2]:
        total += t.n if t.kind == "f" else 1 + (t.n * (t.stride or 1) if t.kind == "lit" else 0)
    return total


def text(head: str, base: int, toks) -> str:
    return f"{head}_{base}" + "".join(t.text() for t in toks)


def loose(toks):
    """migrate_offsets.loose_key's view: field sums between iterations ``[i /*s*/]``."""
    segs, strides, acc = [], [], 0
    for t in toks:
        if t.kind == "f":
            acc += t.n
        elif t.kind == "lit":
            acc += t.n * (t.stride or 1)
        elif t.stride is not None:
            segs.append(acc)
            strides.append(t.stride)
            acc = 0
    segs.append(acc)
    return segs, strides


def reshape(old: str, new: str) -> str | None:
    """``new`` (migrate_offsets' output) written in ``old``'s notation, or None when the
    two have a different structure (then a person has to look)."""
    a, b = parse(old), parse(new)
    if not a or not b or not same_kind(a[0], b[0]):
        return None
    head, _, otoks = a
    _, nbase, ntoks = b
    osegs, _ = loose(otoks)
    nsegs, nstr = loose(ntoks)
    # The migrator may write the size slot of a trailing array as "[i /*s*/]" where the
    # old path says "[i]"; both mean the array's start.
    if (len(osegs) != len(nsegs) and otoks and otoks[-1].kind == "var"
            and otoks[-1].stride is None and ntoks and ntoks[-1].kind == "var"
            and ntoks[-1].stride is not None):
        nsegs, nstr = loose(ntoks[:-1])
    if len(osegs) != len(nsegs):
        return None
    deltas = [n - o for o, n in zip(osegs, nsegs)]
    out, seg, done = [], 0, False
    for t in otoks:
        if t.kind == "var" and t.stride is not None:
            if deltas[seg] and not done:
                out.append(Tok("f", deltas[seg]))
            seg, done = seg + 1, False
            out.append(Tok("var", 0, nstr[seg - 1], t.name))
            continue
        if t.kind == "f" and not done:
            if t.n + deltas[seg] < 0:
                return None
            out.append(Tok("f", t.n + deltas[seg]))
            done = True
            continue
        out.append(t)
    if deltas[seg] and not done:
        # No field in the last segment: the move goes before its suffixes.
        k = len(out)
        while k and out[k - 1].kind in ("var", "lit") and out[k - 1].stride is None:
            k -= 1
        out.insert(k, Tok("f", deltas[seg]))
    return text(head, nbase, out)


def same_kind(a: str, b: str) -> bool:
    """Global vs Global, a local vs a local (the decompiler's type letter may change)."""
    return a == b or (a.endswith("Local") and b.endswith("Local"))


def like(old: str, found: str) -> str | None:
    """A path found in the new source (``found``, maybe longer, with concrete indices)
    written like ``old``, token by token: same fields in the same places, the old index
    names, the new strides. None when the two do not line up."""
    a, b = parse(old), parse(found)
    if not a or not b or not same_kind(a[0], b[0]):
        return None
    head, _, otoks = a
    _, nbase, ntoks = b
    tail = []
    while otoks and otoks[-1].kind == "var" and otoks[-1].stride is None:
        tail.insert(0, otoks[-1])
        otoks = otoks[:-1]
    if len(ntoks) < len(otoks):
        return None
    out = []
    for o, n in zip(otoks, ntoks):
        if o.kind == "f":
            if n.kind != "f":
                return None
            out.append(n)
        elif o.kind == "lit":
            if n.kind != "lit" or n.n != o.n:
                return None
            out.append(n)
        else:
            if n.kind == "f":
                return None
            out.append(Tok("var", 0, n.stride if o.stride is not None else None, o.name))
    return text(head, nbase, out + tail)


def split_last(path: str, k: int) -> str | None:
    """``….f_N`` as ``….f_(N-k).f_k``: how the source writes the y (k=1) and z (k=2) of a
    vector field."""
    p = parse(path)
    if not p:
        return None
    head, base, toks = p
    i = max((j for j, t in enumerate(toks) if t.kind == "f"), default=None)
    if i is None or toks[i].n < k or any(t.kind == "f" or t.stride is not None for t in toks[i + 1:]):
        return None
    toks = toks[:i] + [Tok("f", toks[i].n - k), Tok("f", k)] + toks[i + 1:]
    return text(head, base, toks)


def join_last(path: str) -> str:
    """Undoes split_last."""
    head, base, toks = parse(path)
    fi = [j for j, t in enumerate(toks) if t.kind == "f"]
    if len(fi) >= 2 and fi[-1] == fi[-2] + 1:
        a, b = fi[-2], fi[-1]
        toks = toks[:a] + [Tok("f", toks[a].n + toks[b].n)] + toks[b + 1:]
    return text(head, base, toks)
