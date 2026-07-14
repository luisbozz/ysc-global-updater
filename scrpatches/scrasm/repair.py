"""Repair injected ``customfuncs`` payloads for a new game version.

Because every volatile operand in a payload is fixed-width (a NATIVE is always
4 bytes regardless of index, a CALL always 4 bytes regardless of address), the
payload's byte layout is identical across versions -- only operand *values*
change. So we rewrite operands in place, which cannot disturb jumps or offsets:

* NATIVE  -> old index -> canonical hash -> new index (per-script native tables)
* CALL, internal (target inside the injected block) -> relocate by new base
* CALL, external (an R* function) -> new address via fingerprint matching
* GLOBAL_U24 references are reported (they embed offsets migrated by offsets.ini)

The sacrificial injection base is the unique location of the anchor pattern
``2D 04 3A 00 00 38 03`` in each version.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .disasm import disassemble
from .natives import NativeResolver
from .funcsig import FunctionIndex, build_address_map

# ENTER 4,58 + LOCAL_U8_LOAD 3 -- the sacrificial function the payload overwrites
SACRIFICIAL_ANCHOR = bytes([0x2D, 0x04, 0x3A, 0x00, 0x00, 0x38, 0x03])


def find_anchor(code: bytes, anchor: bytes = SACRIFICIAL_ANCHOR) -> list[int]:
    return [m.start() for m in re.finditer(re.escape(anchor), code)]


@dataclass
class RepairReport:
    script: str
    old_base: int
    new_base: int
    native_updated: list = field(default_factory=list)   # (name, old_idx, new_idx)
    native_unchanged: int = 0
    native_missing: list = field(default_factory=list)   # names absent from new table
    internal_calls: int = 0
    external_resolved: list = field(default_factory=list)  # (old, new, confidence)
    external_unresolved: list = field(default_factory=list)  # old addresses
    stride_updated: list = field(default_factory=list)   # (global, ioffset, old, new)
    stride_review: list = field(default_factory=list)    # (global, ioffset, value)
    globals_seen: set = field(default_factory=set)

    @property
    def ok(self) -> bool:
        return (not self.native_missing and not self.external_unresolved
                and not self.stride_review)

    @property
    def needs_review(self) -> list[int]:
        return sorted(self.external_unresolved)


def _put_u24(buf: bytearray, off: int, val: int) -> None:
    buf[off] = val & 0xFF
    buf[off + 1] = (val >> 8) & 0xFF
    buf[off + 2] = (val >> 16) & 0xFF


def _put_u16(buf: bytearray, off: int, val: int) -> None:
    buf[off] = val & 0xFF
    buf[off + 1] = (val >> 8) & 0xFF


def mine_struct_strides(code: bytes) -> dict[tuple[int, int], int]:
    """Mine ``(global, field_ioffset) -> dominant array stride`` from a script.

    Array strides are the element size of a struct array; they embed exactly the
    field offsets that ``offsets.ini`` migrates. R* accesses the same arrays
    thousands of times, so the dominant stride per (global, ioffset) context is
    unambiguous -- letting us derive the old->new stride map from the scripts
    themselves, with no external input.
    """
    from collections import Counter
    from .disasm import disassemble
    ins = disassemble(code)
    ctx: dict[tuple[int, int], Counter] = {}
    for i in range(2, len(ins)):
        a = ins[i]
        if a.name != "ARRAY_U16":
            continue
        io_ins, g_ins = ins[i - 1], ins[i - 2]
        if io_ins.name != "IOFFSET_S16":
            continue
        if g_ins.name not in ("GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE"):
            continue
        ctx.setdefault((g_ins.u24, io_ins.s16), Counter())[a.u16] += 1
    return {k: c.most_common(1)[0][0] for k, c in ctx.items()}


def repair_payload(old_bytes: bytes, old_base: int, new_base: int,
                   old_resolver: NativeResolver, new_resolver: NativeResolver,
                   addr_map: dict[int, int], script: str = "",
                   confidence_of=None,
                   old_strides: dict[tuple[int, int], int] | None = None,
                   new_strides: dict[tuple[int, int], int] | None = None,
                   ) -> tuple[bytes, RepairReport]:
    ins = disassemble(old_bytes, base=old_base)
    new_hash_index = new_resolver.index_of_hash()
    rep = RepairReport(script, old_base, new_base)
    lo, hi = old_base, old_base + len(old_bytes)
    out = bytearray()
    for idx, i in enumerate(ins):
        b = bytearray(i.to_bytes())
        if i.name == "NATIVE":
            h = old_resolver.hash_at(i.native_index)
            name = old_resolver.name_at(i.native_index)
            new_idx = new_hash_index.get(h)
            if new_idx is None:
                rep.native_missing.append(name)
            else:
                if new_idx != i.native_index:
                    rep.native_updated.append((name, i.native_index, new_idx))
                else:
                    rep.native_unchanged += 1
                b[2] = (new_idx >> 8) & 0xFF
                b[3] = new_idx & 0xFF
        elif i.name == "CALL":
            t = i.call_target
            if lo <= t < hi:  # internal cross-func call -> relocate to new base
                _put_u24(b, 1, new_base + (t - old_base))
                rep.internal_calls += 1
            elif t in addr_map:
                nt = addr_map[t]
                _put_u24(b, 1, nt)
                conf = confidence_of(t) if confidence_of else "match"
                rep.external_resolved.append((t, nt, conf))
            else:
                rep.external_unresolved.append(t)
        elif i.name == "ARRAY_U16" and new_strides is not None and idx >= 2:
            io_ins, g_ins = ins[idx - 1], ins[idx - 2]
            if (io_ins.name == "IOFFSET_S16" and g_ins.name in
                    ("GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE")):
                key = (g_ins.u24, io_ins.s16)
                new_s = new_strides.get(key)
                old_s = (old_strides or {}).get(key)
                if new_s is not None and old_s == i.u16 and new_s != i.u16:
                    _put_u16(b, 1, new_s)
                    rep.stride_updated.append((g_ins.u24, io_ins.s16, i.u16, new_s))
                elif new_s is not None and new_s != i.u16 and old_s != i.u16:
                    rep.stride_review.append((g_ins.u24, io_ins.s16, i.u16))
        elif i.name in ("GLOBAL_U24", "GLOBAL_U24_LOAD", "GLOBAL_U24_STORE"):
            rep.globals_seen.add(i.u24)
        out += b
    return bytes(out), rep


@dataclass
class ScriptContext:
    """Cached per-script data needed to repair every payload in that script."""
    old_full: object
    new_full: object
    old_resolver: NativeResolver
    new_resolver: NativeResolver
    old_index: FunctionIndex
    new_index: FunctionIndex
    addr_map: dict[int, int]
    tier: dict[int, str]
    old_base: int | None
    new_base: int | None
    old_strides: dict = field(default_factory=dict)
    new_strides: dict = field(default_factory=dict)

    @classmethod
    def build(cls, old_full, new_full) -> "ScriptContext":
        oldr = NativeResolver.from_full(old_full)
        newr = NativeResolver.from_full(new_full)
        oidx = FunctionIndex.build(old_full.code, oldr)
        nidx = FunctionIndex.build(new_full.code, newr)
        amap, tier = build_address_map(oidx, nidx)
        ob = find_anchor(old_full.code)
        nb = find_anchor(new_full.code)
        return cls(old_full, new_full, oldr, newr, oidx, nidx, amap, tier,
                   ob[0] if len(ob) == 1 else None,
                   nb[0] if len(nb) == 1 else None,
                   mine_struct_strides(old_full.code),
                   mine_struct_strides(new_full.code))

    def confidence_of(self, old_addr: int) -> str:
        return self.tier.get(old_addr, "unmatched")

    def repair(self, old_bytes: bytes, script: str) -> tuple[bytes, RepairReport]:
        if self.old_base is None or self.new_base is None:
            raise ValueError("sacrificial anchor is not unique; cannot locate base")
        return repair_payload(
            old_bytes, self.old_base, self.new_base,
            self.old_resolver, self.new_resolver, self.addr_map,
            script=script, confidence_of=self.confidence_of,
            old_strides=self.old_strides, new_strides=self.new_strides,
        )
