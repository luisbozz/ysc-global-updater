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
from tools.dialect import read_source

# offset name -> (regex with the numeric Global_/.f_ parts captured, format template)
_ANCHORS: dict[str, tuple[re.Pattern, str]] = {
    # OFFSET_check_creator: first statement of the "can the creator (re)load
    # safely" helper. Anchored on the exact local-declaration block + the
    # literal struct<5> field-constant assignments that immediately follow the
    # Global_ write (the enclosing func_N name/number is NOT stable, this body
    # shape is).
    # Anchored on the five struct-field constants, not on the local declarations
    # above them. The Enhanced corpus is decompiled with names instead of
    # numbered locals -- "Vector3 vector" where Legacy has "struct<5> Var3",
    # "BOOL flag" for "bool bVar0" -- so an anchor spelling those names out
    # silently matches nothing and the offset keeps its old value. That is the
    # worst outcome: it reads as "unchanged" rather than as a failure, and
    # check_creator gates whether the editor loads anything at all, so a stale
    # value leaves every field in the app empty with no error anywhere.
    #
    # The field constants survive the rename because they are part of the game's
    # data, not the decompiler's vocabulary.
    # OFFSET_dprops_number: the dynamic prop count. The creator passes "count minus
    # the field after it" to a limit check; that expression occurs once per corpus.
    # The plain migration mapped 1.71's f_48742 onto an unrelated field (f_49494)
    # while the count moved to f_51387, two slots before the dynamic prop array.
    # The optional '(' and '*' cover both decompilers' spelling.
    "OFFSET_dprops_number": (
        re.compile(
            r"\(?\*?Global_4980736\.f_(\d+) - \*?Global_4980736\.f_\d+\)?, 0, func_\d+\(\)\)"
        ),
        "Global_4980736.f_{0}",
    ),
    "OFFSET_check_creator": (
        re.compile(
            r"Global_(\d+)\s*=\s*1;\s*\w+\s*=\s*false;\s*"
            r"(\w+)\.f_4\s*=\s*3;\s*\2\.f_8\s*=\s*3;\s*\2\.f_64\s*=\s*3;\s*"
            r"\2\.f_75\s*=\s*3;\s*\2\.f_91\s*=\s*3;"
        ),
        "Global_{0}",
    ),
    # OFFSET_hide_creator_menu: part of the native-call chain deciding whether
    # the pause/creator menu may show. Anchored on the surrounding native names
    # (stable API surface) rather than the Global_ numbers (which shift).
    #
    # The closing parenthesis after each term is optional: the newer decompiler
    # prints this chain without the redundant grouping the older one emits.
    # Requiring it would silence the anchor on an Enhanced corpus, and the
    # offset would fall through to REVIEW with its old value intact -- which
    # looks like "nothing changed" and is the one failure mode worth avoiding.
    "OFFSET_hide_creator_menu": (
        re.compile(
            r"NETWORK::IS_COMMERCE_STORE_OPEN\(\)\)?\s*\|\|\s*Global_\d+\)?\s*\|\|\s*"
            r"Global_(\d+)\.f_(\d+)\)?\s*\|\|\s*HUD::IS_WARNING_MESSAGE_ACTIVE\(\)"
        ),
        "Global_{0}.f_{1}",
    ),
    # OFFSET_cps_type / OFFSET_racetype: the creator's mission-type field, under
    # the tuneables root. It has no DATADICT key, so nothing in the semantic or
    # structural path reaches it. What does pin it is the run of literal type
    # constants it is compared against: those are game content and survive a
    # build, while the field number does not. Two terms of the chain are enough
    # to be unique in both corpora; the back-reference keeps it to one field.
    "OFFSET_cps_type": (
        re.compile(
            r"Global_4718592\.f_(\d+) == 6 \|\| Global_4718592\.f_\1 == 7"
        ),
        "Global_4718592.f_{0}",
    ),
    # Same field, second name.
    "OFFSET_racetype": (
        re.compile(
            r"Global_4718592\.f_(\d+) == 6 \|\| Global_4718592\.f_\1 == 7"
        ),
        "Global_4718592.f_{0}",
    ),
    # OFFSET_adlc: a 2D tuneable array written into a data file. The native plus
    # the literal /*5*/ outer dimension is unique under this root in both
    # corpora; the field number itself moves.
    "OFFSET_adlc": (
        re.compile(
            r"DATAARRAY_ADD_INT\([^;]{0,40}, Global_4718592\.f_(\d+)\[[^\]]*/\*5\*/\]\["
        ),
        "Global_4718592.f_{0}",
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


# Offsets that live in a script the main corpus does not carry.
#
# The corpus is the eight creator and launcher scripts, because that is where
# the creator's own data lives. The launch sequence does not: it flips flags in
# maintransition.c, which drives the transition into and out of a creator. Those
# globals were therefore never migrated -- the pipeline could not see them, and
# kept their old values without reporting anything.
#
# Pulling maintransition.c into the corpus proper would change what every other
# resolver sees, for one handful of offsets. It is fetched into a `context/`
# subfolder instead, which neither the corpus glob nor select_sources looks at,
# and read here by name.
#
# Each anchor is a piece of the surrounding code that is game logic rather than
# a global number: a comparison against literal constants, or a call expression
# whose shape survives a rebuild. Every one of these resolves to exactly one
# global in each corpus, or the offset is left alone.
_CONTEXT_FILE = "maintransition.c"

_CONTEXT_ANCHORS = {
    # launch_creator_local_3: the only place two adjacent globals are both
    # tested against zero in one condition.
    "OFFSET_launch_creator_local_3": (
        re.compile(r"if \(Global_(\d+) == 0 \|\| Global_\d+ == 0\)"),
        "Global_{0}",
    ),
    # launch_creator_local_4: a getter whose only call site sits in a long
    # disjunction next to IS_PLAYER_SWITCH_IN_PROGRESS. The function numbers
    # move every build; the shape of the expression does not.
    "OFFSET_launch_creator_local_4": (
        re.compile(
            r"YER_SWITCH_IN_PROGRESS\(\) \|\| func_\d+\(\) == 2\) \|\| "
            r"func_\d+\(\) == 3\) \|\| func_(\d+)\(\)\)"
        ),
        "__getter_func_{0}",      # resolved to its global below
    ),
    # launch_creator_local_5: a state field compared against three literal
    # values. Those constants are game logic and outlive the field number.
    "OFFSET_launch_creator_local_5": (
        re.compile(
            r"if \(\(Global_(\d+) != 4 && Global_\1 != 5\) && Global_\1 != 7\)"
        ),
        "Global_{0}",
    ),
}

# transitionState has no context of its own -- it is only ever read and written
# through a getter/setter pair. What does pin it is its position in a run of
# consecutive one-line getters: three of them return descending global numbers.
# That run occurs four times in the script and this is the last one, in both
# corpora.
_ONE_LINE_GETTER_RE = re.compile(r"^\s*return Global_(\d+);\s*$", re.M)


def _descending_getter_runs(text: str) -> list:
    """Globals that sit in the middle of three consecutive descending getters."""
    values = [int(m.group(1)) for m in _ONE_LINE_GETTER_RE.finditer(text)]
    return [b for a, b, c in zip(values, values[1:], values[2:])
            if a == b + 1 == c + 2]


def _getter_global(text: str, func: str) -> str | None:
    """The global a one-line getter function returns."""
    m = re.search(rf"\w+ {func}\(\)\s*\{{\s*return Global_(\d+);\s*\}}", text)
    return m.group(1) if m else None


def resolve_context_offsets(old_dir: pathlib.Path, new_dir: pathlib.Path) -> dict:
    """``{offset name: (old value, new value)}`` from the context script.

    Both sides are resolved, not just the new one. The caller applies the new
    value only where the ini still holds the old one -- an anchor that resolves
    cleanly in both corpora can still be the wrong anchor for a given offset,
    and the old value is the one piece of evidence that says otherwise.

    Empty when the script is missing, so a corpus fetched before this existed
    simply resolves nothing instead of failing."""
    old_file = old_dir / "context" / _CONTEXT_FILE
    new_file = new_dir / "context" / _CONTEXT_FILE
    if not (old_file.is_file() and new_file.is_file()):
        return {}
    old_text, new_text = read_source(old_file), read_source(new_file)

    def resolve(text: str, pattern: re.Pattern, template: str) -> str | None:
        hits = {m.group(1) for m in pattern.finditer(text)}
        if len(hits) != 1:
            return None       # ambiguous or gone -> leave the offset alone
        value = template.format(next(iter(hits)))
        if value.startswith("__getter_func_"):
            g = _getter_global(text, "func_" + value[len("__getter_func_"):])
            return f"Global_{g}" if g else None
        return value

    out = {}
    for name, (pattern, template) in _CONTEXT_ANCHORS.items():
        before = resolve(old_text, pattern, template)
        after = resolve(new_text, pattern, template)
        if before and after:
            out[name] = (before, after)

    # transitionState: the last descending getter run. Requiring the same number
    # of runs on both sides keeps a restructured script from silently matching a
    # different one.
    old_runs = _descending_getter_runs(old_text)
    new_runs = _descending_getter_runs(new_text)
    if old_runs and len(old_runs) == len(new_runs):
        out["OFFSET_transitionState"] = (f"Global_{old_runs[-1]}",
                                         f"Global_{new_runs[-1]}")
    return out


def _matches(directory: pathlib.Path, pattern: re.Pattern, template: str) -> set:
    values: set = set()
    for path in sorted(directory.glob("*.c")):
        text = read_source(path)
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
            text = read_source(path)
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

# The two corpora are produced by different builds of the GTA V script
# decompiler, and the newer one (used for the Enhanced dumps) writes the same
# code differently:
#
#   Legacy    StringCopy(&(Global_1015489.f_33[iVar3 /*95*/].f_22), ...)
#   Enhanced  TEXT_LABEL_ASSIGN_STRING(&Global_1015489.f_33[i /*95*/].f_22, ...)
#
# Two things changed: the string-assign helper was renamed, and the redundant
# parentheses around an address-of expression were dropped. Neither is a game
# difference, so the anchors accept both spellings; requiring one dialect would
# silently drop every family on the other side of the split.
_STR_ASSIGN = r"(?:StringCopy|TEXT_LABEL_ASSIGN_STRING)"

# saved_*: the one place the block is written from UGC metadata. The index is a
# plain local (``[iVar3 /*95*/]``); the sibling array that shares this call
# shape is indexed by a parameter deref (``[uParam0->f_8 /*95*/]``), so
# requiring a bare identifier between the brackets picks exactly one.
_SAVED_BASE_RE = re.compile(
    rf"{_STR_ASSIGN}\(&\(?Global_(\d+)\.f_(\d+)\[[A-Za-z_]\w*\s*/\*95\*/\]\.f_22\)?,\s*"
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
    r"MISC::ARE_STRINGS_EQUAL\(&\(?Global_(\d+)\.f_(\d+)\[[^\]]*/\*95\*/\]\)?,\s*&\(?"
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
        text = read_source(path)
        for m in pattern.finditer(text):
            found.add(build(m))
    return next(iter(found)) if len(found) == 1 else None


def _published_base(directory: pathlib.Path) -> str | None:
    roots = set()
    arrays = {}
    for path in sorted(directory.glob("*.c")):
        text = read_source(path)
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
