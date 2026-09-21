#!/usr/bin/env python3
"""One diagnostic page per broken scrpatch.

``check_patches.py`` tells you *that* a pattern stopped matching. This tells you
*why*, and how far an automatic fix gets:

  * where in the pattern the match dies (progressive prefix probe)
  * what the pattern was pointing at, disassembled, with each operand marked
    stable or volatile
  * candidate patterns built by wildcarding volatile operands, each verified
    against both builds
  * when no candidate works, the comparable instruction forms in the new build,
    so you can see what the compiler changed

Read-only. Never writes ``data/scrpatches.json`` — a candidate it prints is a
suggestion you apply by hand.

    python3 doctor.py --new 1.73-3889
    python3 doctor.py --new 1.73-3889 --patch "cam fix"
    python3 doctor.py --new 1.73-3889 --all
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT.parent))

import versions  # noqa: E402
from check_patches import (  # noqa: E402
    DISASM, PATCHES, _load, aob_to_regex, classify, is_healthy,
)
from scrasm.disasm import disassemble  # noqa: E402
from scrasm.opcodes import NAME_TO_BYTE, DecodeError  # noqa: E402
from scrasm.yscfull import YscFull  # noqa: E402

# Operand kinds that move between builds, and why. Anything not listed here is
# treated as stable: opcodes never change, and a constant pushed as a literal
# (a native hash, a bit mask, a count) normally survives an update untouched.
VOLATILE = {
    "NATIVE": "native index",
    "CALL": "call address",
    "GLOBAL_U16": "global address",
    "GLOBAL_U24": "global address",
    "GLOBAL_U16_LOAD": "global address",
    "GLOBAL_U24_LOAD": "global address",
    "GLOBAL_U16_STORE": "global address",
    "GLOBAL_U24_STORE": "global address",
    "IOFFSET_U8": "field offset",
    "IOFFSET_U8_LOAD": "field offset",
    "IOFFSET_U8_STORE": "field offset",
    "IOFFSET_S16": "field offset",
    "IOFFSET_S16_LOAD": "field offset",
    "IOFFSET_S16_STORE": "field offset",
    "ARRAY_U8": "array stride",
    "ARRAY_U8_LOAD": "array stride",
    "ARRAY_U8_STORE": "array stride",
    "ARRAY_U16": "array stride",
    "ARRAY_U16_LOAD": "array stride",
    "ARRAY_U16_STORE": "array stride",
    "STATIC_U8": "static slot",
    "STATIC_U16": "static slot",
    "LOCAL_U8": "local slot",
    "LOCAL_U16": "local slot",
}
# Jumps carry a relative target; it shifts whenever anything in between resizes.
VOLATILE_PREFIX = ("J", "IEQ_J", "INE_J", "IGT_J", "IGE_J", "ILT_J", "ILE_J")

BAR = "━"
OK_MARK, BAD_MARK, ARROW = "✓", "✗", "→"

# ---------------------------------------------------------------------------
# Colour. Off unless stdout is a terminal, so a redirect into a file or a pipe
# into grep stays plain text. NO_COLOR is honoured (https://no-color.org).

_CODES = {
    "reset": "0", "bold": "1", "dim": "2",
    "red": "31", "green": "32", "yellow": "33",
    "blue": "34", "magenta": "35", "cyan": "36", "grey": "90",
}
_USE_COLOR = False


def c(text: str, *styles: str) -> str:
    if not _USE_COLOR or not styles:
        return text
    seq = ";".join(_CODES[s] for s in styles)
    return f"\033[{seq}m{text}\033[0m"


def status_colour(status: str) -> tuple:
    return {
        "OK": ("green",),
        "REDERIVED": ("green",),
        "BROKEN": ("red", "bold"),
        "AMBIG_NEW": ("red", "bold"),
        "AMBIG_OLD": ("yellow",),
        "NOT_IN_OLD": ("yellow",),
    }.get(status, ())


def hexdump_pattern(pattern: str) -> str:
    """Colour a pattern so the wildcards stand out from the fixed bytes."""
    if not _USE_COLOR:
        return pattern
    return " ".join(c(t, "grey") if t == "?" else c(t, "cyan") for t in pattern.split())


def is_volatile(name: str) -> str | None:
    if name in VOLATILE:
        return VOLATILE[name]
    if any(name.startswith(p) for p in VOLATILE_PREFIX) and name != "JUMP_TABLE":
        return "jump target"
    return None


def tokens(pattern: str) -> list[str]:
    return pattern.split()


def hits(data: bytes, pattern: str) -> list[int]:
    return [m.start() for m in aob_to_regex(pattern).finditer(data)]


def safe_disassemble(data: bytes, at: int, span: int) -> list:
    """Disassemble from ``at``, shrinking the window until it decodes cleanly.

    A fixed slice usually cuts the last instruction in half, which raises rather
    than returning what it managed to read."""
    for cut in range(span, 1, -1):
        try:
            return disassemble(data[at:at + cut])
        except DecodeError:
            continue
    return []


def break_point(old: bytes, new: bytes, pattern: str) -> tuple[int, int, int] | None:
    """The first token index at which the new build stops matching, with the hit
    counts of the longest still-matching prefix."""
    toks = tokens(pattern)
    prev = (len(hits(old, toks[0])), len(hits(new, toks[0])))
    for n in range(2, len(toks) + 1):
        pre = " ".join(toks[:n])
        ho, hn = len(hits(old, pre)), len(hits(new, pre))
        if hn == 0 and prev[1] > 0:
            return n - 1, prev[0], prev[1]
        prev = (ho, hn)
    return None


def instructions_for(old: bytes, at: int, nbytes: int) -> list:
    """The instructions the pattern spans, decoded from the real old bytes."""
    out = []
    for ins in safe_disassemble(old, at, nbytes + 8):
        if ins.offset >= nbytes:
            break
        out.append(ins)
    return out


def candidates(old: bytes, at: int, pattern: str) -> list[str]:
    """Patterns built by wildcarding the operands of some subset of the spanned
    instructions, fewest wildcards first.

    Only the operands move; the opcode bytes are what makes the pattern mean
    anything, so they are never touched. Instructions the original pattern
    already wildcarded stay wildcarded."""
    toks = tokens(pattern)
    ins = instructions_for(old, at, len(toks))
    if not ins:
        return []

    already = set()
    for idx, i in enumerate(ins):
        span = toks[i.offset + 1: i.offset + 1 + len(i.operands)]
        if span and all(t == "?" for t in span):
            already.add(idx)

    # most volatile first, so the smallest useful candidate surfaces early
    ranked = sorted(
        (idx for idx in range(len(ins)) if idx not in already),
        key=lambda idx: (is_volatile(ins[idx].name) is None, idx),
    )

    seen, out = set(), []
    for size in range(0, len(ranked) + 1):
        for combo in itertools.combinations(ranked, size):
            mask = already | set(combo)
            built = []
            for idx, i in enumerate(ins):
                built.append(f"{old[at + i.offset]:02X}")
                if idx in mask:
                    built += ["?"] * len(i.operands)
                else:
                    built += [f"{b:02X}" for b in
                              old[at + i.offset + 1: at + i.offset + 1 + len(i.operands)]]
            cand = " ".join(built)
            if cand not in seen:
                seen.add(cand)
                out.append(cand)
    return out


def _string_pushes(code: bytes, ins: list, at: int, ysc) -> list:
    """Every ``PUSH_CONST_* ; STRING`` pair in these instructions.

    Returns ``[(instruction, offset_pushed, text)]``. The pushed value is an
    offset into the script's string table."""
    out = []
    for a, b in zip(ins, ins[1:]):
        if b.name != "STRING" or not a.name.startswith("PUSH_CONST_"):
            continue
        ops = a.operands
        if not ops:
            continue
        value = int.from_bytes(ops, "little")
        text = ysc.string_at(value)
        if text:
            out.append((a, value, text))
    return out


