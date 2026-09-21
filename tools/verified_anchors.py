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
    # OFFSET_vsbsout: the ini stores the BASE field of a small literal-index int
    # array (vsclout/vsthout/vsenout/vshwout/vstgout/vsbsout share one array,
    # ``f_BASE[0..5]``; offsets.ini's raw number is exactly the array base, i.e.
    # the same field used for the "vsclout" (index 0) DATADICT key in both
    # corpora. The generic pipeline misses this because the DATADICT_SET_INT
    # call sits behind a helper-function parameter alias it doesn't trace.
    "OFFSET_vsbsout": (
        re.compile(
            r'DATADICT_SET_INT\(\*\w+,\s*"vsclout",\s*Global_4718592\.f_(\d+)\[0\]\)'
        ),
        "Global_4718592.f_{0}",
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


# ---------------------------------------------------------------------------
# actor "weapon slot" sub-array (f_161.f_9 / stride 27 in 1.71): a nested
# array-inside-a-struct-field whose base offset has NO index in offsets.ini
# (Xenvious always reads slot 0), so the generic pipeline's index-aware path
# matching cannot see it (the offsets.ini value and the script's own canonical
# form have a different bracket-nesting shape) and the family-consensus
# post-pass only interpolates a straight-line delta, which is wrong whenever a
# field gets inserted *inside* this sub-array (as happened between 1.71 and
# 1.73: a new "agsf" field pushed OFFSET_actor_agvr from .f_15 to .f_16).
# Each leaf still has a stable, unique DATADICT key though (built via
# func_N(&label, "KEY", idx[, -1]); followed by the writer call on the very
# next line) - so resolve every leaf directly from that key instead of
# guessing a delta.
_ACTOR_ROOT = "Global_4980736"
_ACTOR_WEAPON_SLOT_KEYS = {
    "OFFSET_actor_actv": "actv",
    "OFFSET_actor_achf": "achf",
    "OFFSET_actor_awt": "awt",
    "OFFSET_actor_awr": "awr",
    "OFFSET_actor_awl": "awl",
    "OFFSET_actor_awlr": "awlr",
    "OFFSET_actor_agrd": "agrd",
    "OFFSET_actor_ags": "ags",
    "OFFSET_actor_agvr": "agvr",
}


def _actor_weapon_slot_key_pattern(key: str) -> re.Pattern:
    return re.compile(
        rf'"{re.escape(key)}",\s*\w+,\s*-1(?:,\s*-1)?\);\s*\r?\n\s*'
        rf'\w+\([^\n]*?{re.escape(_ACTOR_ROOT)}\.f_\d+\[\w+\s*/\*\d+\*/\]\.f_(\d+)\.f_9\[\w+\s*/\*(\d+)\*/\]\.f_(\d+)'
    )


def _actor_weapon_slot_matches(directory: pathlib.Path) -> dict:
    """{key -> (root_field, sub-array stride, leaf_field)}, only for keys that
    resolve to exactly one consistent tuple across all *.c files."""
    results = {}
    for key in _ACTOR_WEAPON_SLOT_KEYS.values():
        pattern = _actor_weapon_slot_key_pattern(key)
        found = set()
        for path in sorted(directory.glob("*.c")):
            text = path.read_text(encoding="utf-8", errors="ignore")
            for m in pattern.finditer(text):
                found.add((int(m.group(1)), int(m.group(2)), int(m.group(3))))
        if len(found) == 1:
            results[key] = next(iter(found))
    return results


def resolve_actor_weapon_slot_family(old_dir: pathlib.Path, new_dir: pathlib.Path) -> dict:
    """Resolve OFFSET_actor_actv_bs/_NEXT + the 9 leaf offsets from their
    DATADICT keys. Returns {} unless ALL 9 keys resolve consistently in BOTH
    corpora (all-or-nothing: a partial family is a sign something about the
    call shape changed and a silent guess would be worse than a review flag)."""
    old_m = _actor_weapon_slot_matches(old_dir)
    new_m = _actor_weapon_slot_matches(new_dir)
    wanted = set(_ACTOR_WEAPON_SLOT_KEYS.values())
    if set(old_m) != wanted or set(new_m) != wanted:
        return {}
    if len({v[0] for v in old_m.values()}) != 1 or len({v[1] for v in old_m.values()}) != 1:
        return {}
    new_roots = {v[0] for v in new_m.values()}
    new_strides = {v[1] for v in new_m.values()}
    if len(new_roots) != 1 or len(new_strides) != 1:
        return {}
    new_root, new_stride = next(iter(new_roots)), next(iter(new_strides))

    out = {}
    for offset_name, key in _ACTOR_WEAPON_SLOT_KEYS.items():
        out[offset_name] = f"{_ACTOR_ROOT}.f_{new_root}.f_9.f_{new_m[key][2]}"
    out["OFFSET_actor_actv_bs"] = f"{_ACTOR_ROOT}.f_{new_root}.f_9"
    out["OFFSET_actor_actv_NEXT"] = str(new_stride)
    return out


# ---------------------------------------------------------------------------
# Whole array blocks that move as one unit.
#
# published_* / saved_* / gbtpi+gbtpp are all leaves of ONE array base each
# (``Global_993502.f_4``, ``Global_1011388.f_33``,
# ``Global_4718592.f_3605[..].f_6494``). The generic pipeline reports every leaf
# as ``not_found_in_old``: the bases live in the UGC/launcher code paths, where
# they are never DATADICT-serialized under a stable key, so there is no name to
# look up and nothing structural to step.
#
# Between 1.71 and 1.73 the *layout* of these blocks did not change at all -
# only the base moved (e.g. Global_993502 -> Global_995355). Verified by
# comparing the full set of field numbers accessed on the old base against the
# new one: identical in both families. So instead of resolving 21 leaves
# individually, resolve each BASE from a stable anchor and re-base every leaf
# that hangs off it, keeping its field suffix untouched.
#
# Every anchor below must match exactly ONE base in each corpus, otherwise the
# family is dropped (a silent guess here would ship wrong offsets to users).

# saved_*: the one place the block is written from UGC metadata. The index is a
# plain local (``[iVar3 /*95*/]``); the sibling array that shares this call
# shape is indexed by a parameter deref (``[uParam0->f_8 /*95*/]``), so
# requiring a bare identifier between the brackets picks exactly one.
_SAVED_BASE_RE = re.compile(
    r"StringCopy\(&\(Global_(\d+)\.f_(\d+)\[[A-Za-z_]\w*\s*/\*95\*/\]\.f_22\),\s*"
    r"NETWORK::UGC_GET_CONTENT_NAME"
)

# published_*: no single native pins it - two anchors do. The permissions check
# names the root (via a different field), the string compare names the root AND
# the array field. Two roots satisfy the first, three the second; exactly one
# root satisfies BOTH, in either corpus.
_PUBLISHED_ROOT_RE = re.compile(
    r"Global_(\d+)\.f_\d+\[[^\]]*\][^;]{0,80}?"
    r"NETWORK::NETWORK_START_USER_CONTENT_PERMISSIONS_CHECK"
)
_PUBLISHED_ARRAY_RE = re.compile(
    r"MISC::ARE_STRINGS_EQUAL\(&\(Global_(\d+)\.f_(\d+)\[[^\]]*/\*95\*/\]\),\s*&\("
)

# gbtpi/gbtpp: a 2D array (``f_OUTER[..][13][3]``) under the tuneables root.
# offsets.ini stores the simplified 1D form, which is why the index-aware
# pipeline never matches it. The [13][3] shape is unique under this root.
_GBTP_ROOT = "Global_4718592"
_GBTP_RE = re.compile(
    rf"{_GBTP_ROOT}\.f_(\d+)\[[^\]]*/\*(\d+)\*/\]\.f_(\d+)\[[^\]]*/\*13\*/\]\[[^\]]*/\*3\*/\]"
)


def _sole(directory: pathlib.Path, pattern: re.Pattern, build) -> str | None:
    """The single value ``build(match)`` yields across the corpus, else None."""
    found = set()
    for path in sorted(directory.glob("*.c")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in pattern.finditer(text):
            found.add(build(m))
    return next(iter(found)) if len(found) == 1 else None


def _published_base(directory: pathlib.Path) -> str | None:
    roots = set()
    arrays = {}
    for path in sorted(directory.glob("*.c")):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in _PUBLISHED_ROOT_RE.finditer(text):
            roots.add(m.group(1))
        for m in _PUBLISHED_ARRAY_RE.finditer(text):
            arrays.setdefault(m.group(1), set()).add(m.group(2))
    hits = roots & set(arrays)
    if len(hits) != 1:
        return None
    root = next(iter(hits))
    fields = arrays[root]
    if len(fields) != 1:
        return None
    return (root, next(iter(fields)))


def _saved_base(directory: pathlib.Path):
    return _sole(directory, _SAVED_BASE_RE, lambda m: (m.group(1), m.group(2)))


def _gbtp_base(directory: pathlib.Path):
    return _sole(directory, _GBTP_RE, lambda m: (m.group(1), m.group(2), m.group(3)))


def _flat_rule(old, new):
    """``Global_A.f_B`` base: plain prefix swap, nothing to preserve."""
    pattern = re.compile(rf"^Global_{old[0]}\.f_{old[1]}(?=$|\.)")
    return pattern, f"Global_{new[0]}.f_{new[1]}"


def _gbtp_rule(old, new):
    """``Global_R.f_OUTER[idx /*stride*/].f_INNER`` base: the index variable name
    is assigned by the decompiler and is NOT stable, so keep whatever the
    offsets.ini already uses and swap only the numbers around it."""
    pattern = re.compile(
        rf"^{_GBTP_ROOT}\.f_{old[0]}\[(?P<idx>[^\]]*?)\s*/\*{old[1]}\*/\]\.f_{old[2]}(?=$|\.|\[)"
    )
    return pattern, rf"{_GBTP_ROOT}.f_{new[0]}[\g<idx> /*{new[1]}*/].f_{new[2]}"


_ARRAY_FAMILIES = (
    # (base resolver, rule builder)
    (_saved_base, _flat_rule),          # saved_*
    (_published_base, _flat_rule),      # published_*
    (_gbtp_base, _gbtp_rule),           # gbtpi / gbtpp
)


def resolve_moved_array_bases(old_dir: pathlib.Path, new_dir: pathlib.Path) -> list:
    """``[(old-base pattern, new-base replacement)]`` for array blocks that move
    as a unit. A family is only included when its anchor resolves to a single
    base in BOTH corpora and the base actually moved."""
    rules = []
    for resolve_base, make_rule in _ARRAY_FAMILIES:
        old_base = resolve_base(old_dir)
        new_base = resolve_base(new_dir)
        if old_base and new_base and old_base != new_base:
            rules.append(make_rule(old_base, new_base))
    return rules


_INDEX_NAMES = ("i", "j", "k", "l")
_BARE_INDEX_RE = re.compile(r"\[([A-Za-z_]\w*)(\s*/\*\d+\*/\])")


def _normalize_indices(value: str) -> str:
    """Rename bare array-index variables to the ini's own ``i``/``j``/``k``
    convention. The decompiler names them per function (``bVar0``, ``iVar3``,
    ...), so a value carried straight over from the previous version keeps a
    name that means nothing here; every other migrated value in offsets.ini
    uses the positional convention. Index expressions that are not a plain
    identifier are left alone."""
    count = 0

    def repl(m: re.Match) -> str:
        nonlocal count
        name = _INDEX_NAMES[count] if count < len(_INDEX_NAMES) else m.group(1)
        count += 1
        return f"[{name}{m.group(2)}"

    return _BARE_INDEX_RE.sub(repl, value)


def rebase_value(value: str, rules: list) -> str | None:
    """Re-base one offsets.ini value onto its family's new base, keeping the
    field suffix untouched. ``None`` when no family applies."""
    for pattern, replacement in rules:
        new_value, n = pattern.subn(replacement, value, count=1)
        if n:
            return _normalize_indices(new_value)
    return None
