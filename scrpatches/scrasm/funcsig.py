"""Cross-version function identification by content fingerprint.

Function *addresses* shift every update, so a raw ``CALL 0xADDR`` in an injected
payload goes stale. But a function's *behaviour* is stable: the sequence of
native HASHES it calls (hashes are version-invariant, unlike indices) plus its
parameter/return counts identify it across versions. This lets us map an old
callee address to its new address.
"""

from __future__ import annotations

from dataclasses import dataclass

from .model import Instruction
from .functions import Function
from .natives import NativeResolver


def native_hash_sequence(instructions: list[Instruction], func: Function,
                         resolver: NativeResolver) -> tuple[int, ...]:
    out = []
    for i in range(func.start_index, func.end_index + 1):
        ins = instructions[i]
        if ins.name == "NATIVE":
            out.append(resolver.hash_at(ins.native_index))
    return tuple(out)


_GLOBAL_OPS = ("GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE",
               "GLOBAL_U16", "GLOBAL_U16_LOAD", "GLOBAL_U16_STORE")


def fingerprint(instructions: list[Instruction], func: Function,
                resolver: NativeResolver) -> tuple:
    """A version-stable identity for a function.

    Combines only features that survive a game update: parameter/return
    counts, the native HASHES it calls, the GLOBAL variables it touches, and
    distinctive large immediate constants. Function addresses and native
    indices (both volatile) are deliberately excluded.
    """
    natives = []
    globals_ = set()
    consts = []
    for i in range(func.start_index, func.end_index + 1):
        ins = instructions[i]
        n = ins.name
        if n == "NATIVE":
            natives.append(resolver.hash_at(ins.native_index))
        elif n in _GLOBAL_OPS:
            globals_.add(ins.u24 if n.endswith(("U24", "U24_LOAD", "U24_STORE")) else ins.u16)
        elif n == "PUSH_CONST_U24":
            consts.append(ins.u24)
        elif n == "PUSH_CONST_U32":
            consts.append(int.from_bytes(ins.operands, "little"))
    return (
        func.params,
        func.returns,
        tuple(natives),
        tuple(sorted(globals_)),
        tuple(sorted(consts)),
    )


@dataclass
class FunctionIndex:
    instructions: list[Instruction]
    functions: list[Function]
    resolver: NativeResolver
    by_offset: dict[int, Function]
    fp_to_funcs: dict[tuple, list[Function]]

    @classmethod
    def build(cls, code: bytes, resolver: NativeResolver) -> "FunctionIndex":
        from .disasm import disassemble
        from .functions import iter_functions
        ins = disassemble(code)
        funcs = list(iter_functions(ins))
        by_off = {f.start_offset: f for f in funcs}
        fp_map: dict[tuple, list[Function]] = {}
        for f in funcs:
            fp = fingerprint(ins, f, resolver)
            fp_map.setdefault(fp, []).append(f)
        return cls(ins, funcs, resolver, by_off, fp_map)

    def fingerprint_of(self, func: Function) -> tuple:
        return fingerprint(self.instructions, func, self.resolver)


def build_address_map(old: FunctionIndex, new: FunctionIndex) -> tuple[dict[int, int], dict[int, str]]:
    """Map old function start-offset -> new function start-offset.

    Returns ``(address_map, tier)`` where ``tier[old_offset]`` records how the
    match was made. Three tiers, most-confident first:

    * ``strict``     -- rich fingerprint unique on both sides
    * ``loose``      -- (params, returns, native-hash sequence) unique both sides
    * ``positional`` -- ordering-preserved gap fill between confident anchors
      (functions keep their relative order across updates), only when the gap
      holds the same number of functions on each side and params/returns agree.
    """
    amap: dict[int, int] = {}
    tier: dict[int, str] = {}

    # tier 1: strict
    for fp, olds in old.fp_to_funcs.items():
        news = new.fp_to_funcs.get(fp)
        if len(olds) == 1 and news and len(news) == 1:
            amap[olds[0].start_offset] = news[0].start_offset
            tier[olds[0].start_offset] = "strict"

    # tier 2: loose (params, returns, native hashes)
    def loose_map(idx: FunctionIndex) -> dict[tuple, list[Function]]:
        m: dict[tuple, list[Function]] = {}
        for f in idx.functions:
            key = (f.params, f.returns, native_hash_sequence(idx.instructions, f, idx.resolver))
            m.setdefault(key, []).append(f)
        return m

    old_loose, new_loose = loose_map(old), loose_map(new)
    for key, olds in old_loose.items():
        if len(olds) != 1 or olds[0].start_offset in amap:
            continue
        news = new_loose.get(key)
        if news and len(news) == 1:
            amap[olds[0].start_offset] = news[0].start_offset
            tier[olds[0].start_offset] = "loose"

    # tier 3: positional interpolation between confident anchors
    old_sorted = sorted(old.functions, key=lambda f: f.start_offset)
    new_sorted = sorted(new.functions, key=lambda f: f.start_offset)
    old_pos = {f.start_offset: i for i, f in enumerate(old_sorted)}
    new_pos = {f.start_offset: i for i, f in enumerate(new_sorted)}
    anchors = sorted((old_pos[o], new_pos[n]) for o, n in amap.items())
    for (oi_a, ni_a), (oi_b, ni_b) in zip(anchors, anchors[1:]):
        if oi_b - oi_a != ni_b - ni_a or oi_b - oi_a <= 1:
            continue  # a function was inserted/removed in this gap -> unsafe
        for k in range(1, oi_b - oi_a):
            of, nf = old_sorted[oi_a + k], new_sorted[ni_a + k]
            if of.start_offset in amap:
                continue
            if of.params != nf.params or of.returns != nf.returns:
                continue
            # structural guard: the two functions must be a similar size, else
            # the gap alignment is suspect (net-zero insert+delete) -> skip.
            ol = of.end_index - of.start_index
            nl = nf.end_index - nf.start_index
            if abs(ol - nl) > max(6, 0.35 * max(ol, nl)):
                continue
            amap[of.start_offset] = nf.start_offset
            tier[of.start_offset] = "positional"

    return amap, tier
