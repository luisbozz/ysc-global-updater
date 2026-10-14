"""Carries a scrpatch to a new build through the code itself, not through its bytes.

The pattern's site in the old build is an instruction of some Rockstar function. That
function is found in the new build by its fingerprint and aligned instruction by
instruction (scrasm.crossbuild), which gives the site's counterpart even when every
operand and the code around it moved. From there:

- the pattern is rebuilt from the new bytes, with the old pattern's wildcards kept;
- the bytes the patch writes are carried over: an operand the patch copied from the
  original code takes the new original's operand, a CALL into Rockstar's code the new
  address of that function, a string push the new position of the same text. Operands
  the patch chose itself stay.

Everything it finds is a proposal with both sides shown in the review; nothing here
writes a patch file.
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, field

from check_patches import _load, aob_to_regex
from scrasm.crossbuild import Pair, load
from scrasm.disasm import disassemble

_PAIRS: dict = {}
PLACEHOLDER = re.compile(r"\{\{\s*\w+\s*\}\}")


def pair(script: str, old_build: str, new_build: str) -> Pair:
    key = (script, old_build, new_build)
    if key not in _PAIRS:
        _PAIRS.clear()  # one pair at a time: each script is hundreds of MB
        _PAIRS[key] = Pair(load(old_build, script), load(new_build, script))
    return _PAIRS[key]


def hits(data: bytes, pattern: str) -> list[int]:
    return [m.start() for m in aob_to_regex(pattern).finditer(data)]


@dataclass
class Carried:
    pattern: str | None = None
    offset: int | None = None
    bytes_to_patch: str | None = None
    old_site: int | None = None
    new_site: int | None = None
    notes: list = field(default_factory=list)
    ok: bool = False


def _site(p: Pair, old_off: int):
    """New code offset of an old one: (offset, how) or (None, why)."""
    i = p.a.instruction_at(old_off)
    if i is None:
        return None, "Stelle liegt vor dem Code"
    j, how = p.counterpart(i)
    if j is None:
        return None, how
    return p.b.index.instructions[j].offset + (old_off - p.a.index.instructions[i].offset), how


def _instructions(p_script, start: int, nbytes: int) -> list:
    ins = p_script.index.instructions
    i = p_script.instruction_at(start)
    out = []
    while i is not None and i < len(ins) and ins[i].offset < start + nbytes:
        out.append(i)
        i += 1
    return out


def new_pattern(p: Pair, old_code: bytes, new_code: bytes, at: int, new_at: int,
                pattern: str) -> str | None:
    """The old pattern laid over the new site, instruction by instruction: same wildcards,
    new bytes. None when the instructions there differ."""
    toks = pattern.split()
    old_idx = _instructions(p.a, at, len(toks))
    if not old_idx or p.a.index.instructions[old_idx[0]].offset != at:
        return None
    out = []
    pos = new_at
    for i in old_idx:
        oi = p.a.index.instructions[i]
        j, _ = p.counterpart(i)
        if j is None:
            return None
        ni = p.b.index.instructions[j]
        if ni.name != oi.name or ni.offset != pos or len(ni.operands) != len(oi.operands):
            return None
        rel = oi.offset - at
        for k in range(1 + len(oi.operands)):
            if rel + k >= len(toks):
                break
            out.append("?" if toks[rel + k] == "?" else f"{new_code[pos + k]:02X}")
        pos += 1 + len(ni.operands)
    out = out[:len(toks)]
    return grow(new_code, new_at, out)


def grow(code: bytes, at: int, toks: list, limit: int = 80) -> str:
    """Extends a pattern by the instructions after it until it is unique (or ``limit``
    tokens): operands that move between builds become wildcards, as in doctor."""
    from doctor import is_volatile, safe_disassemble
    pat = " ".join(toks)
    if len(hits(code, pat)) <= 1:
        return pat
    for ins in safe_disassemble(code, at, limit + 16):
        if ins.offset < len(toks):
            continue
        if ins.offset != len(toks):
            break  # the pattern ended inside an instruction
        toks.append(f"{code[at + ins.offset]:02X}")
        wide_push = ins.name.startswith("PUSH_CONST_") and len(ins.operands) >= 3
        if is_volatile(ins.name) or wide_push:
            toks += ["?"] * len(ins.operands)
        else:
            toks += [f"{b:02X}" for b in code[at + ins.offset + 1:at + ins.offset + 1 + len(ins.operands)]]
        pat = " ".join(toks)
        if len(hits(code, pat)) == 1 or len(toks) >= limit:
            break
    return pat


def carry_bytes(p: Pair, w_old: int, w_new: int, patch_hex: str, notes: list) -> str | None:
    """bytes_to_patch for the new build, or None when they cannot be read as code."""
    if PLACEHOLDER.search(patch_hex):
        notes.append("Patch-Bytes enthalten Platzhalter; Bytes unverändert übernommen")
        return patch_hex
    raw = bytes.fromhex(patch_hex.replace(" ", ""))
    # The region the patch covers must still end on an instruction in the new build.
    end_old = w_old + len(raw)
    end_new, _ = _site(p, end_old) if p.a.instruction_at(end_old) is not None else (None, "")
    if end_new is not None and end_new - w_new != len(raw):
        notes.append(f"überschriebener Bereich ist jetzt {end_new - w_new} statt {len(raw)} Bytes lang")
    try:
        pins = disassemble(raw)
    except Exception:
        notes.append("Patch-Bytes sind kein Code; unverändert übernommen")
        return patch_hex
    old_code, new_code = p.a.full.code, p.b.full.code
    orig_idx = _instructions(p.a, w_old, len(raw))
    origs = [p.a.index.instructions[i] for i in orig_idx]
    news = []
    for i in orig_idx:
        j, _ = p.counterpart(i)
        news.append(None if j is None else p.b.index.instructions[j])
    sm = difflib.SequenceMatcher(None, [x.name for x in pins], [x.name for x in origs], autojunk=False)
    from_orig = {}
    for blk in sm.get_matching_blocks():
        for k in range(blk.size):
            from_orig[blk.a + k] = blk.b + k
    out = bytearray(raw)
    changed = 0
    for k, ins in enumerate(pins):
        new_ops = None
        o = from_orig.get(k)
        if o is not None and origs[o].operands == ins.operands and news[o] is not None \
                and news[o].name == ins.name and len(news[o].operands) == len(ins.operands):
            new_ops = news[o].operands                     # copied from the original
        elif ins.name == "CALL":
            t, how = p.call(ins.call_target)
            if t is not None:
                new_ops = t.to_bytes(3, "little")
            elif ins.call_target < len(old_code):
                notes.append(f"CALL 0x{ins.call_target:06X}: Ziel im neuen Build nicht gefunden")
        elif ins.name.startswith("PUSH_CONST_U") and k + 1 < len(pins) and pins[k + 1].name == "STRING":
            v = int.from_bytes(ins.operands, "little")
            t, how = p.string(v)
            if t is not None and t < 1 << (8 * len(ins.operands)):
                new_ops = t.to_bytes(len(ins.operands), "little")
            elif t is None:
                notes.append(f"String {how} im neuen Build nicht eindeutig")
        if new_ops is not None and new_ops != ins.operands:
            out[ins.offset + 1:ins.offset + 1 + len(new_ops)] = new_ops
            changed += 1
    if changed:
        notes.append(f"{changed} Operand(en) in den Patch-Bytes angepasst")
    return " ".join(f"{b:02X}" for b in out)


def carry(patch: dict, old_build: str, new_build: str) -> Carried:
    res = Carried()
    script, pattern = patch["script_name"], patch["pattern"]
    p = pair(script, old_build, new_build)
    # Offsets here are code offsets (pages joined), not offsets in the .ysc.full file.
    old_code, new_code = p.a.full.code, p.b.full.code
    ho = hits(old_code, pattern)
    if len(ho) != 1:
        res.notes.append(f"Pattern trifft den alten Build {len(ho)}x")
        return res
    at = res.old_site = ho[0]
    new_at, how = _site(p, at)
    if new_at is None:
        res.notes.append(how)
        return res
    res.new_site = new_at
    res.notes.append(f"Funktion per Fingerabdruck gefunden ({how})")
    pat = new_pattern(p, old_code, new_code, at, new_at, pattern)
    if pat is None:
        from doctor import derive_pattern, safe_disassemble
        pat = derive_pattern(new_code, new_at, safe_disassemble(new_code, new_at, 96), 96)
        if pat:
            pat = grow(new_code, new_at, pat.split())
            res.notes.append("Instruktionen an der Stelle geändert; Pattern neu abgeleitet")
    if not pat or hits(new_code, pat) != [new_at] or len(hits(_load(script, new_build), pat)) != 1:
        res.notes.append("kein eindeutiges Pattern an der neuen Stelle")
        return res
    res.pattern = pat
    off = int(patch.get("offset") or 0)
    w_new, how = _site(p, at + off)
    if w_new is None or w_new < new_at:
        res.notes.append("Schreibstelle im neuen Build nicht gefunden: " + str(how))
        return res
    res.offset = w_new - new_at
    res.bytes_to_patch = carry_bytes(p, at + off, w_new, patch.get("bytes_to_patch", "").strip(), res.notes)
    res.ok = res.bytes_to_patch is not None
    return res