def _encode_string_push(opcode_name: str, value: int, width: int) -> str | None:
    """A ``PUSH_CONST_* ; STRING`` byte pattern for this exact string offset."""
    byte = NAME_TO_BYTE.get(opcode_name)
    string_byte = NAME_TO_BYTE.get("STRING")
    if byte is None or string_byte is None:
        return None
    try:
        ops = value.to_bytes(width, "little")
    except OverflowError:
        return None
    toks = [f"{byte:02X}"] + [f"{b:02X}" for b in ops] + [f"{string_byte:02X}"]
    return " ".join(toks)


def string_anchored_sites(old_code: bytes, new_code: bytes, at: int, nbytes: int,
                          old_ysc, new_ysc, limit: int = 4) -> tuple[str, list]:
    """Locate the patched region in the new build by the strings it mentions.

    This mirrors how the site is found by hand: search for a label the code
    references (``FMMC_PRP_PAOP``), not for a byte shape. A string *offset*
    moves on every update, but the string *text* is game content and does not,
    so it identifies the same code across builds even when the compiler emitted
    a completely different instruction sequence.

    Byte-shape anchoring cannot do this. Wildcarding the operands leaves only
    opcodes, and an opcode skeleton repeats at unrelated call sites - which is
    exactly how this tool previously pointed at the wrong code."""
    region = instructions_for(old_code, at, nbytes)
    pushes = _string_pushes(old_code, region, at, old_ysc)
    for ins, _value, text in pushes:
        new_offsets = new_ysc.string_offsets(text)
        if len(new_offsets) != 1:
            continue  # ambiguous or gone: not a usable anchor
        pattern = _encode_string_push(ins.name, new_offsets[0], len(ins.operands))
        if not pattern:
            continue
        found = hits(new_code, pattern)
        if 0 < len(found) <= limit:
            head = old_code[at] if region else None
            sites = []
            for a in found:
                start = _block_start(new_code, a, back=nbytes + 16, want_opcode=head)
                if start is None:      # shape changed too much for the hint
                    start = _block_start(new_code, a, back=nbytes + 16)
                start = a if start is None else start
                sites.append((start, safe_disassemble(new_code, start, nbytes + 16)))
            return text, sites
    return "", []


