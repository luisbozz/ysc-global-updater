"""[SCRIPTSPACE] for Enhanced: the page flags a creator script is loaded with to get extra pages.

The game sizes a script resource from the virtual page flags of its entry in script_rel.rpf.
Xenvious raises them (ScriptSpace.cs) so the game allocates room behind the code: extra code
pages for the payload and a data page. The value is "<shipped flags>, <flags to load with>".

The shipped flags come from the RPF entry of the build's .ysc; tools/extract_ysc.py writes them
to scrpatches/disasm/<label>/flags.json. A raw .ysc (RSC7 header) next to the dump works too.

The target layout follows what loaded in game (Enhanced 1.0.1158, all six creators): base
0x2000, one 0x80000 chunk, then 0x40000 chunks up to at least three pages behind the data.
CheckLayout in ScriptSpace.cs has the rules; check() below is the same code.
"""
from __future__ import annotations

import json
import re
import struct
from pathlib import Path

from .common import DISASM, ROOT

PAGE = 0x4000
RAW = ROOT / "enhanced-scripts" / "raw"
# (bit shift, bit count) of each chunk size bucket, largest chunk (base << 8) first.
BUCKETS = ((4, 1), (5, 2), (7, 4), (11, 6), (17, 7), (24, 1), (25, 1), (26, 1), (27, 1))
# Target: base 0x2000 (shift 4), the 0x80000 bucket (base << 6) and the 0x40000 one (base << 5).
TARGET_SHIFT, FIRST, REST = 4, 0x80000, 0x40000


def layout(flags: int) -> list[int]:
    """Chunk sizes the flags describe, in layout order."""
    base = 0x200 << (flags & 0xF)
    sizes = []
    for b, (shift, bits) in enumerate(BUCKETS):
        sizes += [base << (8 - b)] * ((flags >> shift) & ((1 << bits) - 1))
    return sizes


def encode(sizes: list[int], version_bits: int) -> int:
    base = 0x200 << TARGET_SHIFT
    flags = TARGET_SHIFT | (version_bits & 0xF0000000)
    for b, (shift, bits) in enumerate(BUCKETS):
        n = sizes.count(base << (8 - b))
        if n >= 1 << bits:
            raise ValueError(f"{n} chunks of 0x{base << (8 - b):X} do not fit {bits} bit(s)")
        flags |= n << shift
    return flags


def check(shipped: int, target: int) -> str | None:
    """Why the game cannot load the shipped data with the target flags; None when it can."""
    before, after = layout(shipped), layout(target)
    if shipped & 0xF0000000 != target & 0xF0000000:
        return "different resource version bits"
    if len(after) > len(before):
        return f"{len(after)} chunks, the page map has room for {len(before)}"
    end = sum(before)
    if sum(after) - after[-1] >= end:
        return "a chunk lies wholly behind the shipped data"
    if sum(after) < end + 3 * PAGE:
        return "less than three extra pages"
    borders, offset = set(), 0
    for size in before:
        offset += size
        borders.add(offset)
    offset = 0
    for size in after:
        offset += size
        if offset < end and offset not in borders:
            return f"chunk border 0x{offset:X} cuts through a shipped chunk"
    return None


def target(shipped: int) -> int:
    """The flags to load with: the smallest such layout with three pages behind the data."""
    end = sum(layout(shipped))
    rest = max(0, -(-(end + 3 * PAGE - FIRST) // REST))
    return encode([FIRST] + [REST] * rest, shipped)


def shipped_flags(label: str, script: str) -> int | None:
    """Virtual page flags of the build's .ysc: flags.json, else a raw .ysc with RSC7 header."""
    js = DISASM / label / "flags.json"
    if js.exists():
        v = json.loads(js.read_text()).get(script)
        if v:
            return int(v["virtual"], 16)
    for raw in (DISASM / label / f"{script}.ysc", RAW / f"{script}.ysc"):
        if raw.exists():
            head = raw.read_bytes()[:16]
            if head[:4] == b"RSC7":
                return struct.unpack_from("<I", head, 8)[0]
    return None


def values(label: str, scripts: list[str]) -> tuple[dict, dict]:
    """({script: "shipped, target"}, {script: problem}) for the build."""
    from . import pages  # noqa: F401  (puts scrpatches on sys.path)
    from scrasm.yscfull import YscFull
    vals, problems = {}, {}
    for s in scripts:
        dump = DISASM / label / f"{s}.ysc.full"
        shipped = shipped_flags(label, s)
        if shipped is None or not dump.exists():
            problems[s] = "kein Dump mit Seiten-Flags (tools/extract_ysc.py schreibt beides)"
            continue
        if dump.stat().st_size != sum(layout(shipped)):
            # A raw .ysc from another build than the dump.
            problems[s] = (f"Flags 0x{shipped:08X} beschreiben 0x{sum(layout(shipped)):X} Bytes, "
                           f"der Dump hat 0x{dump.stat().st_size:X}")
            continue
        try:
            t = target(shipped)
        except ValueError as e:
            problems[s] = str(e)
            continue
        why = check(shipped, t)
        if why:
            problems[s] = f"0x{t:08X} passt nicht zu 0x{shipped:08X}: {why}"
            continue
        code = YscFull.parse(dump).code_length
        slots = (sum(layout(t)) - -(-sum(layout(shipped)) // PAGE) * PAGE) // PAGE
        # ScriptSpace.Get: code page array of the script plus ours in the first slot, before 0x3E00.
        if (-(-code // PAGE) + slots - 2) * 8 > 0x3E00:
            problems[s] = "Code-Seitenliste passt nicht in die erste Extra-Seite"
            continue
        vals[s] = f"{shipped:08X}, {t:08X}"
    return vals, problems


def scripts_in(ini_text: str) -> list[str]:
    m = re.search(r"^\[SCRIPTSPACE\][^\n]*\n(.*?)(?=^\[|\Z)", ini_text, re.S | re.M)
    return re.findall(r"^(\w+)\s*=", m.group(1), re.M) if m else []


def set_section(ini_text: str, vals: dict, label: str) -> str:
    """[SCRIPTSPACE] with the new values; its comment says which build they are from."""
    m = re.search(r"^\[SCRIPTSPACE\][^\n]*\n(.*?)(?=^\[|\Z)", ini_text, re.S | re.M)
    if not m:
        return ini_text
    body = m.group(1)
    for s, v in vals.items():
        body = re.sub(rf'^({re.escape(s)}\s*=\s*)"[^"]*"', lambda x: f'{x.group(1)}"{v}"', body, flags=re.M)
    body = re.sub(r"^; Shipped flags from \S+\.", f"; Shipped flags from {label}.", body, flags=re.M)
    return ini_text[:m.start(1)] + body + ini_text[m.end(1):]
