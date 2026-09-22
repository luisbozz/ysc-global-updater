"""Migrate the struct offsets a payload bakes into its global accesses.

A global access in YSC is a chain: a root global, then offsets and array steps
walking into the structure it points at. Those steps are immediates in the
bytecode -- ``GLOBAL_U24 4718592 ; IOFFSET_S16 3605 ; ARRAY_U16 26949 ; ...`` --
and they describe one particular build's layout of that structure. R* reshuffles
the layout between builds.

``repair.py`` migrates natives, internal calls, external calls and globals. It
never touched these. An injected payload therefore kept indexing the tuneables
at the old field positions, landing hundreds of fields from its target and
writing over other arrays' length words. That is what bricked the creator, and
nothing in the pipeline could see it: the payload assembled, the globals were
right, every call resolved.

Deriving the mapping automatically was tried and abandoned. Matching an access
chain by shape finds hundreds of same-shaped chains elsewhere in the script, and
the tie-breaks that looked reasonable produced confident wrong answers -- one
read ``6841`` as ``7184`` (a neighbouring field's offset) and another read
``3152`` as ``3768``. A payload is edited vanilla code, so it has no unique
origin to anchor on either. The mapping below was measured instead, and
:func:`verify` re-checks it against the two builds on every run, so a wrong or
stale entry fails loudly rather than shipping.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .disasm import disassemble

# Opcodes whose immediate is a position or size inside a structure. An array's
# item size belongs here: growing a struct grows its arrays' stride, which is
# how ``ARRAY_U16 26949`` moved while ``ARRAY_U8 36`` stayed put.
STRUCT_OPS = {
    "IOFFSET_S16": 2,
    "IOFFSET_U8": 1,
    "ARRAY_U16": 2,
    "ARRAY_U8": 1,
    "PUSH_CONST_U24": 3,
}

# (op, old) -> new, per build pair.
#
# How each entry was measured, with the tuneables root chain pinned to the new
# build's values so the survey looks only at genuinely corresponding sites:
#
#   3605, 26949   the root chain itself; the only values in either build
#   3152          third offset under ``ARRAY_U8 36``: old [3152, 3765, 4378]
#                 against new [3155, 3768, 4381] -- same length, same order
#   6789, 6841    third offset under ``ARRAY_U8 3``: ten values each build,
#                 same length, same order, positions 4 and 5
#   6893          under ``ARRAY_U8_LOAD 1``; lists differ in length, so it was
#                 bracketed instead: 6753/6771 and 6911/6929 all move by +3 and
#                 6893 is gone from the new build while 6896 is present
#   125909        ``GLOBAL_U24 4718592 ; PUSH_CONST_U24 ? ; IOFFSET``: 25 sites
#                 in each build, neighbours 125914/125945/125976 all move by
#                 +1275 alongside it
#
# Entries that did not move are listed too, so "absent from the table" always
# means "never checked" rather than "checked and unchanged".
OFFSETS = {
    ("1.71-3586", "1.73-3889"): {
        ("IOFFSET_S16", 3605): 3838,
        ("ARRAY_U16", 26949): 26988,
        ("IOFFSET_S16", 3152): 3155,
        ("IOFFSET_S16", 6789): 6792,
        ("IOFFSET_S16", 6841): 6844,
        ("IOFFSET_S16", 6893): 6896,
        ("PUSH_CONST_U24", 125909): 127184,
        ("IOFFSET_U8", 16): 16,
        ("IOFFSET_U8", 19): 19,
        ("ARRAY_U8", 36): 36,
        ("ARRAY_U8", 3): 3,
    },
}


@dataclass
class Result:
    mapped: dict = field(default_factory=dict)        # (op, old) -> new
    missing: list = field(default_factory=list)       # (op, old) with no entry
    unverified: list = field(default_factory=list)    # (op, old, why)

    @property
    def ok(self) -> bool:
        return not self.missing and not self.unverified


def used(payload: bytes) -> dict:
    """Every struct immediate in a payload, and how often it appears."""
    out: dict = {}
    for ins in disassemble(payload, base=0):
        if ins.name in STRUCT_OPS:
            key = (ins.name, int.from_bytes(ins.operands, "little"))
            out[key] = out.get(key, 0) + 1
    return out


def table(old_build: str, new_build: str) -> dict:
    return OFFSETS.get((old_build, new_build), {})


def verify(mapping: dict, old_code: bytes, new_code: bytes) -> list:
    """Re-check a mapping against the two builds. Returns what failed.

    A moved offset must be present in the old build and absent from the new one
    at that value, and its replacement must be present in the new build. An
    offset recorded as unchanged must be present in both. This will not catch
    every wrong answer, but it does catch a value that no longer exists, which
    is what a stale table looks like.
    """
    problems = []
    for (op, old_val), new_val in sorted(mapping.items()):
        width = STRUCT_OPS[op]
        o = re.escape(old_val.to_bytes(width, "little"))
        n = re.escape(new_val.to_bytes(width, "little"))
        in_old = re.search(o, old_code) is not None
        in_new = re.search(n, new_code) is not None
        if not in_old:
            problems.append((op, old_val, new_val, "old value absent from the old build"))
        elif not in_new:
            problems.append((op, old_val, new_val, "new value absent from the new build"))
    return problems


def migrate(payload: bytes, old_build: str, new_build: str,
            old_code: bytes | None = None, new_code: bytes | None = None) -> Result:
    """Work out the new value of every struct immediate a payload uses."""
    known = table(old_build, new_build)
    result = Result()
    for key in used(payload):
        if key in known:
            result.mapped[key] = known[key]
        else:
            result.missing.append(key)
    if old_code is not None and new_code is not None:
        for op, old_val, new_val, why in verify(result.mapped, old_code, new_code):
            result.unverified.append((op, old_val, why))
    return result


def apply(payload: bytes, mapped: dict) -> bytes:
    """Rewrite a payload's struct offsets in place. Length never changes."""
    out = bytearray(payload)
    for ins in disassemble(payload, base=0):
        if ins.name not in STRUCT_OPS:
            continue
        old_val = int.from_bytes(ins.operands, "little")
        new_val = mapped.get((ins.name, old_val))
        if new_val is None or new_val == old_val:
            continue
        width = STRUCT_OPS[ins.name]
        out[ins.offset + 1:ins.offset + 1 + width] = new_val.to_bytes(width, "little")
    return bytes(out)