def _block_start(code: bytes, reach: int, back: int,
                 want_opcode: int | None = None) -> int | None:
    """Walk back from ``reach`` to the start of the statement that reaches it.

    Many addresses before ``reach`` decode cleanly - bytecode has no alignment,
    so a wrong start can still produce a valid-looking instruction stream that
    happens to land on ``reach``. Taking the furthest one overshoots into the
    preceding statement, so take the *nearest*, and when the old block's first
    opcode is known, require that too."""
    for n in range(2, back):
        a = reach - n
        if a < 0:
            return None
        if want_opcode is not None and code[a] != want_opcode:
            continue
        try:
            decoded = disassemble(code[a:reach])
        except DecodeError:
            continue
        if sum(len(i.operands) + 1 for i in decoded) == n:
            return a
    return None


def suggest_region_length(old_ins: list, new_ins: list, window: int = 6) -> int | None:
    """How many bytes the patched region covers in the new build.

    The old and the new region do the same thing and therefore end on the same
    statement, but they need not be the same length: replacing one instruction
    with two (``IOFFSET_S16`` -> ``PUSH_CONST_U24`` + ``IOFFSET``) grows the
    region without changing what it does. So align the two by their trailing
    instruction names and measure to the end of that match."""
    if len(old_ins) < window or len(new_ins) < window:
        return None
    tail = [i.name for i in old_ins[-window:]]
    names = [i.name for i in new_ins]
    for start in range(len(names) - window, -1, -1):
        if names[start:start + window] == tail:
            last = new_ins[start + window - 1]
            return last.offset + len(last.operands) + 1
    return None


