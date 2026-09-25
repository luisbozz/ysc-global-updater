"""Resolve offsets into the MP tunables global (Global_262145) by tunable name.

Every tunable field is filled once, in tuneables_processing, from a registration
call that names the tunable: ``Global_262145.f_15989 = ...(joaat("enable_..."), 0)``
on Legacy, ``... = NETWORK::_NETWORK_GET_TUNABLES_REGISTRATION_BOOL(joaat("ENABLE_..."), false)``
on Enhanced, or a bare hash where the decompiler knows no name. The name (as its
joaat hash, which ignores case) survives a build; the field number does not, and
the generic migration has no path for this global at all -- such offsets kept
their old value silently.

A field is resolved only when its hash is registered exactly once in both
corpora.
"""
from __future__ import annotations

import pathlib
import re

ROOT = "Global_262145"
_REG = re.compile(
    r"\bGlobal_262145\.f_(\d+)\s*=\s*[\w:]+\(\s*(?:joaat\(\"([^\"]+)\"\)|(-?\d+))")


def joaat(text: str) -> int:
    h = 0
    for c in text.lower().encode():
        h = (h + c) & 0xFFFFFFFF
        h = (h + (h << 10)) & 0xFFFFFFFF
        h ^= h >> 6
    h = (h + (h << 3)) & 0xFFFFFFFF
    h ^= h >> 11
    return (h + (h << 15)) & 0xFFFFFFFF


def registrations(directory: pathlib.Path) -> dict:
    """field -> tunable hash, for fields registered exactly once."""
    path = directory / "tuneables_processing.c"
    if not path.is_file():
        return {}
    text = path.read_text(encoding="utf-8", errors="replace")
    fields: dict = {}
    seen: dict = {}
    for m in _REG.finditer(text):
        field = int(m.group(1))
        h = joaat(m.group(2)) if m.group(2) else int(m.group(3)) & 0xFFFFFFFF
        seen.setdefault(field, set()).add(h)
    for field, hashes in seen.items():
        if len(hashes) == 1:
            fields[field] = hashes.pop()
    return fields


def resolve(ini_text: str, old_dir: pathlib.Path, new_dir: pathlib.Path) -> dict:
    """OFFSET name -> new value, for every ini value of the form Global_262145.f_N."""
    old = registrations(old_dir)
    new = registrations(new_dir)
    by_hash: dict = {}
    for field, h in new.items():
        by_hash.setdefault(h, []).append(field)
    out = {}
    for m in re.finditer(r'^(OFFSET_\w+)\s*=\s*"Global_262145\.f_(\d+)"', ini_text, re.MULTILINE):
        name, field = m.group(1), int(m.group(2))
        h = old.get(field)
        targets = by_hash.get(h, []) if h is not None else []
        if len(targets) == 1:
            out[name] = f"{ROOT}.f_{targets[0]}"
    return out
