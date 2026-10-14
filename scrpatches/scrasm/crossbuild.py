"""The same script in two builds: which function became which, and which instruction.

Functions are paired by their fingerprint (funcsig.build_address_map). Inside a pair the
instructions are aligned by opcode name (difflib), so an instruction in the old build
has a counterpart in the new one whenever the code around it kept its shape; operands
(addresses, offsets, string positions) are free to differ. Used to carry the payload
sources (customfuncs/page/relocate.py) and the patch sites (doctor.py) to a new build.
"""
from __future__ import annotations

import bisect
import collections
import difflib
import pickle
from dataclasses import dataclass, field
from pathlib import Path

from .funcsig import FunctionIndex, build_address_map
from .natives import NativeResolver
from .yscfull import YscFull

DISASM = Path(__file__).resolve().parents[1] / "disasm"
CACHE = Path(__file__).resolve().parent / ".cache"

CONST_OPS = {"PUSH_CONST_U8", "PUSH_CONST_S16", "PUSH_CONST_U24", "PUSH_CONST_U32"}
STATIC_OPS = {"STATIC_U8", "STATIC_U8_LOAD", "STATIC_U8_STORE",
              "STATIC_U16", "STATIC_U16_LOAD", "STATIC_U16_STORE",
              "STATIC_U24", "STATIC_U24_LOAD", "STATIC_U24_STORE"}
GLOBAL_OPS = {"GLOBAL_U16", "GLOBAL_U16_LOAD", "GLOBAL_U16_STORE",
              "GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE"}
CLASS = {**{n: "const" for n in CONST_OPS}, **{n: "static" for n in STATIC_OPS},
         **{n: "global" for n in GLOBAL_OPS}}


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
    _offsets: list = field(default_factory=list)

    def strings(self) -> bytes:
        return self.full.strings()

    def string_at(self, off: int) -> str | None:
        s = self.full.strings()
        if not 0 <= off < len(s):
            return None
        end = s.find(b"\0", off)
        return s[off:end if end >= 0 else len(s)].decode("latin-1")

    def instruction_at(self, offset: int) -> int | None:
        """Index of the instruction that contains this code offset."""
        if not self._offsets:
            self._offsets = [i.offset for i in self.index.instructions]
        k = bisect.bisect_right(self._offsets, offset) - 1
        return k if k >= 0 else None


def load(build: str, script: str) -> Script:
    path = DISASM / build / f"{script}.ysc.full"
    st = path.stat()
    key = CACHE / f"xb-{build}-{script}-{st.st_size}-{int(st.st_mtime)}.pickle"
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


class Pair:
    """Old and new build of one script."""

    def __init__(self, a: Script, b: Script):
        self.a, self.b = a, b
        self.amap, self.tier = build_address_map(a.index, b.index)
        self._align = {}
        self._near = {}
        self._start_to_k = {f.start_offset: k for k, f in enumerate(b.index.functions)}

    def call(self, addr: int):
        """New start of the function that starts at ``addr`` in the old build."""
        if addr in self.amap:
            return self.amap[addr], self.tier[addr]
        fa = self._func_at.get(addr) if hasattr(self, "_func_at") else None
        if fa is None:
            self._func_at = {f.start_offset: k for k, f in enumerate(self.a.index.functions)}
            fa = self._func_at.get(addr)
        if fa is None:
            return None, "no function starts there"
        fb, how = self.function(fa)
        if fb is None:
            return None, "no match"
        return self.b.index.functions[fb].start_offset, how

    def string(self, off: int):
        """The same text in the new build's string table: (offset, how) or (None, why)."""
        text = self.a.string_at(off)
        if not text:
            return None, "no string"
        s = self.b.strings()
        needle = b"\0" + text.encode("latin-1") + b"\0"
        hits = [0] if s.startswith(needle[1:]) else []
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

    def aligned(self, fa: int, fb: int) -> dict:
        """Old instruction index -> new instruction index inside a function pair."""
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

    def _names(self, index, f) -> list:
        return [index.instructions[i].name for i in range(f.start_index, f.end_index + 1)]

    def function(self, fa: int):
        """(new function index, how) for old function ``fa``, or (None, why).

        Functions without a unique fingerprint (changed too much) are looked for between
        the counterparts of their nearest matched neighbours, by how alike their
        instruction sequences are."""
        fs_a, fs_b = self.a.index.functions, self.b.index.functions
        start = self.amap.get(fs_a[fa].start_offset)
        if start is not None:
            return self._start_to_k[start], self.tier[fs_a[fa].start_offset]
        if fa in self._near:
            return self._near[fa]
        lo = next((self._start_to_k[self.amap[fs_a[k].start_offset]]
                   for k in range(fa - 1, max(-1, fa - 50), -1) if fs_a[k].start_offset in self.amap), None)
        hi = next((self._start_to_k[self.amap[fs_a[k].start_offset]]
                   for k in range(fa + 1, min(len(fs_a), fa + 50)) if fs_a[k].start_offset in self.amap), None)
        result = (None, "Funktion im neuen Build nicht gefunden")
        if lo is None and hi is not None:
            lo = -1
        if lo is not None and hi is None:
            hi = len(fs_b)
        if lo is not None and hi is not None and hi - lo <= 200:
            mine = self._names(self.a.index, fs_a[fa])
            scored = []
            for k in range(lo + 1, hi):
                if (fs_b[k].params, fs_b[k].returns) != (fs_a[fa].params, fs_a[fa].returns):
                    continue
                sm = difflib.SequenceMatcher(None, mine, self._names(self.b.index, fs_b[k]), autojunk=False)
                if sm.real_quick_ratio() >= 0.6 and sm.quick_ratio() >= 0.6:
                    scored.append((sm.ratio(), k))
            scored.sort(reverse=True)
            if scored and scored[0][0] >= 0.6 and (len(scored) == 1 or scored[1][0] < scored[0][0] - 0.1):
                result = (scored[0][1], f"neighbours, {scored[0][0]:.0%} alike")
        self._near[fa] = result
        return result

    def likeliest(self, addr: int):
        """(new function start, similarity) of the most alike function with the same params
        and returns between the matched neighbours, however low; for a person to judge."""
        fs_a, fs_b = self.a.index.functions, self.b.index.functions
        fa = next((k for k, f in enumerate(fs_a) if f.start_offset == addr), None)
        if fa is None:
            return None, 0.0
        lo = next((self._start_to_k[self.amap[fs_a[k].start_offset]]
                   for k in range(fa - 1, max(-1, fa - 50), -1) if fs_a[k].start_offset in self.amap), -1)
        hi = next((self._start_to_k[self.amap[fs_a[k].start_offset]]
                   for k in range(fa + 1, min(len(fs_a), fa + 50)) if fs_a[k].start_offset in self.amap), len(fs_b))
        mine = self._names(self.a.index, fs_a[fa])
        best = (None, 0.0)
        for k in range(lo + 1, min(hi, lo + 201)):
            if (fs_b[k].params, fs_b[k].returns) != (fs_a[fa].params, fs_a[fa].returns):
                continue
            r = difflib.SequenceMatcher(None, mine, self._names(self.b.index, fs_b[k]), autojunk=False).ratio()
            if r > best[1]:
                best = (fs_b[k].start_offset, r)
        return best

    def counterpart(self, i: int):
        """(new instruction index, how the function was matched) or (None, why)."""
        fa = self.a.func_of[i]
        fb, how = self.function(fa)
        if fb is None:
            return None, how
        j = self.aligned(fa, fb).get(i)
        if j is None:
            return None, "Stelle in der Funktion geändert"
        return j, how