def derive_pattern(code: bytes, start: int, ins: list, max_bytes: int) -> str | None:
    """Build a pattern for a relocated block: keep every opcode, wildcard every
    operand that moves, and grow until it is unique in this build."""
    built: list[str] = []
    for i in ins:
        if i.offset >= max_bytes:
            break
        built.append(f"{code[start + i.offset]:02X}")
        # A wide PUSH_CONST carries an address: a string-table offset, or a
        # field offset the compiler could no longer fit in an IOFFSET
        # immediate. Both move. A 1- or 2-byte PUSH_CONST is a real constant
        # from the game logic (a count, an index, a menu id) and is usually the
        # most distinctive byte in the whole block - wildcarding it is what
        # makes a pattern match eight unrelated sites instead of one.
        wide_push = i.name.startswith("PUSH_CONST_") and len(i.operands) >= 3
        if is_volatile(i.name) or wide_push:
            built += ["?"] * len(i.operands)
        else:
            built += [f"{b:02X}" for b in
                      code[start + i.offset + 1: start + i.offset + 1 + len(i.operands)]]
        if len(built) >= 6 and len(hits(code, " ".join(built))) == 1:
            return " ".join(built)
    return None


PROPOSAL_OK = "ok"              # nothing to do
PROPOSAL_AUTO = "auto"          # operand shifted; candidate hits the same site
PROPOSAL_REDERIVED = "rederived"  # shape changed; new pattern found via string anchor
PROPOSAL_MANUAL = "manual"      # nothing usable found


def propose(p: dict, old_build: str, new_build: str) -> dict:
    """What can be done for this patch, as data.

    ``kind`` is one of the PROPOSAL_* constants. For AUTO and REDERIVED the
    result carries a ``pattern`` that has been verified against the builds;
    REDERIVED additionally needs ``derived_for`` written alongside it, and may
    carry a changed ``nbytes`` when the region grew or shrank."""
    script, pattern = p["script_name"], p["pattern"]
    old, new = _load(script, old_build), _load(script, new_build)
    ho, hn = hits(old, pattern), hits(new, pattern)
    disabled = p.get("enabled") is False
    status = ("DISABLED" if disabled
              else classify(len(ho), len(hn), p.get("derived_for"), new_build))
    out = {"patch": p.get("patch_name", "?"), "script": script, "status": status,
           "kind": PROPOSAL_MANUAL, "pattern": None, "nbytes": None,
           "derived_for": None, "reason": ""}
    if disabled:
        # Parked on purpose. Repairing it would be work nobody asked for, and
        # reporting it as broken would be noise.
        out["kind"] = PROPOSAL_OK
        out["reason"] = "abgeschaltet (enabled=false)"
        return out

    values_bad = [v for v in (p.get("values") or [])
                  if not is_healthy(classify(len(hits(old, v.get("pattern", ""))),
                                             len(hits(new, v.get("pattern", ""))),
                                             v.get("derived_for"), new_build))]
    if is_healthy(status) and not values_bad:
        out["kind"] = PROPOSAL_OK
        return out

    if not ho:
        out["reason"] = "Pattern trifft den alten Build nicht"
        return out

    at = ho[0]
    nbytes = len(tokens(p.get("bytes_to_patch", "")))

    # 1. operand shift: a candidate that still hits the very same old site
    if not is_healthy(status):
        for cand in candidates(old, at, pattern)[:40]:
            cold, cnew = hits(old, cand), hits(new, cand)
            if classify(len(cold), len(cnew)) == "OK" and cold == [at]:
                if tokens(cand) != tokens(pattern):
                    out.update(kind=PROPOSAL_AUTO, pattern=cand,
                               reason="Operand gewandert")
                    return out
                break

    # 2. shape change: find the block again by a string it references
    try:
        old_ysc = YscFull.parse(str(DISASM / old_build / f"{script}.ysc.full"))
        new_ysc = YscFull.parse(str(DISASM / new_build / f"{script}.ysc.full"))
    except Exception:
        out["reason"] = "Dumps nicht lesbar"
        return out
    text, sites = string_anchored_sites(old, new, at, nbytes, old_ysc, new_ysc)
    if len(sites) == 1:
        a, decoded = sites[0]
        new_len = suggest_region_length(instructions_for(old, at, nbytes), decoded)
        cand = derive_pattern(new, a, decoded, new_len or nbytes)
        if cand and len(hits(new, cand)) == 1:
            out.update(kind=PROPOSAL_REDERIVED, pattern=cand,
                       nbytes=new_len, derived_for=new_build,
                       reason=f"Instruktionsform geaendert, Anker {text!r}")
            return out

    if values_bad and is_healthy(status):
        out["reason"] = ("Hauptpattern in Ordnung, "
                         + ", ".join(f"values#{v.get('id')}" for v in values_bad)
                         + " gebrochen")
    else:
        out["reason"] = out["reason"] or "kein Kandidat und kein String-Anker"
    return out


