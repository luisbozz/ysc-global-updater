#!/usr/bin/env python3
"""Version-robust anchors for a handful of scalar/code-accessed offsets that are
NOT DATADICT-serialized (no semantic key exists for them) and would otherwise
need to be re-found by hand on every version bump.

Each anchor pins the surrounding, structurally distinctive source text (exact
local-declaration block, literal struct-field constants, or a chain of stable
native-call names) instead of the raw ``Global_`` number or a ``func_N`` name -
both of which are NOT stable across versions (func numbers are per-file/per-
version, Global_ numbers shift whenever earlier globals are inserted/removed).

The anchor is matched against BOTH the old and the new scripts. If it matches
with a single, consistent value in each corpus, the migrated ``Global_``
expression is returned; otherwise (anchor gone, or ambiguous) ``None`` is
returned so the caller can fall through to the normal pipeline / manual review
instead of trusting a stale guess.

Safe/read-only: only reads ``*.c`` files, never writes anything.
"""
from __future__ import annotations

import pathlib
import re

# offset name -> (regex with the numeric Global_/.f_ parts captured, format template)
_ANCHORS: dict[str, tuple[re.Pattern, str]] = {
    # OFFSET_check_creator: first statement of the "can the creator (re)load
    # safely" helper. Anchored on the exact local-declaration block + the
    # literal struct<5> field-constant assignments that immediately follow the
    # Global_ write (the enclosing func_N name/number is NOT stable, this body
    # shape is).
    "OFFSET_check_creator": (
        re.compile(
            r"bool\s+bVar0;\s*int\s+iVar1;\s*int\s+iVar2;\s*struct<5>\s+Var3;\s*"
            r"int\s+iVar98;\s*bool\s+bVar99;\s*int\s+iVar100;\s*"
            r"Global_(\d+)\s*=\s*1;\s*bVar0\s*=\s*false;\s*"
            r"Var3\.f_4\s*=\s*3;\s*Var3\.f_8\s*=\s*3;\s*Var3\.f_64\s*=\s*3;\s*"
            r"Var3\.f_75\s*=\s*3;\s*Var3\.f_91\s*=\s*3;"
        ),
        "Global_{0}",
    ),
    # OFFSET_hide_creator_menu: part of the native-call chain deciding whether
    # the pause/creator menu may show. Anchored on the surrounding native names
    # (stable API surface) rather than the Global_ numbers (which shift).
    "OFFSET_hide_creator_menu": (
        re.compile(
            r"NETWORK::IS_COMMERCE_STORE_OPEN\(\)\)\s*\|\|\s*Global_\d+\)\s*\|\|\s*"
            r"Global_(\d+)\.f_(\d+)\)\s*\|\|\s*HUD::IS_WARNING_MESSAGE_ACTIVE\(\)"
        ),
        "Global_{0}.f_{1}",
    ),
}


def _matches(directory: pathlib.Path, pattern: re.Pattern, template: str) -> set:
    values: set = set()
    for path in sorted(directory.glob("*.c")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in pattern.finditer(text):
            values.add(template.format(*m.groups()))
    return values


def resolve(offset_name: str, old_dir: pathlib.Path, new_dir: pathlib.Path) -> str | None:
    """Resolve a single anchor-known offset, or ``None`` if the offset isn't
    covered, the anchor no longer matches (script structure changed), or the
    matches are ambiguous (safer to fall through than to trust a bad guess)."""
    spec = _ANCHORS.get(offset_name)
    if not spec:
        return None
    pattern, template = spec
    old_values = _matches(old_dir, pattern, template)
    if len(old_values) != 1:
        return None  # anchor missing/ambiguous in the version offsets.ini matches -> don't trust it
    new_values = _matches(new_dir, pattern, template)
    if len(new_values) != 1:
        return None
    return next(iter(new_values))


def build_anchor_map(old_dir: pathlib.Path, new_dir: pathlib.Path) -> dict:
    """Resolve all known anchors at once (cheap: a handful of small regexes
    over ~8 files)."""
    result = {}
    for name in _ANCHORS:
        val = resolve(name, old_dir, new_dir)
        if val:
            result[name] = val
    return result