def render(p: dict, old_build: str, new_build: str, width: int = 74) -> None:
    script, pattern = p["script_name"], p["pattern"]
    old, new = _load(script, old_build), _load(script, new_build)
    ho, hn = hits(old, pattern), hits(new, pattern)
    status = classify(len(ho), len(hn), p.get("derived_for"), new_build)

    title = f" {p['patch_name']} · {script} "
    rule = BAR * 3 + title + BAR * max(3, width - len(title) - 3)
    print("\n" + c(rule, "bold", "blue"))

    nbytes = len(tokens(p.get("bytes_to_patch", "")))
    meta = f"offset {p.get('offset', 0)} · patcht {nbytes} bytes"
    if p.get("derived_for"):
        meta += f" · derived_for {p['derived_for']}"
    print(f"  {c(f'{status:9}', *status_colour(status))}"
          f" alt={len(ho)} neu={len(hn)}        {c(meta, 'grey')}")
    print(f"\n  {c('Pattern', 'bold')}   {hexdump_pattern(pattern)}")

    if not ho:
        print(f"  {ARROW} trifft schon den alten Build nicht. Falscher --old, oder totes Pattern.")
        return

    bp = break_point(old, new, pattern)
    at = ho[0]
    ins = instructions_for(old, at, len(tokens(pattern)))

    if bp:
        n, pho, phn = bp
        toks = tokens(pattern)
        dead = toks[n] if n < len(toks) else "?"
        # n may land on an operand byte; name the instruction that owns it
        owner = next((i for i in ins
                      if i.offset <= n <= i.offset + len(i.operands)), None)
        where = ""
        if owner is not None:
            part = "opcode" if n == owner.offset else f"operand +{n - owner.offset}"
            where = f"{owner.name} ({part})"
        print(f"  {c('Bricht ab', 'bold')} {c(dead, 'red', 'bold'):4} "
              f"{c(where, 'yellow')}")
        print(f"            {c(f'Praefix bis dahin: alt {pho} / neu {phn}', 'grey')}")

    if ins:
        print(f"\n  {c('Alter Block', 'bold')} {c(f'@0x{at:06X}', 'grey')}")
        for i in ins:
            why = is_volatile(i.name)
            ops = i.operands.hex().upper()
            tag = c(f"  {why}", "yellow") if why else c("  stabil", "green")
            print(f"    {c(f'+{i.offset:<3}', 'grey')} "
                  f"{c(f'{i.name:18}', 'magenta')} {c(f'{ops:10}', 'cyan')}{tag}")

    print(f"\n  {c('Kandidaten', 'bold')}")
    found = False
    impostors = []
    for cand in candidates(old, at, pattern)[:40]:
        cold, cnew = hits(old, cand), hits(new, cand)
        if classify(len(cold), len(cnew)) != "OK":
            continue
        # Counting hits is not enough. A pattern can match exactly one site in
        # each build and still point at different code: classify() never checks
        # that the old hit is the site the original pattern meant.
        if cold != [at]:
            impostors.append((cand, cold[0]))
            continue
        if tokens(cand) == tokens(pattern):
            print(f"    {c(OK_MARK, 'green', 'bold')} Hauptpattern ist unveraendert"
                  f" in Ordnung (alt {len(cold)} / neu {len(cnew)})."
                  f" Bruch liegt woanders.")
        else:
            off = p.get("offset", 0)
            print(f"    {c(OK_MARK, 'green', 'bold')} {hexdump_pattern(cand)}")
            print(f"      {c(f'alt {len(cold)}  neu {len(cnew)}', 'grey')}  "
                  f"{c('OK', 'green', 'bold')}   "
                  f"{c(f'trifft alt 0x{at:06X} (dieselbe Stelle)', 'grey')}   "
                  f"{c(f'offset {off} gueltig', 'grey')}")
        found = True
        break
    for cand, where in impostors[:2]:
        print(f"    {c(BAD_MARK, 'red', 'bold')} {hexdump_pattern(cand)}")
        print(f"      {c(f'alt 1 neu 1, aber trifft alt 0x{where:06X} statt 0x{at:06X}', 'yellow')}"
              f" {ARROW} {c('anderer Code, NICHT uebernehmen', 'red', 'bold')}")
    if not found:
        print(f"    {c(BAD_MARK, 'red', 'bold')} kein Kandidat durch Wildcarding"
              f" der Operanden.")
        print(f"    {ARROW} {c('Instruktionsform hat sich geaendert.', 'yellow')}"
              f" Suche die Stelle ueber die Strings, die sie referenziert.")

        old_ysc = YscFull.parse(str(DISASM / old_build / f"{script}.ysc.full"))
        new_ysc = YscFull.parse(str(DISASM / new_build / f"{script}.ysc.full"))
        text, sites = string_anchored_sites(old, new, at, nbytes, old_ysc, new_ysc)
        if not sites:
            print(f"    {c(BAD_MARK, 'red', 'bold')} kein brauchbarer String-Anker."
                  f" Stelle von Hand suchen, siehe README.")
        else:
            print(f"\n  {c('Anker', 'bold')} {c(repr(text), 'cyan')}"
                  f" {c('(String-Text ist versionsstabil, der Offset nicht)', 'grey')}")
            old_ins = instructions_for(old, at, nbytes)
            for a, decoded in sites:
                print(f"\n  {c('Neue Stelle', 'bold')} {c(f'@0x{a:06X}', 'grey')}")
                for i in decoded[:9]:
                    ops = i.operands.hex().upper()
                    print(f"    {c(f'+{i.offset:<3}', 'grey')} "
                          f"{c(f'{i.name:18}', 'magenta')} {c(ops, 'cyan')}")
                new_len = suggest_region_length(old_ins, decoded)
                cand = derive_pattern(new, a, decoded, new_len or nbytes)
                if cand and len(hits(new, cand)) == 1:
                    n_old = len(hits(old, cand))
                    print(f"\n    {c(OK_MARK, 'green', 'bold')} {hexdump_pattern(cand)}")
                    print(f"      {c(f'neu 1 (eindeutig), alt {n_old}', 'grey')}")
                    if new_len and new_len != nbytes:
                        print(f"      {c(f'ACHTUNG: bytes_to_patch muss {nbytes} -> {new_len} wachsen', 'yellow', 'bold')}")
                    print(f"      {c(f'braucht derived_for: {new_build}', 'yellow')}")

    for v in p.get("values") or []:
        vp = v.get("pattern", "")
        vo, vn = len(hits(old, vp)), len(hits(new, vp))
        vs = classify(vo, vn, v.get("derived_for"), new_build)
        if is_healthy(vs):
            continue
        print(f"\n  {c(f'values#' + str(v.get('id')), 'bold')}  "
              f"{c(vs, *status_colour(vs))}   {c(f'alt={vo} neu={vn}', 'grey')}")
        print(f"    {hexdump_pattern(vp)}")
        if vo:
            vat = hits(old, vp)[0]
            vbp = break_point(old, new, vp)
            if vbp:
                n, pho, phn = vbp
                vt = tokens(vp)
                print(f"    bricht ab {c(vt[n] if n < len(vt) else '?', 'red', 'bold')}"
                      f"   {c(f'(Praefix: alt {pho} / neu {phn})', 'grey')}")
            for cand in candidates(old, vat, vp)[:40]:
                co, cn = len(hits(old, cand)), len(hits(new, cand))
                if classify(co, cn) == "OK":
                    print(f"    {c(OK_MARK, 'green', 'bold')} {hexdump_pattern(cand)}"
                          f"   {c(f'alt {co}  neu {cn}', 'grey')}")
                    break
            else:
                print(f"    {c(BAD_MARK, 'red', 'bold')} kein Kandidat durch Wildcarding.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--patches", default=str(PATCHES))
    ap.add_argument("--new", help="New build. Default: newest folder in disasm/.")
    ap.add_argument("--old", help="Old build. Default: the build right before --new.")
    ap.add_argument("--patch", help="Only patches whose name contains this.")
    ap.add_argument("--script", help="Only this script.")
    ap.add_argument("--color", choices=("auto", "always", "never"), default="auto",
                    help="Farbige Ausgabe. Default auto: nur wenn stdout ein "
                         "Terminal ist und NO_COLOR nicht gesetzt ist.")
    ap.add_argument("--all", action="store_true",
                    help="Also show patches that are already OK.")
    args = ap.parse_args()

    global _USE_COLOR
    if args.color == "always":
        _USE_COLOR = True
    elif args.color == "never":
        _USE_COLOR = False
    else:
        _USE_COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    new_build = versions.resolve(DISASM, args.new)
    old_build = (versions.resolve(DISASM, args.old) if args.old
                 else versions.previous(DISASM, new_build))
    if not old_build:
        raise SystemExit("no earlier build in disasm/ -- pass --old <build>")

    patches = json.loads(pathlib.Path(args.patches).read_text(encoding="utf-8"))

    selected = []
    for p in patches:
        if not p.get("pattern") or not p.get("script_name"):
            continue
        if args.patch and args.patch.lower() not in p["patch_name"].lower():
            continue
        if args.script and args.script != p["script_name"]:
            continue
        old, new = _load(p["script_name"], old_build), _load(p["script_name"], new_build)
        if p.get("enabled") is False and not args.all:
            continue
        st = classify(len(hits(old, p["pattern"])), len(hits(new, p["pattern"])),
                      p.get("derived_for"), new_build)
        if is_healthy(st) and any(
            not is_healthy(classify(
                len(hits(old, v.get("pattern", ""))),
                len(hits(new, v.get("pattern", ""))),
                v.get("derived_for"), new_build))
            for v in (p.get("values") or [])
        ):
            st = "BROKEN"
        if args.all or not is_healthy(st):
            selected.append(p)

    print(f"\n  {c('scrpatches doctor', 'bold', 'blue')}   "
          f"{c(old_build, 'cyan')} {ARROW} {c(new_build, 'cyan')}")
    print(f"  {c(f'{len(selected)} von {len(patches)} Patches', 'grey')}")

    for p in selected:
        render(p, old_build, new_build)

    print(f"\n  {c('Nichts wurde geschrieben.', 'grey')} Kandidaten von Hand nach "
          f"{c('data/scrpatches.json', 'cyan')} uebernehmen, dann "
          f"{c('check_patches.py', 'cyan')} erneut.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
